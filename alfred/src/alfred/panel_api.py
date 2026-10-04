# ruff: noqa: E501
"""Per-tab JSON for the v2 panel. Each tab loads only what it shows (spec §2).

Every route: token check (404 when unknown, malformed or expired), IP rate limit, the single
closed-list filter, and ``Cache-Control: no-store`` (added by the security middleware). Every
query filters by the member of the token and goes through ``apply_expense_filter``.

Common envelope
---------------
::

    {"tab": "summary|money|health|agenda|trips", "lang": "pt", "today": "2026-10-14",
     "filter": {"start", "end" (exclusive), "categories", "kind", "states", "trip"},
     "cards": [ {...}, ... ]}

Every card has a stable ``id``, ``empty`` (bool), ``phrase`` and, when it is empty, ``hint``::

    "phrase": null | {"key", "text", "chat" (a quoted chat command or null), "severity": "info"|"attention"}
    "hint":   {"key", "text", "chat", "severity"}     # only with empty=true: what to type in the chat

Money is a plain number in EUR (2 decimals), dates are ISO ``YYYY-MM-DD`` local days. At most one
phrase per card. ``values`` always has the same keys (zeros when empty).

Summary cards (in this order: balance, categories, goals, week, upcoming)
-------------------------------------------------------------------------
``balance``    values {income, expense, balance} (settled entries); ``compare`` null | {start, end,
               income|expense: {previous, delta, pct|null}}; ``projection`` is
               {available: false, reason: "empty"|"filtered"|"not_current_month"} or
               {available: true, sufficient, realized, committed (bills to pay until the month end),
               scheduled_in (income marked "to receive"), variable|null, projected|null, low|null,
               high|null, month_end, days_left, window_days, entries, history_days}. With
               sufficient=false only the realized and committed parts are real (no estimate).
``categories`` values {total, previous_total, delta, pct|null}; items [{category, label, amount,
               pct_of_total, count, previous, delta, delta_pct|null, trend: "up"|"down"|"flat"|"new"}]
               (top 5; 8 in Dinheiro), ``others`` null | {amount, count}, ``compare`` {start, end}.
``goals`` / ``week``  the same cards as in Hábitos / Agenda (see ``panel_tabs``).
``upcoming``   values {to_pay, to_receive, overdue, count_pay, count_receive, count_overdue,
               horizon_days: 30}; items [{name, amount, due, days (negative = overdue), due_text,
               overdue, direction: "pay"|"receive", source: "recurring"|"expense", kind|null,
               category|null, label|null}] (10 at most) and ``more``.

Dinheiro cards (in this order: transactions, month_vs_month, top_expenses, categories, budgets,
fixed_variable, owed, daily, recurring)
------------------------------------------------------------------------------------------------
``transactions`` values {income, expense, balance, count} (sums of settled entries over the whole
               filter, count of all entries); page {number, size: 50, pages, total}; days [{date,
               income, expense, net (settled sums of the WHOLE day), entries_total, entries [{merchant|null,
               category|null, label|null, kind: "expense"|"income", amount, status, settled}]}] for
               the entries of this page only. ``?page=N`` (1-based) is the only non-filter parameter.
``month_vs_month`` values {expense|income: {current, previous, delta, pct|null}}; compare {start, end};
               drivers [{category, label, delta}] (3 biggest rises).
``top_expenses`` values {total, count}; items [{merchant|null, category|null, label|null, amount,
               date, pct_of_total, pct_of_top}] (5).
``categories`` as in Resumo (the category chart).
``budgets``    values {spent, limit, pct|null, month: "YYYY-MM", day, days_in_month}; items [{category,
               label, spent, limit, pct, level: 0|80|100, crossed_on|null, days_to_80|null}], worst first.
``fixed_variable`` values {fixed, variable, total, fixed_pct|null} (fixed = expenses whose merchant is
               the name of a fixed item).
``owed``       values {total, count}; items [{person, amount, note|null, since, days}] (5, oldest first).

``daily``      unit "day"|"week" (weeks beyond 62 days); points [{date, amount}]; values {total, max,
               max_date|null, quiet_days, elapsed_days}.
``recurring``  values {count, monthly_total}; items [{name, amount, kind, frequency, next_due, due_text,
               installment: null | {number, total}, category|null, label|null}].

Filters and the cards that are not ledger entries: fixed items (``upcoming``, ``recurring``) honour
the category filter and are hidden by ``kind=income`` or by a ``state`` that excludes "to_pay"; the
pending entries of ``upcoming`` go through the single filter (period replaced by the next 30 days
and the last year); ``owed`` ignores the ledger filters. Forward-looking cards ignore the period.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import panel_calc as pc
from alfred import panel_phrases as pp
from alfred import panel_tabs as pt
from alfred.audit import audit
from alfred.clock import today_local
from alfred.dashboard import _get_member
from alfred.dashboard_i18n import normalize_lang
from alfred.db import get_session
from alfred.labels import category_label
from alfred.models import Member
from alfred.panel_filters import PanelFilter, parse_filter
from alfred.recurring import monthly_equivalent
from alfred.web_security import limit_dashboard

router = APIRouter(tags=["panel"])

MAX_PAGE = 10_000
_m = pc.money


def _filter_json(f: PanelFilter) -> dict[str, Any]:
    return {
        "start": f.start.isoformat(),
        "end": f.end.isoformat(),
        "categories": list(f.categories),
        "kind": f.kind,
        "states": list(f.states),
        "trip": str(f.trip_id) if f.trip_id else None,
    }


def _envelope(
    tab: str, member: Member, f: PanelFilter, cards: list[dict[str, Any]], today: date | None = None
) -> JSONResponse:
    return JSONResponse(
        {
            "tab": tab,
            "lang": normalize_lang(member.language),
            "today": (today or today_local()).isoformat(),
            "filter": _filter_json(f),
            "cards": cards,
        }
    )


def _phrase(p: pp.Phrase | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"key": p.key, "text": p.text, "chat": p.chat, "severity": p.severity}


def _parse_page(raw: str | None) -> int:
    """``?page=N``: a positive integer up to ``MAX_PAGE``; anything else is page 1."""
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 6:
        return 1
    return min(max(int(raw), 1), MAX_PAGE)


def _compare(cur: Decimal, prev: Decimal) -> dict[str, Any]:
    return {"previous": _m(prev), "delta": _m(cur - prev), "pct": pc.pct_change(cur, prev)}


class _Ctx:
    """What every card of one request shares: the member, the filter, today and a memo of queries
    (so two cards that need the same numbers ask the database once)."""

    def __init__(
        self, session: AsyncSession, member: Member, f: PanelFilter, today: date, page: int = 1
    ) -> None:
        self.session, self.member, self.f, self.today, self.page = session, member, f, today, page
        self.lang = normalize_lang(member.language)
        self.mid = member.id
        self._memo: dict[str, Any] = {}
        ps, pe = pc.prev_period(f, today)
        self.prev_f = pc.derive(f, start=ps, end=pe)
        self.month_first: date | None = f.start if pc.is_calendar_month(f) else None
        self.prev_first: date | None = ps if self.month_first else None

    async def memo(self, key: str, make):
        if key not in self._memo:
            self._memo[key] = await make()
        return self._memo[key]

    def label(self, category: str | None) -> str:
        return category_label(category, self.lang)

    def hint(self, kind: str) -> dict[str, Any]:
        return _phrase(pp.empty_hint(kind, self.lang))  # type: ignore[return-value]

    async def totals(self) -> pc.Totals:
        return await self.memo("totals", lambda: pc.settled_totals(self.session, self.mid, self.f))

    async def prev_totals(self) -> pc.Totals:
        return await self.memo(
            "prev_totals", lambda: pc.settled_totals(self.session, self.mid, self.prev_f)
        )

    async def cats(self) -> list[pc.CategoryRow]:
        return await self.memo("cats", lambda: pc.category_totals(self.session, self.mid, self.f))

    async def prev_cats(self) -> list[pc.CategoryRow]:
        return await self.memo(
            "prev_cats", lambda: pc.category_totals(self.session, self.mid, self.prev_f)
        )

    async def names(self) -> set[str]:
        return await self.memo("names", lambda: pc.recurring_names(self.session, self.mid))

    async def top(self) -> list[pc.Entry]:
        return await self.memo(
            "top", lambda: pc.top_expenses(self.session, self.mid, self.f, limit=5)
        )

    async def daily(self) -> dict[date, tuple[Decimal, int]]:
        return await self.memo("daily", lambda: pc.daily_spending(self.session, self.mid, self.f))

    async def upcoming(self) -> list[pc.Bill]:
        return await self.memo(
            "upcoming", lambda: pc.upcoming(self.session, self.mid, self.f, self.today)
        )

    async def owed(self):
        return await self.memo("owed", lambda: pc.owed_to_me(self.session, self.mid, self.today))

    async def owing(self):
        return await self.memo(
            "owing",
            lambda: pc.owed_to_me(self.session, self.mid, self.today, direction="i_owe"),
        )


# ── Resumo ────────────────────────────────────────────────────────────────────


async def _projection(ctx: _Ctx, realized: Decimal) -> tuple[dict[str, Any], pc.Projection | None]:
    reason = pc.projection_block_reason(ctx.f, ctx.today)
    if reason:
        return {"available": False, "reason": reason}, None
    s, mid, today = ctx.session, ctx.mid, ctx.today
    first = await pc.first_expense_day(s, mid, ctx.f, today)
    history = (today - first).days + 1 if first else 0
    names = await ctx.names()
    window = pc.derive(
        ctx.f, start=today - timedelta(days=pc.PROJ_WINDOW_DAYS - 1), end=today + timedelta(days=1)
    )
    series = await pc.daily_spending(s, mid, window, exclude_names=names)
    committed, scheduled_in = await pc.commitments_until(
        s, mid, ctx.f, today, pc.month_end_of(today)
    )
    proj = pc.project_month(
        realized=realized,
        committed=committed,
        scheduled_in=scheduled_in,
        daily={d: v[0] for d, v in series.items()},
        entries=sum(v[1] for v in series.values()),
        history_days=history,
        today=today,
    )
    body: dict[str, Any] = {
        "available": True,
        "sufficient": proj.sufficient,
        "realized": _m(proj.realized),
        "committed": _m(proj.committed),
        "scheduled_in": _m(proj.scheduled_in),
        "variable": None if proj.variable is None else _m(proj.variable),
        "projected": None if proj.projected is None else _m(proj.projected),
        "low": None if proj.low is None else _m(proj.low),
        "high": None if proj.high is None else _m(proj.high),
        "month_end": proj.month_end.isoformat(),
        "days_left": proj.days_left,
        "window_days": proj.window_days,
        "entries": proj.entries,
        "history_days": proj.history_days,
    }
    return body, proj


async def _balance_card(session: AsyncSession, member: Member, f: PanelFilter) -> dict[str, Any]:
    """Kept for callers that only need the balance (tests); the tabs build it through ``_Ctx``."""
    ctx = _Ctx(session, member, f, today_local())
    return await balance_card(ctx)


async def balance_card(ctx: _Ctx) -> dict[str, Any]:
    t = await ctx.totals()
    empty = t.count == 0
    card: dict[str, Any] = {
        "id": "balance",
        "empty": empty,
        "values": {"income": _m(t.income), "expense": _m(t.expense), "balance": _m(t.balance)},
        "compare": None,
        "projection": {"available": False, "reason": "empty"},
        "phrase": None,
    }
    if empty:
        card["hint"] = ctx.hint("ledger")
        return card
    prev = await ctx.prev_totals()
    if prev.count:
        card["compare"] = {
            "start": ctx.prev_f.start.isoformat(),
            "end": ctx.prev_f.end.isoformat(),
            "income": _compare(t.income, prev.income),
            "expense": _compare(t.expense, prev.expense),
        }
    body, proj = await _projection(ctx, t.balance)
    card["projection"] = body
    if proj is not None:
        card["phrase"] = _phrase(
            pp.rule_balance_vs_projection(
                float(t.balance),
                None if proj.projected is None else float(proj.projected),
                ctx.lang,
                insufficient=not proj.sufficient,
                committed=float(proj.committed),
            )
        )
    return card


def _bill_json(b: pc.Bill, ctx: _Ctx) -> dict[str, Any]:
    return {
        "name": b.name,
        "amount": _m(b.amount),
        "due": b.due.isoformat(),
        "days": b.days,
        "due_text": pp.due_label(b.days, ctx.lang),
        "overdue": b.days < 0,
        "direction": b.direction,
        "source": b.source,
        "kind": b.kind,
        "category": b.category,
        "label": ctx.label(b.category) if b.category else None,
    }


async def upcoming_card(ctx: _Ctx) -> dict[str, Any]:
    bills = await ctx.upcoming()
    pay = [b for b in bills if b.direction == "pay" and b.days >= 0]
    late = [b for b in bills if b.direction == "pay" and b.days < 0]
    recv = [b for b in bills if b.direction == "receive"]
    total = lambda xs: sum((b.amount for b in xs), Decimal(0))  # noqa: E731
    shown = bills[: pc.UPCOMING_LIMIT]
    card: dict[str, Any] = {
        "id": "upcoming",
        "empty": not bills,
        "values": {
            "to_pay": _m(total(pay)),
            "to_receive": _m(total(recv)),
            "overdue": _m(total(late)),
            "count_pay": len(pay),
            "count_receive": len(recv),
            "count_overdue": len(late),
            "horizon_days": pc.UPCOMING_DAYS,
        },
        "items": [_bill_json(b, ctx) for b in shown],
        "more": max(len(bills) - len(shown), 0),
        "phrase": None,
    }
    if not bills:
        card["hint"] = ctx.hint("upcoming")
        return card
    first = (late or pay or [None])[0]
    card["phrase"] = _phrase(
        pp.rule_upcoming(
            len(pay),
            float(total(pay)),
            len(late),
            float(total(late)),
            first.name if first else None,
            ctx.lang,
        )
    )
    return card


def _trend(amount: Decimal, previous: Decimal) -> str:
    if previous <= 0 < amount:
        return "new"
    if amount > previous:
        return "up"
    if amount < previous:
        return "down"
    return "flat"


async def categories_card(ctx: _Ctx, limit: int = 5) -> dict[str, Any]:
    cats, prev = await ctx.cats(), await ctx.prev_cats()
    prev_by = {c.category: c.amount for c in prev}
    total = sum((c.amount for c in cats), Decimal(0))
    prev_total = sum((c.amount for c in prev), Decimal(0))
    items = []
    for c in cats[:limit]:
        before = prev_by.get(c.category, Decimal(0))
        items.append(
            {
                "category": c.category,
                "label": ctx.label(c.category),
                "amount": _m(c.amount),
                "pct_of_total": pc.pct_of(c.amount, total),
                "count": c.count,
                "previous": _m(before),
                "delta": _m(c.amount - before),
                "delta_pct": pc.pct_change(c.amount, before),
                "trend": _trend(c.amount, before),
            }
        )
    rest = cats[limit:]
    card: dict[str, Any] = {
        "id": "categories",
        "empty": not cats,
        "values": {
            "total": _m(total),
            "previous_total": _m(prev_total),
            "delta": _m(total - prev_total),
            "pct": pc.pct_change(total, prev_total),
        },
        "items": items,
        "others": (
            {
                "amount": _m(sum((c.amount for c in rest), Decimal(0))),
                "count": sum(c.count for c in rest),
            }
            if rest
            else None
        ),
        "compare": {"start": ctx.prev_f.start.isoformat(), "end": ctx.prev_f.end.isoformat()},
        "phrase": None,
    }
    if not cats:
        card["hint"] = ctx.hint("ledger")
        return card
    cur_by = {c.category: c.amount for c in cats}
    moves = [
        (ctx.label(k), float(cur_by.get(k, Decimal(0))), float(prev_by.get(k, Decimal(0))))
        for k in set(cur_by) | set(prev_by)
    ]
    card["phrase"] = _phrase(
        pp.rule_category_move(
            moves,
            sum(c.count for c in prev),
            pp.prev_fragment(ctx.prev_first, ctx.lang),
            ctx.lang,
        )
    )
    return card


async def budgets_card(ctx: _Ctx) -> dict[str, Any]:
    rows, ref_first = await ctx.memo(
        "budgets", lambda: pc.budget_usage(ctx.session, ctx.mid, ctx.f, ctx.today)
    )
    spent = sum((r.spent for r in rows), Decimal(0))
    limit = sum((r.limit for r in rows), Decimal(0))
    last = pc.month_end_of(ref_first)
    day = (min(ctx.today, last) - ref_first).days + 1 if ctx.today >= ref_first else 0
    card: dict[str, Any] = {
        "id": "budgets",
        "empty": not rows,
        "values": {
            "spent": _m(spent),
            "limit": _m(limit),
            "pct": pc.budget_pct(spent, limit) if limit > 0 else None,
            "month": f"{ref_first.year:04d}-{ref_first.month:02d}",
            "day": day,
            "days_in_month": last.day,
        },
        "items": [
            {
                "category": r.category,
                "label": ctx.label(r.category),
                "spent": _m(r.spent),
                "limit": _m(r.limit),
                "pct": r.pct,
                "level": r.level,
                "crossed_on": r.crossed_on.isoformat() if r.crossed_on else None,
                "days_to_80": r.days_to_80,
            }
            for r in rows
        ],
        "phrase": None,
    }
    if not rows:
        card["hint"] = ctx.hint("budgets")
        return card
    worst = rows[0]  # sorted by pct, biggest first
    phrase = pp.rule_budget_over(
        ctx.label(worst.category), float(worst.spent), float(worst.limit), ctx.lang
    )
    if phrase is None:
        paced = [r for r in rows if r.days_to_80 is not None]
        if paced:
            soon = min(paced, key=lambda r: (r.days_to_80, r.category))
            phrase = pp.rule_budget_pace(
                ctx.label(soon.category),
                float(soon.spent),
                float(soon.limit),
                soon.days_to_80,
                ctx.lang,
            )
    card["phrase"] = _phrase(phrase)
    return card


def _owed_items(rows) -> list[dict[str, Any]]:
    return [
        {
            "person": r.person,
            "amount": _m(r.remaining),
            "note": r.note,
            "since": r.since.isoformat(),
            "days": r.days,
        }
        for r in rows
    ]


async def owed_card(ctx: _Ctx) -> dict[str, Any]:
    """Who owes the member, plus (``owing``) what the member owes people ("Devo 40 pro Lucas")."""
    rows, total, n = await ctx.owed()
    mine, mine_total, mine_n = await ctx.owing()
    card: dict[str, Any] = {
        "id": "owed",
        "empty": n == 0 and mine_n == 0,
        "values": {"total": _m(total), "count": n},
        "items": _owed_items(rows),
        "owing": {
            "values": {"total": _m(mine_total), "count": mine_n},
            "items": _owed_items(mine),
        },
        "phrase": None,
    }
    if n == 0 and mine_n == 0:
        card["hint"] = ctx.hint("owed")
        return card
    if n:
        oldest = rows[0]
        card["phrase"] = _phrase(
            pp.rule_owed(oldest.person, float(oldest.person_total), oldest.days, ctx.lang)
        )
    return card


# ── Dinheiro ──────────────────────────────────────────────────────────────────


async def transactions_card(ctx: _Ctx) -> dict[str, Any]:
    t = await ctx.totals()
    days = await ctx.memo("day_sums", lambda: pc.day_sums(ctx.session, ctx.mid, ctx.f))
    total = sum(d.count for d in days)
    pages = max((total + pc.PAGE_SIZE - 1) // pc.PAGE_SIZE, 1)
    entries = await pc.transactions_page(ctx.session, ctx.mid, ctx.f, ctx.page)
    by_day = {d.day: d for d in days}
    grouped: dict[date, list[pc.Entry]] = {}
    for e in entries:
        grouped.setdefault(e.day, []).append(e)
    out_days = []
    for day in sorted(grouped, reverse=True):
        ds = by_day.get(day)
        if ds is None:  # an entry arrived between the two queries: fall back to this page's own sum
            settled = [e for e in grouped[day] if e.status in ("paid", "received")]
            ds = pc.DaySum(
                day,
                sum((e.amount for e in settled if e.kind == "income"), Decimal(0)),
                sum((e.amount for e in settled if e.kind == "expense"), Decimal(0)),
                len(grouped[day]),
            )
        out_days.append(
            {
                "date": day.isoformat(),
                "income": _m(ds.income),
                "expense": _m(ds.expense),
                "net": _m(ds.income - ds.expense),
                "entries_total": ds.count,
                "entries": [
                    {
                        "merchant": e.label,
                        "category": e.category,
                        "label": ctx.label(e.category) if e.category else None,
                        "kind": e.kind,
                        "amount": _m(e.amount),
                        "status": e.status,
                        "settled": e.status in ("paid", "received"),
                    }
                    for e in grouped[day]
                ],
            }
        )
    card: dict[str, Any] = {
        "id": "transactions",
        "empty": total == 0,
        "values": {
            "income": _m(t.income),
            "expense": _m(t.expense),
            "balance": _m(t.balance),
            "count": total,
        },
        "page": {"number": ctx.page, "size": pc.PAGE_SIZE, "pages": pages, "total": total},
        "days": out_days,
        "phrase": None,
    }
    if total == 0:
        card["hint"] = ctx.hint("ledger")
        return card
    top = await ctx.top()
    if top:
        card["phrase"] = _phrase(
            pp.rule_largest_entry(
                total,
                top[0].label or ctx.label(top[0].category),
                float(top[0].amount),
                pp.period_fragment(ctx.month_first, ctx.lang),
                ctx.lang,
            )
        )
    return card


async def top_expenses_card(ctx: _Ctx) -> dict[str, Any]:
    top = await ctx.top()
    t = await ctx.totals()
    cats = await ctx.cats()
    count = sum(c.count for c in cats)
    biggest = top[0].amount if top else Decimal(0)
    card: dict[str, Any] = {
        "id": "top_expenses",
        "empty": not top,
        "values": {"total": _m(t.expense), "count": count},
        "items": [
            {
                "merchant": e.label,
                "category": e.category,
                "label": ctx.label(e.category) if e.category else None,
                "amount": _m(e.amount),
                "date": e.day.isoformat(),
                "pct_of_total": pc.pct_of(e.amount, t.expense),
                "pct_of_top": pc.pct_of(e.amount, biggest),
            }
            for e in top
        ],
        "phrase": None,
    }
    if not top:
        card["hint"] = ctx.hint("ledger")
        return card
    card["phrase"] = _phrase(
        pp.rule_top_share(
            top[0].label or ctx.label(top[0].category),
            float(top[0].amount),
            float(t.expense),
            count,
            ctx.lang,
        )
    )
    return card


async def month_vs_month_card(ctx: _Ctx) -> dict[str, Any]:
    t, prev = await ctx.totals(), await ctx.prev_totals()
    cats, prev_cats = await ctx.cats(), await ctx.prev_cats()
    prev_by = {c.category: c.amount for c in prev_cats}
    rises = sorted(
        ((c.amount - prev_by.get(c.category, Decimal(0)), c.category) for c in cats),
        key=lambda x: (-x[0], x[1]),
    )
    rises = [(d, k) for d, k in rises if d > 0][:3]
    card: dict[str, Any] = {
        "id": "month_vs_month",
        "empty": t.count == 0 and prev.count == 0,
        "values": {
            "expense": {"current": _m(t.expense), **_compare(t.expense, prev.expense)},
            "income": {"current": _m(t.income), **_compare(t.income, prev.income)},
        },
        "compare": {"start": ctx.prev_f.start.isoformat(), "end": ctx.prev_f.end.isoformat()},
        "drivers": [{"category": k, "label": ctx.label(k), "delta": _m(d)} for d, k in rises],
        "phrase": None,
    }
    if card["empty"]:
        card["hint"] = ctx.hint("ledger")
        return card
    if rises:
        delta, key = rises[0]
        card["phrase"] = _phrase(
            pp.rule_mom_driver(
                ctx.label(key), float(delta), float(t.expense - prev.expense), ctx.lang
            )
        )
    return card


async def fixed_variable_card(ctx: _Ctx) -> dict[str, Any]:
    names = await ctx.names()
    fixed, variable, n = await ctx.memo(
        "fv", lambda: pc.fixed_variable(ctx.session, ctx.mid, ctx.f, names)
    )
    total = fixed + variable
    card: dict[str, Any] = {
        "id": "fixed_variable",
        "empty": n == 0,
        "values": {
            "fixed": _m(fixed),
            "variable": _m(variable),
            "total": _m(total),
            "fixed_pct": pc.pct_of(fixed, total),
        },
        "phrase": None,
    }
    if n == 0:
        card["hint"] = ctx.hint("ledger")
        return card
    card["phrase"] = _phrase(pp.rule_fixed_variable(float(fixed), float(variable), ctx.lang))
    return card


async def daily_card(ctx: _Ctx) -> dict[str, Any]:
    series = await ctx.daily()
    unit, points = pc.fold_daily(series, ctx.f, ctx.today)
    entries = sum(v[1] for v in series.values())
    total = sum((v for _, v in points), Decimal(0))
    peak = max(points, key=lambda p: (p[1], p[0]), default=None)
    quiet = sum(1 for _, v in points if v == 0) if unit == "day" else 0
    card: dict[str, Any] = {
        "id": "daily",
        "empty": entries == 0,
        "unit": unit,
        "points": [{"date": d.isoformat(), "amount": _m(v)} for d, v in points],
        "values": {
            "total": _m(total),
            "max": _m(peak[1]) if peak else 0.0,
            "max_date": peak[0].isoformat() if peak and peak[1] > 0 else None,
            "quiet_days": quiet,
            "elapsed_days": len(points) if unit == "day" else 0,
        },
        "phrase": None,
    }
    if entries == 0:
        card["hint"] = ctx.hint("ledger")
        return card
    if unit == "day" and peak:
        card["phrase"] = _phrase(
            pp.rule_busiest_day(peak[0], float(peak[1]), quiet, len(points), entries, ctx.lang)
        )
    return card


async def recurring_card(ctx: _Ctx) -> dict[str, Any]:
    items = await ctx.memo("recurring", lambda: pc.recurring_items(ctx.session, ctx.mid, ctx.f))
    monthly = sum((monthly_equivalent(float(i.amount), i.frequency) for i in items), Decimal(0))
    card: dict[str, Any] = {
        "id": "recurring",
        "empty": not items,
        "values": {"count": len(items), "monthly_total": _m(monthly)},
        "items": [
            {
                "name": i.name,
                "amount": _m(i.amount),
                "kind": i.kind,
                "frequency": i.frequency,
                "next_due": i.next_due.isoformat(),
                "due_text": pp.due_label((i.next_due - ctx.today).days, ctx.lang),
                "installment": (
                    {"number": i.installment, "total": i.installments_total}
                    if i.installment
                    else None
                ),
                "category": i.category,
                "label": ctx.label(i.category) if i.category else None,
            }
            for i in items
        ],
        "phrase": None,
    }
    if not items:
        card["hint"] = ctx.hint("recurring")
    return card


async def _open(token: str, request: Request, session: AsyncSession) -> tuple[Member, PanelFilter]:
    member = await _get_member(token, session)
    return member, parse_filter(dict(request.query_params), today_local())


@router.get(
    "/api/d/{token}/summary", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def summary(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    ctx = _Ctx(session, member, f, today_local())
    cards = [
        await balance_card(ctx),
        await categories_card(ctx, 5),
        await pt.goals_card(ctx),
        await pt.week_card(ctx),
        await upcoming_card(ctx),
    ]
    return _envelope("summary", member, f, cards, ctx.today)


@router.get(
    "/api/d/{token}/money", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def money(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    ctx = _Ctx(session, member, f, today_local(), _parse_page(request.query_params.get("page")))
    cards = [
        await transactions_card(ctx),
        await month_vs_month_card(ctx),
        await top_expenses_card(ctx),
        await categories_card(ctx, 8),
        await budgets_card(ctx),
        await fixed_variable_card(ctx),
        await owed_card(ctx),
        await daily_card(ctx),
        await recurring_card(ctx),
    ]
    return _envelope("money", member, f, cards, ctx.today)


@router.get(
    "/api/d/{token}/health", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def health(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """Own endpoint, loaded only when the tab opens; every opening is audited (no content)."""
    member, f = await _open(token, request, session)
    audit(session, "panel_health_opened", member.id)
    # get_session also commits on exit, but the audit row must not depend on that ordering.
    await session.commit()
    ctx = _Ctx(session, member, f, today_local())
    cards = [
        await pt.goals_card(ctx),
        await pt.workouts_card(ctx),
        await pt.training_card(ctx),
        await pt.water_card(ctx),
    ]
    return _envelope("health", member, f, cards, ctx.today)


@router.get(
    "/api/d/{token}/agenda", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def agenda(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    ctx = _Ctx(session, member, f, today_local())
    cards = [
        await pt.week_card(ctx),
        await pt.tasks_card(ctx),
        await pt.month_map_card(ctx),
        await pt.reminders_card(ctx),
        await pt.notes_card(ctx),
    ]
    return _envelope("agenda", member, f, cards, ctx.today)


@router.get(
    "/api/d/{token}/trips", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def trips(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    ctx = _Ctx(session, member, f, today_local())
    cards = [await pt.trip_card(ctx)]
    plan_trip = await pt._plan_trip(ctx)
    if plan_trip is not None:  # itinerary, packing and planned budget belong to a trip that exists
        cards += [
            await pt.packing_card(ctx, plan_trip),
            await pt.itinerary_card(ctx, plan_trip),
            await pt.plan_budget_card(ctx, plan_trip),
        ]
    past = await pt.trips_past_card(ctx)
    if not past["empty"]:  # a card of "other trips" with no other trip would only be noise
        cards.append(past)
    return _envelope("trips", member, f, cards, ctx.today)
