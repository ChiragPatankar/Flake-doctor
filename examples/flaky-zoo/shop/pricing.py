from shop import settings


def format_price(amount):
    symbol = settings.SYMBOLS[settings.currency]
    return f"{symbol}{amount:,.2f}"
