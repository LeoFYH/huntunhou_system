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
    .map((group, index) => {
      const items = (group.items || [])
        .map((item) => `${item.name} ${item.quantity}${item.unit || ""}`)
        .join(" · ");
      return `<div class="store-row"><span class="store-name">分组 ${index + 1}</span> <span class="store-items">· ${escapeHtml(items)}</span></div>`;
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

function renderRejected(rejectedPatches) {
  if (!rejectedPatches?.length) return "";
  const rows = rejectedPatches
    .map((patch, index) => {
      const content = (patch.items || []).map((item) => item.label || `${item.name} ${item.qty || ""}${item.unit || ""}`).join("、");
      const dateLabel = patch.order_date ? `${patch.order_date} · ` : "";
      return `<li>${escapeHtml(dateLabel)}分组 ${index + 1}：${escapeHtml(content || "未填写商品")}</li>`;
    })
    .join("");
  return `<div class="notice">以下加货找不到同下单日期、同分组主订单，请先处理：<ul>${rows}</ul></div>`;
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

function selectOrderBatch(container, batch, mode, button = null) {
  if (!batch) return;
  if (mode === "production") {
    productionBatch = { items: batch.items || [], ids: batch.ids || [], orderDate: batch.order_date || "" };
  } else {
    shipmentBatch = { items: batch.items || [], ids: batch.ids || [], orderDate: batch.order_date || "" };
  }
  $$("[data-accept-order-batch]", container).forEach((item) => {
    item.disabled = false;
    item.textContent = "确认此批";
  });
  if (button) {
    button.disabled = true;
    button.textContent = "已确认";
  }
}

function renderOrderBatches(target, data, mode) {
  const batches = data.batches || [];
  clearOrderBatch(mode);
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
        <div class="ct">下单日期 ${escapeHtml(batch.order_date || "未填写")} · ${counts.orders || 0} 单 · ${counts.stores || 0} 组 · ${counts.items || 0} 行</div>
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
  if (batches.length === 1 && batches[0].order_date) {
    const autoButton = target.querySelector("[data-accept-order-batch]");
    selectOrderBatch(target, batches[0], mode, autoButton);
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

document.addEventListener("click", async (event) => {
  const resetSlot = event.target.closest("[data-reset-slot]");
  const acceptBatch = event.target.closest("[data-accept-order-batch]");
  const retryRobotMark = event.target.closest("[data-retry-robot-mark]");
  const returnRobotOrders = event.target.closest("[data-return-robot-orders]");
  const returnRobotReceipts = event.target.closest("[data-return-robot-receipts]");
  if (resetSlot) {
    const data = await request(`/api/reset/${resetSlot.dataset.resetSlot}`, { method: "DELETE" });
    appState = data.state;
    renderState();
  } else if (acceptBatch) {
    const container = acceptBatch.closest(".ai-confirm");
    const batches = JSON.parse(container.dataset.payload || "[]");
    const batch = batches[Number(acceptBatch.dataset.batchIndex)];
    selectOrderBatch(container, batch, acceptBatch.dataset.acceptOrderBatch, acceptBatch);
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
          return;
        }
        invalidateOrderModulesAfterRollback(mode, succeeded.length || ids.length);
        return { ok: true };
      } catch (error) {
        result.innerHTML += `<div class="notice">${escapeHtml(error.message)}</div>`;
        return { ok: false };
      }
    });
    if (outcome?.ok) {
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
