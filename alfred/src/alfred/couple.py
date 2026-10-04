# ruff: noqa: E501
"""V2-10 — couple mode: two members share the household finances, each keeps the rest private.

Nothing is shared by default. A member marks a lançamento as "da casa" (a button, "foi da casa",
or a category that is always "da casa"); only those rows are visible to the partner, and only
while a ``PartnerLink`` is active. The shared balance is computed from them: who paid what, the
split (50/50 unless changed), and the settlements recorded with "acertamos". Everything is rules and
Decimal arithmetic, no LLM.

Flow: ``convidar parceiro`` issues a 6-character code (7 days) → the other person, already onboarded,
sends ``entrar casa CODE`` → they get a button to accept (explicit consent) → the link is active.
Either side can leave with ``sair da casa`` (asks first); leaving clears the "da casa" mark, so
the rows go back to being private and a later link starts clean.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import day_start, to_local
from alfred.labels import _LABELS, category_label
from alfred.models import (
    SETTLED,
    AuditLog,
    Expense,
    Member,
    PartnerLink,
    PartnerSettlement,
)
from alfred.parsing import strip_accents
from alfred.settings import settings

INVITED, PENDING, ACTIVE, ENDED = "invited", "pending", "active", "ended"
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L: easy to read out loud
CODE_LEN = 6
INVITE_DAYS = 7
MAX_JOIN_FAILS = 5  # wrong codes per hour and member
MAX_ENTRIES = 30  # rows of the panel list
_ZERO = Decimal(0)


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]]


def _dec(value: float | Decimal | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


# ── links ─────────────────────────────────────────────────────────────────────


async def active_link(session: AsyncSession, member_id: uuid.UUID) -> PartnerLink | None:
    return await session.scalar(
        select(PartnerLink).where(
            PartnerLink.status == ACTIVE,
            or_(PartnerLink.inviter_id == member_id, PartnerLink.partner_id == member_id),
        )
    )


def other_id(link: PartnerLink, member_id: uuid.UUID) -> uuid.UUID:
    return link.partner_id if link.inviter_id == member_id else link.inviter_id  # type: ignore[return-value]


def my_pct(link: PartnerLink, member_id: uuid.UUID) -> int:
    """Share of the shared expenses this member pays."""
    return link.inviter_pct if link.inviter_id == member_id else 100 - link.inviter_pct


def _name(member: Member | None, lang: str) -> str:
    who = (member.preferred_name or member.display_name) if member else None
    return (who or "").strip()[:40] or _t("couple_partner_word", lang)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


async def _partner(session: AsyncSession, link: PartnerLink, member_id: uuid.UUID) -> Member | None:
    return await session.get(Member, other_id(link, member_id))


# ── balance ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Balance:
    total: Decimal  # all shared expenses while linked
    paid_inviter: Decimal
    paid_partner: Decimal
    net_inviter: Decimal  # > 0: the partner owes the inviter; < 0: the inviter owes the partner

    def net_for(self, link: PartnerLink, member_id: uuid.UUID) -> Decimal:
        """> 0: the other person owes ``member_id``; < 0: ``member_id`` owes the other person."""
        return self.net_inviter if link.inviter_id == member_id else -self.net_inviter


def compute_net(
    paid_inviter: Decimal,
    paid_partner: Decimal,
    inviter_pct: int,
    settled_by_inviter: Decimal,
    settled_by_partner: Decimal,
) -> Decimal:
    """Pure part: inviter's net position (the partner's is its negative)."""
    total = paid_inviter + paid_partner
    share_inviter = (total * inviter_pct / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return paid_inviter - share_inviter + settled_by_inviter - settled_by_partner


async def _paid_by(
    session: AsyncSession,
    link: PartnerLink,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[Decimal, Decimal]:
    conds = [
        Expense.member_id.in_([link.inviter_id, link.partner_id]),
        Expense.shared.is_(True),
        Expense.transaction_type == "expense",
        Expense.status.in_(SETTLED),
    ]
    if start is not None and end is not None:
        conds += [Expense.expense_date >= start, Expense.expense_date < end]
    rows = (
        await session.execute(
            select(Expense.member_id, func.coalesce(func.sum(Expense.amount), 0))
            .where(*conds)
            .group_by(Expense.member_id)
        )
    ).all()
    paid = {mid: _dec(v) for mid, v in rows}
    return paid.get(link.inviter_id, _ZERO), paid.get(link.partner_id, _ZERO)


async def balance(session: AsyncSession, link: PartnerLink) -> Balance:
    paid_i, paid_p = await _paid_by(session, link)
    rows = (
        await session.execute(
            select(
                PartnerSettlement.member_id, func.coalesce(func.sum(PartnerSettlement.amount), 0)
            )
            .where(PartnerSettlement.link_id == link.id)
            .group_by(PartnerSettlement.member_id)
        )
    ).all()
    settled = {mid: _dec(v) for mid, v in rows}
    net = compute_net(
        paid_i,
        paid_p,
        link.inviter_pct,
        settled.get(link.inviter_id, _ZERO),
        settled.get(link.partner_id, _ZERO),
    )
    return Balance(paid_i + paid_p, paid_i, paid_p, net)


def month_window(today: date) -> tuple[datetime, datetime]:
    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return day_start(first), day_start(nxt)


# ── commands ──────────────────────────────────────────────────────────────────

_MEMBER_WORDS = (
    r"(?:meu |minha |o |a )?(?:parceiro|parceira|marido|esposa|namorado|namorada|noivo|noiva|"
    r"companheiro|companheira|amor)"
)
_INVITE = [
    re.compile(rf"^convidar {_MEMBER_WORDS}$"),
    re.compile(
        r"^invite (?:my |your )?(?:partner|wife|husband|girlfriend|boyfriend|fiance|fiancee|spouse)$"
    ),
    re.compile(
        r"^nodig (?:mijn )?(?:partner|vriend|vriendin|man|vrouw|echtgenoot|echtgenote) uit$"
    ),
    re.compile(
        r"^inviter (?:mon |ma )?(?:partenaire|conjoint|conjointe|copain|copine|mari|femme)$"
    ),
    re.compile(r"^(?:partner|partnerin|ehemann|ehefrau|freund|freundin) einladen$"),
]
_JOIN = [
    re.compile(r"^(?:entrar|entro|juntar|participar)(?: na| em| da)? casa ([a-z0-9]{6})$"),
    re.compile(r"^join (?:the )?(?:home|household|house) ([a-z0-9]{6})$"),
    re.compile(r"^(?:sluit aan bij|doe mee met|join) (?:het )?(?:huis|huishouden) ([a-z0-9]{6})$"),
    re.compile(r"^rejoindre (?:le |la )?(?:foyer|maison|menage) ([a-z0-9]{6})$"),
    re.compile(r"^(?:haushalt|zuhause) beitreten ([a-z0-9]{6})$"),
]
_SUMMARY = {
    "gastos da casa", "financas da casa", "financeiro da casa", "saldo da casa", "quem deve a quem",
    "acerto de contas", "shared expenses", "home expenses", "who owes who", "who owes whom",
    "gedeelde uitgaven", "huishoudbudget", "wie is wie iets schuldig", "depenses communes",
    "depenses du foyer", "qui doit a qui", "gemeinsame ausgaben", "wer schuldet wem",
}  # fmt: skip
_MARK = {
    "foi da casa", "e da casa", "da casa", "marca como da casa", "marcar como da casa",
    "mark as shared", "shared", "was for home", "het was voor het huis", "gedeeld", "voor het huis",
    "c'etait pour la maison", "commun", "etait commun", "war fuer den haushalt", "gemeinsam",
}  # fmt: skip
_UNMARK = {
    "foi pessoal", "e pessoal", "pessoal", "tira da casa", "tirar da casa", "marca como pessoal",
    "mark as personal", "personal", "persoonlijk", "het was persoonlijk", "personnel",
    "c'etait personnel", "persoenlich", "war persoenlich", "privat",
}  # fmt: skip
_SETTLE = {
    "acertamos", "acertamos as contas", "acertamos tudo", "quitamos", "quitamos a casa",
    "we are even", "we settled up", "settled up", "we're even", "we hebben afgerekend",
    "afgerekend", "on a regle", "on est quittes", "wir haben abgerechnet", "abgerechnet",
}  # fmt: skip
_SPLIT = re.compile(
    r"^(?:divisao|dividir|split|verdeling|repartition|aufteilung)\s+(\d{1,3})\s*[/x:\-]\s*(\d{1,3})$"
)
_ALWAYS = re.compile(
    r"^(?:sempre da casa|always shared|always home|altijd gedeeld|toujours commun|immer gemeinsam)\s*[:\-]?\s*(.+)$"
)
_ALWAYS_CLEAR = {
    "nada sempre da casa", "limpar categorias da casa", "nothing always shared", "clear shared categories",
    "niets altijd gedeeld", "rien de toujours commun", "nichts immer gemeinsam",
}  # fmt: skip
_LEAVE = {
    "sair da casa", "desfazer casal", "encerrar casa", "deixar a casa", "leave home", "leave household",
    "end partnership", "stop sharing", "verlaat het huis", "stop met delen", "quitter le foyer",
    "arreter le partage", "haushalt verlassen", "teilen beenden",
}  # fmt: skip

# words people say for the canonical categories (accent-free), on top of the labels themselves
_ALIASES = {
    "mercado": "supermarkt", "supermercado": "supermarkt", "feira": "supermarkt", "groceries": "supermarkt",
    "boodschappen": "supermarkt", "courses": "supermarkt", "lebensmittel": "supermarkt",
    "aluguel": "wonen", "moradia": "wonen", "habitacao": "wonen", "casa": "wonen", "rent": "wonen",
    "huur": "wonen", "loyer": "wonen", "miete": "wonen", "energia": "wonen", "luz": "wonen",
    "internet": "abonnement", "streaming": "abonnement", "lazer": "entertainment",
}  # fmt: skip


def _category_lookup() -> dict[str, str]:
    table: dict[str, str] = dict(_ALIASES)
    for key, names in _LABELS.items():
        if key in ("inkomen", "overig"):
            continue  # an income or "other" is never a default for the house
        table[strip_accents(key.lower())] = key
        for label in names.values():
            table[strip_accents(label.lower())] = key
    return table


def parse_categories(text: str) -> list[str]:
    """Canonical categories named in ``text`` ("mercado e aluguel", "groceries, rent")."""
    lookup = _category_lookup()
    out: list[str] = []
    for word in re.split(
        r"[,;/&+]|\s+e\s+|\s+and\s+|\s+en\s+|\s+et\s+|\s+und\s+", strip_accents(text.lower())
    ):
        word = word.strip(" .!?")
        if word in lookup and lookup[word] not in out:
            out.append(lookup[word])
    return out


def _first(patterns: list[re.Pattern[str]], plain: str) -> re.Match[str] | None:
    for p in patterns:
        if m := p.match(plain):
            return m
    return None


async def handle_couple_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> Reply | None:
    """None unless the message is clearly one of the couple commands (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 80:
        return None

    if _first(_INVITE, plain):
        return await _invite(member, lang, session)
    if m := _first(_JOIN, plain):
        return await _join(m.group(1).upper(), member, lang, session)
    if plain in _SUMMARY:
        return await _summary(member, lang, session)
    if plain in _MARK:
        return await _mark(member, lang, session, True)
    if plain in _UNMARK:
        return await _mark(member, lang, session, False)
    if plain in _SETTLE:
        return await _settle(member, lang, session)
    if m := _SPLIT.match(plain):
        return await _split(int(m.group(1)), int(m.group(2)), member, lang, session)
    if plain in _ALWAYS_CLEAR:
        return await _always(None, member, lang, session)
    if m := _ALWAYS.match(plain):
        return await _always(parse_categories(m.group(1)), member, lang, session)
    if plain in _LEAVE:
        return await _leave_ask(member, lang, session)
    return None


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


async def _invite(member: Member, lang: str, session: AsyncSession) -> Reply:
    link = await active_link(session, member.id)
    if link is not None:
        other = await _partner(session, link, member.id)
        return Reply(_t("couple_already", lang, name=_name(other, lang)), [])
    now = datetime.now(UTC)
    mine = await session.scalar(
        select(PartnerLink).where(
            PartnerLink.inviter_id == member.id, PartnerLink.status.in_((INVITED, PENDING))
        )
    )
    if mine is not None and mine.expires_at <= now:
        mine.status = ENDED
        mine.ended_at = now
        mine = None
    if mine is not None and mine.status == PENDING:
        return Reply(_t("couple_invite_pending", lang), [])
    if mine is None:
        code = _new_code()
        while await session.scalar(select(PartnerLink.id).where(PartnerLink.code == code)):
            code = _new_code()
        mine = PartnerLink(
            inviter_id=member.id,
            code=code,
            status=INVITED,
            expires_at=now + timedelta(days=INVITE_DAYS),
        )
        session.add(mine)
        await session.flush()
        audit(session, "couple_invited", member.id)
    number = "".join(ch for ch in settings.whatsapp_display_number if ch.isdigit())
    tap = ""
    if number:
        url = f"https://wa.me/{number}?text={quote('entrar casa ' + mine.code)}"
        tap = _t("couple_invite_tap", lang, url=url)
    return Reply(_t("couple_invite", lang, code=mine.code, days=INVITE_DAYS, tap=tap), [])


async def _join(code: str, member: Member, lang: str, session: AsyncSession) -> Reply:
    since = datetime.now(UTC) - timedelta(hours=1)
    fails = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.member_id == member.id,
            AuditLog.event == "couple_join_failed",
            AuditLog.created_at >= since,
        )
    )
    if (fails or 0) >= MAX_JOIN_FAILS:
        return Reply(_t("couple_join_toomany", lang), [])
    if await active_link(session, member.id) is not None:
        return Reply(_t("couple_join_has_home", lang), [])
    now = datetime.now(UTC)
    link = await session.scalar(
        select(PartnerLink).where(PartnerLink.code == code, PartnerLink.status == INVITED)
    )
    if link is None or link.expires_at <= now:
        audit(session, "couple_join_failed", member.id)
        return Reply(_t("couple_join_bad", lang), [])
    if link.inviter_id == member.id:
        return Reply(_t("couple_join_self", lang), [])
    if await active_link(session, link.inviter_id) is not None:
        audit(session, "couple_join_failed", member.id)
        return Reply(_t("couple_join_bad", lang), [])
    link.partner_id = member.id
    link.status = PENDING
    await session.flush()
    audit(session, "couple_join_requested", member.id)
    inviter = await session.get(Member, link.inviter_id)
    text = _t("couple_join_ask", lang, name=_name(inviter, lang))
    return Reply(
        text,
        [
            (f"couple_yes:{link.id}", _t("couple_btn_yes", lang)),
            (f"couple_no:{link.id}", _t("couple_btn_no", lang)),
        ],
    )


