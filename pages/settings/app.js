import { createApi } from "./api.js";
import {
  collectFormData,
  renderSchemaFields,
} from "./form-renderer.js";
import {
  renderGroupCards,
  renderGroupDetailHeader,
} from "./group-view.js";

const bridge = window.AstrBotPluginPage;
const root = document.documentElement;
const themeMediaQuery =
  typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
const THEME_STORAGE_KEY = "qqadmin-page-theme-mode";
const DEFAULT_GROUP_ID = "__default__";
const COLLAPSED_GROUP_OBJECT_PATHS = new Set(["perms"]);
const FOLLOW_DEFAULT_KEY = "follow_default";

let api = null;
let bootstrapData = null;
let currentGroup = null;
let allGroups = [];
let detachContextHandler = null;
let detachSystemThemeHandler = null;
let themePreference = loadThemePreference();
let groupRoleSyncToken = 0;

const els = {
  groupForm: document.getElementById("groupForm"),
  groupList: document.getElementById("groupList"),
  groupSearchInput: document.getElementById("groupSearchInput"),
  currentGroupName: document.getElementById("currentGroupName"),
  groupListCount: document.getElementById("groupListCount"),
  toastLayer: document.getElementById("toastLayer"),
  toggleThemeBtn: document.getElementById("toggleThemeBtn"),
  refreshGroupsBtn: document.getElementById("refreshGroupsBtn"),
  saveGroupBtn: document.getElementById("saveGroupBtn"),
  resetGroupBtn: document.getElementById("resetGroupBtn"),
};

function loadThemePreference() {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "auto") {
      return stored;
    }
  } catch {}
  return "auto";
}

function saveThemePreference() {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
  } catch {}
}

function getThemeButtonLabel() {
  if (themePreference === "dark") {
    return "主题：深色";
  }
  if (themePreference === "light") {
    return "主题：浅色";
  }
  return "主题：自动";
}

function updateThemeButton() {
  if (els.toggleThemeBtn) {
    els.toggleThemeBtn.textContent = getThemeButtonLabel();
  }
}

function getBridgeThemeMode(context) {
  if (context?.theme === "dark" || context?.theme === "light") {
    return context.theme;
  }
  return null;
}

function getSystemThemeMode() {
  return themeMediaQuery?.matches ? "dark" : "light";
}

function resolveThemeMode(context) {
  if (themePreference === "dark" || themePreference === "light") {
    return themePreference;
  }

  const bridgeThemeMode = getBridgeThemeMode(context);
  if (bridgeThemeMode) {
    return bridgeThemeMode;
  }

  return getSystemThemeMode();
}

function applyThemeMode(themeMode) {
  root.dataset.theme = themeMode;
  root.style.colorScheme = themeMode;
}

function syncThemeFromContext(context) {
  applyThemeMode(resolveThemeMode(context));
  updateThemeButton();
}

function cycleThemePreference() {
  if (themePreference === "auto") {
    themePreference = "dark";
  } else if (themePreference === "dark") {
    themePreference = "light";
  } else {
    themePreference = "auto";
  }
  saveThemePreference();
  syncThemeFromContext(bridge?.getContext?.());
}

function bindSystemTheme() {
  if (!themeMediaQuery) {
    return;
  }

  const handleThemeChange = () => {
    if (themePreference === "auto") {
      applyThemeMode(resolveThemeMode(bridge?.getContext?.()));
    }
  };

  if (typeof themeMediaQuery.addEventListener === "function") {
    themeMediaQuery.addEventListener("change", handleThemeChange);
    detachSystemThemeHandler = () => {
      themeMediaQuery.removeEventListener("change", handleThemeChange);
    };
    return;
  }

  if (typeof themeMediaQuery.addListener === "function") {
    themeMediaQuery.addListener(handleThemeChange);
    detachSystemThemeHandler = () => {
      themeMediaQuery.removeListener(handleThemeChange);
    };
  }
}

function showToast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  els.toastLayer.appendChild(node);
  setTimeout(() => node.remove(), 2600);
}

function getDefaultGroupConfigValues() {
  const groups = Array.isArray(bootstrapData?.groups) ? bootstrapData.groups : [];
  const defaultGroup = groups.find((group) => group.group_id === DEFAULT_GROUP_ID);
  if (defaultGroup?.config) {
    return defaultGroup.config;
  }
  if (currentGroup?.group_id === DEFAULT_GROUP_ID) {
    return currentGroup?.config || {};
  }
  return {};
}

