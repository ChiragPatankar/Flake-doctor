from shop.invoices import invoice_total


def test_single_line_invoice():
    assert invoice_total({"WID-100": 2}) == 89.30


def test_invoice_total():
    lines = {"WID-100": 1, "GAD-220": 2, "BOL-315": 1, "NUT-404": 2, "SPR-512": 3, "CLP-618": 3}
    assert invoice_total(lines) == 450.87


def test_empty_invoice():
    assert invoice_total({}) == 0
