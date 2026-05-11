"""
Demo data generator for REPO Module.

Generates:
  - instruments.json (15-20 instruments)
  - participants.json (10 participants)
  - sod_balances.json (SOD balances for 3 trading days)
  - trades_2026-05-07.jsonl (50-100 trades)
  - trades_2026-05-08.jsonl (50-100 trades)
  - trades_2026-05-10.jsonl (50-100 trades, includes edge cases)

Usage:
    python scripts/generate_demo_data.py
"""
import json
import os
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

random.seed(42)

BASE_DIR = Path(__file__).parent.parent
DEMO_DIR = BASE_DIR / "demo_data"
INCOMING_DIR = DEMO_DIR / "incoming"
INCOMING_DIR.mkdir(parents=True, exist_ok=True)

TRADING_DAYS = [
    date(2026, 5, 7),
    date(2026, 5, 8),
    date(2026, 5, 10),
]

# ─── Instruments ─────────────────────────────────────────────────────────────

INSTRUMENTS = [
    {"instrument_id": "RU000A0ZYJT2", "short_name": "ОФЗ-26225", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2020-01-01"},
    {"instrument_id": "RU000A101NJ6", "short_name": "ОФЗ-26240", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2021-01-01"},
    {"instrument_id": "RU000A103HT3", "short_name": "ОФЗ-26241", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2022-01-01"},
    {"instrument_id": "RU000A105A95", "short_name": "ОФЗ-26243", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2023-01-01"},
    {"instrument_id": "RU000A106375", "short_name": "ОФЗ-26244", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2023-06-01"},
    {"instrument_id": "RU000A1075T3", "short_name": "ОФЗ-26245", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2024-01-01"},
    {"instrument_id": "RU000A108GX5", "short_name": "ОФЗ-26246", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2024-06-01"},
    {"instrument_id": "RU000A109GX1", "short_name": "ОФЗ-26247", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2025-01-01"},
    {"instrument_id": "RU000A110GX2", "short_name": "ОФЗ-26248", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2025-06-01"},
    {"instrument_id": "RU000A111GX3", "short_name": "ОФЗ-26249", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2026-01-01"},
    {"instrument_id": "RU000A112USD1", "short_name": "ЕВРОБОНД-USD-1", "instrument_type": "BOND",
     "currency": "USD", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/360", "is_active": True, "valid_from": "2023-01-01"},
    {"instrument_id": "RU000A113USD2", "short_name": "ЕВРОБОНД-USD-2", "instrument_type": "BOND",
     "currency": "USD", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/360", "is_active": True, "valid_from": "2024-01-01"},
    {"instrument_id": "RU000A114EUR1", "short_name": "ЕВРОБОНД-EUR-1", "instrument_type": "BOND",
     "currency": "EUR", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2023-06-01"},
    {"instrument_id": "RU000A115CNY1", "short_name": "ЮАНЬ-БОНД-1", "instrument_type": "BOND",
     "currency": "CNY", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2025-01-01"},
    {"instrument_id": "RU000A116CORP", "short_name": "КОРП-БОНД-1", "instrument_type": "CORP_BOND",
     "currency": "RUB", "repo_eligible": True, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2024-01-01"},
    # Non-eligible instrument for edge case testing
    {"instrument_id": "RU000A117NOREPO", "short_name": "НЕ-РЕПО-БОНД", "instrument_type": "BOND",
     "currency": "RUB", "repo_eligible": False, "settlement_mode": "T+0",
     "day_count_convention": "ACT/365", "is_active": True, "valid_from": "2024-01-01"},
]

# ─── Participants ─────────────────────────────────────────────────────────────

PARTICIPANTS = [
    {"participant_id": "BANK_A", "name": "Банк А (Альфа)", "is_active": True},
    {"participant_id": "BANK_B", "name": "Банк Б (Бета)", "is_active": True},
    {"participant_id": "BANK_C", "name": "Банк В (Гамма)", "is_active": True},
    {"participant_id": "BANK_D", "name": "Банк Г (Дельта)", "is_active": True},
    {"participant_id": "BANK_E", "name": "Банк Д (Эпсилон)", "is_active": True},
    {"participant_id": "FUND_1", "name": "Инвестиционный фонд 1", "is_active": True},
    {"participant_id": "FUND_2", "name": "Инвестиционный фонд 2", "is_active": True},
    {"participant_id": "BROKER_1", "name": "Брокер 1", "is_active": True},
    {"participant_id": "BROKER_2", "name": "Брокер 2", "is_active": True},
    {"participant_id": "CORP_1", "name": "Корпоративный клиент 1", "is_active": True},
]

PARTICIPANT_IDS = [p["participant_id"] for p in PARTICIPANTS]

# Eligible instruments for trades
ELIGIBLE_INSTRUMENTS = [i for i in INSTRUMENTS if i["repo_eligible"] and i["is_active"]]
ELIGIBLE_IDS = [i["instrument_id"] for i in ELIGIBLE_INSTRUMENTS]

# Currency map
CURRENCY_MAP = {i["instrument_id"]: i["currency"] for i in INSTRUMENTS}


def calc_leg2(leg1_amount: float, rate: float, days: int, convention: str = "ACT/365") -> float:
    denom = 360 if "360" in convention else 365
    repo_income = round(leg1_amount * rate * days / denom, 2)
    return round(leg1_amount + repo_income, 2)


def generate_sod_balances() -> list[dict]:
    """
    Generate SOD balances for all 3 trading days.
    Balances are generous enough to cover most trades without INSUFFICIENT_BALANCE,
    except for one deliberately insufficient case on day 3.
    """
    balances = []
    for td in TRADING_DAYS:
        for p in PARTICIPANTS:
            pid = p["participant_id"]
            # Cash balances (RUB, USD, EUR, CNY)
            for currency in ["RUB", "USD", "EUR", "CNY"]:
                instrument_id = f"CASH_{currency}"
                # Large cash balance for most participants
                if pid == "CORP_1" and currency == "RUB" and td == TRADING_DAYS[2]:
                    # Deliberately insufficient for day 3 edge case
                    balance = 100.0  # very small
                else:
                    balance = random.uniform(500_000_000, 2_000_000_000)
                    balance = round(balance, 2)
                balances.append({
                    "participant_id": pid,
                    "instrument_id": instrument_id,
                    "balance_type": "CASH",
                    "currency": currency,
                    "position_date": td.isoformat(),
                    "balance": balance,
                })
            # Securities balances
            for instr in ELIGIBLE_INSTRUMENTS:
                iid = instr["instrument_id"]
                balance = random.randint(5000, 50000)
                balances.append({
                    "participant_id": pid,
                    "instrument_id": iid,
                    "balance_type": "SECURITIES",
                    "currency": instr["currency"],
                    "position_date": td.isoformat(),
                    "balance": float(balance),
                })
    return balances


def generate_trades_for_day(
    trade_date: date,
    count: int,
    include_edge_cases: bool = False,
) -> list[dict]:
    """Generate a list of trade records for a given day."""
    trades = []
    used_ids = set()

    def unique_id(prefix: str) -> str:
        for _ in range(1000):
            n = random.randint(1000, 9999)
            tid = f"{prefix}-{trade_date.strftime('%Y%m%d')}-{n:04d}"
            if tid not in used_ids:
                used_ids.add(tid)
                return tid
        raise RuntimeError("Could not generate unique ID")

    roles = ["SECURITY_SELLER", "SECURITY_BUYER", None]  # None = missing (fallback)

    for i in range(count):
        p1, p2 = random.sample(PARTICIPANT_IDS, 2)
        instr = random.choice(ELIGIBLE_INSTRUMENTS)
        iid = instr["instrument_id"]
        currency = instr["currency"]

        amount = random.randint(100, 5000)
        # Leg1 amount based on currency
        if currency == "RUB":
            unit_price = random.uniform(900, 1100)
        elif currency == "USD":
            unit_price = random.uniform(90, 110)
        elif currency == "EUR":
            unit_price = random.uniform(85, 105)
        else:  # CNY
            unit_price = random.uniform(600, 800)
        leg1_amount = round(amount * unit_price, 2)

        rate = round(random.uniform(0.10, 0.25), 6)
        days_to_maturity = random.randint(1, 14)
        maturity_date = trade_date + timedelta(days=days_to_maturity)

        # Vary initiator_role
        role_choice = random.choice(roles)

        trade = {
            "external_trade_id": unique_id("MOEX-REPO"),
            "party_1": p1,
            "party_2": p2,
            "asset": iid,
            "amount": amount,
            "sum": leg1_amount,
            "rate": rate,
            "trade_date": trade_date.isoformat(),
            "maturity_date": maturity_date.isoformat(),
        }
        if role_choice is not None:
            trade["initiator_role"] = role_choice

        trades.append(trade)

    if include_edge_cases:
        # Edge case 1: Unknown instrument → REJECTED (INSTRUMENT_NOT_ELIGIBLE)
        p1, p2 = random.sample(PARTICIPANT_IDS, 2)
        trades.append({
            "external_trade_id": unique_id("MOEX-REPO-BADINS"),
            "party_1": p1,
            "party_2": p2,
            "initiator_role": "SECURITY_SELLER",
            "asset": "UNKNOWN_INSTRUMENT_XYZ",
            "amount": 100,
            "sum": 100000.0,
            "rate": 0.15,
            "trade_date": trade_date.isoformat(),
            "maturity_date": (trade_date + timedelta(days=7)).isoformat(),
        })

        # Edge case 2: INSUFFICIENT_BALANCE — CORP_1 as seller with tiny cash balance
        # CORP_1 has only 100 RUB SOD balance on day 3
        # As SECURITY_BUYER (counterparty pays cash), CORP_1 needs to pay leg1_amount
        # We make CORP_1 the buyer (counterparty) with a large amount
        trades.append({
            "external_trade_id": unique_id("MOEX-REPO-INSUF"),
            "party_1": "BANK_A",
            "party_2": "CORP_1",
            "initiator_role": "SECURITY_BUYER",  # party_2=CORP_1 becomes participant (seller)
            # Actually: SECURITY_BUYER means party_2=participant, party_1=counterparty
            # So CORP_1 is participant (SECURITY_SELLER), BANK_A is counterparty
            # Wait: SECURITY_BUYER → participant=party_2=CORP_1, counterparty=party_1=BANK_A
            # CORP_1 as SECURITY_SELLER: -securities, +cash (OK)
            # BANK_A as SECURITY_BUYER: +securities, -cash (needs large cash)
            # To trigger INSUFFICIENT_BALANCE for CORP_1, make CORP_1 the buyer (pays cash)
            # SECURITY_SELLER → participant=party_1=CORP_1, counterparty=party_2=BANK_A
            # Leg1: CORP_1 -securities +cash, BANK_A +securities -cash
            # But CORP_1 has tiny RUB balance... securities balance is fine
            # Let's make CORP_1 the counterparty (SECURITY_BUYER) who pays cash
            # SECURITY_BUYER → participant=party_2=BANK_A, counterparty=party_1=CORP_1
            # Leg1: BANK_A -securities +cash, CORP_1 +securities -cash
            # CORP_1 needs to pay cash (leg1_amount) but has only 100 RUB
            "asset": "RU000A0ZYJT2",
            "amount": 1000,
            "sum": 95_000_000.0,  # 95M RUB — CORP_1 can't pay this
            "rate": 0.165,
            "trade_date": trade_date.isoformat(),
            "maturity_date": (trade_date + timedelta(days=7)).isoformat(),
        })
        # Fix: make CORP_1 the counterparty who pays cash
        # SECURITY_SELLER → participant=party_1, counterparty=party_2
        # So party_1=BANK_A (seller, gets cash), party_2=CORP_1 (buyer, pays cash)
        # CORP_1 needs 95M RUB but has only 100 → INSUFFICIENT_BALANCE
        trades[-1]["party_1"] = "BANK_A"
        trades[-1]["party_2"] = "CORP_1"
        trades[-1]["initiator_role"] = "SECURITY_SELLER"

        # Edge case 3: Unrecognized initiator_role → VALIDATION_ERROR
        p1, p2 = random.sample(PARTICIPANT_IDS, 2)
        trades.append({
            "external_trade_id": unique_id("MOEX-REPO-BADROLE"),
            "party_1": p1,
            "party_2": p2,
            "initiator_role": "INVALID_ROLE",
            "asset": "RU000A0ZYJT2",
            "amount": 100,
            "sum": 100000.0,
            "rate": 0.15,
            "trade_date": trade_date.isoformat(),
            "maturity_date": (trade_date + timedelta(days=3)).isoformat(),
        })

    return trades


def main():
    print("Generating demo data...")

    # Save instruments
    with open(INCOMING_DIR / "instruments.json", "w", encoding="utf-8") as f:
        json.dump(INSTRUMENTS, f, ensure_ascii=False, indent=2)
    print(f"  instruments.json: {len(INSTRUMENTS)} instruments")

    # Save participants
    with open(INCOMING_DIR / "participants.json", "w", encoding="utf-8") as f:
        json.dump(PARTICIPANTS, f, ensure_ascii=False, indent=2)
    print(f"  participants.json: {len(PARTICIPANTS)} participants")

    # Save SOD balances
    sod_balances = generate_sod_balances()
    with open(INCOMING_DIR / "sod_balances.json", "w", encoding="utf-8") as f:
        json.dump(sod_balances, f, ensure_ascii=False, indent=2)
    print(f"  sod_balances.json: {len(sod_balances)} balance records")

    # Generate trades for each day
    for i, td in enumerate(TRADING_DAYS):
        count = random.randint(50, 100)
        include_edge = (i == 2)  # edge cases on day 3
        trades = generate_trades_for_day(td, count, include_edge_cases=include_edge)
        fname = f"trades_{td.isoformat()}.jsonl"
        with open(INCOMING_DIR / fname, "w", encoding="utf-8") as f:
            for trade in trades:
                f.write(json.dumps(trade, ensure_ascii=False) + "\n")
        print(f"  {fname}: {len(trades)} trades" + (" (with edge cases)" if include_edge else ""))

    print("\nDemo data generated successfully!")
    print(f"Output directory: {INCOMING_DIR}")


if __name__ == "__main__":
    main()
