"""QQ official group management, isolated from the OneBot handlers.

Uses the connected SDK client's token/session; never stores another credential.
All mutations check the human actor, even when a model passes need_auth=False.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote

import aiohttp

from astrbot.api import logger


def is_official(event):
    return getattr(event.platform_meta, 'name', '') in {'qq_official', 'qq_official_webhook'}


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


class OfficialError(Exception):
    def __init__(self, message, *, code=None, status=None):
        super().__init__(message)
        self.code, self.status = code, status


class OfficialAPI:
    def __init__(self, event):
        self.http = getattr(getattr(event.bot, 'api', None), '_http', None)
        self.group = str(event.get_group_id() or '')
        raw = getattr(event.message_obj, 'raw_message', None)
        raw_data = field(raw, 'raw_data', {}) or {}
        group_openid = field(raw, 'group_openid') or field(raw_data, 'group_openid')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', self.group) or group_openid != self.group:
            raise OfficialError('请在 QQ 官方机器人的群聊中使用；频道和私聊不执行群管。')
        self.prefix = '/v2/groups/' + quote(self.group, safe='')

    async def request(self, method, suffix, *, payload=None, params=None):
        if self.http is None:
            raise OfficialError('当前官方机器人客户端没有可用的群管认证连接。')
        domain = 'sandbox.api.sgroup.qq.com' if getattr(self.http, 'is_sandbox', False) else 'api.bot.qq.com'
        url = 'https://' + domain + self.prefix + suffix
        try:
            await asyncio.wait_for(self.http.check_session(), timeout=12)
            kwargs = {'headers': self.http._headers, 'timeout': aiohttp.ClientTimeout(total=12),
                      'allow_redirects': False}
            if payload is not None:
                kwargs['json'] = payload
            if params:
                kwargs['params'] = params
            async with self.http._session.request(method, url, **kwargs) as response:
                if response.content_length and response.content_length > 512 * 1024:
                    raise OfficialError('群管接口返回内容异常，操作结果未确认。')
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    data = None
                code = data.get('code') if isinstance(data, dict) else None
                if not 200 <= response.status < 300 or code not in (None, 0, '0'):
                    logger.warning('[QQAdmin Official] API failed: method=%s status=%s code=%s',
                                   method, response.status, code if isinstance(code, (int, str)) else 'unknown')
                    if str(code) == '11253':
                        message = ('QQ 开放平台尚未给这个机器人开通该群管接口（11253）。'
                                   '需要在开放平台申请对应权限；仅把机器人设为群管理员还不够。')
                    elif str(code) == '11703':
                        message = 'QQ 平台拒绝了群管操作（11703）。请先确认机器人在本群具有群管理员身份。'
                    elif response.status in (401, 403):
                        message = ('QQ 平台拒绝了群管请求。请检查该接口的应用权限，'
                                   '并确认机器人已被设置为本群管理员。')
                    elif response.status == 429:
                        message = '群管接口调用过于频繁，请稍后再试。'
                    elif response.status == 404:
                        message = 'QQ 平台未提供该接口或目标不在当前群中，操作未完成。'
                    elif response.status >= 500:
                        message = 'QQ 群管服务返回异常，操作结果未确认；请先查看群状态，勿重复执行。'
                    else:
                        message = f'群管请求未完成（HTTP {response.status}' + (f'，错误码 {code}' if isinstance(code, int) else '') + '）。'
                    raise OfficialError(message, code=code, status=response.status)
                if response.status == 204:
                    return {}
                if not isinstance(data, dict):
                    raise OfficialError('群管接口未返回可确认的结果，请勿重复执行。')
                return data
        except OfficialError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
            raise OfficialError('连接 QQ 群管服务超时或失败，无法确认操作结果，请先查看群状态再重试。') from None


HELP = '''QQ群管理 · 官方机器人
/群管状态 — 检查当前群的接口授权与机器人身份
/群友信息 — 查看当前页成员
/禁言状态 — 查看全员及成员禁言状态
/入群申请 — 查看待处理申请
/禁言 @成员 60 — 禁言 60 秒；可同时 @ 多人
/解禁 @成员 — 解除禁言
/踢了 @成员 — 移出本群
/群拉黑 @成员 — 移出并加入群黑名单
/批准 成员OpenID 申请ID — 批准指定申请
/驳回 成员OpenID 申请ID — 拒绝指定申请
/群黑名单 — 查看黑名单
/解除群拉黑 成员OpenID — 从黑名单移除
操作保留管理员权限检查，需要机器人具备群管理员身份及对应接口权限。
官方 OpenID 与 QQ 号、私聊 UID 不互通；参数目标应使用当前群的成员 OpenID。
原插件的改名、头衔、设管理员、精华、群公告、文件管理、自动审核和宵禁暂不通过此适配执行。'''


COMMANDS = {
    'set_group_ban': 'mute', 'cancel_group_ban': 'unmute',
    'set_group_kick': 'kick', 'set_group_block': 'block',
    'get_group_member_list': 'members', 'agree_add_group': 'approve', 'refuse_add_group': 'decline',
}
TOOLS = {
    'llm_set_group_ban': 'mute', 'llm_set_group_kick': 'kick', 'llm_set_group_block': 'block',
}


class OfficialHandle:
    def __init__(self, plugin):
        self.plugin = plugin
        self._locks = {}

    def _raw(self, event):
        raw = getattr(event.message_obj, 'raw_message', None)
        return raw, field(raw, 'raw_data', {}) or {}

    def targets(self, event, explicit=''):
        raw, data = self._raw(event)
        known = []
        mentions = data.get('mentions', []) if isinstance(data, dict) else []
        mentions = mentions or field(raw, 'mentions', []) or []
        for item in mentions:
            value = field(item, 'member_openid') or field(item, 'id')
            if field(item, 'is_you', False) or field(item, 'bot', False):
                continue
            if value and str(value) != str(event.get_self_id()):
                known.append(str(value))
        if explicit:
            known = re.split(r'[\s,，]+', str(explicit).strip())
        result = list(dict.fromkeys(known))
        if not result:
            raise OfficialError('请 @ 要操作的群成员；自然语言调用也需要明确当前群的成员 OpenID。')
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value) or value in {str(event.get_self_id()), 'qq_official'} for value in result):
            raise OfficialError('目标必须是当前群的成员 OpenID，不能使用数字 QQ 号、机器人自身或私聊 UID。')
        return result

    async def authorize(self, event, api, key):
        # event.is_admin is checked by AstrBot, not by model arguments.
        if event.is_admin():
            return
        raw, data = self._raw(event)
        author = data.get('author', {}) if isinstance(data, dict) else {}
        role = field(author, 'member_role') or field(field(raw, 'author'), 'member_role')
        if role not in {'owner', 'admin', 'member'}:
            info = await api.request('GET', '/members/' + quote(str(event.get_sender_id()), safe=''))
            if info.get('member_openid') != str(event.get_sender_id()):
                raise OfficialError('无法确认你在本群的身份，未执行管理操作。')
            role = info.get('member_role')
        required = self.plugin.db.get_group_snapshot(api.group).get('perms', {}).get(key, '管理员')
        # Preserve configured stronger restrictions; unknown/level-based rules cannot be inferred.
        allowed = {'管理员': {'owner', 'admin'}, '群主': {'owner'},
                   '高等级成员': {'owner', 'admin'}, '成员': {'owner', 'admin', 'member'}}
        if role not in allowed.get(str(required), set()):
            raise OfficialError(f'此操作需要原插件配置的「{required}」权限；你的群身份未满足要求。')

    async def command(self, name, event, arguments, *, perm_key=None):
        action = COMMANDS.get(name)
        if action is None:
            return ('该命令依赖原 OneBot 接口，当前 QQ 官方适配未提供此操作。'
                    '请发送 /群管帮助 查看已接入功能。')
        arguments = dict(arguments)
        if action == 'mute':
            # QQ's core retains non-bot mention text; do not mistake it for duration.
            text = re.sub(r'<@!?[A-Za-z0-9_-]+>', '', event.get_message_str()).strip()
            parts = text.split()
            if parts and parts[0].lstrip('/#') == '禁言':
                rest = parts[1:]
                if len(rest) > 1 or (rest and not re.fullmatch(r'\d+', rest[0])):
                    return '请使用 /禁言 @成员 秒数，例如 /禁言 @成员 60；本次未执行。'
                arguments['ban_time'] = rest[0] if rest else 60
        return await self.run(action, event, arguments, perm_key=perm_key)

    async def run(self, action, event, arguments=None, *, perm_key=None):
        args = arguments or {}
        if event.is_private_chat() or not event.get_group_id():
            return '请在群聊中 @机器人使用群管命令；私聊不执行群管操作。'
        try:
            api = OfficialAPI(event)
            if action == 'status':
                lines = ['QQ群管理接口检查']
                for label, endpoint in [('机器人身份', '/bot_state'), ('群资料', '/info'), ('禁言接口', '/restrict_chat_setting'), ('成员接口', '/members')]:
                    try:
                        result = await api.request('GET', endpoint)
                        role = result.get('member_role')
                        lines.append(label + '：可访问' + (f'（{role}）' if role in {'admin','member','owner'} else ''))
                    except OfficialError as error:
                        lines.append(label + '：' + str(error))
                return '\n'.join(lines)
            key = {'mute': 'set_group_ban', 'unmute': 'set_group_ban', 'kick': 'set_group_kick',
                   'block': 'set_group_block', 'members': 'get_group_member_list',
                   'approve': 'approve', 'decline': 'approve'}.get(action, action)
            await self.authorize(event, api, perm_key or key)
            if action in {'members', 'blacklist', 'requests', 'mutes'}:
                return await self.read(api, action)
            lock_key = (str(getattr(event.platform_meta, 'id', '')), api.group)
            lock = self._locks.setdefault(lock_key, asyncio.Lock())
            if lock.locked():
                raise OfficialError('本群正在处理另一项管理操作，请稍后再试。')
            async with lock:
                state = await api.request('GET', '/bot_state')
                if state.get('member_role') not in {'admin', 'owner'}:
                    raise OfficialError('机器人目前不是本群管理员，请先在 QQ 群设置中将机器人设为管理员，再使用管理命令。')
                return await self.mutate(api, action, event, args)
        except OfficialError as error:
            return str(error)
        except (ValueError, TypeError):
            return '群管处理遇到格式异常，操作结果未确认；请先查看群状态，勿重复执行。'
        except Exception as error:
            logger.error('[QQAdmin Official] Handler failed (%s)', type(error).__name__)
            return '群管适配发生异常，操作结果未确认；请先查看群状态。'

    async def read(self, api, action):
        endpoint, list_key, title = {
            'members': ('/members', 'members', '群友信息'),
            'blacklist': ('/member_blacklist', 'users', '群黑名单'),
            'requests': ('/join_request_list', 'list', '入群申请'),
            'mutes': ('/restrict_chat_setting', 'members', '禁言状态'),
        }[action]
        result = await api.request('GET', endpoint)
        rows = result.get(list_key, [])
        if not isinstance(rows, list):
            raise OfficialError('群管接口返回列表格式异常。')
        lines = [title + '（当前页）']
        if action == 'mutes':
            mode = field(result.get('global_rule', {}), 'mode', '未知')
            lines.append('全员禁言：' + {'none':'关闭','always':'开启','schedule':'按计划'}.get(mode, str(mode)))
        for i, item in enumerate(rows[:30], 1):
            if not isinstance(item, dict):
                continue
            name = str(item.get('username') or '未提供昵称').replace('\n',' ')[:60]
            lines.append(f'{i}. {name} · {item.get("member_role", "")}\n成员 OpenID：{item.get("member_openid", "未提供")}')
            if action == 'requests':
                lines.append('申请 ID：' + str(item.get('join_request_id', '未提供')))
            if action == 'mutes':
                lines.append('禁言到期：' + str(item.get('mute_expire_at', '未提供')))
        if not rows:
            lines.append('当前没有记录。')
        if result.get('next_cursor'):
            lines.append('还有更多记录，本次仅展示当前页。')
        return '\n'.join(lines)

    async def mutate(self, api, action, event, args):
        explicit = args.get('user_id') or args.get('target_id') or args.get('member_id', '')
        if action in {'approve', 'decline'}:
            parts = str(args.get('extra', '')).split()
            if not explicit and parts:
                explicit = parts[0]
            request_id = args.get('request_id') or (parts[1] if len(parts) > 1 else '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', str(request_id)):
                raise OfficialError('请提供成员 OpenID 和申请 ID；可先发送 /入群申请 查看。')
            targets = self.targets(event, explicit)
            if len(targets) != 1:
                raise OfficialError('请一次处理一个指定的入群申请。')
            # Check that request identity belongs to this group, not a stale/contextual hallucination.
            pending = await api.request('GET', '/join_request_list')
            if not any(str(r.get('member_openid')) == targets[0] and str(r.get('join_request_id')) == str(request_id)
                       for r in pending.get('list', []) if isinstance(r, dict)):
                raise OfficialError('当前页没有这条待处理申请，请重新查询；未执行审批。')
            await api.request('POST', '/approval_join_request/' + quote(targets[0], safe=''),
                              payload={'op': action, 'join_request_id': str(request_id)})
            return 'QQ 平台已接受批准指定入群申请的请求。' if action == 'approve' else 'QQ 平台已接受拒绝指定入群申请的请求。'
        targets = self.targets(event, explicit)
        limit = 20
        if len(targets) > limit:
            raise OfficialError(f'本次最多操作 {limit} 位成员，请减少目标数量。')
        if action == 'unblock':
            listed = await api.request('GET', '/member_blacklist', params={'limit':100})
            known = {str(item.get('member_openid')) for item in listed.get('users', []) if isinstance(item, dict)}
            if any(tid not in known for tid in targets):
                raise OfficialError('当前群黑名单页没有这个成员 OpenID，请重新查询；本次未执行解除拉黑。')
            result = await api.request('POST', '/member_blacklist', payload={'op': 'del', 'member_openids': targets})
            if 'fail_openids' not in result:
                return 'QQ 平台已接受解除群拉黑请求，请查看 /群黑名单 确认结果。'
            failed = result['fail_openids']
            if not isinstance(failed, list) or any(tid not in targets for tid in failed):
                raise OfficialError('平台未返回可确认的解除群拉黑结果，请查看 /群黑名单。')
            return f'解除群拉黑请求已完成，成功 {len(targets)-len(failed)} 位，失败 {len(failed)} 位。'
        for tid in targets:
            info = await api.request('GET', '/members/' + quote(tid, safe=''))
            if info.get('member_openid') != tid or info.get('member_role') != 'member' or info.get('bot') is not False:
                raise OfficialError('目标身份未确认或属于群主、管理员、机器人；没有执行本次操作。')
        if action in {'mute', 'unmute'}:
            duration = 0 if action == 'unmute' else args.get('duration', args.get('ban_time'))
            if duration is None:
                duration = 60
            if isinstance(duration, bool) or not re.fullmatch(r'\d+', str(duration)) or not 0 <= int(duration) <= 2592000:
                raise OfficialError('禁言时长请填写 0～2592000 的整数秒数，0 表示解禁。')
            seconds = int(duration)
            expires = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds') if seconds else ''
            members = [{'op': 'add' if seconds else 'del', 'member_openid': tid, 'mute_expire_at': expires} for tid in targets]
            await api.request('POST', '/restrict_chat_setting', payload={'members': members})
            return (f'QQ 平台已接受为 {len(targets)} 位成员解除禁言的请求。' if not seconds else
                    f'QQ 平台已接受为 {len(targets)} 位成员禁言 {seconds} 秒的请求。')
        if action in {'kick', 'block'}:
            result = await api.request('POST', '/batch_remove_members',
                                       payload={'member_openids': targets, 'add_to_member_blacklist': action == 'block'})
            if result.get('remove_members_result') != 'success':
                raise OfficialError('平台未确认移出成功，请先查看群成员列表，勿重复执行。')
            if action != 'block':
                return f'平台确认已移出 {len(targets)} 位成员。'
            if 'add_to_member_blacklist_fail_openids' not in result:
                return f'平台确认已移出 {len(targets)} 位成员；请查看 /群黑名单 确认拉黑结果。'
            failed = result['add_to_member_blacklist_fail_openids']
            if not isinstance(failed, list) or any(tid not in targets for tid in failed):
                return f'平台确认已移出 {len(targets)} 位成员；群拉黑结果未能确认，请查看 /群黑名单。'
            return f'平台确认已移出 {len(targets)} 位成员。' + (f'其中 {len(failed)} 位未成功加入黑名单。' if failed else '平台确认已加入群黑名单。')
        raise OfficialError('该操作尚未接入官方群管。')


def official_tool(func):
    """Before OneBot LLM implementation, always enforce the official path."""
    signature = inspect.signature(func)
    @wraps(func)
    async def wrapper(plugin, event, *args, **kwargs):
        if is_official(event):
            action = TOOLS.get(func.__name__)
            if action:
                bound = signature.bind(plugin, event, *args, **kwargs)
                bound.apply_defaults()
                yield await plugin.official.run(action, event, dict(bound.arguments))
            else:
                yield '此工具依赖 OneBot 接口，当前 QQ 官方适配不执行；请查看 /群管帮助。'
            return
        async for item in func(plugin, event, *args, **kwargs):
            yield item
    return wrapper
