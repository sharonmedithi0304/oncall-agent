"""Buggy variant 1: discount lookup returns None and is dereferenced (AttributeError)."""

class DiscountRule:
    def __init__(self, code: str, rate: float):
        self.code = code
        self.rate = rate


DISCOUNT_RULES = {
    "book": DiscountRule("BOOK10", 0.10),
    "electronics": DiscountRule("TECH5", 0.05),
}

PRICES = {
    "book": 20.0,
    "electronics": 100.0,
    "clothing": 30.0,
}

TAX_RATES = {
    "book": 0.05,
    "electronics": 0.18,
    "clothing": 0.12,
}

TIER_DISCOUNTS = [0.0, 0.05, 0.10, 0.15]


def get_discount(item: str):
    # Bug: discount lookup returns None
    return None


def apply_discount(item: str, qty: int) -> float:
    discount = get_discount(item)
    # Dereferencing discount directly without checking None -> AttributeError
    return discount.rate


def compute_volume_discount(qty: int) -> float:
    discount = 0.0
    for i in range(min(qty, len(TIER_DISCOUNTS))):
        discount = TIER_DISCOUNTS[i]
    return discount


def get_tax_rate(item: str) -> float:
    return TAX_RATES.get(item, 0.08)


def calculate_checkout(item: str = "book", qty: int = 1) -> dict:
    unit_price = PRICES.get(item, 25.0)
    base_amount = unit_price * qty

    item_discount_rate = apply_discount(item, qty)
    volume_discount_rate = compute_volume_discount(qty)
    total_discount_rate = min(item_discount_rate + volume_discount_rate, 0.50)

    discounted_subtotal = base_amount * (1.0 - total_discount_rate)
    tax_rate = get_tax_rate(item)
    tax_amount = round(discounted_subtotal * tax_rate, 2)
    final_total = round(discounted_subtotal + tax_amount, 2)

    return {
        "item": item,
        "qty": qty,
        "unit_price": unit_price,
        "subtotal": round(base_amount, 2),
        "item_discount_rate": item_discount_rate,
        "volume_discount_rate": volume_discount_rate,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": final_total,
    }
