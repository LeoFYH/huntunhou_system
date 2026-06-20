from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .config import STORAGE_DIR


STATE_PATH = STORAGE_DIR / "state.json"
UPLOAD_DIR = STORAGE_DIR / "uploads"
OUTPUT_DIR = STORAGE_DIR / "outputs"
SEEDED_DIR = STORAGE_DIR / "seeded_files"


MULTI_FILE_SLOTS = {
    "module1_orders",
    "module3_photos",
    "module4_orders",
    "recipe_table",
}

RESIDENT_SLOTS = {
    "safety_stock",
    "production_template",
    "recipe_table",
    "conversion_table",
    "material_template",
    "receipt_template",
}

MODULE_SLOTS = {
    "1": ["module1_orders", "module1_extra_text"],
    "2": ["module2_production", "module2_stock_text"],
    "3": ["module3_photos", "module3_manual_items"],
    "4": ["module4_orders", "module4_ship_text"],
}

SEED_FILES = {
    "production_template": "production_template.xlsx",
    "material_template": "material_issue_template.xlsx",
    "receipt_template": "finished_goods_receipt_template.xlsx",
    "module1_orders": "order_template.xlsx",
    "module4_orders": "order_template.xlsx",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_storage() -> None:
    for path in (STORAGE_DIR, UPLOAD_DIR, OUTPUT_DIR, SEEDED_DIR):
        path.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(
            json.dumps({"files": {}, "text": {}, "outputs": {}, "settings": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_state() -> dict[str, Any]:
    ensure_storage()
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": {}, "text": {}, "outputs": {}, "settings": {}}


def save_state(state: dict[str, Any]) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _slot_is_multi(slot: str) -> bool:
    return slot in MULTI_FILE_SLOTS


def _safe_name(filename: str) -> str:
    cleaned = filename.replace("/", "_").replace("\\", "_").strip()
    return cleaned or "upload.bin"


def _public_meta(meta: dict[str, Any]) -> dict[str, Any]:
    clean = dict(meta)
    clean.pop("path", None)
    return clean


def public_state() -> dict[str, Any]:
    state = load_state()
    files: dict[str, Any] = {}
    for slot, value in state.get("files", {}).items():
        if isinstance(value, list):
            files[slot] = [_public_meta(item) for item in value]
        elif isinstance(value, dict):
            files[slot] = _public_meta(value)
        else:
            files[slot] = value
    outputs = {
        key: {
            "id": key,
            "name": value.get("name"),
            "created_at": value.get("created_at"),
            "url": f"/api/download/{key}",
        }
        for key, value in state.get("outputs", {}).items()
    }
    return {
        "files": files,
        "text": state.get("text", {}),
        "outputs": outputs,
        "settings": state.get("settings", {}),
        "resident_slots": sorted(RESIDENT_SLOTS),
    }


async def save_upload(slot: str, upload: UploadFile) -> dict[str, Any]:
    state = load_state()
    file_id = uuid.uuid4().hex
    filename = _safe_name(upload.filename or "upload.bin")
    slot_dir = UPLOAD_DIR / slot
    slot_dir.mkdir(parents=True, exist_ok=True)
    target = slot_dir / f"{file_id}_{filename}"
    with target.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    meta = {
        "id": file_id,
        "slot": slot,
        "name": filename,
        "path": str(target),
        "size": target.stat().st_size,
        "content_type": upload.content_type or "application/octet-stream",
        "uploaded_at": now_iso(),
    }
    files = state.setdefault("files", {})
    if _slot_is_multi(slot):
        if isinstance(files.get(slot), dict):
            files[slot] = [files[slot]]
        files.setdefault(slot, [])
        files[slot].append(meta)
    else:
        old = files.get(slot)
        if isinstance(old, dict) and old.get("path"):
            Path(old["path"]).unlink(missing_ok=True)
        files[slot] = meta
    save_state(state)
    return _public_meta(meta)


def save_text(slot: str, value: str) -> dict[str, Any]:
    state = load_state()
    payload = {"value": value, "updated_at": now_iso()}
    state.setdefault("text", {})[slot] = payload
    save_state(state)
    return payload


def reset_slot(slot: str) -> None:
    state = load_state()
    value = state.get("files", {}).pop(slot, None)
    if isinstance(value, list):
        for item in value:
            if item.get("path"):
                Path(item["path"]).unlink(missing_ok=True)
    elif isinstance(value, dict) and value.get("path"):
        Path(value["path"]).unlink(missing_ok=True)
    state.get("text", {}).pop(slot, None)
    save_state(state)


def reset_module(module_id: str) -> None:
    for slot in MODULE_SLOTS.get(module_id, []):
        reset_slot(slot)


def slot_paths(slot: str) -> list[Path]:
    state = load_state()
    value = state.get("files", {}).get(slot)
    if isinstance(value, list):
        return [Path(item["path"]) for item in value if item.get("path")]
    if isinstance(value, dict) and value.get("path"):
        return [Path(value["path"])]
    return []


def slot_path(slot: str) -> Path | None:
    paths = slot_paths(slot)
    return paths[-1] if paths else None


def text_value(slot: str) -> str:
    value = load_state().get("text", {}).get(slot, {})
    if isinstance(value, dict):
        return str(value.get("value", ""))
    return ""


def register_output(path: Path, name: str) -> dict[str, Any]:
    state = load_state()
    output_id = uuid.uuid4().hex
    out_dir = OUTPUT_DIR / output_id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    shutil.copy2(path, target)
    state.setdefault("outputs", {})[output_id] = {
        "id": output_id,
        "name": name,
        "path": str(target),
        "created_at": now_iso(),
    }
    save_state(state)
    return {
        "id": output_id,
        "name": name,
        "url": f"/api/download/{output_id}",
        "created_at": state["outputs"][output_id]["created_at"],
    }


def output_path(output_id: str) -> Path | None:
    meta = load_state().get("outputs", {}).get(output_id)
    if not meta or not meta.get("path"):
        return None
    path = Path(meta["path"])
    return path if path.exists() else None


def seed_defaults_once() -> None:
    state = load_state()
    changed = False
    for slot, filename in SEED_FILES.items():
        source = SEEDED_DIR / filename
        if not source.exists():
            continue
        current = state.setdefault("files", {}).get(slot)
        if current:
            continue
        seed_id = uuid.uuid4().hex
        slot_dir = UPLOAD_DIR / slot
        slot_dir.mkdir(parents=True, exist_ok=True)
        target = slot_dir / f"{seed_id}_{filename}"
        shutil.copy2(source, target)
        meta = {
            "id": seed_id,
            "slot": slot,
            "name": filename,
            "path": str(target),
            "size": target.stat().st_size,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "uploaded_at": now_iso(),
            "seeded": True,
        }
        if _slot_is_multi(slot):
            state["files"][slot] = [meta]
        else:
            state["files"][slot] = meta
        changed = True
    if changed:
        save_state(state)
