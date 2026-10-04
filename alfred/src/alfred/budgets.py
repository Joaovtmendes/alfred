"""V2-01 — monthly budgets per category, alerts at 80 % / 100 % and a month-end projection.

Everything here is rules and SQL (no LLM). The router only calls ``handle_budget_command``
(set / list / remove) and ``alert_after_expense`` (one extra line on an expense confirmation).
Texts live in ``STRINGS`` and are merged into the router's ``_STRINGS`` so ``_t`` serves them.
"""

# ruff: noqa: E501
from __future__ import annotations

import calendar
import re
import unicodedata
import uuid
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import month_start, to_local, today_local
from alfred.labels import _LABELS, category_label
from alfred.models import SETTLED, Budget, Expense, Member
from alfred.parsing import strip_accents, to_amount
from alfred.validation import MAX_AMOUNT

# Income is not budgeted; the other nine canonical categories are.
BUDGET_CATEGORIES: tuple[str, ...] = tuple(c for c in _LABELS if c != "inkomen")

# Fewer than this many days into the month a projection is noise ("3 days → ×10"); the panel
# applies the same 10-day history rule (panel_calc.PROJ_MIN_HISTORY_DAYS).
MIN_PROJECTION_DAY = 10
ALERT_LEVELS = (80, 100)


def _norm(text: str) -> str:
    return strip_accents(unicodedata.normalize("NFC", text)).lower().strip()


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for cat, names in _LABELS.items():
        if cat == "inkomen":
            continue
        aliases[cat] = cat
        for name in names.values():
            aliases[_norm(name)] = cat
    extra = {
        "mercado": "supermarkt", "supermercado": "supermarkt", "groceries": "supermarkt",
        "grocery": "supermarkt", "boodschappen": "supermarkt", "courses": "supermarkt",
        "lebensmittel": "supermarkt", "comida": "restaurant", "restaurante": "restaurant",
        "restaurantes": "restaurant", "restaurants": "restaurant", "eten": "restaurant",
        "uit eten": "restaurant", "food": "restaurant", "transporte": "transport",
        "vervoer": "transport", "verkehr": "transport", "saude": "gezondheid",
        "health": "gezondheid", "sante": "gezondheid", "gesundheit": "gezondheid",
        "lazer": "entertainment", "loisirs": "entertainment", "freizeit": "entertainment",
        "entertainment": "entertainment", "uitgaan": "entertainment", "casa": "wonen",
        "moradia": "wonen", "hospedagem": "wonen", "accommodation": "wonen", "accommodatie": "wonen",
        "logies": "wonen", "hebergement": "wonen", "unterkunft": "wonen", "habitacao": "wonen", "housing": "wonen", "huur": "wonen",
        "aluguel": "wonen", "logement": "wonen", "wohnen": "wonen", "roupa": "kleding",
        "roupas": "kleding", "vestuario": "kleding", "clothes": "kleding", "clothing": "kleding",
        "kleren": "kleding", "vetements": "kleding", "kleidung": "kleding",
        "assinatura": "abonnement", "assinaturas": "abonnement", "subscricoes": "abonnement",
        "subscriptions": "abonnement", "subscription": "abonnement", "abonnementen": "abonnement",
        "abonnements": "abonnement", "abos": "abonnement", "outros": "overig", "outro": "overig",
        "other": "overig", "overige": "overig", "autres": "overig", "sonstiges": "overig",
    }  # fmt: skip
    aliases.update(extra)
    return aliases


_ALIASES = _build_aliases()


def resolve_category(raw: str) -> str | None:
    """Canonical category for what the user typed (any of the 5 languages), else None."""
    key = re.sub(r"\s+", " ", _norm(raw)).strip(" .!?")
    for article in ("o ", "a ", "de ", "do ", "da ", "the ", "het ", "de ", "le ", "la ", "das "):
        if key.startswith(article) and key != article.strip():
            key = key[len(article) :]
            break
    return _ALIASES.get(key)


