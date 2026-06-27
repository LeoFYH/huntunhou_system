from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import ROBOT_API_BASE, ROBOT_API_TIMEOUT_SECONDS, ROBOT_API_TOKEN
from .excel_service import normalize_key, to_number


logger = logging.getLogger("huntunhou.robot_service")


def _robot_headers() -> dict[str, str]:
    if not ROBOT_API_TOKEN:
        return {}
    return {"Authorization": f"Bearer {ROBOT_API_TOKEN}"}


def _valid_code(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.upper() in {"#N/A", "N/A", "NULL", "NONE", "无"}:
        return ""
    return text


def _item_key(item: dict[str, Any]) -> str:
    code = _valid_code(item.get("code"))
    if code:
        return f"code:{code}"
    return f"name:{normalize_key(item.get('name') or item.get('product'))}"


def _item_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("product") or "").strip() or "未填写商品"
    qty = item.get("qty") if "qty" in item else item.get("quantity")
    unit = "" if item.get("unit") is None else str(item.get("unit"))
    return f"{name} {qty}{unit}"


def _order_date(order: dict[str, Any]) -> str:
    return str(order.get("order_date") or "").strip()


def _new_grouped() -> dict[str, dict[str, Any]]:
    return {}


def _new_counts() -> dict[str, int]:
    return {"orders": 0, "items": 0, "stores": 0, "base": 0, "patch": 0, "other": 0}


def _grouped_list(grouped: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "store": bucket["store"],
            "orders": bucket["orders"],
            "items": sorted(bucket["items"].values(), key=lambda item: item["name"]),
        }
        for bucket in grouped.values()
    ]


def normalize_robot_orders(payload: dict[str, Any], extra_base_stores: set[str] | None = None) -> dict[str, Any]:
    orders = payload.get("orders") or []
    extra_base_stores = extra_base_stores or set()
    base_keys = {
        (_order_date(order), str(order.get("store") or "").strip())
        for order in orders
        if order.get("kind") == "base" and str(order.get("store") or "").strip()
    }
    all_ids: list[Any] = []
    accepted_ids: list[Any] = []
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    rejected_patches: list[dict[str, Any]] = []
    grouped = _new_grouped()
    batches: dict[str, dict[str, Any]] = {}
    kind_counts = {"base": 0, "patch": 0, "other": 0}
    order_dates = sorted(
        {
            _order_date(order)
            for order in orders
            if _order_date(order)
        }
    )

    for order in orders:
        order_id = order.get("id")
        if order_id is not None and order_id not in all_ids:
            all_ids.append(order_id)
        kind = str(order.get("kind") or "other")
        kind_counts[kind if kind in kind_counts else "other"] += 1
        store = str(order.get("store") or "未填写门店").strip()
        order_date = _order_date(order)
        delivery_note = order.get("deliver_date", "")
        raw_items = order.get("items") or []

        if kind == "patch" and (order_date, store) not in base_keys and store not in extra_base_stores:
            rejected_patches.append(
                {
                    "id": order_id,
                    "store": store,
                    "order_date": order_date,
                    "delivery_note": delivery_note,
                    "items": [
                        {
                            "name": str(item.get("name") or item.get("product") or "").strip(),
                            "qty": item.get("qty") if "qty" in item else item.get("quantity"),
                            "unit": item.get("unit", ""),
                            "label": _item_label(item),
                        }
                        for item in raw_items
                    ],
                    "message": f"{store} 有加货，但没有找到 {store} {order_date or '未填写下单日期'} 的主订单 Excel。",
                }
            )
            continue

        if order_id is not None and order_id not in accepted_ids:
            accepted_ids.append(order_id)

        batch = batches.setdefault(
            order_date,
            {
                "order_date": order_date,
                "ids": [],
                "items": [],
                "grouped": _new_grouped(),
                "counts": _new_counts(),
            },
        )
        if order_id is not None and order_id not in batch["ids"]:
            batch["ids"].append(order_id)
        batch_kind = kind if kind in {"base", "patch"} else "other"
        batch["counts"]["orders"] += 1
        batch["counts"][batch_kind] += 1

        store_bucket = grouped.setdefault(store, {"store": store, "orders": [], "items": {}})
        store_bucket["orders"].append(
            {
                "id": order_id,
                "kind": kind,
                "source": order.get("source", ""),
                "order_no": order.get("order_no", ""),
                "order_date": order_date,
                "delivery_note": delivery_note,
            }
        )
        batch_store_bucket = batch["grouped"].setdefault(store, {"store": store, "orders": [], "items": {}})
        batch_store_bucket["orders"].append(
            {
                "id": order_id,
                "kind": kind,
                "source": order.get("source", ""),
                "order_no": order.get("order_no", ""),
                "order_date": order_date,
                "delivery_note": delivery_note,
            }
        )

        for raw_item in raw_items:
            name = str(raw_item.get("name") or raw_item.get("product") or "").strip()
            qty = to_number(raw_item.get("qty") if "qty" in raw_item else raw_item.get("quantity"))
            if not name or qty is None:
                warnings.append(f"订单 {order_id} 有一行缺商品名或数量，已跳过。")
                continue
            normalized = {
                "store": store,
                "product": name,
                "name": name,
                "quantity": qty,
                "qty": qty,
                "unit": raw_item.get("unit", ""),
                "type": order.get("change_type") or ("基础订单" if kind == "base" else "加货"),
                "code": _valid_code(raw_item.get("code")),
                "spec": raw_item.get("spec", ""),
                "price": raw_item.get("price"),
                "category": raw_item.get("category", ""),
                "source": "robot",
                "robot_order_id": order_id,
                "order_kind": kind,
                "order_no": order.get("order_no", ""),
                "order_date": order_date,
                "delivery_note": delivery_note,
            }
            items.append(normalized)
            batch["items"].append(normalized)

            key = _item_key(raw_item)
            current = store_bucket["items"].setdefault(
                key,
                {
                    "code": normalized["code"],
                    "name": name,
                    "spec": normalized["spec"],
                    "unit": normalized["unit"],
                    "category": normalized["category"],
                    "quantity": 0.0,
                },
            )
            current["quantity"] += float(qty)
            batch_current = batch_store_bucket["items"].setdefault(
                key,
                {
                    "code": normalized["code"],
                    "name": name,
                    "spec": normalized["spec"],
                    "unit": normalized["unit"],
                    "category": normalized["category"],
                    "quantity": 0.0,
                },
            )
            batch_current["quantity"] += float(qty)

    grouped_list = _grouped_list(grouped)
    batch_list = []
    for batch in sorted(batches.values(), key=lambda item: item["order_date"] or "9999-99-99"):
        batch_grouped = _grouped_list(batch["grouped"])
        batch_counts = dict(batch["counts"])
        batch_counts["items"] = len(batch["items"])
        batch_counts["stores"] = len(batch_grouped)
        batch_list.append(
            {
                "order_date": batch["order_date"],
                "ids": batch["ids"],
                "items": batch["items"],
                "grouped": batch_grouped,
                "counts": batch_counts,
            }
        )

    return {
        "ids": accepted_ids,
        "all_ids": all_ids,
        "items": items,
        "grouped": grouped_list,
        "warnings": warnings,
        "rejected_patches": rejected_patches,
        "order_dates": order_dates,
        "batches": batch_list,
        "counts": {
            "orders": len(orders),
            "items": len(items),
            "stores": len(grouped_list),
            "order_dates": len(order_dates),
            "rejected_patches": len(rejected_patches),
            **kind_counts,
        },
    }


