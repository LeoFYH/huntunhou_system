from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.services import robot_service
from backend.services.excel_service import generate_production_workbook
from backend.services.robot_service import normalize_robot_orders


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
            safety_path=None,
            production_template_path=None,
            confirmed_items=[{"product": "鸡腿", "quantity": 2, "unit": "件"}],
            order_date=date(2026, 6, 21),
            output_dir=Path(tmp),
        )
        assert output.name == "排产表_2026-06-21.xlsx"
        assert output.exists()


def test_robot_headers_include_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(robot_service, "ROBOT_API_TOKEN", "shared-token")
    assert robot_service._robot_headers() == {"Authorization": "Bearer shared-token"}
