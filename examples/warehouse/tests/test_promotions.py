import pytest

from shop.catalog import get_product
from shop.catalog_data import CATALOG
from shop.promotions import bundle_price, flash_sale_price

CASES = [
    ('RIV-205S', 20),
    ('WAS-156S', 25),
    ('GEA-163L', 0),
    ('GAS-212S', 25),
    ('BEA-191L', 10),
    ('GAD-107S', 0),
    ('VAL-177L', 10),
    ('BRA-149S', 20),
    ('GAD-107L', 15),
    ('WID-100S', 20),
    ('HIN-142S', 10),
    ('GAS-212L', 25),
    ('SPR-184L', 10),
    ('CLA-198S', 0),
    ('SPR-128L', 10),
]


@pytest.mark.parametrize("sku,pct", CASES)
def test_flash_sale_price(sku, pct):
    expected = round(CATALOG[sku]["price"] * (100 - pct) / 100, 2)
    assert flash_sale_price(sku, pct) == expected


@pytest.mark.parametrize("pct", [-5, 51, 99])
def test_flash_sale_rejects_bad_discount(pct):
    with pytest.raises(ValueError):
        flash_sale_price('GAD-107L', pct)


def test_bundle_price():
    skus = ['BOL-114L', 'NUT-121L']
    listed = sum(CATALOG[s]["price"] for s in skus)
    assert bundle_price(skus, pct=10) == round(listed * 0.9, 2)
