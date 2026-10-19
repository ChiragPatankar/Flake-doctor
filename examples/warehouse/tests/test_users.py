import pytest

from shop.users import display_name, valid_email


@pytest.mark.parametrize("address", [
    "a@b.co", "first.last@example.com", "x+tag@shop.io", "UPPER@EXAMPLE.ORG", "o'neil@pub.ie",
])
def test_valid_emails(address):
    assert valid_email(address)


@pytest.mark.parametrize("address", ["", None, "nope", "a@b", "a b@c.com", "@c.com", "a@.c", "a@b.c"])
def test_invalid_emails(address):
    assert not valid_email(address)


@pytest.mark.parametrize("first,last,expected", [
    ("Ada", "Lovelace", "Ada Lovelace"), (" Ada ", "", "Ada"), ("", "Lovelace", "Lovelace"),
    ("", "", "Customer"), (None, None, "Customer"),
])
def test_display_name(first, last, expected):
    assert display_name(first, last) == expected