function buildGroupFormValues(groupPayload) {
  const defaultValues = getDefaultGroupConfigValues();
  const currentValues = groupPayload?.config || {};
  const followDefault = Boolean(currentValues[FOLLOW_DEFAULT_KEY]);
  const mergedValues = followDefault && !groupPayload?.is_default_group
    ? {
        ...defaultValues,
        [FOLLOW_DEFAULT_KEY]: true,
      }
    : currentValues;
  return mergedValues;
}

function isGroupFieldDisabled(path) {
  if (!currentGroup || currentGroup.is_default_group) {
    return false;
  }
  if (!Boolean(currentGroup.config?.[FOLLOW_DEFAULT_KEY])) {
    return false;
  }
  return path !== FOLLOW_DEFAULT_KEY;
}

function updateGroupActionState() {
  const isDefaultGroup = Boolean(currentGroup?.is_default_group);
  const isFollowingDefault = Boolean(currentGroup?.config?.[FOLLOW_DEFAULT_KEY]);

  els.resetGroupBtn.disabled = isDefaultGroup || isFollowingDefault;
  els.resetGroupBtn.textContent = isDefaultGroup
    ? "默认群不支持重置"
    : isFollowingDefault
      ? "当前正在跟随默认配置"
      : "恢复当前项默认值";
  els.saveGroupBtn.textContent = isDefaultGroup
    ? "保存默认群模板"
    : "保存当前项配置";
}

function normalizeGroups(groups) {
  return Array.isArray(groups) ? groups : [];
}

function applyGroupList(groups) {
  allGroups = normalizeGroups(groups);
  bootstrapData.groups = allGroups;
  filterAndRenderGroups();
}

function scheduleGroupRoleSync(options = {}) {
  const requestToken = ++groupRoleSyncToken;
  void syncGroupRoles(requestToken, options);
}

function filterGroups() {
  const keyword = String(els.groupSearchInput.value || "")
    .trim()
    .toLowerCase();
  if (!keyword) {
    return allGroups;
  }
  return allGroups.filter((group) => {
    const groupId = String(group.group_id || "").toLowerCase();
    const groupName = String(group.group_name || "").toLowerCase();
    return groupId.includes(keyword) || groupName.includes(keyword);
  });
}

function filterAndRenderGroups() {
  const groups = filterGroups();
  els.groupListCount.textContent = `${groups.length} 个群`;
  renderGroupCards({
    root: els.groupList,
    groups,
    currentGroupId: currentGroup?.group_id || "",
    onSelect: async (groupId) => {
      try {
        await switchGroup(groupId);
      } catch (error) {
        showToast(error.message, "error");
      }
    },
  });
}

function renderGroupForm(groupPayload) {
  currentGroup = groupPayload;

  renderGroupDetailHeader(els, groupPayload);
  renderSchemaFields(
    els.groupForm,
    bootstrapData.schema.group || {},
    buildGroupFormValues(groupPayload),
    {
      singleColumn: true,
      collapsedObjectPaths: COLLAPSED_GROUP_OBJECT_PATHS,
      isFieldDisabled: isGroupFieldDisabled,
    }
  );
  bindFollowDefaultToggle();
  updateGroupActionState();
  filterAndRenderGroups();
}

async function loadBootstrapData() {
  const data = await api.safeGet("settings/bootstrap");
  bootstrapData = data;
  applyGroupList(data.groups || []);
}

async function syncGroupRoles(requestToken, options = {}) {
  const { force = false } = options;
  try {
    const groups = await api.safePost("settings/groups/roles", {
      force: force ? "1" : "0",
    });
    if (requestToken !== groupRoleSyncToken) {
      return;
    }
    applyGroupList(groups || []);
  } catch (error) {
    if (requestToken === groupRoleSyncToken) {
      console.debug?.("Failed to sync group bot roles", error);
    }
  }
}

async function refreshGroups() {
  const groups = await api.safePost("settings/groups/refresh", {});
  applyGroupList(groups || []);
}

async function loadGroupConfig(groupId, force = false) {
  const target = String(groupId || currentGroup?.group_id || DEFAULT_GROUP_ID).trim();
  if (!target) {
    showToast("先从左侧选择一个群", "error");
    return;
  }

  const data = await api.safeGet("settings/group", {
    group_id: target,
    force: force ? "1" : "0",
  });
  renderGroupForm(data);
}

