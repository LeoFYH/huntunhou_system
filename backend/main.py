from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from datetime import date, datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import WEB_DIR
from .services.ai_service import parse_text_with_deepseek
from .services.excel_service import (
    extract_receipt_template_skus,
    generate_completed_production_workbook,
    generate_material_issue_workbook,
    generate_production_workbook,
    generate_receipt_workbook,
    generate_shipment_outputs,
    parse_rows,
    summarize_recipe_tables,
)
from .services.robot_service import (
    fetch_robot_orders,
    fetch_robot_receipts,
    import_robot_products,
    mark_robot_orders_fetched,
    mark_robot_receipts_fetched,
    unmark_robot_orders,
    unmark_robot_receipts,
)
from .services.robot_marking import mark_robot_orders_for_output
from .storage import (
    clear_robot_mark_failures,
    ensure_storage,
    output_path,
    public_state,
    record_robot_mark_failures,
    register_output,
    robot_mark_failures,
    reset_module,
    reset_slot,
    save_text,
    save_upload,
    seed_defaults_once,
    slot_path,
    slot_paths,
)


app = FastAPI(title="订单工具", version="0.1.0")


class TextPayload(BaseModel):
    value: str = ""


class AiTextPayload(BaseModel):
    text: str = ""


class GeneratePayload(BaseModel):
    confirmed_items: list[dict[str, Any]] = []
    robot_order_ids: list[Any] = []
    order_date: str | None = None


class RobotRetryPayload(BaseModel):
    ids: list[Any] | None = None


class ReceiptPayload(BaseModel):
    items: list[dict[str, Any]] = []
    robot_receipt_ids: list[Any] = []
    document_date: str | None = None


@app.on_event("startup")
async def startup() -> None:
    ensure_storage()
    seed_defaults_once()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return public_state()


@app.get("/api/recipe-preview")
async def recipe_preview() -> dict[str, Any]:
    return summarize_recipe_tables(slot_paths("recipe_table"))


def _parse_order_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"order_date 格式无效：{value}")


def _robot_failure_ids() -> list[Any]:
    ids: list[Any] = []
    seen: set[str] = set()
    for failure in robot_mark_failures():
        for item in failure.get("ids", []):
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            ids.append(item)
    return ids


@app.get("/api/robot/orders/fetch")
async def robot_fetch_orders(status: str = "new", order_date: str | None = None) -> dict[str, Any]:
    try:
        return await fetch_robot_orders(status=status, order_date=order_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/robot/receipts/fetch")
async def robot_fetch_receipts(date: str | None = None) -> dict[str, Any]:
    try:
        return await fetch_robot_receipts(receipt_date=date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/robot/orders/retry-mark")
async def retry_robot_mark(payload: RobotRetryPayload) -> dict[str, Any]:
    ids = payload.ids if payload.ids is not None else _robot_failure_ids()
    if not ids:
        return {"robot_mark": {"skipped": True, "ids": []}, "warnings": []}
    try:
        result = await mark_robot_orders_fetched(ids)
    except Exception as exc:
        clear_robot_mark_failures(ids)
        record_robot_mark_failures(ids, str(exc), {"action": "retry_mark_fetched"})
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    succeeded = result.get("succeeded", [])
    failed = result.get("failed", [])
    attempted = list(succeeded) + list(failed)
    if attempted:
        clear_robot_mark_failures(attempted)
    warnings: list[str] = []
    if failed:
        warnings.append(f"订单库仍有 {len(failed)} 个 id 标记失败：{failed}")
        record_robot_mark_failures(failed, "mark_fetched retry partial failure", {"action": "retry_mark_fetched"})
    return {"robot_mark": result, "warnings": warnings, "remaining_failures": robot_mark_failures()}


@app.post("/api/robot/orders/unmark")
async def robot_unmark_orders(payload: RobotRetryPayload) -> dict[str, Any]:
    ids = payload.ids or []
    if not ids:
        return {"robot_unmark": {"skipped": True, "ids": []}, "warnings": []}
    try:
        result = await unmark_robot_orders(ids)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    warnings: list[str] = []
    failed = result.get("failed", [])
    if failed:
        warnings.append(f"订单库有 {len(failed)} 个 id 退回失败：{failed}")
    return {"robot_unmark": result, "warnings": warnings}


@app.post("/api/robot/receipts/unmark")
async def robot_unmark_receipts(payload: RobotRetryPayload) -> dict[str, Any]:
    ids = payload.ids or []
    if not ids:
        return {"robot_receipt_unmark": {"skipped": True, "ids": []}, "warnings": []}
    try:
        result = await unmark_robot_receipts(ids)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    warnings: list[str] = []
    failed = result.get("failed", [])
    if failed:
        warnings.append(f"入库数据有 {len(failed)} 个 id 退回失败：{failed}")
    return {"robot_receipt_unmark": result, "warnings": warnings}


async def _import_receipt_template_skus() -> dict[str, Any]:
    path = slot_path("receipt_template")
    if not path:
        return {
            "ok": False,
            "skipped": True,
            "error": "模板已保存，但没有找到可解析的入库单模板文件。",
            "products": [],
        }
    try:
        extracted = extract_receipt_template_skus(path)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "parse",
            "error": f"模板已保存，但 SKU 解析失败：{exc}",
            "products": [],
        }

    products = extracted.get("products", [])
    base = {
        "source_rows": extracted.get("source_rows", 0),
        "unique_rows": extracted.get("unique_rows", 0),
        "deduped": extracted.get("deduped", 0),
        "truncated": extracted.get("truncated", 0),
        "limit": extracted.get("limit", 1000),
        "products": products,
    }
    if not products:
        return {
            **base,
            "ok": False,
            "skipped": True,
            "error": "模板已保存，但没有识别到可导入的 SKU。",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "merged_in_batch": 0,
            "results": [],
        }

    try:
        result = await import_robot_products(products)
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "stage": "bot_import",
            "error": f"模板已保存，但 SKU 导入机器人失败：{exc}",
            "total": len(products),
            "succeeded": 0,
            "failed": len(products),
            "merged_in_batch": 0,
            "results": [],
        }

    failed = int(result.get("failed") or 0)
    return {
        **base,
        "ok": failed == 0,
        "total": result.get("total", len(products)),
        "succeeded": result.get("succeeded", 0),
        "failed": failed,
        "merged_in_batch": result.get("merged_in_batch", 0),
        "results": result.get("results", []),
    }


