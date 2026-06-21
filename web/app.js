const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

let appState = {};
let confirmedOrderItems = [];
let confirmedShipmentItems = [];
let robotFetchedItems = [];
let robotFetchedIds = [];
let acceptedRobotOrderIds = [];
let acceptedRobotDeliverDate = "";

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function filesFor(slot) {
  const files = appState.files?.[slot];
  if (!files) return [];
  return Array.isArray(files) ? files : [files];
}

function storedRobotFailureIds() {
  const failures = appState.settings?.robot_mark_failures || [];
  const ids = [];
  const seen = new Set();
  failures.forEach((failure) => {
    (failure.ids || []).forEach((id) => {
      const key = String(id);
      if (seen.has(key)) return;
      seen.add(key);
      ids.push(id);
    });
  });
  return ids;
}

function renderStoredRobotFailures() {
  const target = $("#productionResult");
  if (!target) return;
  const ids = storedRobotFailureIds();
  if (!ids.length) {
    if (target.dataset.persistedFailureNotice === "true") target.innerHTML = "";
    target.dataset.persistedFailureNotice = "";
    return;
  }
  if (target.innerHTML.trim() && target.dataset.persistedFailureNotice !== "true") return;
  target.dataset.persistedFailureNotice = "true";
  target.innerHTML = `<div class="notice">订单库有历史失败 id：${ids.map(escapeHtml).join("、")}。<button data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(ids))}">重试标记</button></div>`;
}

function renderFiles() {
  $$("[data-files]").forEach((node) => {
    const slot = node.dataset.files;
    const files = filesFor(slot);
    node.innerHTML = "";
    if (!files.length) {
      node.innerHTML = `<span class="chip">未保存</span>`;
      return;
    }
    files.forEach((file) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `<span>${file.seeded ? "已载入" : "已保存"} · ${escapeHtml(file.name)} · ${formatTime(file.uploaded_at)}</span>`;
      node.appendChild(chip);
    });
  });
}

function renderText() {
  $$("[data-text-slot]").forEach((textarea) => {
    const slot = textarea.dataset.textSlot;
    const value = appState.text?.[slot]?.value || "";
    if (document.activeElement !== textarea) textarea.value = value;
  });
}

function renderState() {
  renderFiles();
  renderText();
  renderStoredRobotFailures();
  $("#apiState").textContent = "已连接";
  refreshRecipePreview().catch(() => {});
}

async function loadState() {
  appState = await request("/api/state");
  renderState();
}

async function refreshRecipePreview() {
  const target = $("#recipePreview");
  if (!target) return;
  const preview = await request("/api/recipe-preview");
  if (!preview.file_count) {
    target.innerHTML = "";
    return;
  }
  const productNames = preview.products
    .slice(0, 8)
    .map((item) => escapeHtml(item.name))
    .join("、");
  const unrecognized = preview.files.flatMap((file) =>
    (file.unrecognized_sheets || []).map((sheet) => `${file.name}/${sheet}`),
  );
  target.innerHTML = `
    <div>已识别 <strong>${preview.file_count}</strong> 个文件、<strong>${preview.product_count}</strong> 个成品、<strong>${preview.recipe_rows}</strong> 条配料。</div>
    ${productNames ? `<div>示例成品：${productNames}</div>` : ""}
    ${unrecognized.length ? `<div class="notice">未识别 sheet：${unrecognized.slice(0, 6).map(escapeHtml).join("、")}</div>` : ""}
  `;
}

async function uploadFiles(slot, files) {
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  const data = await request(`/api/upload/${slot}`, { method: "POST", body: form });
  appState = data.state;
  renderState();
}

function debounce(fn, wait = 400) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const saveText = debounce(async (slot, value) => {
  const data = await request(`/api/text/${slot}`, {
    method: "POST",
    body: JSON.stringify({ value }),
  });
  appState = data.state;
}, 500);

function input(name, value = "", placeholder = "") {
  return `<input data-name="${name}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" />`;
}

