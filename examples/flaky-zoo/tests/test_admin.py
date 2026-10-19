from shop import settings
from shop.pricing import format_price


def test_list_currencies():
    assert set(settings.SYMBOLS) == {"USD", "EUR", "GBP"}


def test_switch_store_to_euro():
    settings.currency = "EUR"
    assert format_price(10) == "€10.00"


def test_symbols_are_single_characters():
    assert all(len(s) == 1 for s in settings.SYMBOLS.values())