@app.post("/api/upload/{slot}")
async def upload(slot: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    saved = []
    for item in files:
        saved.append(await save_upload(slot, item))
    response = {"slot": slot, "files": saved, "state": public_state()}
    if slot == "receipt_template":
        response["sku_import"] = await _import_receipt_template_skus()
    return response


@app.post("/api/receipt-template/import-skus")
async def import_receipt_template_skus() -> dict[str, Any]:
    return {"sku_import": await _import_receipt_template_skus(), "state": public_state()}


@app.post("/api/text/{slot}")
async def update_text(slot: str, payload: TextPayload) -> dict[str, Any]:
    return {"slot": slot, "text": save_text(slot, payload.value), "state": public_state()}


@app.delete("/api/reset/{slot}")
async def reset(slot: str) -> dict[str, Any]:
    reset_slot(slot)
    return {"state": public_state()}


@app.delete("/api/reset-module/{module_id}")
async def clear_module(module_id: str) -> dict[str, Any]:
    reset_module(module_id)
    return {"state": public_state()}


def _catalog() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    paths: list[Path] = []
    for slot in (
        "production_template",
        "receipt_template",
        "shipment_template",
    ):
        paths.extend(slot_paths(slot))
    products: dict[str, dict[str, str]] = {}
    stores: dict[str, dict[str, str]] = {}
    for path in paths:
        try:
            rows = parse_rows(path, "generic")
        except Exception:
            continue
        for row in rows:
            key = row.get("product_key")
            if key and key not in products:
                products[key] = {"name": row["product"]}
            store = row.get("store")
            if store:
                stores.setdefault(store, {"name": store})
    return list(products.values()), list(stores.values())


@app.post("/api/ai/parse-order-text")
async def parse_order_text(payload: AiTextPayload) -> dict[str, Any]:
    products, _ = _catalog()
    return await parse_text_with_deepseek(payload.text, products, include_store=False)


@app.post("/api/ai/parse-shipment-text")
async def parse_shipment_text(payload: AiTextPayload) -> dict[str, Any]:
    products, stores = _catalog()
    return await parse_text_with_deepseek(payload.text, products, stores, include_store=True)


@app.post("/api/ai/parse-receipt-photo")
async def parse_receipt_photo() -> dict[str, Any]:
    return {
        "provider": "placeholder",
        "items": [],
        "message": "拍照识别接口已预留；当前先用手动确认行生成入库单。",
    }


@app.post("/api/generate/production")
async def generate_production(payload: GeneratePayload) -> dict[str, Any]:
    order_paths: list[Path] = []
    if not order_paths and not payload.confirmed_items:
        raise HTTPException(status_code=400, detail="请先同步订单库并确认一个下单日期批次。")
    with TemporaryDirectory() as tmp:
        output, warnings = generate_production_workbook(
            order_paths=order_paths,
            production_template_path=slot_path("production_template"),
            safety_stock_path=slot_path("safety_stock_table"),
            confirmed_items=payload.confirmed_items,
            order_date=_parse_order_date(payload.order_date),
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    robot_mark = await mark_robot_orders_for_output(payload.robot_order_ids, warnings, registered, "排产表")
    return {"output": registered, "warnings": warnings, "robot_mark": robot_mark}


@app.post("/api/generate/production-complete-upload")
async def generate_completed_production(
    production_file: UploadFile = File(...),
    document_date: str | None = Form(default=None),
) -> dict[str, Any]:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        production_path = tmp_dir / (production_file.filename or "production.xlsx")
        production_path.write_bytes(await production_file.read())
        output, warnings = generate_completed_production_workbook(
            production_path=production_path,
            document_date=_parse_order_date(document_date),
            output_dir=tmp_dir,
        )
        registered = register_output(output, output.name)
    return {"output": registered, "warnings": warnings}


@app.post("/api/generate/shipment")
async def generate_shipment(payload: GeneratePayload) -> dict[str, Any]:
    order_paths: list[Path] = []
    if not payload.confirmed_items:
        raise HTTPException(status_code=400, detail="请先同步订单库并确认发货批次。")
    with TemporaryDirectory() as tmp:
        output, warnings = generate_shipment_outputs(
            order_paths=order_paths,
            template_path=slot_path("shipment_template"),
            confirmed_items=payload.confirmed_items,
            order_date=_parse_order_date(payload.order_date),
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    robot_mark = await mark_robot_orders_for_output(payload.robot_order_ids, warnings, registered, "发货单")
    return {"output": registered, "warnings": warnings, "robot_mark": robot_mark}


@app.post("/api/generate/material-issue-upload")
async def generate_material_upload(
    production_file: UploadFile = File(...),
    document_date: str | None = Form(default=None),
) -> dict[str, Any]:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        production_path = tmp_dir / (production_file.filename or "production.xlsx")
        production_path.write_bytes(await production_file.read())
        output, missing, warnings = generate_material_issue_workbook(
            production_path=production_path,
            recipe_paths=slot_paths("recipe_table"),
            conversion_path=slot_path("conversion_table"),
            stock_owner_path=slot_path("stock_owner_table"),
            material_template_path=slot_path("material_template"),
            workshop_stock_text="",
            document_date=_parse_order_date(document_date),
            output_dir=tmp_dir,
        )
        if missing:
            return {"status": "missing_config", "missing": missing, "warnings": warnings}
        assert output is not None
        registered = register_output(output, output.name)
    return {"status": "ok", "output": registered, "warnings": warnings}


@app.post("/api/generate/receipt")
async def generate_receipt(payload: ReceiptPayload) -> dict[str, Any]:
    with TemporaryDirectory() as tmp:
        output, warnings = generate_receipt_workbook(
            receipt_template_path=slot_path("receipt_template"),
            items=payload.items,
            document_date=_parse_order_date(payload.document_date),
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    robot_receipt_mark = None
    if payload.robot_receipt_ids:
        try:
            robot_receipt_mark = await mark_robot_receipts_fetched(payload.robot_receipt_ids)
            failed = robot_receipt_mark.get("failed", [])
            if failed:
                warnings.append(f"入库单已生成，但入库库有 {len(failed)} 个 id 标记失败：{failed}")
        except Exception as exc:
            warnings.append(f"入库单已生成，但入库库 mark_fetched 失败：{exc}")
            robot_receipt_mark = {"ok": False, "error": str(exc), "ids": payload.robot_receipt_ids}
    return {"output": registered, "warnings": warnings, "robot_receipt_mark": robot_receipt_mark}


@app.get("/api/download/{output_id}")
async def download(output_id: str) -> FileResponse:
    path = output_path(output_id)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在或已被清理。")
    return FileResponse(path, filename=path.name)


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})