async def handle_couple_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> Reply:
    """``couple_yes|couple_no|couple_end|couple_keep:<link id>`` and ``home:<expense id>``."""
    try:
        ident = uuid.UUID(raw_id)
    except ValueError:
        return Reply(_t("button_gone", lang), [])

    if action == "home":  # the "Da casa" button of an expense confirmation
        exp = await session.scalar(
            select(Expense).where(Expense.id == ident, Expense.member_id == member.id)
        )
        if exp is None:
            return Reply(_t("button_gone", lang), [])
        return await _mark_expense(member, lang, session, exp, True)

    link = await session.get(PartnerLink, ident)
    mine = link is not None and member.id in (link.inviter_id, link.partner_id)
    if not mine or link is None:
        return Reply(_t("couple_gone", lang), [])

    if action == "couple_yes":
        if link.status != PENDING or link.partner_id != member.id:
            return Reply(_t("couple_gone", lang), [])
        if await active_link(session, member.id) or await active_link(session, link.inviter_id):
            return Reply(_t("couple_join_has_home", lang), [])
        link.status = ACTIVE
        link.accepted_at = datetime.now(UTC)
        audit(session, "couple_accepted", member.id)
        inviter = await session.get(Member, link.inviter_id)
        return Reply(_t("couple_accepted", lang, name=_name(inviter, lang)), [])
    if action == "couple_no":
        if link.status != PENDING or link.partner_id != member.id:
            return Reply(_t("couple_gone", lang), [])
        link.status = ENDED
        link.ended_at = datetime.now(UTC)
        audit(session, "couple_declined", member.id)
        return Reply(_t("couple_declined", lang), [])
    if action == "couple_end":
        if link.status != ACTIVE:
            return Reply(_t("couple_gone", lang), [])
        await end_link(session, link)
        audit(session, "couple_left", member.id)
        return Reply(_t("couple_left", lang), [])
    if action == "couple_keep":
        return Reply(_t("couple_stay", lang), [])
    return Reply(_t("couple_gone", lang), [])


