const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

let appState = {};
let productionBatch = { items: [], ids: [], orderDate: "" };
let receiptItems = [];
let shipmentBatch = { items: [], orderDate: "" };

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function todayIso() {
  const date = new Date();
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function filesFor(slot) {
  const files = appState.files?.[slot];
  if (!files) return [];
  return Array.isArray(files) ? files : [files];
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function renderTemplateFiles() {
  $$("[data-files]").forEach((node) => {
    const slot = node.dataset.files;
    const files = filesFor(slot);
    if (!files.length) {
      node.textContent = "未上传";
      return;
    }
    node.textContent = files
      .map((file) => `${file.seeded ? "已载入" : "已保存"} · ${file.name}${formatTime(file.uploaded_at) ? ` · ${formatTime(file.uploaded_at)}` : ""}`)
      .join("；");
  });
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
  const ids = storedRobotFailureIds();
  if (!target || !ids.length || target.innerHTML.trim()) return;
  target.innerHTML = `<div class="notice">订单库有历史失败 id：${ids.map(escapeHtml).join("、")}。<button class="mini" data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(ids))}">重试标记</button></div>`;
}

async function refreshRecipePreview() {
  const target = $("#recipePreview");
  if (!target) return;
  const preview = await request("/api/recipe-preview");
  if (!preview.file_count) {
    target.textContent = "";
    return;
  }
  target.textContent = `已识别 ${preview.file_count} 个文件、${preview.product_count} 个成品、${preview.recipe_rows} 条配料`;
}

function renderState() {
  renderTemplateFiles();
  renderStoredRobotFailures();
  $("#apiState").textContent = "已连接";
  refreshRecipePreview().catch(() => {});
}

async function loadState() {
  appState = await request("/api/state");
  renderState();
}

async function uploadFiles(slot, files) {
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  const data = await request(`/api/upload/${slot}`, { method: "POST", body: form });
  appState = data.state;
  renderState();
}

function storeRows(groups) {
  if (!groups?.length) return `<div class="store-row"><span class="store-items">没有可确认的数据</span></div>`;
  return groups
    .map((group) => {
      const items = (group.items || [])
        .map((item) => `${item.name} ${item.quantity}${item.unit || ""}`)
        .join(" · ");
      return `<div class="store-row"><span class="store-name">${escapeHtml(group.store)}</span> <span class="store-items">· ${escapeHtml(items)}</span></div>`;
    })
    .join("");
}

function renderRejected(rejectedPatches) {
  if (!rejectedPatches?.length) return "";
  const rows = rejectedPatches
    .map((patch) => {
      const content = (patch.items || []).map((item) => item.label || `${item.name} ${item.qty || ""}${item.unit || ""}`).join("、");
      const dateLabel = patch.order_date ? `${patch.order_date} · ` : "";
      return `<li>${escapeHtml(dateLabel)}${escapeHtml(patch.store)}：${escapeHtml(content || "未填写商品")}</li>`;
    })
    .join("");
  return `<div class="notice">以下加货找不到同下单日期、同门店主订单，请先处理：<ul>${rows}</ul></div>`;
}

function renderOrderBatches(target, data, mode) {
  const batches = data.batches || [];
  let html = "";
  (data.warnings || []).forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  html += renderRejected(data.rejected_patches || []);
  if (!batches.length) {
    target.classList.remove("hidden");
    target.innerHTML = `${html}<div class="notice">没有可确认的数据。</div>`;
    return;
  }
  if (batches.length > 1) {
    html += `<div class="notice">本次包含 ${batches.length} 个下单日期，已拆成多个批次，请选一个。</div>`;
  }
  html += batches
    .map((batch, index) => {
      const counts = batch.counts || {};
      return `
        <div class="ct">下单日期 ${escapeHtml(batch.order_date || "未填写")} · ${counts.orders || 0} 单 · ${counts.stores || 0} 门店 · ${counts.items || 0} 行</div>
        ${storeRows(batch.grouped || [])}
        <div class="confirm-btns">
          <button class="mini ok" data-accept-order-batch="${mode}" data-batch-index="${index}" ${batch.order_date ? "" : "disabled"}>确认此批</button>
        </div>
      `;
    })
    .join("");
  target.dataset.payload = JSON.stringify(batches);
  target.classList.remove("hidden");
  target.innerHTML = html;
}

function renderReceiptSync(data) {
  const target = $("#receiptSyncResult");
  let html = "";
  (data.warnings || []).forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  html += `<div class="ct">入库数据 · ${data.counts?.stores || 0} 门店 · ${data.counts?.items || 0} 行</div>`;
  html += storeRows(data.grouped || []);
  html += `<div class="confirm-btns"><button class="mini ok" id="acceptReceiptSync">确认入库数据</button></div>`;
  target.dataset.payload = JSON.stringify(data.items || []);
  target.classList.remove("hidden");
  target.innerHTML = html;
}

function renderDownload(target, data) {
  const warnings = data.warnings || [];
  const missing = data.missing || [];
  let html = "";
  if (missing.length) html += `<div class="notice">缺少：${missing.map(escapeHtml).join("、")}</div>`;
  warnings.forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  if (data.output) {
    html += `<div class="download"><span>${escapeHtml(data.output.name)}</span><a href="${data.output.url}">下载</a></div>`;
  }
  if (data.robot_mark && data.robot_mark.ok !== false && !data.robot_mark.skipped) {
    html += `<div class="download"><span>订单库已标记为已拉取</span></div>`;
  }
  if (data.robot_mark?.failed?.length) {
    const failedIds = data.robot_mark.failed;
    html += `<div class="notice">订单库部分 id 标记失败：${failedIds.map(escapeHtml).join("、")}。<button class="mini" data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(failedIds))}">重试标记</button></div>`;
  }
  target.innerHTML = html;
}

function selectedDate(id) {
  return $(id).value || todayIso();
}

document.addEventListener("change", async (event) => {
  const templateInput = event.target.closest("input[type=file][data-slot]");
  if (templateInput?.files?.length) {
    await uploadFiles(templateInput.dataset.slot, templateInput.files);
    templateInput.value = "";
    return;
  }
  if (event.target.id === "productionRunFile") {
    const file = event.target.files?.[0];
    const label = $("#productionRunFileName");
    if (file) {
      label.textContent = `已选择 · ${file.name}`;
      label.classList.remove("hidden");
    } else {
      label.classList.add("hidden");
    }
  }
});

document.addEventListener("click", async (event) => {
  const resetSlot = event.target.closest("[data-reset-slot]");
  const acceptBatch = event.target.closest("[data-accept-order-batch]");
  const retryRobotMark = event.target.closest("[data-retry-robot-mark]");
  if (resetSlot) {
    const data = await request(`/api/reset/${resetSlot.dataset.resetSlot}`, { method: "DELETE" });
    appState = data.state;
    renderState();
  } else if (acceptBatch) {
    const container = acceptBatch.closest(".ai-confirm");
    const batches = JSON.parse(container.dataset.payload || "[]");
    const batch = batches[Number(acceptBatch.dataset.batchIndex)];
    if (!batch) return;
    if (acceptBatch.dataset.acceptOrderBatch === "production") {
      productionBatch = { items: batch.items || [], ids: batch.ids || [], orderDate: batch.order_date || "" };
    } else {
      shipmentBatch = { items: batch.items || [], orderDate: batch.order_date || "" };
    }
    $$("[data-accept-order-batch]", container).forEach((button) => {
      button.disabled = false;
      button.textContent = "确认此批";
    });
    acceptBatch.disabled = true;
    acceptBatch.textContent = "已确认";
  } else if (event.target.closest("#acceptReceiptSync")) {
    const container = $("#receiptSyncResult");
    receiptItems = JSON.parse(container.dataset.payload || "[]");
    event.target.disabled = true;
    event.target.textContent = "已确认";
  } else if (retryRobotMark) {
    const notice = retryRobotMark.closest(".notice");
    const ids = JSON.parse(retryRobotMark.dataset.robotMarkIds || "[]");
    retryRobotMark.disabled = true;
    retryRobotMark.textContent = "正在重试";
    try {
      const data = await request("/api/robot/orders/retry-mark", {
        method: "POST",
        body: JSON.stringify({ ids }),
      });
      const failed = data.robot_mark?.failed || [];
      notice.textContent = failed.length ? `仍有 id 标记失败：${failed.join("、")}` : "订单库失败 id 已重试成功。";
      if (data.remaining_failures) {
        appState.settings = { ...(appState.settings || {}), robot_mark_failures: data.remaining_failures };
      }
    } catch (error) {
      notice.textContent = error.message;
    }
  }
});

$("#refreshState").addEventListener("click", loadState);

$("#syncOrders").addEventListener("click", async () => {
  const target = $("#orderSyncResult");
  target.classList.remove("hidden");
  target.innerHTML = `<div class="notice">正在同步订单库...</div>`;
  try {
    const data = await request(`/api/robot/orders/fetch?status=new&order_date=${encodeURIComponent(selectedDate("#dateModule1"))}`);
    renderOrderBatches(target, data, "production");
  } catch (error) {
    target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#syncReceipts").addEventListener("click", async () => {
  const target = $("#receiptSyncResult");
  target.classList.remove("hidden");
  target.innerHTML = `<div class="notice">正在同步入库数据...</div>`;
  try {
    const data = await request(`/api/robot/receipts/fetch?date=${encodeURIComponent(selectedDate("#dateModule3"))}`);
    renderReceiptSync(data);
  } catch (error) {
    target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#syncShipment").addEventListener("click", async () => {
  const target = $("#shipmentSyncResult");
  target.classList.remove("hidden");
  target.innerHTML = `<div class="notice">正在同步订单库...</div>`;
  try {
    const data = await request(`/api/robot/orders/fetch?status=all&order_date=${encodeURIComponent(selectedDate("#dateModule4"))}`);
    renderOrderBatches(target, data, "shipment");
  } catch (error) {
    target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateProduction").addEventListener("click", async () => {
  try {
    const data = await request("/api/generate/production", {
      method: "POST",
      body: JSON.stringify({
        confirmed_items: productionBatch.items,
        robot_order_ids: productionBatch.ids,
        order_date: productionBatch.orderDate || selectedDate("#dateModule1"),
      }),
    });
    renderDownload($("#productionResult"), data);
    if (data.robot_mark && data.robot_mark.ok !== false) {
      productionBatch = { items: [], ids: [], orderDate: "" };
    }
  } catch (error) {
    $("#productionResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateMaterial").addEventListener("click", async () => {
  const file = $("#productionRunFile").files?.[0];
  if (!file) {
    $("#materialResult").innerHTML = `<div class="notice">请先上传填好的完整排产表。</div>`;
    return;
  }
  const form = new FormData();
  form.append("production_file", file);
  form.append("document_date", selectedDate("#dateModule2"));
  try {
    const data = await request("/api/generate/material-issue-upload", { method: "POST", body: form });
    renderDownload($("#materialResult"), data);
  } catch (error) {
    $("#materialResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateReceipt").addEventListener("click", async () => {
  try {
    const data = await request("/api/generate/receipt", {
      method: "POST",
      body: JSON.stringify({ items: receiptItems, document_date: selectedDate("#dateModule3") }),
    });
    renderDownload($("#receiptResult"), data);
  } catch (error) {
    $("#receiptResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$("#generateShipment").addEventListener("click", async () => {
  try {
    const data = await request("/api/generate/shipment", {
      method: "POST",
      body: JSON.stringify({ confirmed_items: shipmentBatch.items, order_date: shipmentBatch.orderDate || selectedDate("#dateModule4") }),
    });
    renderDownload($("#shipmentResult"), data);
  } catch (error) {
    $("#shipmentResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
});

$$("[data-date]").forEach((input) => {
  if (!input.value) input.value = todayIso();
});

loadState().catch((error) => {
  $("#apiState").textContent = error.message;
});
