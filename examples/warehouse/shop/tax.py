RATES = {
    "WA": 0.065, "OR": 0.0, "CA": 0.0725, "NY": 0.04, "TX": 0.0625, "MA": 0.0625,
    "IL": 0.0625, "FL": 0.06, "NJ": 0.06625, "CO": 0.029, "AZ": 0.056, "NV": 0.0685,
}


def sales_tax(amount, state):
    if state not in RATES:
        raise ValueError("no tax rate for %s" % state)
    return round(amount * RATES[state], 2)