_WORD = r"(?:orcamentos?|budgets?|budgetten|begrotingen|begroting)"
_PREP = r"(?:de\s+|do\s+|da\s+|para\s+|for\s+|voor\s+|pour\s+|fuer\s+|fur\s+|in\s+|em\s+|on\s+|op\s+|sur\s+|auf\s+)?"
_SET_RE = re.compile(
    r"^(?:(?:define|defina|definir|muda|mude|mudar|altera|altere|ajusta|ajuste|coloca|set|change|update|"
    r"stel|zet|wijzig|fixe|definis|change|setze|aendere)\s+)?"
    r"(?:(?:o|um|meu|my|a|mijn|een|mon|un|mein|ein)\s+)?" + _WORD + r"\s+" + _PREP +
    r"(?P<cat>[a-z][a-z ]{1,28}?)\s+(?:(?:para|to|naar|a|auf|de|of|van|at|a\s+hauteur\s+de)\s+)?"
    r"(?:[€]\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|eur|euros?)?"
    r"(?:\s*(?:por\s+mes|mensais|mensal|per\s+maand|maandelijks|a\s+month|monthly|par\s+mois|pro\s+monat))?\s*[.!]*$"
)  # fmt: skip
_REMOVE_RE = re.compile(
    r"^(?:tira|tire|tirar|remove|remova|remover|apaga|apague|apagar|exclui|exclua|delete|verwijder|"
    r"supprime|supprimer|loesche|loeschen|elimina)\s+"
    r"(?:(?:o|a|the|het|de|le|la|das|my|meu|mijn|mon|mein)\s+)?" + _WORD + r"\s+" + _PREP +
    r"(?P<cat>[a-z][a-z ]{1,28})\s*[.!]*$"
)  # fmt: skip
_LIST_WORDS = {
    "orcamentos", "orcamento", "meus orcamentos", "meu orcamento", "budgets", "budget",
    "my budgets", "my budget", "budgetten", "mijn budgetten", "mijn budget",
    "mes budgets", "mon budget", "meine budgets", "mein budget", "begroting", "begrotingen",
}  # fmt: skip


def is_list_request(body_plain: str) -> bool:
    plain = re.sub(r"\s+", " ", body_plain).strip(" .!?")
    return plain in _LIST_WORDS


def parse_set(body_plain: str) -> tuple[str, float] | None | str:
    """(raw_category, amount) for a "set budget" message; None when it is not one."""
    m = _SET_RE.match(body_plain.strip())
    if not m:
        return None
    amount = to_amount(m.group("amt"))
    if amount is None or amount > MAX_AMOUNT:
        return None
    return m.group("cat").strip(), amount


# ── queries ──────────────────────────────────────────────────────────────────


def _month_window(day: date, tz) -> tuple[datetime, datetime]:
    start = month_start(day)
    end = month_start(start + timedelta(days=32))
    return datetime.combine(start, time.min, tzinfo=tz), datetime.combine(end, time.min, tzinfo=tz)