async def end_link(session: AsyncSession, link: PartnerLink) -> None:
    """Close the link and make every "da casa" row private again (a later link starts clean)."""
    link.status = ENDED
    link.ended_at = datetime.now(UTC)
    ids = [i for i in (link.inviter_id, link.partner_id) if i is not None]
    await session.execute(update(Expense).where(Expense.member_id.in_(ids)).values(shared=False))
    await session.flush()


async def release(session: AsyncSession, member_id: uuid.UUID) -> None:
    """Before a member is erased: the other person's rows go back to private, the links go away."""
    links = (
        await session.execute(
            select(PartnerLink).where(
                or_(PartnerLink.inviter_id == member_id, PartnerLink.partner_id == member_id)
            )
        )
    ).scalars()
    for link in list(links):
        others = [i for i in (link.inviter_id, link.partner_id) if i not in (None, member_id)]
        if others:
            await session.execute(
                update(Expense).where(Expense.member_id.in_(others)).values(shared=False)
            )
        await session.execute(delete(PartnerLink).where(PartnerLink.id == link.id))


# ── marking ───────────────────────────────────────────────────────────────────


async def _mark(member: Member, lang: str, session: AsyncSession, shared: bool) -> Reply:
    link = await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    exp = await session.scalar(
        select(Expense)
        .where(Expense.member_id == member.id, Expense.transaction_type == "expense")
        .order_by(Expense.created_at.desc(), Expense.id)
        .limit(1)
    )
    if exp is None:
        return Reply(_t("home_no_expense", lang), [])
    return await _mark_expense(member, lang, session, exp, shared, link)


