import pytest
from demo_app.checkout_logic import (
    apply_discount,
    compute_volume_discount,
    get_tax_rate,
    calculate_checkout,
)


def test_discount_logic():
    # Pristine: book has 10% discount, unknown items return 0.0 without crashing
    assert apply_discount("book", 1) == 0.10
    assert apply_discount("unknown_item", 1) == 0.0


def test_volume_discount_tiers():
    # Pristine: supports qty 1, 2, 3, and volume tiers > 3 without IndexError
    assert compute_volume_discount(1) == 0.0
    assert compute_volume_discount(2) == 0.05
    assert compute_volume_discount(3) == 0.10
    assert compute_volume_discount(4) == 0.15
    assert compute_volume_discount(10) == 0.15


def test_tax_rates_and_keys():
    # Pristine: correct tax rates for book, electronics, clothing without KeyError
    assert get_tax_rate("book") == 0.05
    assert get_tax_rate("electronics") == 0.18
    assert get_tax_rate("clothing") == 0.12


def test_full_checkout_calculation():
    res = calculate_checkout("book", 4)
    assert res["item"] == "book"
    assert res["qty"] == 4
    assert res["total"] > 0
