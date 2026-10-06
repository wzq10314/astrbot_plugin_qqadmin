from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .config import PluginConfig
from .data import QQAdminDB
from .group_info_cache import QQGroupInfoCache
from .permission import perm_manager
from .utils import parse_bool

DEFAULT_GROUP_ID = "__default__"
FOLLOW_DEFAULT_KEY = "follow_default"


class QQAdminPageService:
    def __init__(self, cfg: PluginConfig, db: QQAdminDB, group_cache: QQGroupInfoCache):
        self.cfg = cfg
        self.db = db
        self.group_cache = group_cache
        self.schema = self._load_schema(cfg.plugin_dir / "_conf_schema.json")

    @property
    def group_schema(self) -> dict[str, Any]:
        return {
            FOLLOW_DEFAULT_KEY: {
                "description": "跟随默认配置",
                "hint": "开启后，该群直接沿用默认群配置，下面的群专属配置项将不可编辑。",
                "type": "bool",
                "default": True,
            },
            **self.schema.get("default", {}).get("items", {}),
            **self._get_group_overlay_schema(),
        }

    async def get_bootstrap_payload(self) -> dict[str, Any]:
        return {
            "schema": {
                "group": self.group_schema,
            },
            "groups": await self.list_groups(),
        }

    def get_default_group_entry(self) -> dict[str, Any]:
        return {
            "group_id": DEFAULT_GROUP_ID,
            "group_name": "默认群",
            "avatar": "",
            "member_count": 0,
            "max_member_count": 0,
            "bot_role": "unknown",
            "is_default_group": True,
            "config": {
                FOLLOW_DEFAULT_KEY: False,
                **copy.deepcopy(self.cfg.build_group_default_config()),
            },
        }

    async def list_groups(self, force: bool = False) -> list[dict[str, Any]]:
        groups = await self.group_cache.list_groups(force=force)
        return await self._build_group_entries(groups)

    async def list_groups_with_bot_roles(
        self, force: bool = False
    ) -> list[dict[str, Any]]:
        groups = await self.group_cache.list_groups_with_bot_roles(
            force_bot_roles=force
        )
        return await self._build_group_entries(groups)

    async def _build_group_entries(
        self,
        groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [self.get_default_group_entry()]
        stale_group_ids: list[str] = []

        for group in groups:
            group_id = str(group.get("group_id", "")).strip()
            if self._should_delete_group(group):
                stale_group_ids.append(group_id)
                continue
            result.append(
                {
                    **group,
                    "is_default_group": False,
                }
            )

        for group_id in stale_group_ids:
            await self._delete_group_data(group_id)

        return result

    async def get_group_config(
        self,
        group_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        if str(group_id).strip() == DEFAULT_GROUP_ID:
            return self.get_default_group_config()

        group_id = self._normalize_group_id(group_id)
        follow_default = self.db.is_group_follow_default(group_id)
        group_info = await self.group_cache.get_group(group_id, force=force)
        if self._should_delete_group(group_info):
            await self._delete_group_data(group_id)
            raise ValueError(f"group {group_id} no longer exists and has been deleted")
        return {
            "group_id": group_id,
            "group_info": group_info,
            "config": {
                FOLLOW_DEFAULT_KEY: follow_default,
                **self.db.get_group_snapshot(group_id),
            },
            "is_default_group": False,
        }

    async def update_group_config(
        self, group_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if str(group_id).strip() == DEFAULT_GROUP_ID:
            return await self.update_default_group_config(payload)

        group_id = self._normalize_group_id(group_id)
        current = self.db.get_group_snapshot(group_id)
        follow_default_current = self.db.is_group_follow_default(group_id)
        sanitized = self._sanitize_value(
            payload,
            {"type": "object", "items": self.group_schema},
            {FOLLOW_DEFAULT_KEY: follow_default_current, **current},
        )
        follow_default = bool(sanitized.pop(FOLLOW_DEFAULT_KEY, True))
        if follow_default:
            await self.db.follow_default(group_id)
        else:
            await self.db.replace_group(group_id, sanitized)
        self.group_cache.invalidate(group_id)
        return await self.get_group_config(group_id)

    async def reset_group_config(self, group_id: str) -> dict[str, Any]:
        if str(group_id).strip() == DEFAULT_GROUP_ID:
            raise ValueError("default group does not support reset")

        group_id = self._normalize_group_id(group_id)
        await self.db.follow_default(group_id)
        self.group_cache.invalidate(group_id)
        return await self.get_group_config(group_id)

    def get_default_group_config(self) -> dict[str, Any]:
        return {
            "group_id": DEFAULT_GROUP_ID,
            "group_info": {
                "group_id": DEFAULT_GROUP_ID,
                "group_name": "默认群",
                "avatar": "",
                "member_count": 0,
                "max_member_count": 0,
            },
            "config": {
                FOLLOW_DEFAULT_KEY: False,
                **copy.deepcopy(self.cfg.build_group_default_config()),
            },
            "is_default_group": True,
        }

    async def update_default_group_config(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current = copy.deepcopy(self.cfg.build_group_default_config())
        sanitized = self._sanitize_value(
            payload,
            {"type": "object", "items": self.group_schema},
            {FOLLOW_DEFAULT_KEY: False, **current},
        )
        sanitized.pop(FOLLOW_DEFAULT_KEY, None)
        self._apply_group_level_updates(sanitized)
        self.db.default_cfg = self.cfg.build_group_default_config()
        self.cfg.refresh_runtime_settings()
        self.cfg.save_config()
        self.group_cache.invalidate()
        return self.get_default_group_config()

    @staticmethod
    def _load_schema(schema_path: Path) -> dict[str, Any]:
        try:
            return json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("加载页面 schema 失败: %s", exc)
            return {}

    @staticmethod
    def _normalize_group_id(group_id: str | int | None) -> str:
        gid = str(group_id or "").strip()
        if not gid or not gid.isdigit():
            raise ValueError("group_id must be a numeric string")
        return gid

    async def _delete_group_data(self, group_id: str) -> None:
        normalized_group_id = self._normalize_group_id(group_id)
        await self.db.delete_group(normalized_group_id)
        self.group_cache.remove_group(normalized_group_id)

    @staticmethod
    def _should_delete_group(group_info: dict[str, Any]) -> bool:
        group_id = str(group_info.get("group_id", "")).strip()
        if not group_id or group_id == DEFAULT_GROUP_ID:
            return False
        try:
            member_count = int(group_info.get("member_count", 0))
        except (TypeError, ValueError):
            member_count = 0
        return member_count <= 0

    def _apply_group_level_updates(self, updated: dict[str, Any]) -> None:
        default_fields = self.schema.get("default", {}).get("items", {})
        default_updates = {
            key: value for key, value in updated.items() if key in default_fields
        }
        self._merge_dict(self.cfg.default, default_updates)

        if "admin_audit" in updated:
            self.cfg.admin_audit = updated["admin_audit"]
        if "random_ban_time" in updated:
            self.cfg.random_ban_time = updated["random_ban_time"]
        if "vote_ban" in updated:
            self.cfg.vote_ban.ttl = updated["vote_ban"]["ttl"]
            self.cfg.vote_ban.threshold = updated["vote_ban"]["threshold"]
        if "llm_get_msg_count" in updated:
            self.cfg.llm_get_msg_count = updated["llm_get_msg_count"]
        if "level_threshold" in updated:
            self.cfg.level_threshold = updated["level_threshold"]
        if "perms" in updated:
            self._merge_dict(self.cfg.perms, updated["perms"])

        perm_manager.refresh(self.cfg, self.db)

    def _get_group_overlay_schema(self) -> dict[str, Any]:
        keys = [
            "admin_audit",
            "random_ban_time",
            "vote_ban",
            "llm_get_msg_count",
            "level_threshold",
            "perms",
        ]
        return {
            key: copy.deepcopy(self.schema[key]) for key in keys if key in self.schema
        }

    @staticmethod
    def _merge_dict(target: dict[str, Any], source: dict[str, Any] | None) -> None:
        if source is None:
            return
        target.clear()
        target.update(copy.deepcopy(source))

    def _sanitize_value(
        self,
        value: Any,
        schema: dict[str, Any],
        current: Any = None,
    ) -> Any:
        field_type = schema.get("type", "string")

        if field_type == "object":
            items = schema.get("items", {})
            payload = value if isinstance(value, dict) else {}
            current_map = current if isinstance(current, dict) else {}
            result: dict[str, Any] = {}
            for key, child_schema in items.items():
                child_current = current_map.get(key, child_schema.get("default"))
                child_value = payload[key] if key in payload else child_current
                result[key] = self._sanitize_value(
                    child_value, child_schema, child_current
                )
            return result

        if field_type == "bool":
            parsed = parse_bool(value)
            if parsed is None:
                raise ValueError(f"invalid bool value: {value}")
            return parsed

        if field_type == "int":
            parsed = int(value)
            slider = schema.get("slider", {})
            minimum = slider.get("min")
            maximum = slider.get("max")
            if minimum is not None:
                parsed = max(int(minimum), parsed)
            if maximum is not None:
                parsed = min(int(maximum), parsed)
            return parsed

        if field_type == "list":
            if value is None:
                return []
            if isinstance(value, str):
                items = re.split(r"[\n,，]+", value)
            elif isinstance(value, list):
                items = value
            else:
                raise ValueError(f"invalid list value: {value}")
            return [str(item).strip() for item in items if str(item).strip()]

        options = schema.get("options")
        parsed = str(value or "")
        if options and parsed not in options:
            return str(current if current is not None else schema.get("default", ""))
        return parsed
