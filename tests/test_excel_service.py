from pathlib import Path

import pytest

from openpyxl import Workbook

from backend.services.excel_service import aggregate_orders, parse_recipe_table, parse_rows, recipe_required_qty, safety_stock_map, summarize_recipe_tables


def save_order(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "鼓楼"
    ws.append(["馄饨侯（鼓楼）店产品订货单"])
    ws.append([])
    ws.append(["序号", "类别", "编码", "原料名称", "规格", "单位", "单价", "订货数量"])
    ws.append([1, "馄饨", "A1", "鸡汤鲜肉馄饨", "260g", "箱", 10, 2])
    ws.append([2, "馄饨", "A2", "鸡汤虾肉馄饨", "500g", "箱", 12, None])
    wb.save(path)


def save_safety(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["商品名称", "安全库存数"])
    ws.append(["鸡汤鲜肉馄饨", 60])
    wb.save(path)


def test_parse_and_aggregate_order(tmp_path: Path) -> None:
    order = tmp_path / "order.xlsx"
    save_order(order)
    rows = parse_rows(order, "order")
    assert rows[0]["product"] == "鸡汤鲜肉馄饨"
    assert rows[0]["order_qty"] == 2
    summary = aggregate_orders([order])
    item = next(iter(summary.values()))
    assert item["quantity"] == 2


def test_safety_stock_map(tmp_path: Path) -> None:
    safety = tmp_path / "safety.xlsx"
    save_safety(safety)
    values = safety_stock_map(safety)
    assert next(iter(values.values())) == 60


def test_parse_feed_sheet_recipe(tmp_path: Path) -> None:
    path = tmp_path / "feed.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "鸡蛋面"
    ws["A1"] = "鸡蛋面投料单"
    ws["J3"] = "订单量"
    ws["L3"] = 10
    ws.append([])
    ws.append([])
    ws.append(["NO.", "原料名称", "单品净重  g", "得率", "原料使用量 g", "", "生产个数\n（订单量+保存样）"])
    ws.append([1, "面粉", 100, 0.5, "=C5/D5", "g", "=$L$3+2"])
    ws.append(["TTL", "", "=SUM(C5:C5)"])
    wb.save(path)

    recipes = parse_recipe_table(path)
    assert recipes[0]["finished"] == "鸡蛋面"
    assert recipes[0]["raw"] == "面粉"
    assert recipes[0]["qty"] == 0.2
    assert recipe_required_qty(recipes[0], 10) == pytest.approx(2.4)
    summary = summarize_recipe_tables([path])
    assert summary["file_count"] == 1
    assert summary["product_count"] == 1
    assert summary["recipe_rows"] == 1
