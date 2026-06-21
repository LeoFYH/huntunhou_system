from backend.services import robot_service
from backend.services.robot_service import normalize_robot_orders


def test_normalize_robot_orders_groups_by_store_and_rejects_patch_without_base() -> None:
    payload = {
        "orders": [
            {
                "id": 123,
                "kind": "base",
                "source": "excel",
                "store": "鼓楼店",
                "order_no": "A001",
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
                "deliver_date": "2026-06-22",
                "items": [{"code": None, "name": "鸡汤虾肉馄饨", "unit": "箱", "qty": 2}],
            },
            {
                "id": 789,
                "kind": "patch",
                "source": "text",
                "store": "老三家",
                "deliver_date": "2026-06-22",
                "items": [{"code": "#N/A", "name": "鸡腿", "unit": "件", "qty": 20}],
            },
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["ids"] == [123, 456]
    assert result["all_ids"] == [123, 456, 789]
    assert result["target_deliver_date"] == "2026-06-22"
    assert result["counts"]["orders"] == 3
    assert result["counts"]["items"] == 2
    assert result["counts"]["stores"] == 1
    assert result["counts"]["rejected_patches"] == 1
    assert result["rejected_patches"][0]["store"] == "老三家"
    assert result["rejected_patches"][0]["items"][0]["label"] == "鸡腿 20件"
    gulou = next(group for group in result["grouped"] if group["store"] == "鼓楼店")
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
                "deliver_date": "2026-06-22",
                "items": [{"code": "#N/A", "name": "鸡腿", "unit": "件", "qty": 20}],
            }
        ]
    }
    result = normalize_robot_orders(payload, extra_base_stores={"老三家"})
    assert result["ids"] == [789]
    assert result["rejected_patches"] == []
    assert result["counts"]["items"] == 1
    assert result["grouped"][0]["store"] == "老三家"


def test_normalize_robot_orders_blocks_multiple_deliver_dates() -> None:
    payload = {
        "orders": [
            {"id": 1, "kind": "base", "store": "A", "deliver_date": "2026-06-22", "items": []},
            {"id": 2, "kind": "base", "store": "B", "deliver_date": "2026-06-23", "items": []},
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["target_deliver_date"] is None
    assert result["deliver_dates"] == ["2026-06-22", "2026-06-23"]
    assert result["blocking_reasons"]


def test_robot_headers_include_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(robot_service, "ROBOT_API_TOKEN", "shared-token")
    assert robot_service._robot_headers() == {"Authorization": "Bearer shared-token"}
