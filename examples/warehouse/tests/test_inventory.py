import pytest

from shop.inventory import Inventory


@pytest.fixture
def inv():
    return Inventory({"WID-100S": 10, "BOL-114S": 3})


@pytest.mark.parametrize("qty", [1, 2, 5, 10])
def test_reserve_reduces_stock(inv, qty):
    inv.reserve("WID-100S", qty)
    assert inv.available("WID-100S") == 10 - qty


@pytest.mark.parametrize("qty", [4, 11, 100])
def test_cannot_reserve_more_than_available(inv, qty):
    with pytest.raises(ValueError, match="left"):
        inv.reserve("BOL-114S", qty)


@pytest.mark.parametrize("qty", [0, -3])
def test_reserve_rejects_non_positive(inv, qty):
    with pytest.raises(ValueError):
        inv.reserve("WID-100S", qty)


def test_restock_new_sku(inv):
    inv.restock("NEW-1", 4)
    assert inv.available("NEW-1") == 4
