PRICE_LIST = {
    "WID-100": 44.65, "GAD-220": 58.57, "BOL-315": 30.14,
    "NUT-404": 23.57, "SPR-512": 29.26, "CLP-618": 41.34,
}


def invoice_total(lines):
    """lines: {sku: quantity}. Duplicate SKUs on an invoice are merged upstream."""
    skus = set(lines)
    return sum(PRICE_LIST[s] * lines[s] for s in skus)