function renderConfirm(container, items, includeStore) {
  container.classList.remove("hidden");
  const rows = items.length ? items : [{ store: "", product: "", quantity: "", unit: "", type: "新增" }];
  container.innerHTML = `
    <div class="confirm-title">识别结果</div>
    <div class="confirm-rows">
      ${rows
        .map(
          (item) => `
          <div class="confirm-grid ${includeStore ? "" : "no-store"}">
            ${includeStore ? input("store", item.store || "", "门店") : ""}
            ${input("product", item.product || "", "商品")}
            ${input("quantity", item.quantity ?? "", "数量")}
            ${input("unit", item.unit || "", "单位")}
            ${input("type", item.type || "新增", "类型")}
            <button class="icon-btn" data-remove-row title="删除">×</button>
          </div>
        `,
        )
        .join("")}
    </div>
    <div class="actions">
      <button class="secondary" data-add-confirm-row>添加一行</button>
      <button class="primary" data-accept-confirm>确认并入</button>
    </div>
  `;
}

function collectConfirm(container, includeStore) {
  return $$(".confirm-grid", container)
    .map((row) => {
      const item = {};
      $$("input", row).forEach((node) => {
        item[node.dataset.name] = node.value.trim();
      });
      item.quantity = Number(item.quantity || 0);
      if (!includeStore) delete item.store;
      return item;
    })
    .filter((item) => item.product && item.quantity);
}

function renderDownload(target, data) {
  target.dataset.persistedFailureNotice = "";
  const warnings = data.warnings || [];
  const missing = data.missing || [];
  let html = "";
  if (missing.length) {
    html += `<div class="notice">缺少：${missing.map(escapeHtml).join("、")}</div>`;
  }
  warnings.forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  if (data.output) {
    html += `<div class="download"><span>${escapeHtml(data.output.name)}</span><a href="${data.output.url}">下载</a></div>`;
  }
  if (data.robot_mark && data.robot_mark.ok !== false && !data.robot_mark.skipped) {
    html += `<div class="download"><span>订单库已标记为已拉取</span></div>`;
  }
  if (data.robot_mark && data.robot_mark.failed && data.robot_mark.failed.length) {
    const failedIds = data.robot_mark.failed;
    html += `<div class="notice">订单库部分 id 标记失败：${failedIds.map(escapeHtml).join("、")}。失败 id 已本地记录。<button data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(failedIds))}">重试标记</button></div>`;
  }
  target.innerHTML = html;
}

function renderRobotFetch(data) {
  const target = $("#robotFetchResult");
  robotFetchedItems = data.items || [];
  robotFetchedIds = data.ids || [];
  acceptedRobotOrderIds = [];
  acceptedRobotDeliverDate = "";

  const warnings = data.warnings || [];
  const rejectedPatches = data.rejected_patches || [];
  const blockingReasons = data.blocking_reasons || [];
  const counts = data.counts || {};
  let html = "";

  warnings.forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  blockingReasons.forEach((reason) => {
    html += `<div class="notice">${escapeHtml(reason)}</div>`;
  });
  if (rejectedPatches.length) {
    const rejectedRows = rejectedPatches
      .map((patch) => {
        const content = (patch.items || [])
          .map((item) => item.label || `${item.name} ${item.qty || ""}${item.unit || ""}`)
          .join("、");
        return `<li>${escapeHtml(patch.store)}：${escapeHtml(content || "未填写商品")}</li>`;
      })
      .join("");
    html += `<div class="notice">以下加货找不到对应门店的主订单，请先上传这些门店的主订单：<ul>${rejectedRows}</ul></div>`;
  }

  if (!robotFetchedItems.length) {
    target.innerHTML = `${html}<div class="notice">订单库没有可并入的待处理订单。</div>`;
    return;
  }

  const canAccept = !blockingReasons.length;
  const groups = (data.grouped || [])
    .map((group) => {
      const items = (group.items || [])
        .slice(0, 18)
        .map((item) => `<li>${escapeHtml(item.name)} × ${escapeHtml(item.quantity)}${escapeHtml(item.unit || "")}</li>`)
        .join("");
      const more = (group.items || []).length > 18 ? `<li>还有 ${(group.items || []).length - 18} 条...</li>` : "";
      return `
        <div class="robot-store">
          <div class="robot-store-title">
            <span>${escapeHtml(group.store)}</span>
            <span>${(group.orders || []).length} 单 · ${(group.items || []).length} 个品</span>
          </div>
          <ul>${items}${more}</ul>
        </div>
      `;
    })
    .join("");

  target.innerHTML = `
    ${html}
    <div class="robot-panel">
      <div class="robot-panel-head">
        <span>订单库本批全貌</span>
        <span>${counts.orders || 0} 单 · ${counts.stores || 0} 门店 · ${counts.items || 0} 行${data.target_deliver_date ? ` · 到货 ${escapeHtml(data.target_deliver_date)}` : ""}</span>
      </div>
      ${groups}
      <div class="actions">
        <button class="primary" id="acceptRobotFetch" ${canAccept ? "" : "disabled"}>确认并入本批</button>
      </div>
    </div>
  `;
  target.dataset.deliverDate = data.target_deliver_date || "";
}

