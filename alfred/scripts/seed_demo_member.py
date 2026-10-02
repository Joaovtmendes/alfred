#!/usr/bin/env python3
"""Demo member for the v2 panel: the numbers of the approved mockups, idempotent.

Creates (or reuses) the member ``31000000000`` with ``dashboard_v2 = true`` and consent
``pending`` (no cron message is ever sent to it), then rebuilds ONLY that member's expenses,
budgets and fixed bills, so running it twice never duplicates anything. Other members are never
read or written. Prints the panel link.

October 2026: income 3.840,00, paid spending 1.997,70, balance 1.842,30; the restaurant budget
(100) is over by 23; two bills are still to pay and do not count towards the balance.

Usage: ``python scripts/seed_demo_member.py`` (needs DATABASE_URL; BASE_URL for the printed link).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import delete, select  # noqa: E402

from alfred import panel_tokens  # noqa: E402
from alfred.clock import day_start  # noqa: E402
from alfred.db import AsyncSessionLocal, engine  # noqa: E402
from alfred.models import (  # noqa: E402
    Base,
    Budget,
    Expense,
    Household,
    Member,
    Message,
    RecurringItem,
)
from alfred.settings import settings  # noqa: E402

DEMO_PHONE = "31000000000"

# (type, amount, merchant, category, status, day of October 2026)
ROWS = [
    ("income", 3840.00, "Salário", "inkomen", "received", 1),
    ("expense", 1150.00, "Aluguel", "wonen", "paid", 1),
    ("expense", 94.40, "Albert Heijn", "supermarkt", "paid", 1),
    ("expense", 123.00, "Restaurante", "restaurant", "paid", 2),
    ("expense", 630.30, "Outros", "overig", "paid", 2),
    ("expense", 118.00, "Energia", "wonen", "to_pay", 2),
    ("expense", 138.00, "Seguro", "overig", "to_pay", 2),
]
EXPECTED_ROWS = len(ROWS)


async def run() -> str:
    """Seed the demo member and return its panel token (the same one on every run)."""
    async with AsyncSessionLocal() as s:
        member = (
            await s.execute(select(Member).where(Member.wa_phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if member is None:
            hh = Household(name="demo")
            s.add(hh)
            await s.flush()
            member = Member(household_id=hh.id, wa_phone=DEMO_PHONE, display_name="Joao")
            s.add(member)
        member.language = "pt"
        # Not "accepted": the cron reminders and the weekly/monthly summaries only select accepted
        # members, and the demo has a fake number and a bill due on the 18th. The panel link does
        # not look at consent.
        member.consent_state = "pending"
        member.dashboard_v2 = True
        await s.flush()
        for model in (Expense, Budget, RecurringItem):
            await s.execute(delete(model).where(model.member_id == member.id))
        for kind, amount, merchant, category, status, day in ROWS:
            s.add(
                Expense(
                    member_id=member.id,
                    household_id=member.household_id,
                    transaction_type=kind,
                    amount=amount,
                    merchant=merchant,
                    category=category,
                    status=status,
                    expense_date=day_start(date(2026, 10, day)).replace(hour=12),
                )
            )
        s.add(
            Budget(
                member_id=member.id,
                household_id=member.household_id,
                category="restaurant",
                monthly_limit=100.00,
            )
        )
        s.add(
            RecurringItem(
                member_id=member.id,
                household_id=member.household_id,
                name="Energia",
                amount=118.00,
                category="wonen",
                kind="fixed",
                frequency="monthly",
                due_day=18,
                next_due_date=date(2026, 10, 18),
            )
        )
        await panel_tokens.ensure_panel_token(s, member)
        await s.commit()
        token = str(member.dashboard_token)
    await engine.dispose()
    return token


async def purge() -> None:
    """Remove the demo member and everything that hangs off it (used by the tests' teardown)."""
    async with AsyncSessionLocal() as s:
        member = (
            await s.execute(select(Member).where(Member.wa_phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if member is not None:
            for table in reversed(Base.metadata.sorted_tables):
                if "member_id" in table.c and table.name != "member":
                    await s.execute(delete(table).where(table.c.member_id == member.id))
            await s.execute(delete(Message).where(Message.household_id == member.household_id))
            await s.execute(delete(Member).where(Member.id == member.id))
            await s.execute(delete(Household).where(Household.id == member.household_id))
            await s.commit()
    await engine.dispose()


def main() -> None:
    token = asyncio.run(run())
    base = (settings.base_url or "http://localhost:8000").rstrip("/")
    print(f"{base}/d/{token}")


if __name__ == "__main__":
    main()
