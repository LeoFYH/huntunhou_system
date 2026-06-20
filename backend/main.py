from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import WEB_DIR
from .services.ai_service import parse_text_with_deepseek
from .services.excel_service import (
    generate_material_issue_workbook,
    generate_production_workbook,
    generate_receipt_workbook,
    generate_shipment_outputs,
    parse_rows,
)
from .storage import (
    ensure_storage,
    output_path,
    public_state,
    register_output,
    reset_module,
    reset_slot,
    save_text,
    save_upload,
    seed_defaults_once,
    slot_path,
    slot_paths,
    text_value,
)


app = FastAPI(title="馄饨侯订单工具", version="0.1.0")


class TextPayload(BaseModel):
    value: str = ""


class AiTextPayload(BaseModel):
    text: str = ""


class GeneratePayload(BaseModel):
    confirmed_items: list[dict[str, Any]] = []


class MaterialPayload(BaseModel):
    stock_field: str = "inventory"
    workshop_stock_text: str = ""


class ReceiptPayload(BaseModel):
    items: list[dict[str, Any]] = []


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


@app.post("/api/upload/{slot}")
async def upload(slot: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    saved = []
    for item in files:
        saved.append(await save_upload(slot, item))
    return {"slot": slot, "files": saved, "state": public_state()}


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
        "module1_orders",
        "module4_orders",
        "production_template",
        "safety_stock",
        "module2_production",
        "receipt_template",
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
    order_paths = slot_paths("module1_orders")
    if not order_paths and not payload.confirmed_items:
        raise HTTPException(status_code=400, detail="请先上传订单或确认文字加单。")
    with TemporaryDirectory() as tmp:
        output, warnings = generate_production_workbook(
            order_paths=order_paths,
            safety_path=slot_path("safety_stock"),
            production_template_path=slot_path("production_template"),
            confirmed_items=payload.confirmed_items,
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    return {"output": registered, "warnings": warnings}


@app.post("/api/generate/shipment")
async def generate_shipment(payload: GeneratePayload) -> dict[str, Any]:
    order_paths = slot_paths("module4_orders")
    if not order_paths and not payload.confirmed_items:
        raise HTTPException(status_code=400, detail="请先上传原始订单或确认文字发货。")
    with TemporaryDirectory() as tmp:
        output, warnings = generate_shipment_outputs(
            order_paths=order_paths,
            confirmed_items=payload.confirmed_items,
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    return {"output": registered, "warnings": warnings}


@app.post("/api/generate/material-issue")
async def generate_material(payload: MaterialPayload) -> dict[str, Any]:
    stock_field = "theory_stock" if payload.stock_field == "theory_stock" else "inventory"
    workshop_text = payload.workshop_stock_text or text_value("module2_stock_text")
    with TemporaryDirectory() as tmp:
        output, missing, warnings = generate_material_issue_workbook(
            production_path=slot_path("module2_production"),
            recipe_path=slot_path("recipe_table"),
            conversion_path=slot_path("conversion_table"),
            material_template_path=slot_path("material_template"),
            workshop_stock_text=workshop_text,
            stock_field=stock_field,
            output_dir=Path(tmp),
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
            output_dir=Path(tmp),
        )
        registered = register_output(output, output.name)
    return {"output": registered, "warnings": warnings}


@app.get("/api/download/{output_id}")
async def download(output_id: str) -> FileResponse:
    path = output_path(output_id)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在或已被清理。")
    return FileResponse(path, filename=path.name)


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")