function receiptRow(item = {}) {
  return `
    <div class="receipt-grid">
      ${input("code", item.code || "", "编码")}
      ${input("product", item.product || "", "商品")}
      ${input("spec", item.spec || "", "规格")}
      ${input("unit", item.unit || "", "单位")}
      ${input("quantity", item.quantity || "", "数量")}
      <button class="icon-btn" data-remove-row title="删除">×</button>
    </div>
  `;
}

function addReceiptRow(item = {}) {
  $("#receiptRows").insertAdjacentHTML("beforeend", receiptRow(item));
}

function collectReceiptRows() {
  return $$(".receipt-grid", $("#receiptRows"))
    .map((row) => {
      const item = {};
      $$("input", row).forEach((node) => {
        item[node.dataset.name] = node.value.trim();
      });
      item.quantity = Number(item.quantity || 0);
      return item;
    })
    .filter((item) => item.product && item.quantity);
}

document.addEventListener("change", async (event) => {
  const inputNode = event.target.closest("input[type=file][data-slot]");
  if (!inputNode || !inputNode.files.length) return;
  try {
    await uploadFiles(inputNode.dataset.slot, inputNode.files);
    inputNode.value = "";
  } catch (error) {
    alert(error.message);
  }
});

document.addEventListener("input", (event) => {
  const textarea = event.target.closest("[data-text-slot]");
  if (!textarea) return;
  saveText(textarea.dataset.textSlot, textarea.value);
});

document.addEventListener("click", async (event) => {
  const resetSlot = event.target.closest("[data-reset-slot]");
  const resetModule = event.target.closest("[data-reset-module]");
  const removeRow = event.target.closest("[data-remove-row]");
  const addConfirm = event.target.closest("[data-add-confirm-row]");
  const acceptConfirm = event.target.closest("[data-accept-confirm]");
  const acceptRobotFetch = event.target.closest("#acceptRobotFetch");
  const retryRobotMark = event.target.closest("[data-retry-robot-mark]");
  if (resetSlot) {
    const data = await request(`/api/reset/${resetSlot.dataset.resetSlot}`, { method: "DELETE" });
    appState = data.state;
    renderState();
  } else if (resetModule) {
    const data = await request(`/api/reset-module/${resetModule.dataset.resetModule}`, { method: "DELETE" });
    appState = data.state;
    renderState();
  } else if (removeRow) {
    removeRow.closest(".confirm-grid, .receipt-grid").remove();
  } else if (addConfirm) {
    const container = addConfirm.closest(".confirm");
    const includeStore = container.id === "shipmentConfirm";
    $(".confirm-rows", container).insertAdjacentHTML(
      "beforeend",
      `<div class="confirm-grid ${includeStore ? "" : "no-store"}">
        ${includeStore ? input("store", "", "门店") : ""}
        ${input("product", "", "商品")}
        ${input("quantity", "", "数量")}
        ${input("unit", "", "单位")}
        ${input("type", "新增", "类型")}
        <button class="icon-btn" data-remove-row title="删除">×</button>
      </div>`,
    );
  } else if (acceptConfirm) {
    const container = acceptConfirm.closest(".confirm");
    const includeStore = container.id === "shipmentConfirm";
    const items = collectConfirm(container, includeStore);
    if (includeStore) confirmedShipmentItems = items;
    else confirmedOrderItems = items;
    container.classList.add("hidden");
  } else if (acceptRobotFetch) {
    confirmedOrderItems = [...confirmedOrderItems, ...robotFetchedItems];
    acceptedRobotOrderIds = [...robotFetchedIds];
    acceptedRobotDeliverDate = $("#robotFetchResult").dataset.deliverDate || "";
    acceptRobotFetch.disabled = true;
    acceptRobotFetch.textContent = "已并入，生成排产表后标记已拉取";
  } else if (retryRobotMark) {
    const notice = retryRobotMark.closest(".notice");
    let ids = [];
    try {
      ids = JSON.parse(retryRobotMark.dataset.robotMarkIds || "[]");
    } catch {
      ids = [];
    }
    retryRobotMark.disabled = true;
    retryRobotMark.textContent = "正在重试...";
    try {
      const data = await request("/api/robot/orders/retry-mark", {
        method: "POST",
        body: JSON.stringify({ ids }),
      });
      if (data.remaining_failures) {
        appState.settings = { ...(appState.settings || {}), robot_mark_failures: data.remaining_failures };
      }
      const failed = data.robot_mark?.failed || [];
      if (failed.length) {
        notice.innerHTML = `订单库仍有 id 标记失败：${failed.map(escapeHtml).join("、")}。<button data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(failed))}">重试标记</button>`;
      } else {
        notice.textContent = "订单库失败 id 已重试标记成功。";
      }
    } catch (error) {
      notice.innerHTML = `${escapeHtml(error.message)} <button data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(ids))}">重试标记</button>`;
    }
  }
});

