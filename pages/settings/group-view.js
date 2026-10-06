function buildGroupRoleBadge(group) {
  const role = String(group?.bot_role || "").toLowerCase();
  if (role !== "owner" && role !== "admin") {
    return null;
  }

  const badge = document.createElement("span");
  badge.className = `group-role-badge ${role}`;

  const icon = document.createElement("span");
  icon.className = `group-role-icon ${role}`;
  icon.textContent = role === "owner" ? "主" : "管";
  badge.appendChild(icon);

  const text = document.createElement("span");
  text.textContent = role === "owner" ? "群主" : "管理员";
  badge.appendChild(text);

  return badge;
}

export function renderGroupCards({
  root,
  groups,
  currentGroupId,
  onSelect,
}) {
  root.innerHTML = "";

  if (!groups.length) {
    root.classList.add("empty-state");
    root.textContent = "当前没有可显示的群。";
    return;
  }

  root.classList.remove("empty-state");

  groups.forEach((group) => {
    const card = document.createElement("article");
    card.className = "group-card";
    if (group.group_id === currentGroupId) {
      card.classList.add("is-active");
    }

    const avatar = document.createElement("img");
    avatar.className = "group-card-avatar";
    avatar.src =
      group.avatar ||
      "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96' viewBox='0 0 96 96'><rect width='96' height='96' rx='24' fill='%23e8c49a'/><text x='48' y='56' text-anchor='middle' font-size='34' fill='%23824f1f' font-family='Arial'>D</text></svg>";
    avatar.alt = `${group.group_name} 群头像`;
    avatar.loading = "lazy";
    card.appendChild(avatar);

    const main = document.createElement("div");
    main.className = "group-card-main";

    const title = document.createElement("div");
    title.className = "group-card-title";

    const name = document.createElement("div");
    name.className = "group-card-name";
    name.textContent = group.group_name || `群 ${group.group_id}`;
    title.appendChild(name);

    const roleBadge = buildGroupRoleBadge(group);
    if (roleBadge) {
      title.appendChild(roleBadge);
    }

    main.appendChild(title);

    const subline = document.createElement("div");
    subline.className = "group-card-subline";
    if (group.is_default_group) {
      subline.innerHTML = `
        <span class="group-card-id">默认模板</span>
        <span>新群继承这里的配置</span>
      `;
    } else {
      subline.innerHTML = `
        <span class="group-card-id">${group.group_id}</span>
        <span>${group.member_count || 0} 人</span>
      `;
    }
    main.appendChild(subline);

    card.appendChild(main);

    card.addEventListener("click", () => {
      onSelect?.(group.group_id);
    });

    root.appendChild(card);
  });
}

export function renderGroupDetailHeader(els, payload) {
  const info = payload.group_info || {};
  els.currentGroupName.textContent = info.group_name || `群 ${payload.group_id}`;
}
