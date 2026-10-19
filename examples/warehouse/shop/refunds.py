from shop.payments import get_provider

REFUND_WINDOW_DAYS = 30


def refund_order(order):
    """order: dict with 'method', 'amount', 'age_days'."""
    if order["age_days"] > REFUND_WINDOW_DAYS:
        raise ValueError("refund window has closed")
    return get_provider(order["method"]).refund(order["amount"])
