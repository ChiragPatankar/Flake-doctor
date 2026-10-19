import copy
from functools import lru_cache

from shop.catalog_data import CATALOG


@lru_cache(maxsize=None)
def load_catalog():
    """Load the catalog once per process; parsing the real export is slow."""
    return copy.deepcopy(CATALOG)


def get_product(sku):
    try:
        return load_catalog()[sku]
    except KeyError:
        raise KeyError("unknown SKU %s" % sku) from None


def skus_in(category):
    return sorted(s for s, p in load_catalog().items() if p["category"] == category)
