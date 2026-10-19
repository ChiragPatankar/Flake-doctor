import pytest

from shop.tax import RATES, sales_tax


@pytest.mark.parametrize("state", sorted(RATES))
@pytest.mark.parametrize("amount", [0, 19.99, 100, 1234.56])
def test_sales_tax_is_rate_times_amount(state, amount):
    assert sales_tax(amount, state) == round(amount * RATES[state], 2)


def test_unknown_state():
    with pytest.raises(ValueError):
        sales_tax(10, "ZZ")