function bindFollowDefaultToggle() {
  const followDefaultInput = els.groupForm.querySelector(
    `[data-path="${FOLLOW_DEFAULT_KEY}"]`
  );
  if (!followDefaultInput) {
    return;
  }

  followDefaultInput.addEventListener("change", () => {
    if (!currentGroup?.config) {
      return;
    }
    currentGroup.config[FOLLOW_DEFAULT_KEY] = Boolean(followDefaultInput.checked);
    renderGroupForm(currentGroup);
  });
}

function getCurrentGroupFormPayload() {
  return collectFormData(els.groupForm);
}

async function persistGroupConfig(groupId, options = {}) {
  const {
    refreshList = true,
    rerenderCurrent = true,
    successMessage = "",
  } = options;
  const target = String(groupId || currentGroup?.group_id || "").trim();
  if (!target) {
    showToast("先加载群配置再保存", "error");
    return null;
  }
  const payload = getCurrentGroupFormPayload();
  const data = await api.safePost("settings/group", {
    group_id: target,
    config: payload,
  });
  if (rerenderCurrent) {
    renderGroupForm(data);
  }
  if (refreshList) {
    await refreshGroups();
  }
  if (successMessage) {
    showToast(successMessage);
  }
  return data;
}

async function saveCurrentGroupBeforeSwitch(nextGroupId) {
  const currentGroupId = String(currentGroup?.group_id || "").trim();
  const targetGroupId = String(nextGroupId || "").trim();
  if (!currentGroupId || !targetGroupId || currentGroupId === targetGroupId) {
    return;
  }
  if (!els.groupForm.querySelector("[data-path]")) {
    return;
  }

  await persistGroupConfig(currentGroupId, {
    refreshList: false,
    rerenderCurrent: false,
  });
}

async function switchGroup(groupId) {
  const target = String(groupId || "").trim();
  if (!target) {
    return;
  }

  await saveCurrentGroupBeforeSwitch(target);
  await loadGroupConfig(target);
}

async function saveGroupConfig() {
  const target = String(currentGroup?.group_id || "").trim();
  const data = await persistGroupConfig(target, {
    successMessage: `群 ${target} 配置已保存`,
  });
  return data;
}

async function resetGroupConfig() {
  const target = String(currentGroup?.group_id || "").trim();
  if (!target) {
    showToast("先加载群配置再重置", "error");
    return;
  }
  const data = await api.safePost("settings/group/reset", { group_id: target });
  renderGroupForm(data);
  await refreshGroups();
  showToast(`群 ${target} 已恢复默认群配置`);
}

function bindEvents() {
  els.toggleThemeBtn.addEventListener("click", () => {
    cycleThemePreference();
  });

  els.refreshGroupsBtn.addEventListener("click", async () => {
    try {
      await refreshGroups();
      scheduleGroupRoleSync({ force: true });
      if (currentGroup?.group_id) {
        await loadGroupConfig(currentGroup.group_id);
      }
      showToast("群列表已同步");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.saveGroupBtn.addEventListener("click", async () => {
    try {
      await saveGroupConfig();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.resetGroupBtn.addEventListener("click", async () => {
    try {
      if (currentGroup?.is_default_group) {
        showToast("默认群模板不支持重置", "error");
        return;
      }
      if (currentGroup?.config?.[FOLLOW_DEFAULT_KEY]) {
        showToast("当前群正在跟随默认配置，无需重置", "error");
        return;
      }
      await resetGroupConfig();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.groupSearchInput.addEventListener("input", () => {
    filterAndRenderGroups();
  });
}

async function init() {
  bindSystemTheme();
  updateThemeButton();
  applyThemeMode(resolveThemeMode(null));

  if (!bridge) {
    return;
  }

  try {
    api = createApi(bridge);
  } catch (error) {
    return;
  }

  try {
    if (typeof bridge.ready === "function") {
      const context = await Promise.race([
        bridge.ready(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("Bridge ready timeout")), 5000)
        ),
      ]);
      syncThemeFromContext(context);
    }

    if (typeof bridge.onContext === "function") {
      detachContextHandler = bridge.onContext((context) => {
        syncThemeFromContext(context);
      });
    } else {
      syncThemeFromContext(bridge.getContext?.());
    }

    bindEvents();
    await loadBootstrapData();
    scheduleGroupRoleSync();
    await loadGroupConfig(DEFAULT_GROUP_ID);
  } catch (error) {
    const message = error?.message || "页面初始化失败";
    showToast(message, "error");
  }
}

window.addEventListener("beforeunload", () => {
  detachContextHandler?.();
  detachSystemThemeHandler?.();
});

init();
