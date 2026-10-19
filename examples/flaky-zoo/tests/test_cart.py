from decimal import Decimal

from shop.cart import cart_total


def test_cart_total():
    assert cart_total([19.99, 5.01]) == Decimal("25.00")


def test_cart_discount():
    assert cart_total([100], discount_pct=15) == Decimal("85.00")
