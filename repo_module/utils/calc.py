"""
Financial calculations for REPO Module.
Leg2 amount formula with HALF_UP rounding.
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date


def calculate_leg2_amount(
    leg1_amount: Decimal,
    rate: Decimal,
    leg1_settlement_date: date,
    leg2_settlement_date: date,
    day_count_convention: str = "ACT/365",
) -> tuple[Decimal, Decimal, int]:
    """
    Calculate Leg2 amount using REPO formula.

    Formula:
        Repo_Income = ROUND(leg1_amount * rate * Days / denominator, 2, HALF_UP)
        Leg2_Amount = ROUND(leg1_amount + Repo_Income, 2, HALF_UP)

    Returns:
        (leg2_amount, repo_income, days_to_maturity)
    """
    days = (leg2_settlement_date - leg1_settlement_date).days
    if days <= 0:
        raise ValueError(f"leg2_settlement_date must be after leg1_settlement_date, got days={days}")

    denominator = _get_denominator(day_count_convention)

    # Intermediate calculation with full precision (Decimal handles this natively)
    repo_income_raw = leg1_amount * rate * Decimal(days) / Decimal(denominator)
    repo_income = repo_income_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    leg2_amount_raw = leg1_amount + repo_income
    leg2_amount = leg2_amount_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return leg2_amount, repo_income, days


def _get_denominator(day_count_convention: str) -> int:
    """Return day count denominator based on convention."""
    convention_map = {
        "ACT/365": 365,
        "ACT/360": 360,
        "30/360": 360,
    }
    return convention_map.get(day_count_convention, 365)


def round_half_up(value: Decimal, places: int = 2) -> Decimal:
    """Round a Decimal value using HALF_UP convention."""
    quantizer = Decimal(10) ** -places
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)
