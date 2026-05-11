"""
Unit tests for Leg2 calculation formula and role mapping.
"""
import pytest
from datetime import date
from decimal import Decimal

from repo_module.utils.calc import calculate_leg2_amount, round_half_up
from repo_module.models.domain import IncomingTrade
from repo_module.services.trade_service import map_roles, ValidationError
from repo_module.models.domain import InitiatorRole, RejectionType


class TestLeg2Calculation:
    """Tests for Leg2 amount formula with HALF_UP rounding."""

    def test_example_from_tz(self):
        """Test the exact example from the TZ document."""
        leg1_amount = Decimal("95000000.00")
        rate = Decimal("0.165")
        leg1_date = date(2026, 5, 5)
        leg2_date = date(2026, 5, 12)

        leg2_amount, repo_income, days = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date
        )

        assert days == 7
        assert repo_income == Decimal("300616.44")
        assert leg2_amount == Decimal("95300616.44")

    def test_1_day_repo(self):
        """Test 1-day REPO."""
        leg1_amount = Decimal("1000000.00")
        rate = Decimal("0.15")
        leg1_date = date(2026, 5, 7)
        leg2_date = date(2026, 5, 8)

        leg2_amount, repo_income, days = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date
        )

        assert days == 1
        expected_income = round(1000000 * 0.15 * 1 / 365, 2)
        assert float(repo_income) == pytest.approx(expected_income, abs=0.01)

    def test_14_day_repo(self):
        """Test 14-day REPO."""
        leg1_amount = Decimal("50000000.00")
        rate = Decimal("0.18")
        leg1_date = date(2026, 5, 7)
        leg2_date = date(2026, 5, 21)

        leg2_amount, repo_income, days = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date
        )

        assert days == 14
        assert leg2_amount > leg1_amount

    def test_half_up_rounding(self):
        """Test HALF_UP rounding: 0.5 → 1, not 0."""
        # Construct a case where intermediate result ends in .5
        # 1000000 * 0.1825 * 1 / 365 = 500.0 exactly
        leg1_amount = Decimal("1000000.00")
        rate = Decimal("0.1825")
        leg1_date = date(2026, 5, 7)
        leg2_date = date(2026, 5, 8)

        leg2_amount, repo_income, days = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date
        )
        # 1000000 * 0.1825 / 365 = 500.0
        assert repo_income == Decimal("500.00")

    def test_act_360_convention(self):
        """Test ACT/360 day count convention."""
        leg1_amount = Decimal("1000000.00")
        rate = Decimal("0.15")
        leg1_date = date(2026, 5, 7)
        leg2_date = date(2026, 5, 14)

        leg2_365, _, _ = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date, "ACT/365"
        )
        leg2_360, _, _ = calculate_leg2_amount(
            leg1_amount, rate, leg1_date, leg2_date, "ACT/360"
        )

        # ACT/360 gives slightly higher income
        assert leg2_360 > leg2_365

    def test_invalid_dates_raises(self):
        """Test that leg2_date <= leg1_date raises ValueError."""
        with pytest.raises(ValueError, match="leg2_settlement_date must be after"):
            calculate_leg2_amount(
                Decimal("1000000"),
                Decimal("0.15"),
                date(2026, 5, 10),
                date(2026, 5, 10),  # same date
            )

    def test_round_half_up_function(self):
        """Test the round_half_up utility."""
        # 0.005 rounded to 2 places → 0.01 (HALF_UP)
        assert round_half_up(Decimal("0.005")) == Decimal("0.01")
        assert round_half_up(Decimal("0.004")) == Decimal("0.00")
        assert round_half_up(Decimal("2.455")) == Decimal("2.46")
        assert round_half_up(Decimal("2.445")) == Decimal("2.45")
        # 0.5 rounded to 2 decimal places stays 0.50 (already at 2 places)
        assert round_half_up(Decimal("0.5")) == Decimal("0.50")
        # 0.5 rounded to 0 places → 1
        assert round_half_up(Decimal("0.5"), places=0) == Decimal("1")


class TestRoleMapping:
    """Tests for party_1/party_2 role mapping."""

    def _make_trade(self, initiator_role=None, party_1="BANK_A", party_2="BANK_B"):
        data = {
            "external_trade_id": "TEST-001",
            "party_1": party_1,
            "party_2": party_2,
            "asset": "RU000A0ZYJT2",
            "amount": 100,
            "sum": 1000000.0,
            "rate": 0.15,
            "trade_date": "2026-05-07",
            "maturity_date": "2026-05-14",
        }
        if initiator_role is not None:
            data["initiator_role"] = initiator_role
        return IncomingTrade(**data)

    def test_security_seller_mapping(self):
        """SECURITY_SELLER: party_1 = participant, party_2 = counterparty."""
        trade = self._make_trade("SECURITY_SELLER")
        role, participant, counterparty = map_roles(trade)
        assert role == InitiatorRole.SECURITY_SELLER
        assert participant == "BANK_A"
        assert counterparty == "BANK_B"

    def test_security_buyer_mapping(self):
        """SECURITY_BUYER: party_2 = participant, party_1 = counterparty."""
        trade = self._make_trade("SECURITY_BUYER")
        role, participant, counterparty = map_roles(trade)
        assert role == InitiatorRole.SECURITY_BUYER
        assert participant == "BANK_B"
        assert counterparty == "BANK_A"

    def test_missing_role_fallback(self):
        """Missing initiator_role → DEFAULT_SELLER fallback, party_1 = participant."""
        trade = self._make_trade(initiator_role=None)
        role, participant, counterparty = map_roles(trade)
        assert role == InitiatorRole.DEFAULT_SELLER
        assert participant == "BANK_A"
        assert counterparty == "BANK_B"

    def test_unknown_role_raises_validation_error(self):
        """Unknown initiator_role → ValidationError with INITIATOR_ROLE_UNKNOWN."""
        trade = self._make_trade("INVALID_ROLE")
        with pytest.raises(ValidationError) as exc_info:
            map_roles(trade)
        assert exc_info.value.rejection_type == RejectionType.INITIATOR_ROLE_UNKNOWN

    def test_case_insensitive_role(self):
        """Role matching should be case-insensitive."""
        trade = self._make_trade("security_seller")
        role, participant, counterparty = map_roles(trade)
        assert role == InitiatorRole.SECURITY_SELLER

    def test_role_with_whitespace(self):
        """Role with leading/trailing whitespace should be handled."""
        trade = self._make_trade("  SECURITY_BUYER  ")
        role, participant, counterparty = map_roles(trade)
        assert role == InitiatorRole.SECURITY_BUYER