async def _mark_expense(
    member: Member,
    lang: str,
    session: AsyncSession,
    exp: Expense,
    shared: bool,
    link: PartnerLink | None = None,
) -> Reply:
    link = link or await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    if exp.transaction_type != "expense":
        return Reply(_t("home_only_expense", lang), [])
    exp.shared = shared
    await session.flush()
    audit(session, "expense_shared" if shared else "expense_unshared", member.id)
    name = exp.merchant or exp.description or category_label(exp.category, lang)
    partner = await _partner(session, link, member.id)
    key = "home_marked" if shared else "home_unmarked"
    return Reply(
        _t(key, lang, name=name, amount=_fmt(_dec(exp.amount)), partner=_name(partner, lang)), []
    )


async def is_home_by_default(session: AsyncSession, member: Member, category: str | None) -> bool:
    """True when the member has an active link and this category is one they always share."""
    if not category or category not in (member.home_categories or []):
        return False
    return await active_link(session, member.id) is not None


async def _always(
    cats: list[str] | None, member: Member, lang: str, session: AsyncSession
) -> Reply:
    if await active_link(session, member.id) is None:
        return Reply(_t("couple_none", lang), [])
    if cats is None:
        member.home_categories = None
        await session.flush()
        return Reply(_t("couple_always_cleared", lang), [])
    if not cats:
        return Reply(_t("couple_always_none", lang), [])
    member.home_categories = cats
    await session.flush()
    audit(session, "couple_categories_set", member.id, n=len(cats))
    return Reply(
        _t("couple_always_set", lang, cats=", ".join(category_label(c, lang) for c in cats)), []
    )


# ── summary, settle, split, leave ────────────────────────────────────────────


def _balance_line(net: Decimal, partner: str, lang: str) -> str:
    if net > 0:
        return _t("couple_owes_me", lang, name=_cap(partner), amount=_fmt(net))
    if net < 0:
        return _t("couple_i_owe", lang, name=partner, amount=_fmt(-net))
    return _t("couple_even_line", lang)


async def _summary(member: Member, lang: str, session: AsyncSession) -> Reply:
    from alfred.clock import today_local

    link = await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    partner = _name(await _partner(session, link, member.id), lang)
    today = today_local()
    start, end = month_window(today)
    paid_i, paid_p = await _paid_by(session, link, start, end)
    mine, theirs = (paid_i, paid_p) if link.inviter_id == member.id else (paid_p, paid_i)
    pct = my_pct(link, member.id)
    bal = await balance(session, link)
    text = _t(
        "couple_summary",
        lang,
        month=f"{today:%m/%Y}",
        total=_fmt(mine + theirs),
        mine=_fmt(mine),
        theirs=_fmt(theirs),
        name=partner,
        name_c=_cap(partner),
        pct=pct,
        pct2=100 - pct,
        balance=_balance_line(bal.net_for(link, member.id), partner, lang),
    )
    if member.home_categories:
        text += "\n" + _t(
            "couple_always_line",
            lang,
            cats=", ".join(category_label(c, lang) for c in member.home_categories),
        )
    return Reply(text, [])


async def _settle(member: Member, lang: str, session: AsyncSession) -> Reply:
    link = await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    bal = await balance(session, link)
    net_inviter = bal.net_inviter
    if net_inviter == 0:
        return Reply(_t("couple_even", lang), [])
    debtor = link.partner_id if net_inviter > 0 else link.inviter_id
    amount = abs(net_inviter)
    session.add(PartnerSettlement(link_id=link.id, member_id=debtor, amount=float(amount)))
    await session.flush()
    audit(session, "couple_settled", member.id)
    partner = _name(await _partner(session, link, member.id), lang)
    key = "couple_settled_me" if debtor == member.id else "couple_settled_other"
    return Reply(
        _t(key, lang, name=_cap(partner) if debtor != member.id else partner, amount=_fmt(amount)),
        [],
    )


async def _split(mine: int, theirs: int, member: Member, lang: str, session: AsyncSession) -> Reply:
    link = await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    if mine + theirs != 100 or not (1 <= mine <= 99):
        return Reply(_t("couple_split_bad", lang), [])
    link.inviter_pct = mine if link.inviter_id == member.id else 100 - mine
    await session.flush()
    audit(session, "couple_split_set", member.id, pct=mine)
    partner = _name(await _partner(session, link, member.id), lang)
    return Reply(_t("couple_split_set", lang, mine=mine, theirs=100 - mine, name=partner), [])


