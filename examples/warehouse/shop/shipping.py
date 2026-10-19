BRACKETS = [(1, 4.99), (5, 9.99), (10, 14.99), (25, 24.99)]
HEAVY_RATE_PER_KG = 1.10


def shipping_cost(weight_kg):
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    for limit, cost in BRACKETS:
        if weight_kg <= limit:
            return cost
    return round(24.99 + (weight_kg - 25) * HEAVY_RATE_PER_KG, 2)