$("#refreshState").addEventListener("click", loadState);

$("#parseOrder").addEventListener("click", async () => {
  const text = $('[data-text-slot="module1_extra_text"]').value;
  const data = await request("/api/ai/parse-order-text", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  renderConfirm($("#orderConfirm"), data.items || [], false);
  if (data.message) $("#orderConfirm").insertAdjacentHTML("afterbegin", `<div class="notice">${escapeHtml(data.message)}</div>`);
});

$("#fetchRobotOrders").addEventListener("click", async () => {
  const target = $("#robotFetchResult");
  target.innerHTML = `<div class="notice">正在从订单库拉取...</div>`;
  try {
    const data = await request("/api/robot/orders/fetch");
    renderRobotFetch(data);
  } catch (error) {
    target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#parseShipment").addEventListener("click", async () => {
  const text = $('[data-text-slot="module4_ship_text"]').value;
  const data = await request("/api/ai/parse-shipment-text", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  renderConfirm($("#shipmentConfirm"), data.items || [], true);
  if (data.message) $("#shipmentConfirm").insertAdjacentHTML("afterbegin", `<div class="notice">${escapeHtml(data.message)}</div>`);
});

$("#generateProduction").addEventListener("click", async () => {
  try {
    const data = await request("/api/generate/production", {
      method: "POST",
      body: JSON.stringify({
        confirmed_items: confirmedOrderItems,
        robot_order_ids: acceptedRobotOrderIds,
        target_date: acceptedRobotDeliverDate || null,
      }),
    });
    renderDownload($("#productionResult"), data);
    if (data.robot_mark && data.robot_mark.ok !== false) {
      acceptedRobotOrderIds = [];
    }
  } catch (error) {
    $("#productionResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateShipment").addEventListener("click", async () => {
  try {
    const data = await request("/api/generate/shipment", {
      method: "POST",
      body: JSON.stringify({ confirmed_items: confirmedShipmentItems }),
    });
    renderDownload($("#shipmentResult"), data);
  } catch (error) {
    $("#shipmentResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateMaterial").addEventListener("click", async () => {
  const workshopStockText = $('[data-text-slot="module2_stock_text"]').value;
  const data = await request("/api/generate/material-issue", {
    method: "POST",
    body: JSON.stringify({ workshop_stock_text: workshopStockText }),
  });
  renderDownload($("#materialResult"), data);
});

$("#addReceiptRow").addEventListener("click", () => addReceiptRow());

$("#generateReceipt").addEventListener("click", async () => {
  const data = await request("/api/generate/receipt", {
    method: "POST",
    body: JSON.stringify({ items: collectReceiptRows() }),
  });
  renderDownload($("#receiptResult"), data);
});

addReceiptRow();
loadState().catch((error) => {
  $("#apiState").textContent = error.message;
});