def normalize_robot_receipts(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("items") or payload.get("receipts") or payload.get("orders") or []
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    summary: dict[str, dict[str, Any]] = {}
    ids: list[Any] = []
    for row in rows:
        receipt_id = row.get("id")
        if receipt_id is not None and receipt_id not in ids:
            ids.append(receipt_id)
        raw_items = row.get("items") if isinstance(row.get("items"), list) else [row]
        for raw_item in raw_items:
            name = str(raw_item.get("name") or raw_item.get("product") or "").strip()
            qty = to_number(raw_item.get("qty") if "qty" in raw_item else raw_item.get("quantity"))
            if not name or qty is None:
                warnings.append(f"入库数据 {receipt_id or ''} 有一行缺商品名或数量，已跳过。")
                continue
            normalized = {
                "product": name,
                "name": name,
                "quantity": qty,
                "qty": qty,
                "unit": raw_item.get("unit", ""),
                "code": _valid_code(raw_item.get("code")),
                "spec": raw_item.get("spec", ""),
                "source": "robot_receipt",
                "receipt_id": receipt_id,
            }
            items.append(normalized)
            key = _item_key(raw_item)
            current = summary.setdefault(
                key,
                {
                    "code": normalized["code"],
                    "name": name,
                    "spec": normalized["spec"],
                    "unit": normalized["unit"],
                    "quantity": 0.0,
                },
            )
            current["quantity"] += float(qty)
    items_summary = sorted(summary.values(), key=lambda item: item["name"])
    return {
        "ids": ids,
        "items": items,
        "items_summary": items_summary,
        "warnings": warnings,
        "counts": {"items": len(items), "products": len(items_summary), "records": len(rows)},
    }


async def fetch_robot_orders(
    status: str = "new",
    extra_base_stores: set[str] | None = None,
    order_date: str | None = None,
) -> dict[str, Any]:
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE。")
    params = {"status": status}
    if order_date:
        params["order_date"] = order_date
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{ROBOT_API_BASE}/api/orders",
            params=params,
            headers=_robot_headers(),
        )
        response.raise_for_status()
    return normalize_robot_orders(response.json(), extra_base_stores=extra_base_stores)


