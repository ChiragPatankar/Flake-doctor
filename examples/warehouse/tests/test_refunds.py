import pytest

from shop.refunds import refund_order


def test_refund_card_order():
    result = refund_order({"method": "card", "amount": 42.0, "age_days": 3})
    assert result == {"status": "refunded", "amount": 42.0}


def test_refund_window_closed():
    with pytest.raises(ValueError, match="window"):
        refund_order({"method": "card", "amount": 42.0, "age_days": 45})
