const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

let appState = {};
let productionBatch = { items: [], ids: [], orderDate: "" };
let receiptBatch = { items: [], ids: [], date: "" };
let shipmentBatch = { items: [], ids: [], orderDate: "" };

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
  } catch (_error) {
    throw new Error("后端连接中断或服务无响应，请确认服务正常后重试。");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败 (${response.status})`);
  return data;
}

async function runOnce(button, busyHtml, task) {
  if (!button || button.disabled) return undefined;
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = busyHtml;
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.innerHTML = originalHtml;
  }
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

function renderSkuImportStatus(result) {
  const target = $("#receiptSkuImportStatus");
  if (!target || !result) return;
  const products = result.products || [];
  const productRows = products
    .map((item) => {
      const meta = [item.spec, item.unit, item.category].filter(Boolean).join(" · ");
      return `<li><b>${escapeHtml(item.name || "")}</b>${meta ? `<span>${escapeHtml(meta)}</span>` : ""}</li>`;
    })
    .join("");
  const detail = products.length
    ? `<details class="sku-details" open><summary>查看导入 SKU（${products.length} 个）</summary><ul>${productRows}</ul></details>`
    : "";
  const counts = [
    `解析行 ${result.source_rows ?? 0}`,
    `去重后 ${result.unique_rows ?? result.total ?? 0}`,
    `成功 ${result.succeeded ?? 0}`,
    `失败 ${result.failed ?? 0}`,
    `批内合并 ${result.merged_in_batch ?? 0}`,
  ];
  if (result.deduped) counts.push(`去重 ${result.deduped}`);
  if (result.truncated) counts.push(`截断 ${result.truncated}`);
  const title = result.ok ? "SKU 已导入机器人库" : (result.error || "模板已保存，但 SKU 导入失败");
  target.classList.remove("hidden");
  target.innerHTML = `
    <div class="${result.ok ? "sku-ok" : "sku-fail"}">
      <div class="sku-title">${escapeHtml(title)}</div>
      <div class="sku-counts">${counts.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
      ${detail}
    </div>
  `;
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
  if (slot === "receipt_template") renderSkuImportStatus(data.sku_import);
}

function parseQuantity(value) {
  const text = String(value ?? "").replace(",", "").trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function groupOrderItemsByStore(items) {
  const groups = [];
  const byStore = new Map();
  items.forEach((item, index) => {
    const store = String(item.store || "未填写门店").trim() || "未填写门店";
    if (!byStore.has(store)) {
      const group = { store, items: [] };
      byStore.set(store, group);
      groups.push(group);
    }
    byStore.get(store).items.push({ item, index });
  });
  return groups;
}

function editableOrderRows(batch, batchIndex) {
  const items = batch.items || [];
  if (!items.length) return `<div class="store-row"><span class="store-items">没有可确认的数据</span></div>`;
  return groupOrderItemsByStore(items)
    .map((group, groupIndex) => {
      const rows = group.items
        .map(({ item, index }, itemIndex) => {
          const product = item.product || item.name || "";
          const quantity = item.quantity ?? item.qty ?? "";
          const price = item.price ?? "";
          return `
            <div class="edit-row" data-edit-batch="${batchIndex}" data-edit-index="${index}">
              <span class="edit-sequence">${itemIndex + 1}</span>
              <input data-order-edit-field data-edit-category value="${escapeHtml(item.category || "")}" aria-label="类别" />
              <input data-order-edit-field data-edit-code value="${escapeHtml(item.code || "")}" aria-label="编码" />
              <input data-order-edit-field class="edit-product" data-edit-product value="${escapeHtml(product)}" aria-label="原料名称" />
              <input data-order-edit-field data-edit-spec value="${escapeHtml(item.spec || "")}" aria-label="规格" />
              <input data-order-edit-field data-edit-unit value="${escapeHtml(item.unit || "")}" aria-label="单位" />
              <input data-order-edit-field class="edit-price" data-edit-price inputmode="decimal" value="${escapeHtml(price)}" aria-label="单价" />
              <input data-order-edit-field class="edit-quantity" data-edit-quantity inputmode="decimal" value="${escapeHtml(quantity)}" aria-label="订货数量" />
            </div>
          `;
        })
        .join("");
      return `
        <div class="edit-store-group" data-edit-store-group>
          <div class="edit-store-bar">
            <label>门店/分组 <input data-order-edit-field data-edit-store value="${escapeHtml(group.store)}" aria-label="门店/分组" /></label>
            <span>${group.items.length} 行</span>
          </div>
          <div class="edit-table">
            <div class="edit-sheet">
              <div class="edit-head">
                <span>序号</span><span>类别</span><span>编码</span><span>原料名称</span><span>规格</span><span>单位</span><span>单价</span><span>订货数量</span>
              </div>
              ${rows}
            </div>
          </div>
        </div>
      `;
    })
    .join("");
}

function itemRows(items) {
  if (!items?.length) return `<div class="store-row"><span class="store-items">没有可确认的数据</span></div>`;
  return items
    .map((item) => {
      const content = `${item.quantity}${item.unit || ""}`;
      return `<div class="store-row"><span class="store-name">${escapeHtml(item.name)}</span> <span class="store-items">· ${escapeHtml(content)}</span></div>`;
    })
    .join("");
}

function renderClearOrderSyncButton(mode) {
  return `
    <div class="sync-clear-bar">
      <button class="mini" data-clear-order-sync="${escapeHtml(mode)}">清空当前同步结果</button>
      <span>只清空本页面未确认内容；订单库里的加货/订单不会被删除，下次同步仍会出现。</span>
    </div>
  `;
}

function renderRejected(rejectedPatches, mode) {
  if (!rejectedPatches?.length) return "";
  const rows = rejectedPatches
    .map((patch, index) => {
      const content = (patch.items || []).map((item) => item.label || `${item.name} ${item.qty || ""}${item.unit || ""}`).join("、");
      const dateLabel = patch.order_date ? `${patch.order_date} · ` : "";
      const storeLabel = patch.store ? `${patch.store}：` : `分组 ${index + 1}：`;
      return `<li>${escapeHtml(dateLabel)}${escapeHtml(storeLabel)}${escapeHtml(content || "未填写商品")}</li>`;
    })
    .join("");
  return `
    <div class="notice">
      以下是加货补丁，但当前没有同下单日期、同门店主订单，不能生成排产表：
      <ul>${rows}</ul>
      <div>处理方式：先让对应主订单进入订单库后重新同步；或者点下面清空，只是这次先不处理它，不会删除 bot 库里的加货。</div>
    </div>
  `;
}

function emptyOrderBatch() {
  return { items: [], ids: [], orderDate: "" };
}

function clearOrderBatch(mode) {
  if (mode === "production") {
    productionBatch = emptyOrderBatch();
  } else {
    shipmentBatch = emptyOrderBatch();
  }
}

function clearOrderSyncResult(mode) {
  clearOrderBatch(mode);
  const target = mode === "shipment" ? $("#shipmentSyncResult") : $("#orderSyncResult");
  if (!target) return;
  target.classList.add("hidden");
  target.innerHTML = "";
  delete target.dataset.payload;
  delete target.dataset.selectedBatchIndex;
  delete target.dataset.selectedOrderMode;
}

function clearReceiptSyncResult() {
  receiptBatch = { items: [], ids: [], date: "" };
  const target = $("#receiptSyncResult");
  if (!target) return;
  target.classList.add("hidden");
  target.innerHTML = "";
  delete target.dataset.payload;
}

function confirmHardClear(kind, dateValue) {
  const source = kind === "receipts" ? "产成品入库库" : "订单库";
  const moduleText = kind === "receipts" ? "模块3" : "模块1/模块4";
  const first = window.confirm(`强力清空会彻底删除 ${dateValue} 的${source}数据，并清掉${moduleText}当前页面同步结果。删除后工具里将再也同步不到这些单据。确定继续吗？`);
  if (!first) return false;
  return window.confirm(`二次确认：这不是普通清空页面，会删除 bot 数据库里的 ${dateValue} 当日单据。只有在当天数据彻底乱掉、准备人工统计时才点“确定”。`);
}

function robotClearCount(result) {
  const value = result?.deleted ?? result?.deleted_count ?? result?.cleared ?? result?.count ?? result?.total;
  if (value !== undefined && value !== null) return value;
  for (const key of ["ids", "deleted_ids", "cleared_ids", "succeeded"]) {
    if (Array.isArray(result?.[key])) return result[key].length;
  }
  return null;
}

function hardClearMessage(kind, dateValue, data) {
  const result = data.robot_clear || {};
  const count = robotClearCount(result);
  const source = kind === "receipts" ? "入库数据" : "订单单据";
  const countText = count === null ? "" : `，共 ${escapeHtml(count)} 条`;
  return `${escapeHtml(dateValue)} 当日${source}已强力清空${countText}。当前页面同步结果已清掉；如需恢复只能重新让机器人入库/接单。`;
}

function applyHardClearUi(kind, mode, dateValue, data) {
  if (data.state) {
    appState = data.state;
    renderState();
  }
  const message = hardClearMessage(kind, dateValue, data);
  if (kind === "receipts") {
    clearReceiptSyncResult();
    $("#receiptResult").innerHTML = `<div class="download"><span>${message}</span></div>`;
    return;
  }
  clearOrderSyncResult("production");
  clearOrderSyncResult("shipment");
  const notice = `<div class="download"><span>${message}</span></div>`;
  $("#productionResult").innerHTML = notice;
  $("#shipmentResult").innerHTML = notice;
}

function collectEditedOrderItems(container, batch, batchIndex) {
  const rows = $$(`[data-edit-batch="${batchIndex}"]`, container);
  if (!rows.length) return batch.items || [];
  return rows
    .map((row) => {
      const index = Number(row.dataset.editIndex);
      const original = (batch.items || [])[index] || {};
      const store = row.closest("[data-edit-store-group]")?.querySelector("[data-edit-store]")?.value.trim() || original.store || "未填写门店";
      const category = row.querySelector("[data-edit-category]")?.value.trim() || "";
      const code = row.querySelector("[data-edit-code]")?.value.trim() || "";
      const product = row.querySelector("[data-edit-product]")?.value.trim() || "";
      const spec = row.querySelector("[data-edit-spec]")?.value.trim() || "";
      const unit = row.querySelector("[data-edit-unit]")?.value.trim() || "";
      const price = parseQuantity(row.querySelector("[data-edit-price]")?.value);
      const quantity = parseQuantity(row.querySelector("[data-edit-quantity]")?.value);
      return {
        ...original,
        store,
        category,
        code,
        product,
        name: product,
        spec,
        unit,
        price,
        quantity,
        qty: quantity,
      };
    })
    .filter((item) => item.product && item.quantity !== null);
}

function applyOrderBatch(container, batch, mode, batchIndex) {
  const items = collectEditedOrderItems(container, batch, batchIndex);
  if (mode === "production") {
    productionBatch = { items, ids: batch.ids || [], orderDate: batch.order_date || "" };
  } else {
    shipmentBatch = { items, ids: batch.ids || [], orderDate: batch.order_date || "" };
  }
}

function selectOrderBatch(container, batch, mode, button = null, batchIndex = 0) {
  if (!batch) return;
  applyOrderBatch(container, batch, mode, batchIndex);
  container.dataset.selectedBatchIndex = String(batchIndex);
  container.dataset.selectedOrderMode = mode;
  $$("[data-accept-order-batch]", container).forEach((item) => {
    item.disabled = false;
    item.textContent = "确认此批";
  });
  if (button) {
    button.disabled = true;
    button.textContent = "已确认";
  }
}

function updateSelectedOrderBatch(container) {
  if (!container?.dataset.selectedBatchIndex) return;
  const batches = JSON.parse(container.dataset.payload || "[]");
  const batchIndex = Number(container.dataset.selectedBatchIndex);
  const batch = batches[batchIndex];
  if (!batch) return;
  applyOrderBatch(container, batch, container.dataset.selectedOrderMode || "production", batchIndex);
}

function renderOrderBatches(target, data, mode) {
  const batches = data.batches || [];
  clearOrderBatch(mode);
  let html = "";
  (data.warnings || []).forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  html += renderRejected(data.rejected_patches || [], mode);
  if (!batches.length) {
    target.classList.remove("hidden");
    target.innerHTML = `${html}<div class="notice">没有可确认的数据。</div>${renderClearOrderSyncButton(mode)}`;
    return;
  }
  if (batches.length > 1) {
    html += `<div class="notice">本次包含 ${batches.length} 个下单日期，已拆成多个批次，请选一个。</div>`;
  }
  html += batches
    .map((batch, index) => {
      const counts = batch.counts || {};
      return `
        <div class="ct">下单日期 ${escapeHtml(batch.order_date || "未填写")} · ${counts.orders || 0} 单 · ${counts.stores || 0} 组 · ${counts.items || 0} 行</div>
        ${editableOrderRows(batch, index)}
        <div class="confirm-btns">
          <button class="mini ok" data-accept-order-batch="${mode}" data-batch-index="${index}" ${batch.order_date ? "" : "disabled"}>确认此批</button>
        </div>
      `;
    })
    .join("");
  html += renderClearOrderSyncButton(mode);
  target.dataset.payload = JSON.stringify(batches);
  target.classList.remove("hidden");
  target.innerHTML = html;
  if (batches.length === 1 && batches[0].order_date) {
    const autoButton = target.querySelector("[data-accept-order-batch]");
    selectOrderBatch(target, batches[0], mode, autoButton, 0);
  }
}

function renderReceiptSync(data) {
  const target = $("#receiptSyncResult");
  receiptBatch = { items: [], ids: [], date: "" };
  let html = "";
  (data.warnings || []).forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  html += `<div class="ct">产成品入库数据 · ${data.counts?.products || 0} 个品 · ${data.counts?.items || 0} 行</div>`;
  html += itemRows(data.items_summary || []);
  html += `<div class="confirm-btns"><button class="mini ok" id="acceptReceiptSync">确认入库数据</button></div>`;
  target.dataset.payload = JSON.stringify({ items: data.items || [], ids: data.ids || [] });
  target.classList.remove("hidden");
  target.innerHTML = html;
}

function clearOrderSyncResults() {
  ["#orderSyncResult", "#shipmentSyncResult"].forEach((selector) => {
    const target = $(selector);
    if (!target) return;
    target.classList.add("hidden");
    target.innerHTML = "";
    delete target.dataset.payload;
  });
}

function invalidateOrderModulesAfterRollback(sourceMode, count) {
  productionBatch = emptyOrderBatch();
  shipmentBatch = emptyOrderBatch();
  clearOrderSyncResults();
  const sourceLabel = sourceMode === "shipment" ? "模块 4 出货" : "模块 1 排产";
  const countText = count ? `${count} 张订单` : "本批订单";
  const message = `${sourceLabel}已退回${countText}为未拉取；模块 1 排产和模块 4 出货都需要重新同步订单库。`;
  ["#productionResult", "#shipmentResult"].forEach((selector) => {
    const target = $(selector);
    if (!target) return;
    target.innerHTML = `<div class="notice">${escapeHtml(message)}</div>`;
  });
}

async function syncOrderModule(mode, noticeText = "正在同步订单库...") {
  const isShipment = mode === "shipment";
  const target = isShipment ? $("#shipmentSyncResult") : $("#orderSyncResult");
  const dateSelector = isShipment ? "#dateModule4" : "#dateModule1";
  const status = isShipment ? "all" : "new";
  target.classList.remove("hidden");
  target.innerHTML = `<div class="notice">${escapeHtml(noticeText)}</div>`;
  const data = await request(`/api/robot/orders/fetch?status=${status}&order_date=${encodeURIComponent(selectedDate(dateSelector))}`);
  renderOrderBatches(target, data, mode);
}

async function refreshOrderModuleAfterRollback(mode) {
  const target = mode === "shipment" ? $("#shipmentSyncResult") : $("#orderSyncResult");
  try {
    await syncOrderModule(mode, "退回成功，正在重新同步订单库...");
  } catch (error) {
    target.classList.remove("hidden");
    target.innerHTML = `<div class="notice">退回成功，但重新同步失败：${escapeHtml(error.message)}</div>`;
  }
}

async function refreshOrderModulesAfterRollback() {
  await Promise.all([refreshOrderModuleAfterRollback("production"), refreshOrderModuleAfterRollback("shipment")]);
}

function orderDateLabel(mode, fallbackDate = "") {
  if (fallbackDate) return fallbackDate;
  if (mode === "shipment") return shipmentBatch.orderDate || selectedDate("#dateModule4");
  return productionBatch.orderDate || selectedDate("#dateModule1");
}

function renderOrderRollbackBlock(ids, mode, dateLabel, summary) {
  if (!ids?.length) return "";
  const modeText = mode === "shipment" ? "本次发货使用" : "本批已锁定";
  const detail =
    mode === "shipment"
      ? `${modeText} ${ids.length} 张订单（下单日期 ${escapeHtml(dateLabel)}）。如果在这里退回，本批订单会变回未拉取，模块 1 排产和模块 4 出货都需要重新同步。`
      : `${modeText} ${ids.length} 张订单（下单日期 ${escapeHtml(dateLabel)}）。填表期间新来的加货不在这批里。退回后模块 1 排产和模块 4 出货都需要重新同步。`;
  return `
    <div class="download"><span>${escapeHtml(summary)}</span></div>
    <div class="lockbar"><span>${detail}</span></div>
    <button class="return-btn" data-return-order-mode="${mode}" data-return-robot-orders="${escapeHtml(JSON.stringify(ids))}">作废本批 · 退回订单</button>
  `;
}

function renderDownload(target, data, options = {}) {
  const warnings = data.warnings || [];
  const missing = data.missing || [];
  const mode = options.mode || "production";
  let html = "";
  if (missing.length) html += `<div class="notice">缺少：${missing.map(escapeHtml).join("、")}</div>`;
  warnings.forEach((warning) => {
    html += `<div class="notice">${escapeHtml(warning)}</div>`;
  });
  if (data.output) {
    html += `<div class="download"><span>${escapeHtml(data.output.name)}</span><a href="${data.output.url}">下载</a></div>`;
  }
  if (data.robot_mark && !data.robot_mark.skipped) {
    const lockedIds = data.robot_mark.succeeded || (data.robot_mark.ok !== false ? data.robot_mark.ids || [] : []);
    if (lockedIds.length) {
      const dateLabel = orderDateLabel(mode, options.orderLock?.date || "");
      html += renderOrderRollbackBlock(lockedIds, mode, dateLabel, `${lockedIds.length} 张订单已标记为已拉取`);
    }
  }
  if (!data.robot_mark && options.orderLock?.ids?.length) {
    const lockedIds = options.orderLock.ids;
    const dateLabel = orderDateLabel(mode, options.orderLock.date || "");
    html += renderOrderRollbackBlock(lockedIds, mode, dateLabel, `${lockedIds.length} 张订单用于本次发货`);
  }
  if (data.robot_mark?.failed?.length) {
    const failedIds = data.robot_mark.failed;
    html += `<div class="notice">订单库部分 id 标记失败：${failedIds.map(escapeHtml).join("、")}。<button class="mini" data-retry-robot-mark data-robot-mark-ids="${escapeHtml(JSON.stringify(failedIds))}">重试标记</button></div>`;
  }
  target.innerHTML = html;
}

function renderReceiptDownload(target, data) {
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
  if (data.robot_receipt_mark && !data.robot_receipt_mark.skipped) {
    const lockedIds = data.robot_receipt_mark.succeeded || (data.robot_receipt_mark.ok !== false ? data.robot_receipt_mark.ids || [] : []);
    if (lockedIds.length) {
      html += `<div class="download"><span>${lockedIds.length} 条入库数据已标记为已拉取</span></div>`;
      const dateLabel = receiptBatch.date || selectedDate("#dateModule3");
      html += `<div class="lockbar"><span>本批已锁定 ${lockedIds.length} 条入库数据（入库日期 ${escapeHtml(dateLabel)}）。若要把新入库数据纳进来，点下面退回后重新同步。</span></div>`;
      html += `<button class="return-btn" data-return-robot-receipts="${escapeHtml(JSON.stringify(lockedIds))}">作废本批 · 退回入库数据</button>`;
    }
  }
  if (data.robot_receipt_mark?.failed?.length) {
    const failedIds = data.robot_receipt_mark.failed;
    html += `<div class="notice">入库库部分 id 标记失败：${failedIds.map(escapeHtml).join("、")}。</div>`;
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
  if (event.target.id === "productionCompleteFile") {
    const file = event.target.files?.[0];
    const label = $("#productionCompleteFileName");
    if (file) {
      label.textContent = `已选择 · ${file.name}`;
      label.classList.remove("hidden");
    } else {
      label.classList.add("hidden");
    }
  }
});

document.addEventListener("input", (event) => {
  if (!event.target.closest("[data-order-edit-field]")) return;
  updateSelectedOrderBatch(event.target.closest(".ai-confirm"));
});

document.addEventListener("click", async (event) => {
  const resetSlot = event.target.closest("[data-reset-slot]");
  const acceptBatch = event.target.closest("[data-accept-order-batch]");
  const clearOrderSync = event.target.closest("[data-clear-order-sync]");
  const hardClear = event.target.closest("[data-hard-clear]");
  const retryRobotMark = event.target.closest("[data-retry-robot-mark]");
  const returnRobotOrders = event.target.closest("[data-return-robot-orders]");
  const returnRobotReceipts = event.target.closest("[data-return-robot-receipts]");
  if (hardClear) {
    const kind = hardClear.dataset.hardClear || "orders";
    const mode = hardClear.dataset.hardClearMode || "production";
    const dateSelector = hardClear.dataset.dateSelector;
    const dateValue = selectedDate(dateSelector);
    const resultTarget = mode === "receipt" ? $("#receiptResult") : mode === "shipment" ? $("#shipmentResult") : $("#productionResult");
    if (!confirmHardClear(kind, dateValue)) return;
    await runOnce(hardClear, "正在强力清空...", async () => {
      try {
        const endpoint = kind === "receipts" ? "/api/robot/receipts/clear-date" : "/api/robot/orders/clear-date";
        const body = kind === "receipts" ? { date: dateValue } : { order_date: dateValue };
        const data = await request(endpoint, {
          method: "POST",
          body: JSON.stringify(body),
        });
        applyHardClearUi(kind, mode, dateValue, data);
      } catch (error) {
        resultTarget.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
      }
    });
  } else if (resetSlot) {
    const data = await request(`/api/reset/${resetSlot.dataset.resetSlot}`, { method: "DELETE" });
    appState = data.state;
    renderState();
    if (resetSlot.dataset.resetSlot === "receipt_template") {
      $("#receiptSkuImportStatus")?.classList.add("hidden");
      if ($("#receiptSkuImportStatus")) $("#receiptSkuImportStatus").innerHTML = "";
    }
  } else if (clearOrderSync) {
    clearOrderSyncResult(clearOrderSync.dataset.clearOrderSync || "production");
  } else if (acceptBatch) {
    const container = acceptBatch.closest(".ai-confirm");
    const batches = JSON.parse(container.dataset.payload || "[]");
    const batchIndex = Number(acceptBatch.dataset.batchIndex);
    const batch = batches[batchIndex];
    selectOrderBatch(container, batch, acceptBatch.dataset.acceptOrderBatch, acceptBatch, batchIndex);
  } else if (event.target.closest("#acceptReceiptSync")) {
    const container = $("#receiptSyncResult");
    const payload = JSON.parse(container.dataset.payload || "{}");
    receiptBatch = {
      items: payload.items || [],
      ids: payload.ids || [],
      date: selectedDate("#dateModule3"),
    };
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
  } else if (returnRobotOrders) {
    const ids = JSON.parse(returnRobotOrders.dataset.returnRobotOrders || "[]");
    if (!ids.length) return;
    const mode = returnRobotOrders.dataset.returnOrderMode || "production";
    const outcome = await runOnce(returnRobotOrders, "正在退回...", async () => {
      const result = mode === "shipment" ? $("#shipmentResult") : $("#productionResult");
      try {
        const data = await request("/api/robot/orders/unmark", {
          method: "POST",
          body: JSON.stringify({ ids }),
        });
        const failed = data.robot_unmark?.failed || [];
        const succeeded = data.robot_unmark?.succeeded || [];
        if (failed.length) {
          result.innerHTML += `<div class="notice">部分订单退回失败：${failed.map(escapeHtml).join("、")}。成功退回：${succeeded.map(escapeHtml).join("、") || "无"}。</div>`;
          return { ok: false };
        }
        return { ok: true, count: succeeded.length || ids.length };
      } catch (error) {
        result.innerHTML += `<div class="notice">${escapeHtml(error.message)}</div>`;
        return { ok: false };
      }
    });
    if (outcome?.ok) {
      invalidateOrderModulesAfterRollback(mode, outcome.count);
      await refreshOrderModulesAfterRollback();
      $$("[data-return-robot-orders]").forEach((button) => {
        button.disabled = true;
        button.textContent = "已退回，请重新同步";
        button.removeAttribute("data-return-robot-orders");
      });
    }
  } else if (returnRobotReceipts) {
    const ids = JSON.parse(returnRobotReceipts.dataset.returnRobotReceipts || "[]");
    if (!ids.length) return;
    const outcome = await runOnce(returnRobotReceipts, "正在退回...", async () => {
      const result = $("#receiptResult");
      try {
        const data = await request("/api/robot/receipts/unmark", {
          method: "POST",
          body: JSON.stringify({ ids }),
        });
        const failed = data.robot_receipt_unmark?.failed || [];
        const succeeded = data.robot_receipt_unmark?.succeeded || [];
        if (failed.length) {
          result.innerHTML += `<div class="notice">部分入库数据退回失败：${failed.map(escapeHtml).join("、")}。成功退回：${succeeded.map(escapeHtml).join("、") || "无"}。</div>`;
          return;
        }
        receiptBatch = { items: [], ids: [], date: "" };
        $("#receiptSyncResult").classList.add("hidden");
        $("#receiptSyncResult").innerHTML = "";
        result.innerHTML += `<div class="download"><span>本批入库数据已退回为未拉取，请重新同步入库数据。</span></div>`;
        return { ok: true };
      } catch (error) {
        result.innerHTML += `<div class="notice">${escapeHtml(error.message)}</div>`;
        return { ok: false };
      }
    });
    if (outcome?.ok) {
      returnRobotReceipts.disabled = true;
      returnRobotReceipts.textContent = "已退回，请重新同步";
      returnRobotReceipts.removeAttribute("data-return-robot-receipts");
    }
  }
});

$("#refreshState").addEventListener("click", loadState);

$("#syncOrders").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在同步...`, async () => {
    try {
      await syncOrderModule("production");
    } catch (error) {
      const target = $("#orderSyncResult");
      target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$("#syncReceipts").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在同步...`, async () => {
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
});

$("#syncShipment").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在同步...`, async () => {
    try {
      await syncOrderModule("shipment");
    } catch (error) {
      const target = $("#shipmentSyncResult");
      target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$("#generateProduction").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在生成...`, async () => {
    const target = $("#productionResult");
    if (!productionBatch.items.length) {
      target.innerHTML = `<div class="notice">请先同步订单库并确认一个批次。</div>`;
      return;
    }
    target.innerHTML = `<div class="notice">正在生成待补充排产表...</div>`;
    try {
      const data = await request("/api/generate/production", {
        method: "POST",
        body: JSON.stringify({
          confirmed_items: productionBatch.items,
          robot_order_ids: productionBatch.ids,
          order_date: productionBatch.orderDate || selectedDate("#dateModule1"),
        }),
      });
      renderDownload(target, data, { mode: "production" });
    } catch (error) {
      target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$("#generateCompletedProduction").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在生成...`, async () => {
    const file = $("#productionCompleteFile").files?.[0];
    if (!file) {
      $("#productionCompleteResult").innerHTML = `<div class="notice">请先上传填好盘点库存数和入库数的排产表。</div>`;
      return;
    }
    const form = new FormData();
    form.append("production_file", file);
    form.append("document_date", selectedDate("#dateModule1"));
    try {
      const data = await request("/api/generate/production-complete-upload", { method: "POST", body: form });
      renderDownload($("#productionCompleteResult"), data);
    } catch (error) {
      $("#productionCompleteResult").innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$("#generateMaterial").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在生成...`, async () => {
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
});

$("#generateReceipt").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在生成...`, async () => {
    const target = $("#receiptResult");
    if (!receiptBatch.items.length) {
      target.innerHTML = `<div class="notice">请先同步入库数据并确认。</div>`;
      return;
    }
    try {
      const data = await request("/api/generate/receipt", {
        method: "POST",
        body: JSON.stringify({
          items: receiptBatch.items,
          robot_receipt_ids: receiptBatch.ids,
          document_date: receiptBatch.date || selectedDate("#dateModule3"),
        }),
      });
      renderReceiptDownload(target, data);
    } catch (error) {
      target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$("#generateShipment").addEventListener("click", async (event) => {
  await runOnce(event.currentTarget, `正在生成...`, async () => {
    const target = $("#shipmentResult");
    if (!shipmentBatch.items.length) {
      target.innerHTML = `<div class="notice">请先同步订单库并确认发货批次。</div>`;
      return;
    }
    try {
      const orderDate = shipmentBatch.orderDate || selectedDate("#dateModule4");
      const orderLock = { ids: shipmentBatch.ids || [], date: orderDate };
      const data = await request("/api/generate/shipment", {
        method: "POST",
        body: JSON.stringify({
          confirmed_items: shipmentBatch.items,
          robot_order_ids: shipmentBatch.ids,
          order_date: orderDate,
        }),
      });
      renderDownload(target, data, { mode: "shipment", orderLock });
    } catch (error) {
      target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    }
  });
});

$$("[data-date]").forEach((input) => {
  if (!input.value) input.value = todayIso();
});

loadState().catch((error) => {
  $("#apiState").textContent = error.message;
});