async def month_spent(
    session: AsyncSession, member_id: uuid.UUID, category: str, day: date
) -> Decimal:
    """Expenses of ``category`` in the local month of ``day`` (income never counts)."""
    from alfred.clock import local_tz

    start, end = _month_window(day, local_tz())
    total = await session.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.member_id == member_id,
            Expense.transaction_type == "expense",
            Expense.category == category,
            Expense.status.in_(SETTLED),
            Expense.expense_date >= start,
            Expense.expense_date < end,
        )
    )
    return Decimal(str(total or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def project_month_end(spent: Decimal, today: date) -> Decimal | None:
    """Linear projection (spent ÷ days elapsed × days in month); None before day 10."""
    if today.day < MIN_PROJECTION_DAY:
        return None
    days = calendar.monthrange(today.year, today.month)[1]
    return (spent / today.day * days).quantize(Decimal("0.01"), ROUND_HALF_UP)


def percent(spent: Decimal, limit: Decimal) -> int:
    return int(spent * 100 / limit) if limit > 0 else 0


def _dec(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)


# ── alert on an expense confirmation ─────────────────────────────────────────


async def alert_after_expense(
    session: AsyncSession, member: Member, category: str | None, when: datetime, lang: str
) -> str:
    """Extra line for a just-recorded expense that crosses 80 % / 100 % of its category budget.

    Empty when there is no budget, the expense is not from the current month, or the level was
    already announced this month. A jump from 0 straight past 100 % announces only 100 %.
    Expects the new expense to be flushed already.
    """
    from alfred.conversation import _fmt_eur, _t

    if not category:
        return ""
    budget = await session.scalar(
        select(Budget).where(Budget.member_id == member.id, Budget.category == category)
    )
    if budget is None:
        return ""
    today = today_local()
    if month_start(to_local(when).date()) != month_start(today):
        return ""
    limit = _dec(budget.monthly_limit)
    spent = await month_spent(session, member.id, category, today)
    pct = percent(spent, limit)
    level = 100 if pct >= 100 else 80 if pct >= 80 else 0
    month = month_start(today)
    announced = budget.last_alert_level if budget.last_alert_month == month else 0
    if level == 0 or level <= announced:
        return ""
    budget.last_alert_month = month
    budget.last_alert_level = level
    session.add(budget)
    cat = category_label(category, lang)
    key = "budget_alert_100" if level == 100 else "budget_alert_80"
    text = _t(
        key, lang, pct=pct, cat=cat, spent=_fmt_eur(float(spent)), limit=_fmt_eur(float(limit))
    )
    proj = project_month_end(spent, today)
    if proj is not None and level < 100 and proj > limit:
        text += _t("budget_alert_proj", lang, proj=_fmt_eur(float(proj)))
    return text


async def alerts_for_categories(
    session: AsyncSession, member: Member, categories: list[str | None], lang: str
) -> str:
    """Alert lines for several just-recorded expenses (one per category, in order)."""
    seen: set[str] = set()
    out = ""
    for cat in categories:
        if cat and cat not in seen:
            seen.add(cat)
            out += await alert_after_expense(
                session, member, cat, datetime.now().astimezone(), lang
            )
    return out


# ── commands ─────────────────────────────────────────────────────────────────


async def handle_budget_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    """Reply for "orçamento mercado 400" / "meus orçamentos" / "tira o orçamento de lazer"."""
    from alfred.conversation import _fmt_eur, _t

    if is_list_request(body_plain):
        return await _list(member, lang, session)

    m_rm = _REMOVE_RE.match(body_plain.strip())
    if m_rm:
        cat = resolve_category(m_rm.group("cat"))
        if cat is None:
            return _unknown(lang)
        res = await session.execute(
            delete(Budget).where(Budget.member_id == member.id, Budget.category == cat)
        )
        if not res.rowcount:
            return _t("budget_none_to_remove", lang, cat=category_label(cat, lang))
        audit(session, "budget_removed", member.id)
        return _t("budget_removed", lang, cat=category_label(cat, lang))

    parsed = parse_set(body_plain)
    if not parsed:
        return None
    raw_cat, amount = parsed
    cat = resolve_category(raw_cat)
    if cat is None:
        return _unknown(lang)
    existing = await session.scalar(
        select(Budget).where(Budget.member_id == member.id, Budget.category == cat)
    )
    if existing is None:
        session.add(
            Budget(
                id=uuid.uuid4(),
                member_id=member.id,
                household_id=member.household_id,
                category=cat,
                monthly_limit=amount,
            )
        )
    else:
        existing.monthly_limit = amount
        # a new limit is a fresh start for this month's alerts
        existing.last_alert_level = 0
        existing.last_alert_month = None
        session.add(existing)
    await session.flush()
    audit(session, "budget_set", member.id)
    return _t("budget_set", lang, cat=category_label(cat, lang), limit=_fmt_eur(amount))


def _unknown(lang: str) -> str:
    from alfred.conversation import _t

    cats = ", ".join(category_label(c, lang) for c in BUDGET_CATEGORIES)
    return _t("budget_unknown_category", lang, cats=cats)


async def _list(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.clock import month_name
    from alfred.conversation import _fmt_eur, _t

    rows = (
        (await session.execute(select(Budget).where(Budget.member_id == member.id))).scalars().all()
    )
    if not rows:
        return _t("budget_list_empty", lang)
    today = today_local()
    lines = [_t("budget_list_header", lang, month=month_name(today, lang))]
    for b in sorted(
        rows,
        key=lambda r: (
            BUDGET_CATEGORIES.index(r.category) if r.category in BUDGET_CATEGORIES else 99
        ),
    ):
        limit = _dec(b.monthly_limit)
        spent = await month_spent(session, member.id, b.category, today)
        pct = percent(spent, limit)
        proj = project_month_end(spent, today)
        row = _t(
            "budget_row",
            lang,
            cat=category_label(b.category, lang),
            spent=_fmt_eur(float(spent)),
            limit=_fmt_eur(float(limit)),
            pct=pct,
        )
        if proj is not None and spent > 0:
            row += _t("budget_row_proj", lang, proj=_fmt_eur(float(proj)))
        lines.append(row)
    return "\n".join(lines)


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "budget_set": {
        "pt": (
            "Feito, teto de {limit} em *{cat}* por mês.",
            "Combinado: {limit} por mês em *{cat}*. Eu aviso quando chegar perto.",
        ),
        "nl": (
            "Gelukt, maximaal {limit} per maand voor *{cat}*.",
            "Afgesproken: {limit} per maand voor *{cat}*. Ik waarschuw je als je in de buurt komt.",
        ),
        "en": (
            "Done, a cap of {limit} a month on *{cat}*.",
            "Got it: {limit} a month for *{cat}*. I'll warn you when you get close.",
        ),
        "fr": (
            "C'est noté, plafond de {limit} par mois pour *{cat}*.",
            "D'accord : {limit} par mois pour *{cat}*. Je te préviens quand tu t'approches.",
        ),
        "de": (
            "Erledigt, Limit von {limit} pro Monat für *{cat}*.",
            "Alles klar: {limit} pro Monat für *{cat}*. Ich warne dich, wenn es knapp wird.",
        ),
    },
    "budget_removed": {
        "pt": "Orçamento de *{cat}* removido.",
        "nl": "Budget voor *{cat}* verwijderd.",
        "en": "Budget for *{cat}* removed.",
        "fr": "Budget *{cat}* supprimé.",
        "de": "Budget für *{cat}* entfernt.",
    },
    "budget_none_to_remove": {
        "pt": "Você não tem orçamento em *{cat}*.",
        "nl": "Je hebt geen budget voor *{cat}*.",
        "en": "You don't have a budget for *{cat}*.",
        "fr": "Tu n'as pas de budget pour *{cat}*.",
        "de": "Du hast kein Budget für *{cat}*.",
    },
    "budget_unknown_category": {
        "pt": "Não reconheci essa categoria. Use uma destas: {cats}.",
        "nl": "Die categorie ken ik niet. Kies uit: {cats}.",
        "en": "I didn't recognise that category. Pick one of: {cats}.",
        "fr": "Je ne reconnais pas cette catégorie. Choisis parmi : {cats}.",
        "de": "Diese Kategorie kenne ich nicht. Wähle eine von: {cats}.",
    },
    "budget_list_empty": {
        "pt": "Você ainda não tem orçamentos. Para criar um, diga por exemplo “orçamento mercado 400”.",
        "nl": "Je hebt nog geen budgetten. Maak er een met bijvoorbeeld “budget boodschappen 400”.",
        "en": "You don't have any budgets yet. Create one, for example “budget groceries 400”.",
        "fr": "Tu n'as pas encore de budgets. Crée-en un, par exemple « budget courses 400 ».",
        "de": "Du hast noch keine Budgets. Lege eines an, zum Beispiel „Budget Lebensmittel 400“.",
    },
    "budget_list_header": {
        "pt": "Seus orçamentos de {month}:",
        "nl": "Je budgetten voor {month}:",
        "en": "Your budgets for {month}:",
        "fr": "Tes budgets pour {month} :",
        "de": "Deine Budgets für {month}:",
    },
    "budget_row": {
        "pt": "• {cat}: {spent} de {limit} ({pct}%)",
        "nl": "• {cat}: {spent} van {limit} ({pct}%)",
        "en": "• {cat}: {spent} of {limit} ({pct}%)",
        "fr": "• {cat} : {spent} sur {limit} ({pct} %)",
        "de": "• {cat}: {spent} von {limit} ({pct} %)",
    },
    "budget_row_proj": {
        "pt": " · fecha o mês em ~{proj}",
        "nl": " · eindigt de maand op ~{proj}",
        "en": " · on track for ~{proj}",
        "fr": " · fin de mois vers ~{proj}",
        "de": " · Monatsende bei ~{proj}",
    },
    "budget_alert_80": {
        "pt": "\n⚠️ Você já usou {pct}% do orçamento de {cat} ({spent} de {limit}).",
        "nl": "\n⚠️ Je hebt al {pct}% van je budget voor {cat} gebruikt ({spent} van {limit}).",
        "en": "\n⚠️ You've already used {pct}% of your {cat} budget ({spent} of {limit}).",
        "fr": "\n⚠️ Tu as déjà utilisé {pct} % du budget {cat} ({spent} sur {limit}).",
        "de": "\n⚠️ Du hast schon {pct} % deines Budgets für {cat} verbraucht ({spent} von {limit}).",
    },
    "budget_alert_100": {
        "pt": "\n⚠️ O orçamento de {cat} estourou: {spent} de {limit}.",
        "nl": "\n⚠️ Je budget voor {cat} is overschreden: {spent} van {limit}.",
        "en": "\n⚠️ Your {cat} budget is blown: {spent} of {limit}.",
        "fr": "\n⚠️ Le budget {cat} est dépassé : {spent} sur {limit}.",
        "de": "\n⚠️ Das Budget für {cat} ist überschritten: {spent} von {limit}.",
    },
    "budget_alert_proj": {
        "pt": " No ritmo atual, fecha o mês em ~{proj}.",
        "nl": " Op dit tempo eindig je de maand op ~{proj}.",
        "en": " At this pace you'll end the month at ~{proj}.",
        "fr": " À ce rythme, tu finis le mois à ~{proj}.",
        "de": " In diesem Tempo landest du am Monatsende bei ~{proj}.",
    },
}
