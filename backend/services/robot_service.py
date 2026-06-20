from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from ..config import ROBOT_API_BASE, ROBOT_API_TIMEOUT_SECONDS
from .excel_service import normalize_key, to_number


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


def normalize_robot_orders(payload: dict[str, Any]) -> dict[str, Any]:
    orders = payload.get("orders") or []
    base_stores = {
        str(order.get("store") or "").strip()
        for order in orders
        if order.get("kind") == "base" and str(order.get("store") or "").strip()
    }
    ids: list[Any] = []
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    grouped: dict[str, dict[str, Any]] = {}
    kind_counts = {"base": 0, "patch": 0, "other": 0}

    for order in orders:
        order_id = order.get("id")
        if order_id is not None and order_id not in ids:
            ids.append(order_id)
        kind = str(order.get("kind") or "other")
        kind_counts[kind if kind in kind_counts else "other"] += 1
        store = str(order.get("store") or "未填写门店").strip()
        if kind == "patch" and store not in base_stores:
            warnings.append(f"加货单 {order_id} 未找到同门店基础订单，已单独列出。")

        store_bucket = grouped.setdefault(store, {"store": store, "orders": [], "items": {}})
        store_bucket["orders"].append(
            {
                "id": order_id,
                "kind": kind,
                "source": order.get("source", ""),
                "order_no": order.get("order_no", ""),
            }
        )

        for raw_item in order.get("items") or []:
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
            }
            items.append(normalized)

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

    grouped_list = []
    for bucket in grouped.values():
        grouped_list.append(
            {
                "store": bucket["store"],
                "orders": bucket["orders"],
                "items": sorted(bucket["items"].values(), key=lambda item: item["name"]),
            }
        )

    return {
        "ids": ids,
        "items": items,
        "grouped": grouped_list,
        "warnings": warnings,
        "counts": {
            "orders": len(orders),
            "items": len(items),
            "stores": len(grouped_list),
            **kind_counts,
        },
    }


async def fetch_robot_orders(status: str = "new") -> dict[str, Any]:
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{ROBOT_API_BASE}/api/orders", params={"status": status})
        response.raise_for_status()
    return normalize_robot_orders(response.json())


async def mark_robot_orders_fetched(ids: list[Any]) -> dict[str, Any]:
    if not ids:
        return {"skipped": True, "ids": []}
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE，无法标记已拉取。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{ROBOT_API_BASE}/api/orders/mark_fetched", json={"ids": ids})
        response.raise_for_status()
    return response.json() if response.content else {"ok": True, "ids": ids}
