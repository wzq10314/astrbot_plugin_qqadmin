function pathJoin(prefix, key) {
  return prefix ? `${prefix}.${key}` : key;
}

function getCollapsedObjectPaths(options = {}) {
  if (options.collapsedObjectPaths instanceof Set) {
    return options.collapsedObjectPaths;
  }
  if (Array.isArray(options.collapsedObjectPaths)) {
    return new Set(options.collapsedObjectPaths);
  }
  return new Set();
}

function normalizeOptions(options = {}) {
  return {
    ...options,
    collapsedObjectPaths: getCollapsedObjectPaths(options),
  };
}

function isDisabledPath(path, options = {}) {
  if (typeof options.isFieldDisabled !== "function") {
    return false;
  }
  return Boolean(options.isFieldDisabled(path));
}

function setByPath(target, path, value) {
  const parts = path.split(".");
  let cursor = target;

  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      cursor[part] = value;
      return;
    }
    if (!cursor[part] || typeof cursor[part] !== "object") {
      cursor[part] = {};
    }
    cursor = cursor[part];
  });
}

function buildField(path, key, schema, value, options = {}) {
  const type = schema.type || "string";
  const disabled = isDisabledPath(path, options);

  if (type === "object") {
    const wrapper = document.createElement("section");
    wrapper.className = "form-object";
    const isCollapsible = options.collapsedObjectPaths.has(path);
    if (disabled) {
      wrapper.classList.add("is-disabled");
    }
    let bodyHost = wrapper;

    if (isCollapsible) {
      wrapper.classList.add("is-collapsible");

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "form-object-toggle";
      toggle.disabled = disabled;

      const copy = document.createElement("span");
      copy.className = "form-object-toggle-copy section-head";

      const title = document.createElement("span");
      title.className = "section-title";
      title.textContent = schema.description || key;
      copy.appendChild(title);

      if (schema.hint) {
        const hint = document.createElement("span");
        hint.className = "section-hint";
        hint.textContent = schema.hint;
        copy.appendChild(hint);
      }

      const action = document.createElement("span");
      action.className = "form-object-toggle-action";

      let collapsed = true;
      const syncCollapsedState = () => {
        wrapper.classList.toggle("is-collapsed", collapsed);
        toggle.setAttribute("aria-expanded", String(!collapsed));
        action.textContent = collapsed ? "展开" : "收起";
      };

      toggle.appendChild(copy);
      toggle.appendChild(action);
      toggle.addEventListener("click", () => {
        collapsed = !collapsed;
        syncCollapsedState();
      });
      wrapper.appendChild(toggle);

      const body = document.createElement("div");
      body.className = "form-object-body";
      wrapper.appendChild(body);
      bodyHost = body;

      syncCollapsedState();
    } else {
      const header = document.createElement("div");
      header.className = "section-head";

      const title = document.createElement("div");
      title.className = "section-title";
      title.textContent = schema.description || key;
      header.appendChild(title);

      if (schema.hint) {
        const hint = document.createElement("div");
        hint.className = "section-hint";
        hint.textContent = schema.hint;
        header.appendChild(hint);
      }

      wrapper.appendChild(header);
    }

    const grid = document.createElement("div");
    grid.className = "field-grid";
    if (options.singleColumn) {
      grid.classList.add("single-column");
    }
    Object.entries(schema.items || {}).forEach(([childKey, childSchema]) => {
      grid.appendChild(
        buildField(
          pathJoin(path, childKey),
          childKey,
          childSchema,
          value?.[childKey] ?? childSchema.default,
          options
        )
      );
    });
    bodyHost.appendChild(grid);
    return wrapper;
  }

  const field = document.createElement("label");
  field.className = "field";
  if (type === "bool") {
    field.classList.add("checkbox-field");
  }
  if (disabled) {
    field.classList.add("is-disabled");
  }

  const copy = document.createElement("div");
  copy.className = "field-copy";

  const label = document.createElement("div");
  label.className = "field-label";
  label.textContent = schema.description || key;
  copy.appendChild(label);

  if (schema.hint) {
    const hint = document.createElement("div");
    hint.className = "field-hint";
    hint.textContent = schema.hint;
    copy.appendChild(hint);
  }

  field.appendChild(copy);

  const control = document.createElement("div");
  control.className = "field-control";

  let input;
  if (type === "bool") {
    const shell = document.createElement("span");
    shell.className = "switch";
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    const slider = document.createElement("span");
    slider.className = "slider";
    shell.appendChild(input);
    shell.appendChild(slider);
    control.appendChild(shell);
  } else if (schema.options?.length) {
    input = document.createElement("select");
    schema.options.forEach((option) => {
      const node = document.createElement("option");
      node.value = option;
      node.textContent = option;
      if (String(value ?? schema.default ?? "") === option) {
        node.selected = true;
      }
      input.appendChild(node);
    });
    control.appendChild(input);
  } else if (type === "int") {
    input = document.createElement("input");
    input.type = "number";
    input.value = String(value ?? schema.default ?? 0);
    if (schema.slider) {
      if (schema.slider.min !== undefined) input.min = schema.slider.min;
      if (schema.slider.max !== undefined) input.max = schema.slider.max;
      if (schema.slider.step !== undefined) input.step = schema.slider.step;
    }
    control.appendChild(input);
  } else if (type === "list") {
    input = document.createElement("textarea");
    input.value = Array.isArray(value) ? value.join("\n") : "";
    input.placeholder = "每行一个条目，也支持粘贴后分行整理";
    control.appendChild(input);
  } else {
    const multiline =
      type === "text" ||
      String(value || "").includes("\n") ||
      key.includes("welcome");
    input = document.createElement(multiline ? "textarea" : "input");
    if (!multiline) {
      input.type = "text";
    }
    input.value = String(value ?? schema.default ?? "");
    control.appendChild(input);
  }

  input.dataset.path = path;
  input.dataset.type = type;
  input.disabled = disabled;
  field.appendChild(control);
  return field;
}

export function renderSchemaFields(root, schema, values, options = {}) {
  const normalizedOptions = normalizeOptions(options);
  root.innerHTML = "";

  const grid = document.createElement("div");
  grid.className = "field-grid";
  if (normalizedOptions.singleColumn) {
    grid.classList.add("single-column");
  }

  Object.entries(schema).forEach(([key, fieldSchema]) => {
    grid.appendChild(
      buildField(
        key,
        key,
        fieldSchema,
        values?.[key] ?? fieldSchema.default,
        normalizedOptions
      )
    );
  });

  root.appendChild(grid);
}

export function collectFormData(root) {
  const payload = {};
  root.querySelectorAll("[data-path]").forEach((node) => {
    const { path, type } = node.dataset;
    let value;

    if (type === "bool") {
      value = node.checked;
    } else if (type === "int") {
      value = Number(node.value || 0);
    } else if (type === "list") {
      value = node.value
        .split(/\n+/)
        .map((item) => item.trim())
        .filter(Boolean);
    } else {
      value = node.value;
    }

    setByPath(payload, path, value);
  });
  return payload;
}
