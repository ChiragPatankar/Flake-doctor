import pytest

from shop.checkout import order_total


@pytest.mark.parametrize("items,state,expected", [
    pytest.param({'WID-100S': 2}, 'WA', 129.11, id='standard'),
    pytest.param({'BOL-114S': 1}, 'TX', 32.56, id='case1'),
    pytest.param({'NUT-121S': 3}, 'NY', 170.83, id='case2'),
    pytest.param({'COU-219L': 1}, 'OR', 81.69, id='case3'),
    pytest.param({'RIV-205L': 2, 'SPI-226S': 3, 'CLI-135L': 3}, 'NY', 445.51, id='case4'),
    pytest.param({'COU-219L': 3}, 'OR', 240.09, id='case5'),
    pytest.param({'PUL-170S': 2, 'BEA-191S': 3, 'SPR-184S': 2}, 'WA', 310.1, id='case6'),
    pytest.param({'BRA-149L': 1}, 'CA', 25.28, id='case7'),
    pytest.param({'LAT-233S': 3}, 'OR', 195.57, id='case8'),
    pytest.param({'BRA-149L': 3, 'WID-100L': 3, 'BOL-114L': 1}, 'OR', 203.79, id='case9')
])
def test_order_total(items, state, expected):
    assert order_total(items, state) == expected


def test_order_total_unknown_state():
    with pytest.raises(ValueError):
        order_total({'WID-100S': 1}, "ZZ")
