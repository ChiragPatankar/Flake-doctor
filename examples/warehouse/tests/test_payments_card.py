import pytest

from shop.payments.card import CardProvider


@pytest.mark.parametrize("amount,fee", [(100, 2.9), (19.99, 0.58), (0.5, 0.01)])
def test_card_fee(amount, fee):
    assert CardProvider().charge(amount)["fee"] == fee


def test_card_refund():
    assert CardProvider().refund(10)["status"] == "refunded"
