import pytest

from shop.shipping import shipping_cost


@pytest.mark.parametrize("weight,cost", [
    (0.1, 4.99), (1, 4.99), (1.01, 9.99), (5, 9.99), (7.5, 14.99), (10, 14.99),
    (10.5, 24.99), (25, 24.99), (26, 26.09), (40, 41.49), (100, 107.49),
])
def test_shipping_brackets(weight, cost):
    assert shipping_cost(weight) == cost


@pytest.mark.parametrize("weight", [0, -1])
def test_rejects_non_positive_weight(weight):
    with pytest.raises(ValueError):
        shipping_cost(weight)
