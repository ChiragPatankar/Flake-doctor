from shop.pricing import format_price


def test_default_currency():
    assert format_price(5) == "$5.00"


def test_thousands_separator():
    assert format_price(1234.5).endswith("1,234.50")
