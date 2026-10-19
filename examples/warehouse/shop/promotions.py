from shop.catalog import get_product

FLASH_SALE_MAX_PCT = 50


def flash_sale_price(sku, pct):
    """Price of `sku` during a flash sale of `pct` percent off."""
    if not 0 <= pct <= FLASH_SALE_MAX_PCT:
        raise ValueError("flash sale discount must be between 0 and %d%%" % FLASH_SALE_MAX_PCT)
    product = get_product(sku)
    if pct:
        product["price"] = round(product["price"] * (100 - pct) / 100, 2)
        product["on_sale"] = True
    return product["price"]


def bundle_price(skus, pct=10):
    """Bundles get `pct` off the summed list price."""
    total = sum(get_product(s)["price"] for s in skus)
    return round(total * (100 - pct) / 100, 2)