async def _leave_ask(member: Member, lang: str, session: AsyncSession) -> Reply:
    link = await active_link(session, member.id)
    if link is None:
        return Reply(_t("couple_none", lang), [])
    partner = _name(await _partner(session, link, member.id), lang)
    bal = await balance(session, link)
    return Reply(
        _t(
            "couple_leave_ask",
            lang,
            name=partner,
            balance=_balance_line(bal.net_for(link, member.id), partner, lang),
        ),
        [
            (f"couple_end:{link.id}", _t("couple_btn_leave", lang)),
            (f"couple_keep:{link.id}", _t("couple_btn_keep", lang)),
        ],
    )


# ── panel ─────────────────────────────────────────────────────────────────────


async def has_home(session: AsyncSession, member_id: uuid.UUID) -> bool:
    return await active_link(session, member_id) is not None


async def panel_cards(
    session: AsyncSession, member: Member, lang: str, today: date
) -> list[dict[str, object]]:
    """Cards of the "Casa" tab; empty without an active link."""
    link = await active_link(session, member.id)
    if link is None:
        return []
    partner_member = await _partner(session, link, member.id)
    partner = _name(partner_member, lang)
    start, end = month_window(today)
    paid_i, paid_p = await _paid_by(session, link, start, end)
    mine, theirs = (paid_i, paid_p) if link.inviter_id == member.id else (paid_p, paid_i)
    pct = my_pct(link, member.id)
    bal = await balance(session, link)
    net = bal.net_for(link, member.id)
    balance_card: dict[str, object] = {
        "id": "home_balance",
        "empty": False,
        "values": {
            "month_total": float(mine + theirs),
            "paid_me": float(mine),
            "paid_partner": float(theirs),
            "my_pct": pct,
            "partner_pct": 100 - pct,
            "balance": float(net),  # > 0: the partner owes me
            "partner": partner,
        },
        "phrase": None,
    }
    rows = (
        await session.execute(
            select(Expense)
            .where(
                Expense.member_id.in_([link.inviter_id, link.partner_id]),
                Expense.shared.is_(True),
                Expense.transaction_type == "expense",
                Expense.status.in_(SETTLED),
                Expense.expense_date >= start,
                Expense.expense_date < end,
            )
            .order_by(Expense.expense_date.desc(), Expense.id)
            .limit(MAX_ENTRIES)
        )
    ).scalars()
    items = [
        {
            "date": to_local(e.expense_date).date().isoformat(),
            "merchant": e.merchant,
            "category": e.category,
            "label": category_label(e.category, lang),
            "amount": float(_dec(e.amount)),
            "who": "me" if e.member_id == member.id else "partner",
        }
        for e in rows
    ]
    entries_card: dict[str, object] = {
        "id": "home_entries",
        "empty": not items,
        "values": {"count": len(items), "partner": partner},
        "items": items,
        "phrase": None,
    }
    if not items:
        entries_card["hint"] = {
            "key": "home_entries",
            "text": _t("home_entries_hint", lang),
            "chat": None,
            "severity": "info",
        }
    return [balance_card, entries_card]


