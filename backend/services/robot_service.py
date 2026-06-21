from __future__ import annotations

from typing import Any

import httpx

from ..config import ROBOT_API_BASE, ROBOT_API_TIMEOUT_SECONDS, ROBOT_API_TOKEN
from .excel_service import normalize_key, to_number


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


def normalize_robot_orders(payload: dict[str, Any], extra_base_stores: set[str] | None = None) -> dict[str, Any]:
    orders = payload.get("orders") or []
    extra_base_stores = extra_base_stores or set()
    base_stores = {
        str(order.get("store") or "").strip()
        for order in orders
        if order.get("kind") == "base" and str(order.get("store") or "").strip()
    }
    attachable_stores = base_stores | extra_base_stores
    all_ids: list[Any] = []
    accepted_ids: list[Any] = []
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    rejected_patches: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    kind_counts = {"base": 0, "patch": 0, "other": 0}
    deliver_dates = sorted(
        {
            str(order.get("deliver_date") or "").strip()
            for order in orders
            if str(order.get("deliver_date") or "").strip()
        }
    )

    for order in orders:
        order_id = order.get("id")
        if order_id is not None and order_id not in all_ids:
            all_ids.append(order_id)
        kind = str(order.get("kind") or "other")
        kind_counts[kind if kind in kind_counts else "other"] += 1
        store = str(order.get("store") or "未填写门店").strip()
        raw_items = order.get("items") or []

        if kind == "patch" and store not in attachable_stores:
            rejected_patches.append(
                {
                    "id": order_id,
                    "store": store,
                    "deliver_date": order.get("deliver_date", ""),
                    "items": [
                        {
                            "name": str(item.get("name") or item.get("product") or "").strip(),
                            "qty": item.get("qty") if "qty" in item else item.get("quantity"),
                            "unit": item.get("unit", ""),
                            "label": _item_label(item),
                        }
                        for item in raw_items
                    ],
                    "message": f"{store} 有加货，但没有找到 {store} 今天的主订单 Excel。",
                }
            )
            continue

        if order_id is not None and order_id not in accepted_ids:
            accepted_ids.append(order_id)

        store_bucket = grouped.setdefault(store, {"store": store, "orders": [], "items": {}})
        store_bucket["orders"].append(
            {
                "id": order_id,
                "kind": kind,
                "source": order.get("source", ""),
                "order_no": order.get("order_no", ""),
                "deliver_date": order.get("deliver_date", ""),
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
                "deliver_date": order.get("deliver_date", ""),
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

    grouped_list = [
        {
            "store": bucket["store"],
            "orders": bucket["orders"],
            "items": sorted(bucket["items"].values(), key=lambda item: item["name"]),
        }
        for bucket in grouped.values()
    ]

    return {
        "ids": accepted_ids,
        "all_ids": all_ids,
        "items": items,
        "grouped": grouped_list,
        "warnings": warnings,
        "rejected_patches": rejected_patches,
        "deliver_dates": deliver_dates,
        "target_deliver_date": deliver_dates[0] if len(deliver_dates) == 1 else None,
        "blocking_reasons": ["订单库返回了多个到货日期，请按 deliver_date 分批同步。"] if len(deliver_dates) > 1 else [],
        "counts": {
            "orders": len(orders),
            "items": len(items),
            "stores": len(grouped_list),
            "rejected_patches": len(rejected_patches),
            **kind_counts,
        },
    }


async def fetch_robot_orders(status: str = "new", extra_base_stores: set[str] | None = None) -> dict[str, Any]:
    if not ROBOT_API_BASE:
        raise RuntimeError("未配置 ROBOT_API_BASE。")
    async with httpx.AsyncClient(timeout=ROBOT_API_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{ROBOT_API_BASE}/api/orders",
            params={"status": status},
            headers=_robot_headers(),
        )
        response.raise_for_status()
    return normalize_robot_orders(response.json(), extra_base_stores=extra_base_stores)


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
