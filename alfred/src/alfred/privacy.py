"""GDPR rights: erase all of a member's data, export it as JSON.

Both walk ``Base.metadata`` instead of a hand-written table list, so a table added in a
later sprint is covered automatically (a test pins this).
"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Table, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.db import Base
from alfred.models import Household, Member

# member_id / author_id point at the person; audit_log is anonymised by FK (SET NULL).
_PERSON_COLUMNS = ("member_id", "author_id")
_SKIP_ERASE = {"member", "household", "audit_log"}
_SECRET_COLUMNS = {"dashboard_token", "export_token"}


def _person_column(table: Table):
    for name in _PERSON_COLUMNS:
        if name in table.c:
            return table.c[name]
    return None


def _person_condition(table: Table, member_id):
    """WHERE clause selecting the rows that belong to a person, or None for tables that never do.

    ``partner_link`` points at two people (inviter and partner): either side owns the row.
    """
    if "inviter_id" in table.c and "partner_id" in table.c:
        return or_(table.c.inviter_id == member_id, table.c.partner_id == member_id)
    col = _person_column(table)
    return None if col is None else col == member_id


async def erase_member(session: AsyncSession, member: Member) -> None:
    """Delete everything stored about ``member`` (and the household if nobody else is in it).

    The caller must not touch ORM objects afterwards; ``session.expunge_all()`` is done here.
    """
    member_id, household_id = member.id, member.household_id
    others = await session.scalar(
        select(func.count())
        .select_from(Member)
        .where(Member.household_id == household_id, Member.id != member_id)
    )
    tables = [t for t in reversed(Base.metadata.sorted_tables) if t.name not in _SKIP_ERASE]
    from alfred.couple import release

    await release(session, member_id)  # the partner's rows go back to private; links go away
    for table in tables:
        cond = _person_condition(table, member_id)
        if cond is not None:
            await session.execute(delete(table).where(cond))
    if not others:
        for table in tables:
            if "household_id" in table.c:
                await session.execute(delete(table).where(table.c.household_id == household_id))
    audit(session, "member_erased", None)  # no member reference: the person is gone
    await session.flush()
    await session.execute(delete(Member).where(Member.id == member_id))
    if not others:
        await session.execute(delete(Household).where(Household.id == household_id))
    session.expunge_all()


async def export_member_data(session: AsyncSession, member: Member) -> dict[str, Any]:
    """Everything stored about ``member`` as JSON-safe dicts, one list per table."""
    out: dict[str, Any] = {"member_id": str(member.id), "tables": {}}
    for table in Base.metadata.sorted_tables:
        if table.name in ("household", "audit_log"):
            continue
        if table.name == "member":
            cond = table.c.id == member.id
        else:
            cond = _person_condition(table, member.id)
            if cond is None:
                continue
        rows = (await session.execute(select(table).where(cond))).mappings().all()
        out["tables"][table.name] = [
            {k: v for k, v in dict(r).items() if k not in _SECRET_COLUMNS} for r in rows
        ]
    return jsonable_encoder(out)
