from decimal import Decimal


def cart_total(prices, discount_pct=0):
    total = sum(Decimal(str(p)) for p in prices)
    total -= total * Decimal(discount_pct) / 100
    return total.quantize(Decimal("0.01"))
