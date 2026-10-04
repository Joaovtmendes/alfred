# ruff: noqa: E501
"""V2-14 — free-form questions about the member's own numbers, and saved views.

The model never writes SQL. It turns the question into a small JSON *spec* (metric, group_by,
period, compare_to, filters, top_n) that ``validation.sanitize_analysis_spec`` checks against
closed lists; this module runs fixed, parameterised queries for that spec and formats the card.
A question that does not fit the spec gets an honest "here is what I can do" answer.

Limits: ``DAILY_LIMIT`` LLM analyses per member per local day (counted from ``llm_usage``,
purpose ``analysis``, so the cost shows up in the V1-21 metrics) and ``MAX_VIEWS`` saved views.
Running a saved view costs no LLM call, so it is not limited.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.clock import local_tz, today_local
from alfred.labels import category_label
from alfred.llm import extract_analysis_spec
from alfred.models import SETTLED, Expense, LlmUsage, Member, SavedView
from alfred.validation import sanitize_analysis_spec

DAILY_LIMIT = 10
MAX_VIEWS = 20
MAX_VIEW_NAME = 40


@dataclass(frozen=True)
class Spec:
    metric: str = "spent"
    group_by: str = "none"
    period: str = "this_month"
    compare_to: str = "none"
    category: str | None = None
    merchant: str | None = None
    top_n: int = 5


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # exclusive

    def label(self) -> str:
        return f"{self.start:%d/%m/%Y}–{self.end - timedelta(days=1):%d/%m/%Y}"


@dataclass
class Result:
    window: Window
    total: Decimal
    rows: list[tuple[str, Decimal]]
    prev_window: Window | None = None
    prev_total: Decimal | None = None
    prev_by_key: dict[str, Decimal] | None = None


# ── periods ───────────────────────────────────────────────────────────────────


def add_months(d: date, n: int) -> date:
    year, month0 = divmod(d.year * 12 + d.month - 1 + n, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _natural(period: str, today: date) -> tuple[date, date, str, int]:
    """(start, natural end exclusive, unit "m"|"d", size) of a named period."""
    m0 = today.replace(day=1)
    if period == "this_month":
        return m0, add_months(m0, 1), "m", 1
    if period == "last_month":
        return add_months(m0, -1), m0, "m", 1
    if period == "last_3_months":
        return add_months(m0, -3), m0, "m", 3
    if period == "last_6_months":
        return add_months(m0, -6), m0, "m", 6
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1), "m", 12
    if period == "last_year":
        return date(today.year - 1, 1, 1), date(today.year, 1, 1), "m", 12
    if period == "last_7_days":
        return today - timedelta(days=6), today + timedelta(days=1), "d", 7
    if period == "last_30_days":
        return today - timedelta(days=29), today + timedelta(days=1), "d", 30
    raise ValueError(period)


def windows(spec: Spec, today: date) -> tuple[Window, Window | None]:
    """The window asked for and the one to compare it with.

    A period still in progress (this month, this year) is compared with the *same number of
    days* of the earlier period, so a half month is never set against a whole one.
    """
    start, nat_end, unit, size = _natural(spec.period, today)
    end = min(nat_end, today + timedelta(days=1))
    cur = Window(start, end)
    if spec.compare_to == "none":
        return cur, None
    span = end - start
    if spec.compare_to == "previous_period":
        if unit == "m":
            ps = add_months(start, -size)
            pe = min(add_months(nat_end, -size), ps + span)
        else:
            ps, pe = start - timedelta(days=size), start
    else:  # same_period_last_year
        ps = add_months(start, -12)
        pe = min(add_months(nat_end, -12), ps + span)
    return cur, Window(ps, pe)


# ── queries ───────────────────────────────────────────────────────────────────


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _dec(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _statement(spec: Spec, member_id, win: Window, grouped: bool, limit: bool):
    tz = local_tz()
    start = datetime.combine(win.start, time.min, tzinfo=tz)
    end = datetime.combine(win.end, time.min, tzinfo=tz)
    value = {
        "spent": func.sum(Expense.amount),
        "income": func.sum(Expense.amount),
        "net": func.sum(
            case((Expense.transaction_type == "income", Expense.amount), else_=-Expense.amount)
        ),
        "count": func.count(Expense.id),
        "average": func.avg(Expense.amount),
    }[spec.metric]
    if grouped and spec.group_by == "category":
        key = func.coalesce(Expense.category, "overig")
    elif grouped and spec.group_by == "month":
        key = func.to_char(func.timezone(tz.key, Expense.expense_date), "YYYY-MM")
    elif grouped and spec.group_by == "merchant":
        key = func.lower(func.coalesce(Expense.merchant, "?"))
    else:
        key = None
    cols = [key.label("k"), value.label("v")] if key is not None else [value.label("v")]
    stmt = select(*cols).where(
        Expense.member_id == member_id,
        Expense.expense_date >= start,
        Expense.expense_date < end,
        Expense.status.in_(SETTLED),
    )
    if spec.metric == "income":
        stmt = stmt.where(Expense.transaction_type == "income")
    elif spec.metric != "net":
        stmt = stmt.where(Expense.transaction_type == "expense")
    if spec.category:
        stmt = stmt.where(Expense.category == spec.category)
    if spec.merchant:
        stmt = stmt.where(Expense.merchant.ilike(_like(spec.merchant), escape="\\"))
    if key is not None:
        stmt = stmt.group_by(key)
        if spec.group_by == "month":
            stmt = stmt.order_by(key)
        else:
            stmt = stmt.order_by(value.desc(), key)
            if limit:
                stmt = stmt.limit(spec.top_n)
    return stmt


async def _total(session: AsyncSession, spec: Spec, member_id, win: Window) -> Decimal:
    return _dec(await session.scalar(_statement(spec, member_id, win, False, False)))


async def _rows(
    session: AsyncSession, spec: Spec, member_id, win: Window, limit: bool
) -> list[tuple[str, Decimal]]:
    if spec.group_by == "none":
        return []
    return [
        (str(k), _dec(v))
        for k, v in (await session.execute(_statement(spec, member_id, win, True, limit))).all()
    ]


async def run(session: AsyncSession, member_id, spec: Spec, today: date) -> Result:
    cur, prev = windows(spec, today)
    res = Result(
        cur,
        await _total(session, spec, member_id, cur),
        await _rows(session, spec, member_id, cur, True),
    )
    if prev is not None:
        res.prev_window = prev
        res.prev_total = await _total(session, spec, member_id, prev)
        if spec.group_by in ("category", "merchant"):
            res.prev_by_key = dict(await _rows(session, spec, member_id, prev, False))
    return res


async def analyses_today(session: AsyncSession, member_id) -> int:
    midnight = datetime.combine(today_local(), time.min, tzinfo=local_tz())
    return int(
        await session.scalar(
            select(func.count())
            .select_from(LlmUsage)
            .where(
                LlmUsage.member_id == member_id,
                LlmUsage.purpose == "analysis",
                LlmUsage.created_at >= midnight,
            )
        )
        or 0
    )


# ── formatting ────────────────────────────────────────────────────────────────


def _value(spec: Spec, v: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return str(int(v)) if spec.metric == "count" else _fmt_eur(float(v))


def _delta(cur: Decimal, prev: Decimal | None) -> str | None:
    if prev is None or prev == 0:
        return None
    return f"{(cur - prev) / abs(prev) * 100:+.0f}%".replace("-", "−")


def _key_label(spec: Spec, key: str, lang: str) -> str:
    if spec.group_by == "category":
        return category_label(key, lang)
    if spec.group_by == "month":
        y, m = key.split("-")
        return f"{m}/{y}"
    return key.capitalize()


def format_result(spec: Spec, res: Result, lang: str) -> str:
    from alfred.conversation import _t

    parts = [_t(f"analysis_metric_{spec.metric}", lang), _t(f"analysis_period_{spec.period}", lang)]
    if spec.category:
        parts.append(category_label(spec.category, lang))
    if spec.merchant:
        parts.append(f"“{spec.merchant}”")
    lines = [" · ".join(parts), f"({res.window.label()})"]
    if res.total == 0 and not res.rows:
        return "\n".join(lines + [_t("analysis_empty", lang)])
    lines.append(f"*{_value(spec, res.total)}*")
    if res.prev_total is not None and res.prev_window is not None:
        delta = _delta(res.total, res.prev_total)
        lines.append(
            _t(
                "analysis_vs",
                lang,
                when=res.prev_window.label(),
                value=_value(spec, res.prev_total),
            )
            + (f" ({delta})" if delta else "")
        )
    for key, v in res.rows:
        line = f"• {_key_label(spec, key, lang)}: {_value(spec, v)}"
        if res.prev_by_key is not None:
            d = _delta(v, res.prev_by_key.get(key))
            line += f" ({d})" if d else ""
        lines.append(line)
    return "\n".join(lines)


# ── free-form question ────────────────────────────────────────────────────────

_MONEY = re.compile(
    r"\b(gast\w*|receit\w*|recebi|ganhei|saldo|quanto|uitgegeven|uitgaven|inkomsten|verdiend|hoeveel|"
    r"spent|spend\w*|income|earn\w*|how much|depens\w*|revenus?|combien|ausgegeben|ausgaben|einnahmen|wieviel|verdient)\b"
)
_MARKER = re.compile(
    r"(compar|\bvs\b|versus|contra o|trimestre|semestre|ultimos|\d+ meses|este ano|ano passado|por categoria|"
    r"por mes|\bmedia\b|maiores|maior gasto|\btop \d|per categorie|per maand|vergelijk|vorig jaar|dit jaar|"
    r"afgelopen|laatste|gemiddeld|grootste|by category|per month|average|biggest|largest|last \d+ months|"
    r"this year|last year|quarter|par categorie|par mois|moyenne|par rapport|derniers|cette annee|"
    r"annee derniere|plus gros|nach kategorie|pro monat|durchschnitt|vergleich|letzte|dieses jahr|letztes jahr)"
)


def looks_like_analysis(body_plain: str) -> bool:
    """Cheap gate so ordinary messages never cost an LLM call."""
    return (
        len(body_plain) >= 12
        and bool(_MONEY.search(body_plain))
        and bool(_MARKER.search(body_plain))
    )


def _spec(data: dict) -> Spec:
    return Spec(**{k: data[k] for k in Spec.__dataclass_fields__})


async def _save_draft(session: AsyncSession, member_id, data: dict) -> None:
    await session.execute(
        delete(SavedView).where(SavedView.member_id == member_id, SavedView.name.is_(None))
    )
    session.add(SavedView(member_id=member_id, name=None, spec=data))
    await session.flush()


async def handle_analysis_question(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    if not looks_like_analysis(body_plain):
        return None
    if await analyses_today(session, member.id) >= DAILY_LIMIT:
        return _t("analysis_limit", lang, n=DAILY_LIMIT)
    raw = await extract_analysis_spec(body, lang)
    if raw is None:
        return None  # no model available: let the normal reply path handle it
    data = sanitize_analysis_spec(raw.get("spec")) if raw.get("supported") is not False else None
    if data is None:
        return _t("analysis_unsupported", lang)
    spec = _spec(data)
    res = await run(session, member.id, spec, today_local())
    await _save_draft(session, member.id, data)
    return format_result(spec, res, lang) + "\n" + _t("analysis_save_hint", lang)


# ── saved views ───────────────────────────────────────────────────────────────

_Q = r"""['"“”‘’]?"""
_SAVE = re.compile(
    r"^(?:salva|salvar|guarda|guardar|save|sla|bewaar|enregistre|sauvegarde|speichere|speichern)\s+"
    r"(?:essa|esta|a|this|deze|cette|diese|die)?\s*(?:visao|analise|view|weergave|analyse|vue|ansicht)\s+"
    rf"(?:como|as|op als|als|sous|com o nome)\s+{_Q}(.+?){_Q}$"
)
_DELETE = re.compile(
    r"^(?:apaga|apagar|remove|remover|delete|verwijder|supprime|supprimer|loesche|lösche)\s+(?:a\s+|the\s+|de\s+|la\s+|die\s+)?"
    rf"(?:visao|view|weergave|vue|ansicht)\s+{_Q}(.+?){_Q}$"
)
_RUN = re.compile(
    r"^(?:roda|rodar|executa|executar|run|voer uit|lance|execute|starte)\s+(?:a\s+|the\s+|de\s+|la\s+|die\s+)?"
    rf"((?:visao|view|weergave|vue|ansicht)\s+)?{_Q}(.+?){_Q}$"
)
_LIST = {
    "minhas visoes", "as minhas visoes", "minhas visoes salvas", "my views", "my saved views", "saved views",
    "mijn weergaven", "mijn opgeslagen weergaven", "mes vues", "mes vues enregistrees", "meine ansichten",
    "meine gespeicherten ansichten",
}  # fmt: skip


def _clean_name(raw: str) -> str:
    return " ".join(raw.replace("'", " ").replace('"', " ").split())[:MAX_VIEW_NAME]


async def _named(session: AsyncSession, member_id) -> list[SavedView]:
    return list(
        (
            await session.execute(
                select(SavedView)
                .where(SavedView.member_id == member_id, SavedView.name.is_not(None))
                .order_by(SavedView.created_at)
            )
        ).scalars()
    )


def _find(views: list[SavedView], name: str) -> SavedView | None:
    from alfred.parsing import strip_accents

    want = strip_accents(name.lower()).strip()
    for v in views:
        if strip_accents((v.name or "").lower()).strip() == want:
            return v
    return None


async def handle_view_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = " ".join(body_plain.split()).strip(" .!?")
    if plain in _LIST:
        views = await _named(session, member.id)
        if not views:
            return _t("views_empty", lang)
        return _t("views_header", lang) + "\n" + "\n".join(f"• {v.name}" for v in views)

    if m := _SAVE.match(plain):
        name = _clean_name(m.group(1))
        if not name:
            return None
        draft = await session.scalar(
            select(SavedView).where(SavedView.member_id == member.id, SavedView.name.is_(None))
        )
        if draft is None:
            return _t("view_no_last", lang)
        views = await _named(session, member.id)
        if _find(views, name):
            return _t("view_name_taken", lang, name=name)
        if len(views) >= MAX_VIEWS:
            return _t("view_limit", lang, n=MAX_VIEWS)
        draft.name = name
        await session.flush()
        return _t("view_saved", lang, name=name)

    if m := _DELETE.match(plain):
        view = _find(await _named(session, member.id), _clean_name(m.group(1)))
        if view is None:
            return _t("view_not_found", lang, name=_clean_name(m.group(1)))
        await session.delete(view)
        await session.flush()
        return _t("view_deleted", lang, name=view.name)

    if m := _RUN.match(plain):
        name = _clean_name(m.group(2))
        view = _find(await _named(session, member.id), name)
        if view is None:
            # "roda ..." alone is too generic to claim; only an explicit "visão ..." is ours.
            return _t("view_not_found", lang, name=name) if m.group(1) else None
        data = sanitize_analysis_spec(view.spec)
        if data is None:
            return _t("analysis_unsupported", lang)
        spec = _spec(data)
        return format_result(spec, await run(session, member.id, spec, today_local()), lang)
    return None


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "analysis_metric_spent": _all("Gastos", "Uitgaven", "Spending", "Dépenses", "Ausgaben"),
    "analysis_metric_income": _all("Receitas", "Inkomsten", "Income", "Revenus", "Einnahmen"),
    "analysis_metric_net": _all("Saldo", "Saldo", "Balance", "Solde", "Saldo"),
    "analysis_metric_count": _all(
        "Lançamentos", "Transacties", "Transactions", "Opérations", "Buchungen"
    ),
    "analysis_metric_average": _all(
        "Média por lançamento",
        "Gemiddeld per transactie",
        "Average per transaction",
        "Moyenne par opération",
        "Durchschnitt pro Buchung",
    ),
    "analysis_period_this_month": _all(
        "este mês", "deze maand", "this month", "ce mois", "diesen Monat"
    ),
    "analysis_period_last_month": _all(
        "mês passado", "vorige maand", "last month", "le mois dernier", "letzten Monat"
    ),
    "analysis_period_last_3_months": _all(
        "últimos 3 meses",
        "laatste 3 maanden",
        "last 3 months",
        "3 derniers mois",
        "letzte 3 Monate",
    ),
    "analysis_period_last_6_months": _all(
        "últimos 6 meses",
        "laatste 6 maanden",
        "last 6 months",
        "6 derniers mois",
        "letzte 6 Monate",
    ),
    "analysis_period_this_year": _all(
        "este ano", "dit jaar", "this year", "cette année", "dieses Jahr"
    ),
    "analysis_period_last_year": _all(
        "ano passado", "vorig jaar", "last year", "l'année dernière", "letztes Jahr"
    ),
    "analysis_period_last_7_days": _all(
        "últimos 7 dias", "laatste 7 dagen", "last 7 days", "7 derniers jours", "letzte 7 Tage"
    ),
    "analysis_period_last_30_days": _all(
        "últimos 30 dias", "laatste 30 dagen", "last 30 days", "30 derniers jours", "letzte 30 Tage"
    ),
    "analysis_vs": _all(
        "Antes ({when}): {value}",
        "Eerder ({when}): {value}",
        "Before ({when}): {value}",
        "Avant ({when}) : {value}",
        "Davor ({when}): {value}",
    ),
    "analysis_empty": _all(
        "Não achei lançamentos nesse recorte.",
        "Ik vond geen transacties voor deze selectie.",
        "I found no transactions for that selection.",
        "Je n'ai trouvé aucune opération pour cette sélection.",
        "Ich habe für diese Auswahl keine Buchungen gefunden.",
    ),
    "analysis_unsupported": _all(
        "Ainda não consigo responder isso. Sei fazer: totais de gastos, receitas ou saldo, por categoria, mês ou estabelecimento, em períodos como este mês, últimos 3 meses ou este ano, e comparar com o período anterior ou com o ano passado.",
        "Dat kan ik nog niet beantwoorden. Ik kan wel: totalen van uitgaven, inkomsten of saldo, per categorie, maand of winkel, over periodes zoals deze maand, laatste 3 maanden of dit jaar, en vergelijken met de vorige periode of vorig jaar.",
        "I can't answer that yet. I can do: totals of spending, income or balance, by category, month or merchant, over periods like this month, the last 3 months or this year, and compare with the previous period or last year.",
        "Je ne peux pas encore répondre à ça. Je sais faire : totaux de dépenses, revenus ou solde, par catégorie, mois ou commerçant, sur des périodes comme ce mois, les 3 derniers mois ou cette année, et comparer avec la période précédente ou l'année dernière.",
        "Das kann ich noch nicht beantworten. Ich kann: Summen für Ausgaben, Einnahmen oder Saldo, nach Kategorie, Monat oder Händler, für Zeiträume wie diesen Monat, die letzten 3 Monate oder dieses Jahr, und mit dem vorherigen Zeitraum oder dem letzten Jahr vergleichen.",
    ),
    "analysis_limit": _all(
        'Você já fez {n} análises hoje, que é o limite diário. Amanhã tem mais. As visões salvas continuam valendo ("minhas visões").',
        'Je hebt vandaag al {n} analyses gedaan, dat is de daglimiet. Morgen kan weer. Opgeslagen weergaven blijven werken ("mijn weergaven").',
        'You\'ve already run {n} analyses today, which is the daily limit. More tomorrow. Saved views still work ("my views").',
        "Tu as déjà fait {n} analyses aujourd'hui, c'est la limite quotidienne. Demain, c'est reparti. Les vues enregistrées marchent toujours (\"mes vues\").",
        'Du hast heute schon {n} Analysen gemacht, das ist das Tageslimit. Morgen geht es weiter. Gespeicherte Ansichten funktionieren weiter ("meine Ansichten").',
    ),
    "analysis_save_hint": _all(
        "Quer guardar? Responda: salva essa visão como 'nome'.",
        "Opslaan? Antwoord: sla deze weergave op als 'naam'.",
        "Want to keep it? Reply: save this view as 'name'.",
        "Tu veux la garder ? Réponds : enregistre cette vue sous 'nom'.",
        "Speichern? Antworte: speichere diese Ansicht als 'Name'.",
    ),
    "view_saved": _all(
        "Visão '{name}' salva. Para rodar: roda '{name}'.",
        "Weergave '{name}' opgeslagen. Uitvoeren: voer uit '{name}'.",
        "View '{name}' saved. To run it: run '{name}'.",
        "Vue '{name}' enregistrée. Pour la lancer : lance '{name}'.",
        "Ansicht '{name}' gespeichert. Zum Ausführen: starte '{name}'.",
    ),
    "view_no_last": _all(
        "Não tenho nenhuma análise recente para salvar. Faça uma pergunta primeiro, por exemplo: quanto gastei com restaurante nos últimos 3 meses?",
        "Ik heb geen recente analyse om op te slaan. Stel eerst een vraag, bijvoorbeeld: hoeveel heb ik de laatste 3 maanden aan restaurants uitgegeven?",
        "I have no recent analysis to save. Ask a question first, for example: how much did I spend on restaurants in the last 3 months?",
        "Je n'ai aucune analyse récente à enregistrer. Pose d'abord une question, par exemple : combien ai-je dépensé au restaurant ces 3 derniers mois ?",
        "Ich habe keine aktuelle Analyse zum Speichern. Stell zuerst eine Frage, zum Beispiel: wie viel habe ich in den letzten 3 Monaten im Restaurant ausgegeben?",
    ),
    "view_name_taken": _all(
        "Já existe uma visão chamada '{name}'. Escolha outro nome.",
        "Er bestaat al een weergave '{name}'. Kies een andere naam.",
        "A view called '{name}' already exists. Pick another name.",
        "Une vue '{name}' existe déjà. Choisis un autre nom.",
        "Es gibt schon eine Ansicht '{name}'. Wähle einen anderen Namen.",
    ),
    "view_limit": _all(
        "Você já tem {n} visões salvas, que é o máximo. Apague uma: apaga a visão 'nome'.",
        "Je hebt al {n} opgeslagen weergaven, dat is het maximum. Verwijder er een: verwijder weergave 'naam'.",
        "You already have {n} saved views, which is the maximum. Delete one: delete view 'name'.",
        "Tu as déjà {n} vues enregistrées, c'est le maximum. Supprime-en une : supprime la vue 'nom'.",
        "Du hast schon {n} gespeicherte Ansichten, das ist das Maximum. Lösche eine: lösche Ansicht 'Name'.",
    ),
    "view_not_found": _all(
        "Não achei a visão '{name}'. Veja as suas com: minhas visões.",
        "Ik vond de weergave '{name}' niet. Bekijk ze met: mijn weergaven.",
        "I couldn't find the view '{name}'. See yours with: my views.",
        "Je n'ai pas trouvé la vue '{name}'. Vois les tiennes avec : mes vues.",
        "Ich habe die Ansicht '{name}' nicht gefunden. Deine siehst du mit: meine Ansichten.",
    ),
    "view_deleted": _all(
        "Visão '{name}' apagada.",
        "Weergave '{name}' verwijderd.",
        "View '{name}' deleted.",
        "Vue '{name}' supprimée.",
        "Ansicht '{name}' gelöscht.",
    ),
    "views_header": _all(
        "Suas visões salvas:",
        "Je opgeslagen weergaven:",
        "Your saved views:",
        "Tes vues enregistrées :",
        "Deine gespeicherten Ansichten:",
    ),
    "views_empty": _all(
        "Você ainda não tem visões salvas. Faça uma análise e responda: salva essa visão como 'nome'.",
        "Je hebt nog geen opgeslagen weergaven. Doe een analyse en antwoord: sla deze weergave op als 'naam'.",
        "You have no saved views yet. Run an analysis and reply: save this view as 'name'.",
        "Tu n'as pas encore de vues enregistrées. Fais une analyse et réponds : enregistre cette vue sous 'nom'.",
        "Du hast noch keine gespeicherten Ansichten. Mach eine Analyse und antworte: speichere diese Ansicht als 'Name'.",
    ),
}
