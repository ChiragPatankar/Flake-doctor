from shop.catalog import get_product
from shop.shipping import shipping_cost
from shop.tax import sales_tax


def order_total(items, state):
    """items: {sku: quantity}. Returns subtotal + shipping + tax, rounded to cents."""
    subtotal = sum(get_product(s)["price"] * q for s, q in items.items())
    weight = sum(get_product(s)["weight_kg"] * q for s, q in items.items())
    subtotal = round(subtotal, 2)
    return round(subtotal + shipping_cost(weight) + sales_tax(subtotal, state), 2)