async def fetch_robot_receipts(receipt_date: str | None = None) -> dict[str, Any]:
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE。")
    params = {}
    if receipt_date:
        params["date"] = receipt_date
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{ROBOT_API_BASE}/api/receipts",
            params=params,
            headers=_robot_headers(),
        )
        response.raise_for_status()
    return normalize_robot_receipts(response.json())


async def import_robot_products(products: list[dict[str, str]]) -> dict[str, Any]:
    if not products:
        return {
            "skipped": True,
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "merged_in_batch": 0,
            "results": [],
        }
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法导入 SKU。")
    payload = {"products": products}
    logger.info("robot_products_import_request %s", payload)
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/products/import",
            json=payload,
            headers=_robot_headers(),
        )
        response.raise_for_status()
    result = response.json()
    result.setdefault("total", len(products))
    result.setdefault("succeeded", 0)
    result.setdefault("failed", 0)
    result.setdefault("merged_in_batch", 0)
    result.setdefault("results", [])
    return result


async def mark_robot_orders_fetched(ids: list[Any]) -> dict[str, Any]:
    if not ids:
        return {"skipped": True, "ids": []}
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法标记已拉取。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/orders/mark_fetched",
            json={"ids": ids},
            headers=_robot_headers(),
        )
        response.raise_for_status()
    if not response.content:
        return {"ok": True, "succeeded": ids, "failed": []}
    result = response.json()
    if "succeeded" not in result and "failed" not in result:
        result = {"ok": True, "succeeded": ids, "failed": [], "raw": result}
    result.setdefault("succeeded", [])
    result.setdefault("failed", [])
    result["ok"] = not result["failed"]
    return result


async def mark_robot_receipts_fetched(ids: list[Any]) -> dict[str, Any]:
    if not ids:
        return {"skipped": True, "ids": []}
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法标记入库数据已拉取。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/receipts/mark_fetched",
            json={"ids": ids},
            headers=_robot_headers(),
        )
        response.raise_for_status()
    if not response.content:
        return {"ok": True, "succeeded": ids, "failed": []}
    result = response.json()
    if "succeeded" not in result and "failed" not in result:
        result = {"ok": True, "succeeded": ids, "failed": [], "raw": result}
    result.setdefault("succeeded", [])
    result.setdefault("failed", [])
    result["ok"] = not result["failed"]
    return result


async def unmark_robot_orders(ids: list[Any]) -> dict[str, Any]:
    if not ids:
        return {"skipped": True, "ids": []}
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法退回订单。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/orders/unmark",
            json={"ids": ids},
            headers=_robot_headers(),
        )
        response.raise_for_status()
    if not response.content:
        return {"ok": True, "succeeded": ids, "failed": []}
    result = response.json()
    if "succeeded" not in result and "failed" not in result:
        result = {"ok": True, "succeeded": ids, "failed": [], "raw": result}
    result.setdefault("succeeded", [])
    result.setdefault("failed", [])
    result["ok"] = not result["failed"]
    return result


async def unmark_robot_receipts(ids: list[Any]) -> dict[str, Any]:
    if not ids:
        return {"skipped": True, "ids": []}
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法退回入库数据。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/receipts/unmark",
            json={"ids": ids},
            headers=_robot_headers(),
        )
        response.raise_for_status()
    if not response.content:
        return {"ok": True, "succeeded": ids, "failed": []}
    result = response.json()
    if "succeeded" not in result and "failed" not in result:
        result = {"ok": True, "succeeded": ids, "failed": [], "raw": result}
    result.setdefault("succeeded", [])
    result.setdefault("failed", [])
    result["ok"] = not result["failed"]
    return result


async def clear_robot_orders_by_date(order_date: str) -> dict[str, Any]:
    if not order_date:
        raise RuntimeError("缺少 order_date，无法清空订单。")
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法清空订单库。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/orders/clear_by_date",
            json={"order_date": order_date},
            headers=_robot_headers(),
        )
        if response.status_code == 404:
            raise RuntimeError("机器人接口未部署：POST /api/orders/clear_by_date。请先更新 bot 后再清空。")
        response.raise_for_status()
    result = response.json() if response.content else {}
    result.setdefault("order_date", order_date)
    result.setdefault("ok", True)
    return result


async def clear_robot_receipts_by_date(receipt_date: str) -> dict[str, Any]:
    if not receipt_date:
        raise RuntimeError("缺少 date，无法清空入库数据。")
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法清空入库库。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{ROBOT_API_BASE}/api/receipts/clear_by_date",
            json={"date": receipt_date},
            headers=_robot_headers(),
        )
        if response.status_code == 404:
            raise RuntimeError("机器人接口未部署：POST /api/receipts/clear_by_date。请先更新 bot 后再清空。")
        response.raise_for_status()
    result = response.json() if response.content else {}
    result.setdefault("date", receipt_date)
    result.setdefault("ok", True)
    return result
