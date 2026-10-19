from shop.coupons import generate_codes


def test_codes_have_requested_length():
    assert all(len(c) == 2 for c in generate_codes(3))


def test_codes_are_unique():
    codes = generate_codes(3)
    assert len(set(codes)) == 3
