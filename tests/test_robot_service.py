from backend.services.robot_service import normalize_robot_orders


def test_normalize_robot_orders_groups_by_store_and_warns_patch_without_base() -> None:
    payload = {
        "orders": [
            {
                "id": 123,
                "kind": "base",
                "source": "excel",
                "store": "鼓楼店",
                "order_no": "A001",
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
                "items": [{"code": None, "name": "鸡汤虾肉馄饨", "unit": "箱", "qty": 2}],
            },
            {
                "id": 789,
                "kind": "patch",
                "source": "text",
                "store": "老三家",
                "items": [{"code": "#N/A", "name": "鸡腿", "unit": "件", "qty": 20}],
            },
        ]
    }
    result = normalize_robot_orders(payload)
    assert result["ids"] == [123, 456, 789]
    assert result["counts"]["orders"] == 3
    assert result["counts"]["items"] == 3
    assert result["counts"]["stores"] == 2
    assert any("789" in warning for warning in result["warnings"])
    gulou = next(group for group in result["grouped"] if group["store"] == "鼓楼店")
    assert len(gulou["orders"]) == 2
    assert sum(item["quantity"] for item in gulou["items"]) == 3
