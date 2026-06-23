from __future__ import annotations

from typing import Any, Optional

from ..storage import clear_robot_mark_failures, record_robot_mark_failures
from .robot_service import mark_robot_orders_fetched


async def mark_robot_orders_for_output(
    ids: list[Any],
    warnings: list[str],
    registered: dict[str, Any],
    document_label: str,
) -> Optional[dict[str, Any]]:
    if not ids:
        return None
    try:
        robot_mark = await mark_robot_orders_fetched(ids)
        succeeded = robot_mark.get("succeeded", [])
        failed = robot_mark.get("failed", [])
        if succeeded:
            clear_robot_mark_failures(succeeded)
        if failed:
            warnings.append(f"{document_label}已生成，但订单库有 {len(failed)} 个 id 标记失败，可稍后重试：{failed}")
            record_robot_mark_failures(
                failed,
                "mark_fetched partial failure",
                {"output_id": registered["id"], "output_name": registered["name"]},
            )
        return robot_mark
    except Exception as exc:
        warnings.append(f"{document_label}已生成，但订单库 mark_fetched 失败：{exc}")
        record_robot_mark_failures(
            ids,
            str(exc),
            {"output_id": registered["id"], "output_name": registered["name"]},
        )
        return {"ok": False, "error": str(exc), "ids": ids}
