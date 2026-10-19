import pytest

from shop.catalog import get_product, load_catalog, skus_in

ALL_SKUS = [
    'WID-100S',
    'WID-100L',
    'GAD-107S',
    'GAD-107L',
    'BOL-114S',
    'BOL-114L',
    'NUT-121S',
    'NUT-121L',
    'SPR-128S',
    'SPR-128L',
    'CLI-135S',
    'CLI-135L',
    'HIN-142S',
    'HIN-142L',
    'BRA-149S',
    'BRA-149L',
    'WAS-156S',
    'WAS-156L',
    'GEA-163S',
    'GEA-163L',
    'PUL-170S',
    'PUL-170L',
    'VAL-177S',
    'VAL-177L',
    'SPR-184S',
    'SPR-184L',
    'BEA-191S',
    'BEA-191L',
    'CLA-198S',
    'CLA-198L',
    'RIV-205S',
    'RIV-205L',
    'GAS-212S',
    'GAS-212L',
    'COU-219S',
    'COU-219L',
    'SPI-226S',
    'SPI-226L',
    'LAT-233S',
    'LAT-233L'
]


@pytest.mark.parametrize("sku", ALL_SKUS)
def test_product_has_required_fields(sku):
    product = get_product(sku)
    assert set(product) >= {"name", "price", "weight_kg", "category"}
    assert product["price"] > 0
    assert product["weight_kg"] > 0


def test_unknown_sku():
    with pytest.raises(KeyError, match="unknown SKU"):
        get_product("NOPE-1")


@pytest.mark.parametrize("category", ["hardware", "tools", "fasteners"])
def test_skus_in_category_are_sorted(category):
    skus = skus_in(category)
    assert skus == sorted(skus)
    assert all(load_catalog()[s]["category"] == category for s in skus)