# ── texts ─────────────────────────────────────────────────────────────────────


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "couple_partner_word": _all(
        "seu par", "je partner", "your partner", "ton partenaire", "dein Partner"
    ),
    "couple_invite": _all(
        "Para dividir o financeiro, peça para a outra pessoa abrir o WhatsApp do Alfred, aceitar os termos e mandar:\n\n*entrar casa {code}*\n{tap}\nO código vale {days} dias. Só o que cada um marcar como *da casa* fica visível para os dois; o resto continua privado.",
        "Om de financiën te delen, vraag de ander om WhatsApp van Alfred te openen, de voorwaarden te accepteren en te sturen:\n\n*entrar casa {code}*\n{tap}\nDe code is {days} dagen geldig. Alleen wat jullie als *gedeeld* markeren is voor beiden zichtbaar; de rest blijft privé.",
        "To share the household finances, ask the other person to open Alfred on WhatsApp, accept the terms and send:\n\n*join home {code}*\n{tap}\nThe code is valid for {days} days. Only what each of you marks as *shared* is visible to both; everything else stays private.",
        "Pour partager les finances, demande à l'autre personne d'ouvrir Alfred sur WhatsApp, d'accepter les conditions et d'envoyer :\n\n*rejoindre foyer {code}*\n{tap}\nLe code est valable {days} jours. Seul ce que chacun marque comme *commun* est visible par les deux ; le reste reste privé.",
        "Um die Finanzen zu teilen, bitte die andere Person, Alfred auf WhatsApp zu öffnen, die Bedingungen zu akzeptieren und zu senden:\n\n*haushalt beitreten {code}*\n{tap}\nDer Code gilt {days} Tage. Nur was ihr jeweils als *gemeinsam* markiert, sehen beide; alles andere bleibt privat.",
    ),
    "couple_invite_tap": _all(
        "Ou toque aqui: {url}\n",
        "Of tik hier: {url}\n",
        "Or tap here: {url}\n",
        "Ou touche ici : {url}\n",
        "Oder tippe hier: {url}\n",
    ),
    "couple_invite_pending": _all(
        "Já existe um convite em andamento: a outra pessoa só precisa aceitar.",
        "Er loopt al een uitnodiging: de ander hoeft alleen nog te accepteren.",
        "An invitation is already under way: the other person just needs to accept.",
        "Une invitation est déjà en cours : l'autre personne n'a plus qu'à accepter.",
        "Es läuft schon eine Einladung: die andere Person muss nur noch annehmen.",
    ),
    "couple_already": _all(
        "Você já divide o financeiro com {name}. Para encerrar, escreva *sair da casa*.",
        "Je deelt de financiën al met {name}. Schrijf *verlaat het huis* om te stoppen.",
        "You already share the finances with {name}. To stop, write *leave home*.",
        "Tu partages déjà les finances avec {name}. Pour arrêter, écris *quitter le foyer*.",
        "Du teilst die Finanzen schon mit {name}. Zum Beenden schreib *haushalt verlassen*.",
    ),
    "couple_join_bad": _all(
        "Esse código não vale: pode estar errado ou ter expirado. Peça um novo com *convidar parceiro*.",
        "Deze code werkt niet: hij is fout of verlopen. Vraag een nieuwe met *nodig partner uit*.",
        "That code does not work: it may be wrong or expired. Ask for a new one with *invite partner*.",
        "Ce code ne marche pas : il est faux ou expiré. Demande-en un nouveau avec *inviter partenaire*.",
        "Dieser Code gilt nicht: er ist falsch oder abgelaufen. Frag einen neuen mit *partner einladen* an.",
    ),
    "couple_join_toomany": _all(
        "Muitas tentativas com código errado. Tente de novo em uma hora.",
        "Te veel pogingen met een foute code. Probeer het over een uur opnieuw.",
        "Too many attempts with a wrong code. Try again in an hour.",
        "Trop d'essais avec un mauvais code. Réessaie dans une heure.",
        "Zu viele Versuche mit falschem Code. Versuch es in einer Stunde erneut.",
    ),
    "couple_join_self": _all(
        "Esse código é seu. Quem precisa usá-lo é a outra pessoa.",
        "Dit is jouw code. De ander moet hem gebruiken.",
        "That code is yours. The other person has to use it.",
        "Ce code est le tien. C'est l'autre personne qui doit l'utiliser.",
        "Das ist dein Code. Die andere Person muss ihn verwenden.",
    ),
    "couple_join_has_home": _all(
        "Você já divide o financeiro com alguém. Escreva *sair da casa* antes de entrar em outra.",
        "Je deelt de financiën al met iemand. Schrijf *verlaat het huis* voordat je bij een andere aansluit.",
        "You already share the finances with someone. Write *leave home* before joining another.",
        "Tu partages déjà les finances avec quelqu'un. Écris *quitter le foyer* avant d'en rejoindre un autre.",
        "Du teilst die Finanzen schon mit jemandem. Schreib *haushalt verlassen*, bevor du einem anderen beitrittst.",
    ),
    "couple_join_ask": _all(
        "{name} convidou você para dividir o financeiro da casa. Funciona assim: cada um continua com seus lançamentos privados; só o que for marcado como *da casa* aparece para os dois (valor, categoria, loja e quem pagou) e o Alfred calcula quem deve a quem. Você pode sair quando quiser. Aceita?",
        "{name} nodigt je uit om de huishoudfinanciën te delen. Zo werkt het: ieder houdt eigen boekingen privé; alleen wat als *gedeeld* is gemarkeerd, zien jullie allebei (bedrag, categorie, winkel en wie betaalde) en Alfred rekent uit wie wie iets schuldig is. Je kunt altijd stoppen. Accepteer je?",
        "{name} invited you to share the household finances. Here is how it works: each of you keeps your own entries private; only what is marked as *shared* shows for both (amount, category, shop and who paid) and Alfred works out who owes whom. You can leave any time. Do you accept?",
        "{name} t'invite à partager les finances du foyer. Voici comment ça marche : chacun garde ses opérations privées ; seul ce qui est marqué *commun* apparaît pour les deux (montant, catégorie, magasin et qui a payé) et Alfred calcule qui doit quoi à qui. Tu peux partir quand tu veux. Tu acceptes ?",
        "{name} lädt dich ein, die Haushaltsfinanzen zu teilen. So funktioniert es: Jeder behält seine Buchungen privat; nur was als *gemeinsam* markiert ist, sehen beide (Betrag, Kategorie, Geschäft und wer bezahlt hat) und Alfred rechnet aus, wer wem wie viel schuldet. Du kannst jederzeit aussteigen. Nimmst du an?",
    ),
    "couple_gone": _all(
        "Esse convite não vale mais.",
        "Deze uitnodiging geldt niet meer.",
        "That invitation is no longer valid.",
        "Cette invitation n'est plus valable.",
        "Diese Einladung gilt nicht mehr.",
    ),
    "couple_btn_yes": _all("Aceitar", "Accepteren", "Accept", "Accepter", "Annehmen"),
    "couple_btn_no": _all("Recusar", "Weigeren", "Decline", "Refuser", "Ablehnen"),
    "couple_accepted": _all(
        "Pronto! Agora você e {name} dividem o financeiro da casa. Marque um gasto com o botão *Da casa* ou escreva *foi da casa*, e veja tudo em *gastos da casa*. A divisão padrão é 50/50 (mude com *divisão 60/40*).",
        "Klaar! Jij en {name} delen nu de huishoudfinanciën. Markeer een uitgave met de knop *Gedeeld* of schrijf *gedeeld*, en bekijk alles met *gedeelde uitgaven*. De verdeling is standaard 50/50 (wijzig met *verdeling 60/40*).",
        "Done! You and {name} now share the household finances. Mark an expense with the *Shared* button or write *shared*, and see everything with *shared expenses*. The default split is 50/50 (change it with *split 60/40*).",
        "C'est fait ! Toi et {name} partagez maintenant les finances du foyer. Marque une dépense avec le bouton *Commun* ou écris *commun*, et vois tout avec *dépenses communes*. La répartition par défaut est 50/50 (change-la avec *répartition 60/40*).",
        "Fertig! Du und {name} teilt jetzt die Haushaltsfinanzen. Markiere eine Ausgabe mit dem Knopf *Gemeinsam* oder schreib *gemeinsam*, und sieh alles unter *gemeinsame Ausgaben*. Die Aufteilung ist standardmäßig 50/50 (ändern mit *aufteilung 60/40*).",
    ),
    "couple_declined": _all(
        "Tudo bem, não compartilhei nada.",
        "Prima, ik heb niets gedeeld.",
        "No problem, I shared nothing.",
        "Pas de souci, je n'ai rien partagé.",
        "Kein Problem, ich habe nichts geteilt.",
    ),
    "couple_none": _all(
        "Você ainda não divide o financeiro com ninguém. Escreva *convidar parceiro* para começar.",
        "Je deelt de financiën nog met niemand. Schrijf *nodig partner uit* om te beginnen.",
        "You do not share the finances with anyone yet. Write *invite partner* to start.",
        "Tu ne partages pas encore les finances avec quelqu'un. Écris *inviter partenaire* pour commencer.",
        "Du teilst die Finanzen noch mit niemandem. Schreib *partner einladen*, um zu starten.",
    ),
    "home_no_expense": _all(
        "Não achei um lançamento seu para marcar.",
        "Ik vond geen boeking van jou om te markeren.",
        "I could not find an entry of yours to mark.",
        "Je n'ai trouvé aucune opération de toi à marquer.",
        "Ich habe keine Buchung von dir zum Markieren gefunden.",
    ),
    "home_only_expense": _all(
        "Só gastos podem ser da casa, receitas não.",
        "Alleen uitgaven kunnen gedeeld zijn, inkomsten niet.",
        "Only expenses can be shared, not income.",
        "Seules les dépenses peuvent être communes, pas les revenus.",
        "Nur Ausgaben können gemeinsam sein, Einnahmen nicht.",
    ),
    "home_marked": _all(
        "Marquei como da casa: {name}, {amount}. {partner} já vê esse lançamento.",
        "Gemarkeerd als gedeeld: {name}, {amount}. {partner} ziet deze boeking nu.",
        "Marked as shared: {name}, {amount}. {partner} can now see this entry.",
        "Marqué comme commun : {name}, {amount}. {partner} voit maintenant cette opération.",
        "Als gemeinsam markiert: {name}, {amount}. {partner} sieht diese Buchung jetzt.",
    ),
    "home_unmarked": _all(
        "Voltou a ser pessoal: {name}, {amount}.",
        "Weer persoonlijk: {name}, {amount}.",
        "Back to personal: {name}, {amount}.",
        "De nouveau personnel : {name}, {amount}.",
        "Wieder privat: {name}, {amount}.",
    ),
    "home_auto_suffix": _all(" (da casa)", " (gedeeld)", " (shared)", " (commun)", " (gemeinsam)"),
    "btn_home": _all("Da casa", "Gedeeld", "Shared", "Commun", "Gemeinsam"),
    "couple_owes_me": _all(
        "{name} te deve {amount}.",
        "{name} is jou {amount} schuldig.",
        "{name} owes you {amount}.",
        "{name} te doit {amount}.",
        "{name} schuldet dir {amount}.",
    ),
    "couple_i_owe": _all(
        "Você deve {amount} a {name}.",
        "Jij bent {name} {amount} schuldig.",
        "You owe {name} {amount}.",
        "Tu dois {amount} à {name}.",
        "Du schuldest {name} {amount}.",
    ),
    "couple_even_line": _all(
        "Vocês estão em dia.",
        "Jullie staan quitte.",
        "You are even.",
        "Les comptes sont équilibrés.",
        "Alles ausgeglichen.",
    ),
    "couple_summary": _all(
        "Casa de vocês, {month}:\nTotal da casa: {total}\n• Você pagou {mine}\n• {name_c} pagou {theirs}\nDivisão: você {pct}% e {name} {pct2}%.\n{balance}",
        "Jullie huis, {month}:\nTotaal gedeeld: {total}\n• Jij betaalde {mine}\n• {name_c} betaalde {theirs}\nVerdeling: jij {pct}% en {name} {pct2}%.\n{balance}",
        "Your home, {month}:\nShared total: {total}\n• You paid {mine}\n• {name_c} paid {theirs}\nSplit: you {pct}% and {name} {pct2}%.\n{balance}",
        "Foyer commun, {month} :\nTotal commun : {total}\n• Tu as payé {mine}\n• {name_c} a payé {theirs}\nRépartition : toi {pct}% et {name} {pct2}%.\n{balance}",
        "Euer Haushalt, {month}:\nGemeinsam gesamt: {total}\n• Du hast {mine} bezahlt\n• {name_c} hat {theirs} bezahlt\nAufteilung: du {pct}% und {name} {pct2}%.\n{balance}",
    ),
    "couple_always_line": _all(
        "Sempre da casa: {cats}.",
        "Altijd gedeeld: {cats}.",
        "Always shared: {cats}.",
        "Toujours commun : {cats}.",
        "Immer gemeinsam: {cats}.",
    ),
    "couple_even": _all(
        "Vocês já estão em dia, não há nada para acertar.",
        "Jullie staan al quitte, er valt niets af te rekenen.",
        "You are already even, there is nothing to settle.",
        "Les comptes sont déjà équilibrés, il n'y a rien à régler.",
        "Alles schon ausgeglichen, es gibt nichts abzurechnen.",
    ),
    "couple_settled_me": _all(
        "Anotei o acerto: você pagou {amount} a {name}. Agora vocês estão em dia.",
        "Afrekening genoteerd: jij betaalde {name} {amount}. Jullie staan nu quitte.",
        "Settlement noted: you paid {name} {amount}. You are even now.",
        "Règlement noté : tu as payé {amount} à {name}. Les comptes sont équilibrés.",
        "Abrechnung notiert: du hast {name} {amount} bezahlt. Jetzt ist alles ausgeglichen.",
    ),
    "couple_settled_other": _all(
        "Anotei o acerto: {name} pagou {amount} a você. Agora vocês estão em dia.",
        "Afrekening genoteerd: {name} betaalde jou {amount}. Jullie staan nu quitte.",
        "Settlement noted: {name} paid you {amount}. You are even now.",
        "Règlement noté : {name} t'a payé {amount}. Les comptes sont équilibrés.",
        "Abrechnung notiert: {name} hat dir {amount} bezahlt. Jetzt ist alles ausgeglichen.",
    ),
    "couple_split_set": _all(
        "Divisão atualizada: você {mine}% e {name} {theirs}%. Vale para o saldo todo, do passado e do futuro.",
        "Verdeling aangepast: jij {mine}% en {name} {theirs}%. Geldt voor het hele saldo, verleden en toekomst.",
        "Split updated: you {mine}% and {name} {theirs}%. It applies to the whole balance, past and future.",
        "Répartition mise à jour : toi {mine}% et {name} {theirs}%. Elle vaut pour tout le solde, passé et futur.",
        "Aufteilung geändert: du {mine}% und {name} {theirs}%. Die Aufteilung gilt für den ganzen Saldo, Vergangenheit und Zukunft.",
    ),
    "couple_split_bad": _all(
        "Use dois números que somem 100, com a sua parte primeiro. Exemplo: *divisão 60/40*.",
        "Gebruik twee getallen die samen 100 zijn, jouw deel eerst. Voorbeeld: *verdeling 60/40*.",
        "Use two numbers that add up to 100, your share first. Example: *split 60/40*.",
        "Utilise deux nombres dont la somme fait 100, ta part d'abord. Exemple : *répartition 60/40*.",
        "Nimm zwei Zahlen, die zusammen 100 ergeben, dein Anteil zuerst. Beispiel: *aufteilung 60/40*.",
    ),
    "couple_always_set": _all(
        "Combinado: {cats} ficam como da casa sempre que você lançar. Para desfazer: *nada sempre da casa*.",
        "Afgesproken: {cats} zijn voortaan gedeeld als je ze boekt. Ongedaan maken: *niets altijd gedeeld*.",
        "Agreed: {cats} will be shared whenever you record them. To undo: *nothing always shared*.",
        "Convenu : {cats} seront communs chaque fois que tu les enregistres. Pour annuler : *rien de toujours commun*.",
        "Abgemacht: {cats} sind künftig gemeinsam, wenn du sie buchst. Zum Rückgängigmachen: *nichts immer gemeinsam*.",
    ),
    "couple_always_none": _all(
        "Não reconheci essas categorias. Exemplos: supermercado, habitação, lazer.",
        "Ik herken die categorieën niet. Voorbeelden: supermarkt, wonen, entertainment.",
        "I did not recognise those categories. Examples: groceries, housing, entertainment.",
        "Je n'ai pas reconnu ces catégories. Exemples : supermarché, logement, loisirs.",
        "Ich habe diese Kategorien nicht erkannt. Beispiele: Supermarkt, Wohnen, Freizeit.",
    ),
    "couple_always_cleared": _all(
        "Pronto: nenhuma categoria é da casa por padrão agora.",
        "Klaar: geen enkele categorie is nu standaard gedeeld.",
        "Done: no category is shared by default now.",
        "C'est fait : aucune catégorie n'est commune par défaut.",
        "Fertig: keine Kategorie ist jetzt standardmäßig gemeinsam.",
    ),
    "couple_leave_ask": _all(
        "Quer mesmo sair da casa de {name}? Os lançamentos voltam a ser só seus e o acerto em aberto deixa de ser acompanhado. Agora: {balance} Se quiser, acerte antes com *acertamos*.",
        "Wil je echt het huis met {name} verlaten? Je boekingen zijn weer alleen van jou en het openstaande saldo wordt niet meer bijgehouden. Nu: {balance} Reken desnoods eerst af met *afgerekend*.",
        "Do you really want to leave the home with {name}? Your entries become yours alone again and the open balance is no longer tracked. Now: {balance} If you like, settle first with *settled up*.",
        "Veux-tu vraiment quitter le foyer avec {name} ? Tes opérations redeviennent les tiennes et le solde ouvert n'est plus suivi. Maintenant : {balance} Si tu veux, règle d'abord avec *on a réglé*.",
        "Willst du den Haushalt mit {name} wirklich verlassen? Deine Buchungen gehören wieder nur dir und der offene Saldo wird nicht mehr verfolgt. Jetzt: {balance} Rechne vorher ggf. mit *abgerechnet* ab.",
    ),
    "couple_btn_leave": _all("Sair da casa", "Verlaten", "Leave", "Quitter", "Verlassen"),
    "couple_btn_keep": _all("Ficar", "Blijven", "Stay", "Rester", "Bleiben"),
    "couple_left": _all(
        "Pronto, você saiu. Seus lançamentos voltaram a ser só seus. Vale avisar a outra pessoa.",
        "Klaar, je bent eruit. Je boekingen zijn weer alleen van jou. Laat het de ander weten.",
        "Done, you left. Your entries are yours alone again. Worth letting the other person know.",
        "C'est fait, tu as quitté le foyer. Tes opérations redeviennent les tiennes. Pense à prévenir l'autre personne.",
        "Fertig, du bist raus. Deine Buchungen gehören wieder nur dir. Sag es der anderen Person.",
    ),
    "couple_stay": _all(
        "Ok, continuo com a casa de vocês.",
        "Oké, ik blijf bij jullie huis.",
        "OK, I stay with your shared home.",
        "D'accord, je garde le foyer commun.",
        "OK, ich bleibe bei eurem gemeinsamen Haushalt.",
    ),
    "home_entries_hint": _all(
        "Ainda não há gastos da casa neste mês. Marque um lançamento com o botão Da casa.",
        "Er zijn deze maand nog geen gedeelde uitgaven. Markeer een boeking met de knop Gedeeld.",
        "No shared expenses this month yet. Mark an entry with the Shared button.",
        "Pas encore de dépenses communes ce mois-ci. Marque une opération avec le bouton Commun.",
        "Diesen Monat gibt es noch keine gemeinsamen Ausgaben. Markiere eine Buchung mit dem Knopf Gemeinsam.",
    ),
}
