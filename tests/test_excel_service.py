from pathlib import Path

from openpyxl import Workbook

from backend.services.excel_service import aggregate_orders, parse_rows, safety_stock_map


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
