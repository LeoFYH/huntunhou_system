from __future__ import annotations

import math
import re
import zipfile
from collections import defaultdict
from collections import Counter
from copy import copy
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter


NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
CELL_REF_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")


@dataclass
class TableMap:
    header_row: int
    data_start: int
    columns: dict[str, int]


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).strip()


def normalize_key(value: Any) -> str:
    text = normalize_text(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def clean_finished_name(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"投料单$", "", text)
    text = re.sub(r"（停用）|\(停用\)", "", text)
    return text.strip()


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.startswith("="):
        return None
    match = NUMBER_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


def display_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else round(float(value), 4)


def last_nonempty_row(ws) -> int:
    rows = [row for (row, _col), cell in ws._cells.items() if cell.value not in (None, "")]
    return max(rows, default=1)


def _header_text(ws, row: int, col: int) -> str:
    parts = []
    for r in (row, row + 1):
        if r <= ws.max_row:
            value = ws.cell(r, col).value
            if value not in (None, ""):
                parts.append(str(value))
    return normalize_text("".join(parts))


def _find_col(headers: dict[int, str], keywords: Iterable[str]) -> int | None:
    for keyword in keywords:
        for col, text in headers.items():
            if keyword in text:
                return col
    return None


def detect_table(ws, purpose: str = "generic") -> TableMap | None:
    scan_rows = min(last_nonempty_row(ws), 30)
    for row in range(1, scan_rows + 1):
        current_headers = {col: normalize_text(ws.cell(row, col).value) for col in range(1, ws.max_column + 1)}
        headers = {col: _header_text(ws, row, col) for col in range(1, ws.max_column + 1)}
        product = _find_col(current_headers, ["商品名称", "原料名称", "存货名称", "产品名称"])
        if not product:
            continue

        columns: dict[str, int] = {"product": product}
        pairs = {
            "sequence": ["序号"],
            "category": ["类别", "备注"],
            "code": ["存货编码", "产品编码", "编码"],
            "spec": ["规格型号", "商品规格", "规格"],
            "unit": ["主计量", "单位"],
            "price": ["单价"],
            "order_qty": ["订货数量"],
            "qty": ["数量"],
            "safety": ["安全库存数", "安全库存"],
            "inventory": ["盘点库存数", "盘点库存"],
            "inbound": ["入库数", "入库"],
            "outbound": ["出库数量", "出库数", "订货数量"],
            "theory_stock": ["理论库存数", "理论库存"],
            "production": ["理论排产", "排产量", "产量"],
        }
        for key, keywords in pairs.items():
            col = _find_col(headers, keywords)
            if col:
                columns[key] = col

        if purpose == "order" and "order_qty" not in columns and "qty" in columns:
            columns["order_qty"] = columns["qty"]
        if purpose == "material" and "qty" not in columns and "order_qty" in columns:
            columns["qty"] = columns["order_qty"]

        return TableMap(header_row=row, data_start=row + 1, columns=columns)
    return None


def infer_store_name(ws, path: Path) -> str:
    for cell in ("A1", "B1", "A2", "B2"):
        value = ws[cell].value
        if not value:
            continue
        text = str(value)
        match = re.search(r"馄饨侯[（(]?([^）)店]+)[）)]?店", text)
        if match:
            return f"{match.group(1)}店"
    title = ws.title.replace("订货", "").strip()
    if title and not title.lower().startswith("sheet"):
        return title if title.endswith(("店", "学校")) else f"{title}店"
    return path.stem


def parse_rows(path: Path, purpose: str = "generic") -> list[dict[str, Any]]:
    wb = load_workbook(path, data_only=True)
    parsed: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        table = detect_table(ws, purpose)
        if not table:
            continue
        store = infer_store_name(ws, path)
        columns = table.columns
        for row in range(table.data_start, last_nonempty_row(ws) + 1):
            product = ws.cell(row, columns["product"]).value
            if not normalize_text(product):
                continue
            item: dict[str, Any] = {
                "product": str(product).strip(),
                "product_key": normalize_key(product),
                "store": store,
                "source": path.name,
                "row": row,
            }
            for key in ("sequence", "category", "code", "spec", "unit", "price", "order_qty", "qty", "safety", "inventory", "inbound", "outbound", "theory_stock", "production"):
                col = columns.get(key)
                if not col:
                    continue
                value = ws.cell(row, col).value
                if key in {"price", "order_qty", "qty", "safety", "inventory", "inbound", "outbound", "theory_stock", "production"}:
                    item[key] = to_number(value)
                else:
                    item[key] = "" if value is None else str(value).strip()
            parsed.append(item)
    return parsed


def aggregate_orders(paths: list[Path], confirmed_items: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in parse_rows(path, "order"):
            qty = row.get("order_qty")
            if qty is None or qty == 0:
                continue
            key = row["product_key"]
            current = summary.setdefault(
                key,
                {
                    "product": row["product"],
                    "category": row.get("category", ""),
                    "code": row.get("code", ""),
                    "spec": row.get("spec", ""),
                    "unit": row.get("unit", ""),
                    "price": row.get("price"),
                    "quantity": 0.0,
                    "stores": defaultdict(float),
                },
            )
            current["quantity"] += float(qty)
            current["stores"][row["store"]] += float(qty)

    for item in confirmed_items or []:
        product = str(item.get("product") or item.get("name") or "").strip()
        if not product:
            continue
        qty = to_number(item.get("quantity"))
        if qty is None:
            continue
        key = normalize_key(product)
        current = summary.setdefault(
            key,
            {
                "product": product,
                "category": item.get("category", ""),
                "code": item.get("code", ""),
                "spec": item.get("spec", ""),
                "unit": item.get("unit", ""),
                "price": None,
                "quantity": 0.0,
                "stores": defaultdict(float),
            },
        )
        current["quantity"] += float(qty)
        store = str(item.get("store") or "文字加单").strip()
        current["stores"][store] += float(qty)
    return summary


def safety_stock_map(path: Path | None) -> dict[str, float]:
    if not path:
        return {}
    result: dict[str, float] = {}
    for row in parse_rows(path, "safety"):
        qty = row.get("safety")
        if qty is None:
            qty = row.get("qty")
        if qty is None:
            continue
        result[row["product_key"]] = float(qty)
    return result


def product_catalog(paths: list[Path]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path:
            continue
        for row in parse_rows(path, "generic"):
            key = row["product_key"]
            if key in catalog:
                continue
            catalog[key] = {
                "product": row["product"],
                "category": row.get("category", ""),
                "code": row.get("code", ""),
                "spec": row.get("spec", ""),
                "unit": row.get("unit", ""),
                "price": row.get("price"),
            }
    return catalog


def copy_row_format(ws, src_row: int, dst_row: int, max_col: int) -> None:
    for col in range(1, max_col + 1):
        src = ws.cell(src_row, col)
        dst = ws.cell(dst_row, col)
        if src.has_style:
            dst._style = copy(src._style)
        if src.number_format:
            dst.number_format = src.number_format
        if src.alignment:
            dst.alignment = copy(src.alignment)
        if src.border:
            dst.border = copy(src.border)
        if src.fill:
            dst.fill = copy(src.fill)
        if src.font:
            dst.font = copy(src.font)


def write_basic_headers(ws, title: str, headers: list[str]) -> None:
    ws.title = "Sheet1"
    ws.append([title])
    ws.append(headers)
    ws.freeze_panes = "A3"
    for idx, header in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(idx)].width = max(12, min(24, len(header) * 2))


def generate_production_workbook(
    order_paths: list[Path],
    safety_path: Path | None,
    production_template_path: Path | None,
    confirmed_items: list[dict[str, Any]] | None,
    output_dir: Path,
) -> tuple[Path, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    orders = aggregate_orders(order_paths, confirmed_items)

    template_safety = safety_stock_map(production_template_path)
    safety = safety_stock_map(safety_path) or template_safety
    if not safety:
        warnings.append("未上传安全库存表，也未能从排产模板读取安全库存数。")

    catalog_paths = [p for p in [production_template_path, safety_path, *order_paths] if p]
    catalog = product_catalog(catalog_paths)
    for key, order in orders.items():
        catalog.setdefault(
            key,
            {
                "product": order["product"],
                "category": order.get("category", ""),
                "code": order.get("code", ""),
                "spec": order.get("spec", ""),
                "unit": order.get("unit", ""),
                "price": order.get("price"),
            },
        )

    keys = list(catalog.keys())
    rows = []
    for idx, key in enumerate(keys, 1):
        meta = catalog[key]
        order_qty = orders.get(key, {}).get("quantity", 0.0)
        safety_qty = safety.get(key)
        if key in orders and safety_qty is None:
            warnings.append(f"{meta['product']} 没有安全库存数，排产量留空。")
        rows.append(
            {
                "sequence": idx,
                "category": meta.get("category", ""),
                "product": meta.get("product", ""),
                "spec": meta.get("spec", ""),
                "unit": meta.get("unit", ""),
                "price": meta.get("price"),
                "inventory": None,
                "safety": safety_qty,
                "inbound": None,
                "outbound": order_qty,
                "theory_stock_formula": None,
                "production": safety_qty,
            }
        )

    today = date.today()
    if production_template_path:
        wb = load_workbook(production_template_path)
        ws = wb.worksheets[0]
        table = detect_table(ws, "production") or TableMap(
            header_row=2,
            data_start=4,
            columns={
                "sequence": 1,
                "category": 2,
                "product": 3,
                "spec": 4,
                "unit": 5,
                "price": 6,
                "inventory": 7,
                "safety": 8,
                "inbound": 9,
                "outbound": 10,
                "theory_stock": 11,
                "production": 12,
            },
        )
        max_col = max(12, ws.max_column)
        data_start = max(4, table.data_start)
        clear_to = max(last_nonempty_row(ws), data_start + len(rows) + 5)
        for r in range(data_start, clear_to + 1):
            copy_row_format(ws, data_start, r, max_col)
            for c in range(1, max_col + 1):
                ws.cell(r, c).value = None
        cols = table.columns
        for cell in ("G2", "I2", "J2"):
            if ws[cell].value is not None:
                ws[cell].value = today
        if ws["K2"].value is not None:
            ws["K2"].value = today + timedelta(days=1)
        if ws["L2"].value is not None:
            ws["L2"].value = today + timedelta(days=1)
        for offset, item in enumerate(rows):
            r = data_start + offset
            values = {
                "sequence": item["sequence"],
                "category": item["category"],
                "product": item["product"],
                "spec": item["spec"],
                "unit": item["unit"],
                "price": display_number(item["price"]),
                "inventory": None,
                "safety": display_number(item["safety"]),
                "inbound": None,
                "outbound": display_number(item["outbound"]),
                "theory_stock": f"=G{r}+I{r}-J{r}" if cols.get("theory_stock") else None,
                "production": display_number(item["production"]),
            }
            for key, value in values.items():
                col = cols.get(key)
                if col:
                    ws.cell(r, col).value = value
    else:
        wb = Workbook()
        ws = wb.active
        headers = ["序号", "类别", "商品名称", "商品规格", "单位", "单价（元）", "盘点库存数", "安全库存数", "入库数", "出库数量", "理论库存数", "排产量"]
        write_basic_headers(ws, "排产单", headers)
        for item in rows:
            ws.append(
                [
                    item["sequence"],
                    item["category"],
                    item["product"],
                    item["spec"],
                    item["unit"],
                    display_number(item["price"]),
                    None,
                    display_number(item["safety"]),
                    None,
                    display_number(item["outbound"]),
                    None,
                    display_number(item["production"]),
                ]
            )
    output = output_dir / f"排产表_{today.isoformat()}.xlsx"
    wb.save(output)
    return output, warnings


def _best_order_sheet(path: Path):
    wb = load_workbook(path)
    best_ws = None
    best_table = None
    best_count = -1
    for ws in wb.worksheets:
        table = detect_table(ws, "order")
        if not table or "order_qty" not in table.columns:
            continue
        count = 0
        for r in range(table.data_start, last_nonempty_row(ws) + 1):
            if to_number(ws.cell(r, table.columns["order_qty"]).value):
                count += 1
        if count > best_count:
            best_ws, best_table, best_count = ws, table, count
    return wb, best_ws, best_table


def generate_shipment_outputs(
    order_paths: list[Path],
    confirmed_items: list[dict[str, Any]] | None,
    output_dir: Path,
) -> tuple[Path, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    store_products: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    store_template: dict[str, Path] = {}

    for path in order_paths:
        for row in parse_rows(path, "order"):
            qty = row.get("order_qty")
            if qty is None or qty == 0:
                continue
            store_products[row["store"]][row["product_key"]] += float(qty)
            store_template.setdefault(row["store"], path)

    for item in confirmed_items or []:
        store = str(item.get("store") or "").strip()
        product = str(item.get("product") or item.get("name") or "").strip()
        qty = to_number(item.get("quantity"))
        if not store or not product or qty is None:
            continue
        store_products[store][normalize_key(product)] += float(qty)

    if not store_products:
        warnings.append("没有可生成发货单的订货数量或确认发货文本。")

    generated: list[Path] = []
    today = date.today().isoformat()
    first_template = order_paths[0] if order_paths else None
    for store, products in store_products.items():
        template = store_template.get(store) or first_template
        if not template:
            wb = Workbook()
            ws = wb.active
            write_basic_headers(ws, f"{store}发货单", ["序号", "原料名称", "规格", "单位", "订货数量"])
            for idx, (key, qty) in enumerate(products.items(), 1):
                ws.append([idx, key, "", "", display_number(qty)])
        else:
            wb, ws, table = _best_order_sheet(template)
            if not ws or not table:
                warnings.append(f"{template.name} 没有识别到订单格式，已跳过。")
                continue
            for other in list(wb.worksheets):
                if other is not ws:
                    wb.remove(other)
            ws.title = store[:31]
            qty_col = table.columns.get("order_qty")
            product_col = table.columns["product"]
            if qty_col:
                for r in range(table.data_start, last_nonempty_row(ws) + 1):
                    product_key = normalize_key(ws.cell(r, product_col).value)
                    ws.cell(r, qty_col).value = display_number(products.get(product_key, 0.0)) or None
        safe_store = re.sub(r"[^\w\u4e00-\u9fff]+", "_", store).strip("_") or "门店"
        output = output_dir / f"{safe_store}_发货单_{today}.xlsx"
        wb.save(output)
        generated.append(output)

    if len(generated) == 1:
        return generated[0], warnings
    zip_path = output_dir / f"发货单_{today}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in generated:
            archive.write(path, path.name)
    return zip_path, warnings


def _eval_linear_formula(ws, cell_ref: str, cache: dict[str, tuple[float, float]], stack: set[str] | None = None) -> tuple[float, float]:
    stack = stack or set()
    cell_ref = cell_ref.replace("$", "").upper()
    if cell_ref in cache:
        return cache[cell_ref]
    if cell_ref in stack:
        return (1.0, 0.0)
    stack.add(cell_ref)
    value = ws[cell_ref].value
    if value in (None, ""):
        stack.remove(cell_ref)
        return (1.0, 0.0)
    if isinstance(value, (int, float)):
        stack.remove(cell_ref)
        return (0.0, float(value))
    expr = str(value).strip()
    if expr.startswith("="):
        expr = expr[1:]
    expr = expr.replace("$", "").upper()
    expr = expr.replace("L3", "Q")

    def repl(match: re.Match[str]) -> str:
        ref = f"{match.group(1)}{match.group(2)}"
        if ref == "L3":
            return "Q"
        a, b = _eval_linear_formula(ws, ref, cache, stack)
        return f"(({a})*Q+({b}))"

    expr = CELL_REF_RE.sub(repl, expr)
    if not re.fullmatch(r"[0-9Qq+\-*/(). ]+", expr):
        stack.remove(cell_ref)
        return (1.0, 0.0)

    def evaluate(qty: float) -> float:
        return float(eval(expr.replace("Q", str(qty)), {"__builtins__": {}}, {}))

    try:
        b = evaluate(0)
        a = evaluate(1) - b
    except Exception:
        a, b = 1.0, 0.0
    cache[cell_ref] = (a, b)
    stack.remove(cell_ref)
    return a, b


def _parse_feed_sheet(ws) -> list[dict[str, Any]]:
    title_name = clean_finished_name(ws.title)
    first_cell_name = clean_finished_name(ws["A1"].value)
    finished = title_name or first_cell_name
    if not finished:
        return []
    rows: list[dict[str, Any]] = []
    formula_cache: dict[str, tuple[float, float]] = {}
    last = last_nonempty_row(ws)
    header_rows = []
    for r in range(1, last + 1):
        headers = [normalize_text(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)]
        if "原料名称" in headers and any("单品净重" in value for value in headers) and "得率" in headers:
            header_rows.append(r)

    for header_row in header_rows:
        for r in range(header_row + 1, last + 1):
            marker = normalize_text(ws.cell(r, 1).value)
            raw_name = normalize_text(ws.cell(r, 2).value)
            if not raw_name:
                continue
            if raw_name == "原料名称":
                break
            if marker.upper() == "TTL":
                break
            net_weight_g = to_number(ws.cell(r, 3).value)
            yield_rate = to_number(ws.cell(r, 4).value) or 1.0
            if net_weight_g is None or not yield_rate:
                continue
            qty_per_unit_kg = float(net_weight_g) / float(yield_rate) / 1000
            units_multiplier, units_addend = _eval_linear_formula(ws, f"G{r}", formula_cache)
            rows.append(
                {
                    "finished_key": normalize_key(finished),
                    "finished": finished,
                    "raw_key": normalize_key(raw_name),
                    "raw": raw_name,
                    "unit": "kg",
                    "spec": "",
                    "code": "",
                    "qty": qty_per_unit_kg,
                    "units_multiplier": units_multiplier,
                    "units_addend": units_addend,
                    "source_sheet": ws.title,
                    "source_row": r,
                }
            )
    return rows


def parse_recipe_table(path: Path) -> list[dict[str, Any]]:
    wb = load_workbook(path, data_only=False)
    feed_rows: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        feed_rows.extend(_parse_feed_sheet(ws))
    if feed_rows:
        return feed_rows

    rows = parse_rows(path, "generic")
    recipes = []
    for row in rows:
        finished = row.get("category") or row.get("product")
        raw_name = row.get("product")
        qty = row.get("qty") or row.get("order_qty")
        if not finished or not raw_name or qty is None:
            continue
        recipes.append(
            {
                "finished_key": normalize_key(finished),
                "finished": finished,
                "raw_key": normalize_key(raw_name),
                "raw": raw_name,
                "unit": row.get("unit", ""),
                "spec": row.get("spec", ""),
                "code": row.get("code", ""),
                "qty": float(qty),
            }
        )
    return recipes


def parse_recipe_tables(paths: list[Path]) -> list[dict[str, Any]]:
    recipes: list[dict[str, Any]] = []
    for path in paths:
        recipes.extend(parse_recipe_table(path))
    return recipes


def summarize_recipe_tables(paths: list[Path]) -> dict[str, Any]:
    files = []
    total_rows = 0
    total_products: Counter[str] = Counter()
    for path in paths:
        workbook_rows = 0
        workbook_products: Counter[str] = Counter()
        unrecognized_sheets: list[str] = []
        try:
            wb = load_workbook(path, data_only=False)
        except Exception as exc:
            files.append(
                {
                    "name": path.name,
                    "recipe_rows": 0,
                    "products": [],
                    "unrecognized_sheets": [],
                    "error": str(exc),
                }
            )
            continue

        for ws in wb.worksheets:
            rows = _parse_feed_sheet(ws)
            if rows:
                workbook_rows += len(rows)
                workbook_products.update(row["finished"] for row in rows)
            elif last_nonempty_row(ws) > 1 or normalize_text(ws["A1"].value):
                unrecognized_sheets.append(ws.title)

        if workbook_rows == 0:
            fallback_rows = parse_recipe_table(path)
            workbook_rows = len(fallback_rows)
            workbook_products.update(row["finished"] for row in fallback_rows)

        total_rows += workbook_rows
        total_products.update(workbook_products)
        files.append(
            {
                "name": path.name,
                "recipe_rows": workbook_rows,
                "products": [{"name": name, "rows": count} for name, count in workbook_products.most_common()],
                "unrecognized_sheets": unrecognized_sheets,
            }
        )

    return {
        "file_count": len(paths),
        "recipe_rows": total_rows,
        "product_count": len(total_products),
        "products": [{"name": name, "rows": count} for name, count in total_products.most_common()],
        "files": files,
    }


def recipe_required_qty(recipe: dict[str, Any], production_qty: float) -> float:
    multiplier = float(recipe.get("units_multiplier", 1.0))
    addend = float(recipe.get("units_addend", 0.0))
    units = multiplier * float(production_qty) + addend
    return float(recipe["qty"]) * max(units, 0.0)


def parse_workshop_stock(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in re.split(r"[\n,，;；]+", text):
        match = NUMBER_RE.search(line)
        if not match:
            continue
        name = line[: match.start()].strip(" ：:\t")
        if not name:
            name = line[match.end() :].strip(" ：:\t")
        if name:
            result[normalize_key(name)] = float(match.group())
    return result


def parse_conversion_table(path: Path | None) -> dict[str, float]:
    if not path:
        return {}
    conversions: dict[str, float] = {}
    for row in parse_rows(path, "generic"):
        qty = row.get("qty") or row.get("order_qty")
        if qty:
            conversions[row["product_key"]] = float(qty)
    return conversions


def generate_material_issue_workbook(
    production_path: Path | None,
    recipe_paths: list[Path],
    conversion_path: Path | None,
    material_template_path: Path | None,
    workshop_stock_text: str,
    output_dir: Path,
) -> tuple[Path | None, list[str], list[str]]:
    missing = []
    if not production_path:
        missing.append("已填好的排产表")
    if not recipe_paths:
        missing.append("原材料配方表/投料单")
    if not conversion_path:
        missing.append("单位换算表")
    if missing:
        return None, missing, []

    warnings: list[str] = []
    production_rows = parse_rows(production_path, "production")
    recipes = parse_recipe_tables(recipe_paths)
    conversions = parse_conversion_table(conversion_path)
    workshop_stock = parse_workshop_stock(workshop_stock_text)

    recipe_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recipe in recipes:
        recipe_by_product[recipe["finished_key"]].append(recipe)

    material_qty: dict[str, dict[str, Any]] = {}
    for row in production_rows:
        safety = row.get("safety")
        if safety is None:
            continue
        inventory = row.get("inventory") or 0.0
        inbound = row.get("inbound") or 0.0
        outbound = row.get("outbound") or 0.0
        current = row.get("theory_stock")
        if current is None:
            current = float(inventory) + float(inbound) - float(outbound)
        current = float(current)
        if current >= float(safety) * 0.5:
            continue
        production_qty = float(row.get("production") or safety)
        product_recipes = recipe_by_product.get(row["product_key"], [])
        if not product_recipes:
            warnings.append(f"{row['product']} 触发领料，但没有找到对应投料单/配方。")
            continue
        for recipe in product_recipes:
            raw_key = recipe["raw_key"]
            need = recipe_required_qty(recipe, production_qty)
            need -= workshop_stock.get(raw_key, 0.0)
            if need <= 0:
                continue
            factor = conversions.get(raw_key)
            issue_qty = math.ceil(need / factor) if factor else need
            if not factor:
                warnings.append(f"{recipe['raw']} 没有换算规格，按原始用量输出。")
            current_item = material_qty.setdefault(
                raw_key,
                {
                    "code": recipe.get("code", ""),
                    "raw": recipe["raw"],
                    "spec": recipe.get("spec", ""),
                    "unit": recipe.get("unit", ""),
                    "qty": 0.0,
                },
            )
            current_item["qty"] += issue_qty

    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    if material_template_path:
        wb = load_workbook(material_template_path)
        ws = wb.worksheets[0]
        table = detect_table(ws, "material")
        data_start = table.data_start if table else 2
        cols = table.columns if table else {"code": 1, "product": 2, "spec": 3, "unit": 4, "qty": 5}
        max_col = max(ws.max_column, 14)
        for r in range(data_start, max(last_nonempty_row(ws), data_start + len(material_qty) + 5) + 1):
            copy_row_format(ws, data_start, r, max_col)
            for c in range(1, max_col + 1):
                ws.cell(r, c).value = None
        for offset, item in enumerate(material_qty.values()):
            r = data_start + offset
            for key, value in {
                "code": item["code"],
                "product": item["raw"],
                "spec": item["spec"],
                "unit": item["unit"],
                "qty": display_number(item["qty"]),
            }.items():
                col = cols.get(key)
                if col:
                    ws.cell(r, col).value = value
    else:
        wb = Workbook()
        ws = wb.active
        write_basic_headers(ws, "材料出库单", ["存货编码", "存货名称", "规格型号", "主计量", "数量"])
        for item in material_qty.values():
            ws.append([item["code"], item["raw"], item["spec"], item["unit"], display_number(item["qty"])])

    output = output_dir / f"材料出库单_{today}.xlsx"
    wb.save(output)
    return output, missing, warnings


def generate_receipt_workbook(
    receipt_template_path: Path | None,
    items: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[Path, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    today = date.today().isoformat()
    clean_items = []
    for item in items:
        name = str(item.get("product") or item.get("name") or "").strip()
        qty = to_number(item.get("quantity"))
        if not name or qty is None:
            continue
        clean_items.append(
            {
                "code": item.get("code", ""),
                "product": name,
                "spec": item.get("spec", ""),
                "unit": item.get("unit", ""),
                "quantity": qty,
            }
        )
    if not clean_items:
        warnings.append("没有可写入入库单的产成品行。")
    if receipt_template_path:
        wb = load_workbook(receipt_template_path)
        ws = wb.worksheets[0]
        table = detect_table(ws, "receipt")
        data_start = table.data_start if table else 2
        cols = table.columns if table else {"code": 1, "product": 2, "spec": 3, "unit": 4, "qty": 5}
        max_col = max(ws.max_column, 12)
        for r in range(data_start, max(last_nonempty_row(ws), data_start + len(clean_items) + 5) + 1):
            copy_row_format(ws, data_start, r, max_col)
            for c in range(1, max_col + 1):
                ws.cell(r, c).value = None
        for offset, item in enumerate(clean_items):
            r = data_start + offset
            for key, value in {
                "code": item["code"],
                "product": item["product"],
                "spec": item["spec"],
                "unit": item["unit"],
                "qty": display_number(item["quantity"]),
            }.items():
                col = cols.get(key)
                if col:
                    ws.cell(r, col).value = value
    else:
        wb = Workbook()
        ws = wb.active
        write_basic_headers(ws, "产成品入库单", ["存货编码", "存货名称", "规格型号", "主计量", "数量"])
        for item in clean_items:
            ws.append([item["code"], item["product"], item["spec"], item["unit"], display_number(item["quantity"])])
    output = output_dir / f"产成品入库单_{today}.xlsx"
    wb.save(output)
    return output, warnings
