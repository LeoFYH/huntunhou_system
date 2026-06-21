import asyncio
from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path

from openpyxl import Workbook, load_workbook

from backend.services import robot_service
from backend.services.excel_service import generate_completed_production_workbook, generate_material_issue_workbook, generate_production_workbook
from backend.services.robot_service import normalize_robot_orders, normalize_robot_receipts


def test_normalize_robot_orders_groups_by_order_date_and_rejects_patch_without_base() -> None:
    payload = {
        "orders": [
            {
                "id": 123,
                "kind": "base",
                "source": "excel",
                "store": "鼓楼店",
                "order_no": "A001",
                "order_date": "2026-06-21",
                "deliver_date": "2026-06-22",
                "items": [
                    {
                        "code": "05020094",
                        "name": "鸡汤虾肉馄饨",
                        "spec": "500g/袋*12袋",
                        "unit": "箱",
                        "qty": 1,
                        "price": 399.11,
                        "category": "馄饨",
                    }
                ],
            },
            {
                "id": 456,
                "kind": "patch",
                "source": "text",
                "store": "鼓楼店",
                "change_type": "add",
                "order_date": "2026-06-21",
                "deliver_date": "2026-06-23",
                "items": [{"code": None, "name": "鸡汤虾肉馄饨", "unit": "箱", "qty": 2}],
            },
            {
                "id": 789,
                "kind": "patch",
                "source": "text",
                "store": "老三家",
                "order_date": "2026-06-21",
                "items": [{"code": "#N/A", "name": "鸡腿", "unit": "件", "qty": 20}],
            },
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["ids"] == [123, 456]
    assert result["all_ids"] == [123, 456, 789]
    assert result["order_dates"] == ["2026-06-21"]
    assert "deliver_dates" not in result
    assert "target_deliver_date" not in result
    assert "blocking_reasons" not in result
    assert result["counts"]["orders"] == 3
    assert result["counts"]["items"] == 2
    assert result["counts"]["stores"] == 1
    assert result["counts"]["rejected_patches"] == 1
    assert result["rejected_patches"][0]["store"] == "老三家"
    assert result["rejected_patches"][0]["order_date"] == "2026-06-21"
    assert result["rejected_patches"][0]["items"][0]["label"] == "鸡腿 20件"

    assert len(result["batches"]) == 1
    batch = result["batches"][0]
    assert batch["order_date"] == "2026-06-21"
    assert batch["ids"] == [123, 456]
    assert batch["counts"]["items"] == 2
    gulou = next(group for group in batch["grouped"] if group["store"] == "鼓楼店")
    assert len(gulou["orders"]) == 2
    assert sum(item["quantity"] for item in gulou["items"]) == 3


def test_normalize_robot_orders_accepts_patch_when_uploaded_base_store_exists() -> None:
    payload = {
        "orders": [
            {
                "id": 789,
                "kind": "patch",
                "source": "text",
                "store": "老三家",
                "order_date": "2026-06-21",
                "items": [{"code": "#N/A", "name": "鸡腿", "unit": "件", "qty": 20}],
            }
        ]
    }
    result = normalize_robot_orders(payload, extra_base_stores={"老三家"})
    assert result["ids"] == [789]
    assert result["rejected_patches"] == []
    assert result["counts"]["items"] == 1
    assert result["batches"][0]["order_date"] == "2026-06-21"
    assert result["batches"][0]["grouped"][0]["store"] == "老三家"


def test_normalize_robot_orders_splits_multiple_order_dates_without_blocking() -> None:
    payload = {
        "orders": [
            {
                "id": 1,
                "kind": "base",
                "store": "A",
                "order_date": "2026-06-21",
                "items": [{"name": "豆浆", "qty": 1, "unit": "箱"}],
            },
            {
                "id": 2,
                "kind": "base",
                "store": "B",
                "order_date": "2026-06-22",
                "items": [{"name": "面条", "qty": 2, "unit": "箱"}],
            },
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["order_dates"] == ["2026-06-21", "2026-06-22"]
    assert [batch["order_date"] for batch in result["batches"]] == ["2026-06-21", "2026-06-22"]
    assert "blocking_reasons" not in result
    assert result["batches"][0]["ids"] == [1]
    assert result["batches"][1]["ids"] == [2]


def test_patch_requires_base_on_same_order_date_when_base_is_from_robot() -> None:
    payload = {
        "orders": [
            {"id": 1, "kind": "base", "store": "鼓楼店", "order_date": "2026-06-21", "items": []},
            {
                "id": 2,
                "kind": "patch",
                "store": "鼓楼店",
                "order_date": "2026-06-22",
                "items": [{"name": "鸡腿", "qty": 20, "unit": "件"}],
            },
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["ids"] == [1]
    assert result["rejected_patches"][0]["id"] == 2
    assert result["rejected_patches"][0]["order_date"] == "2026-06-22"


def test_generate_production_workbook_uses_order_date_for_filename() -> None:
    with TemporaryDirectory() as tmp:
        output, _warnings = generate_production_workbook(
            order_paths=[],
            production_template_path=None,
            safety_stock_path=None,
            confirmed_items=[{"product": "鸡腿", "quantity": 2, "unit": "件"}],
            order_date=date(2026, 6, 21),
            output_dir=Path(tmp),
        )
        assert output.name == "排产表_待补充_2026-06-21.xlsx"
        assert output.exists()
        wb = load_workbook(output, data_only=False)
        ws = wb.active
        assert ws["H3"].value is None
        assert ws["K3"].value is None
        assert ws["L3"].value == "=H3"


def test_generate_production_workbook_outputs_only_order_items_from_template() -> None:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        template_path = tmp_dir / "production_template.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "排产单"
        ws["G2"] = "日期"
        ws.append(["序号", "类别", "编码", "商品名称", "规格", "单位", "单价", "盘点库存数", "安全库存数", "入库数", "出库数量", "理论库存数", "排产量"])
        ws.append([1, "模板", "T1", "模板SKU1", "100g", "箱", 1, None, None, None, None, None, None])
        ws.append([2, "馄饨", "T2", "订单商品", "500g", "箱", 9.5, None, None, None, None, None, None])
        wb.save(template_path)

        output, _warnings = generate_production_workbook(
            order_paths=[],
            production_template_path=template_path,
            safety_stock_path=None,
            confirmed_items=[{"product": "订单商品", "quantity": 3, "unit": "箱"}],
            order_date=date(2026, 6, 21),
            output_dir=tmp_dir,
        )

        wb = load_workbook(output, data_only=False)
        ws = wb.active
        assert ws["C4"].value == "T2"
        assert ws["D4"].value == "订单商品"
        assert ws["K4"].value == 3
        assert ws["D5"].value is None
        products = [ws.cell(row, 4).value for row in range(4, ws.max_row + 1) if ws.cell(row, 4).value]
        assert products == ["订单商品"]


def test_generate_production_workbook_fills_safety_from_safety_table() -> None:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        template_path = tmp_dir / "production_template.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "排产单"
        ws["G2"] = "日期"
        ws.append(["序号", "类别", "编码", "商品名称", "规格", "单位", "单价", "盘点库存数", "安全库存数", "入库数", "出库数量", "理论库存数", "排产量"])
        ws.append([1, "馄饨", "T2", "订单商品", "500g", "箱", 9.5, None, None, None, None, None, None])
        wb.save(template_path)

        safety_path = tmp_dir / "safety.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["随便列", "品名", "库存标准"])
        ws.append(["x", "订单商品", 80])
        wb.save(safety_path)

        output, _warnings = generate_production_workbook(
            order_paths=[],
            production_template_path=template_path,
            safety_stock_path=safety_path,
            confirmed_items=[{"product": "订单商品", "quantity": 3, "unit": "箱"}],
            order_date=date(2026, 6, 21),
            output_dir=tmp_dir,
        )

        wb = load_workbook(output, data_only=False)
        ws = wb.active
        assert ws["I4"].value == 80
        assert ws["K4"].value == 3
        assert ws["L4"].value is None
        assert ws["M4"].value == "=I4"


def test_generate_completed_production_workbook_calculates_theory_stock() -> None:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        production_path = tmp_dir / "draft.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["序号", "类别", "编码", "商品名称", "规格", "单位", "单价", "盘点库存数", "安全库存数", "入库数", "出库数量", "理论库存数", "理论排产"])
        ws.append([1, "馄饨", "T2", "订单商品", "500g", "箱", 9.5, 20, 80, 5, 3, None, "=I2"])
        wb.save(production_path)

        output, warnings = generate_completed_production_workbook(
            production_path=production_path,
            document_date=date(2026, 6, 21),
            output_dir=tmp_dir,
        )

        assert warnings == []
        assert output.name == "排产表_2026-06-21.xlsx"
        wb = load_workbook(output, data_only=False)
        ws = wb.active
        assert ws["L2"].value == 22
        assert ws["M2"].value == "=I2"


def test_generate_material_issue_workbook_adds_warehouse_from_owner_table() -> None:
    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        production_path = tmp_dir / "production.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["商品名称", "盘点库存数", "安全库存数", "入库数", "出库数量", "排产量"])
        ws.append(["鸡腿", 10, 100, 0, 0, 100])
        wb.save(production_path)

        recipe_path = tmp_dir / "recipe.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "鸡腿投料单"
        ws.append(["", "", "", "", "", "", ""])
        ws.append(["", "原料名称", "单品净重 g", "得率", "", "", ""])
        ws.append(["", "猪肉馅", 100, 1, "", "", ""])
        wb.save(recipe_path)

        conversion_path = tmp_dir / "conversion.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["存货名称", "数量"])
        ws.append(["猪肉馅", 2])
        wb.save(conversion_path)

        owner_path = tmp_dir / "owner.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["存货名称", "所属库"])
        ws.append(["猪肉馅", "冷冻"])
        wb.save(owner_path)

        output, missing, warnings = generate_material_issue_workbook(
            production_path=production_path,
            recipe_paths=[recipe_path],
            conversion_path=conversion_path,
            stock_owner_path=owner_path,
            material_template_path=None,
            workshop_stock_text="",
            document_date=date(2026, 6, 21),
            output_dir=tmp_dir,
        )

        assert missing == []
        assert warnings == []
        assert output is not None
        wb = load_workbook(output)
        ws = wb.active
        assert ws["F2"].value == "所属库"
        assert ws["F3"].value == "冷冻"


def test_normalize_robot_receipts_summarizes_finished_goods_without_store() -> None:
    result = normalize_robot_receipts(
        {
            "receipts": [
                {
                    "id": "r1",
                    "items": [
                        {"name": "鸡汤虾肉馄饨", "qty": "2", "unit": "箱"},
                        {"name": "鸡汤虾肉馄饨", "qty": 3, "unit": "箱"},
                    ],
                }
            ]
        }
    )
    assert result["ids"] == ["r1"]
    assert result["counts"]["items"] == 2
    assert result["counts"]["products"] == 1
    assert "store" not in result["items"][0]
    assert "grouped" not in result
    assert result["items_summary"][0]["quantity"] == 5


def test_robot_headers_include_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(robot_service, "ROBOT_API_TOKEN", "shared-token")
    assert robot_service._robot_headers() == {"Authorization": "Bearer shared-token"}


def test_unmark_robot_orders_skips_empty_ids() -> None:
    assert asyncio.run(robot_service.unmark_robot_orders([])) == {"skipped": True, "ids": []}
