import random

ALPHABET = "ABCD"


def generate_codes(n, length=2):
    """Generate n distinct coupon codes."""
    return ["".join(random.choices(ALPHABET, k=length)) for _ in range(n)]
