# ruff: noqa: E501
"""Panel assessor phrases: rules over numbers already computed, no LLM (spec §2).

A rule is a pure function ``(numbers, lang) -> Phrase | None``. ``None`` means "nothing relevant
to say" and the card shows no phrase. At most one phrase per card, and only above a minimum amount
of data (each rule documents its threshold). The suggestion is always a command to type in the
chat (straight quotes), never a product, investment or financial decision; the text describes and
compares the member's own records and never gives a cause ("because") or a piece of advice
(``FORBIDDEN_STEMS`` is checked by a test in the five languages).

The catalogue has four parts, all audited by ``scripts/message_audit.py``:

* ``PHRASES``: the sentences (one entry per rule variant, plus the empty-state hints);
* ``CHAT``: the command each phrase suggests (some take ``{name}``-like values);
* ``LABELS``: small fragments the phrases and the API compose (due dates, periods);
* ``UNITS``: singular/plural of "day" and "bill" (not audited: no placeholders).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from alfred.clock import month_name

LANGS = ("pt", "nl", "en", "fr", "de")

PHRASES: dict[str, dict[str, str]] = {
    # ── Rule 1 · month-end projection ─────────────────────────────────────────
    "balance_projection": {
        "pt": "Se as contas marcadas acontecerem e o gasto seguir o seu ritmo, você fecha o mês com cerca de {projected}. É uma estimativa, não uma garantia.",
        "nl": "Als de geplande rekeningen doorgaan en je uitgaven hetzelfde tempo houden, sluit je de maand af met ongeveer {projected}. Dit is een schatting, geen garantie.",
        "en": "If the scheduled bills happen and your spending keeps its pace, you end the month with about {projected}. This is an estimate, not a guarantee.",
        "fr": "Si les factures prévues tombent et que tes dépenses gardent leur rythme, tu finis le mois avec environ {projected}. C'est une estimation, pas une garantie.",
        "de": "Wenn die geplanten Rechnungen anfallen und deine Ausgaben im gleichen Tempo weitergehen, schließt du den Monat mit etwa {projected} ab. Das ist eine Schätzung, keine Garantie.",
    },
    "projection_insufficient": {
        "pt": "Ainda faltam dados para estimar o fim do mês: só aparecem o saldo realizado e as contas marcadas ({committed}).",
        "nl": "Er ontbreken nog gegevens om het einde van de maand te schatten: alleen het gerealiseerde saldo en de geplande rekeningen ({committed}) staan erin.",
        "en": "There is not enough data yet to estimate the month end: only the realized balance and the scheduled bills ({committed}) are shown.",
        "fr": "Il manque encore des données pour estimer la fin du mois : seuls le solde réalisé et les factures prévues ({committed}) apparaissent.",
        "de": "Für eine Schätzung zum Monatsende fehlen noch Daten: Es werden nur der bisherige Saldo und die geplanten Rechnungen ({committed}) gezeigt.",
    },
    # ── Rules 2 and 3 · budgets ───────────────────────────────────────────────
    "budget_near": {
        "pt": "{category}: {spent} de {limit} ({pct}% do orçamento).",
        "nl": "{category}: {spent} van {limit} ({pct}% van het budget).",
        "en": "{category}: {spent} of {limit} ({pct}% of the budget).",
        "fr": "{category} : {spent} sur {limit} ({pct} % du budget).",
        "de": "{category}: {spent} von {limit} ({pct} % des Budgets).",
    },
    "budget_over": {
        "pt": "{category} passou do orçamento: {spent} de {limit} ({pct}%).",
        "nl": "{category} zit boven het budget: {spent} van {limit} ({pct}%).",
        "en": "{category} is over budget: {spent} of {limit} ({pct}%).",
        "fr": "{category} dépasse le budget : {spent} sur {limit} ({pct} %).",
        "de": "{category} liegt über dem Budget: {spent} von {limit} ({pct} %).",
    },
    "budget_pace": {
        "pt": "{category} chega a 80% do orçamento em cerca de {days}, no ritmo atual.",
        "nl": "{category} bereikt over ongeveer {days} 80% van het budget, in het huidige tempo.",
        "en": "{category} reaches 80% of the budget in about {days} at the current pace.",
        "fr": "{category} atteint 80 % du budget dans environ {days}, au rythme actuel.",
        "de": "{category} erreicht bei diesem Tempo in etwa {days} 80 % des Budgets.",
    },
    # ── Rule 4 · biggest move by category, against the previous period ────────
    "categories_rise": {
        "pt": "{category} é o que mais cresceu: {delta} a mais do que {prev}.",
        "nl": "{category} is het meest gestegen: {delta} meer dan {prev}.",
        "en": "{category} grew the most: {delta} more than {prev}.",
        "fr": "{category} est ce qui a le plus augmenté : {delta} de plus que {prev}.",
        "de": "{category} ist am stärksten gestiegen: {delta} mehr als {prev}.",
    },
    "categories_fall": {
        "pt": "{category} foi o que mais caiu: {delta} a menos do que {prev}.",
        "nl": "{category} is het meest gedaald: {delta} minder dan {prev}.",
        "en": "{category} fell the most: {delta} less than {prev}.",
        "fr": "{category} est ce qui a le plus baissé : {delta} de moins que {prev}.",
        "de": "{category} ist am stärksten gesunken: {delta} weniger als {prev}.",
    },
    # ── Rule 5 · upcoming bills ───────────────────────────────────────────────
    "upcoming_ok": {
        "pt": "Contas a pagar nos próximos 30 dias: {bills}, somando {total}. Nada está atrasado.",
        "nl": "Rekeningen de komende 30 dagen: {bills}, samen {total}. Niets is te laat.",
        "en": "Bills in the next 30 days: {bills}, adding up to {total}. Nothing is overdue.",
        "fr": "Factures des 30 prochains jours : {bills}, pour un total de {total}. Rien n'est en retard.",
        "de": "Rechnungen der nächsten 30 Tage: {bills}, zusammen {total}. Nichts ist überfällig.",
    },
    "upcoming_overdue": {
        "pt": "{overdue} em atraso, somando {late_total}.",
        "nl": "{overdue} te laat, samen {late_total}.",
        "en": "{overdue} overdue, adding up to {late_total}.",
        "fr": "{overdue} en retard, pour un total de {late_total}.",
        "de": "{overdue} überfällig, zusammen {late_total}.",
    },
    # ── Rule 6 · who owes me ──────────────────────────────────────────────────
    "owed_open": {
        "pt": "{person} deve {amount} a você há {days}.",
        "nl": "{person} is je {amount} schuldig, al {days}.",
        "en": "{person} has owed you {amount} for {days}.",
        "fr": "{person} te doit {amount} depuis {days}.",
        "de": "{person} schuldet dir seit {days} {amount}.",
    },
    # ── Rule 7 · days in the black ────────────────────────────────────────────
    "blue_days": {
        "pt": "{blue} de {elapsed} dias no azul; a maior sequência foi de {longest}.",
        "nl": "{blue} van {elapsed} dagen in de plus; de langste reeks was {longest}.",
        "en": "{blue} of {elapsed} days in the black; the longest streak was {longest}.",
        "fr": "{blue} jours sur {elapsed} dans le vert ; la plus longue série a duré {longest}.",
        "de": "{blue} von {elapsed} Tagen im Plus; die längste Serie dauerte {longest}.",
    },
    # exactly one day in the black: only French changes ("1 jour", not "1 jours")
    "blue_days_one": {
        "pt": "{blue} de {elapsed} dias no azul; a maior sequência foi de {longest}.",
        "nl": "{blue} van {elapsed} dagen in de plus; de langste reeks was {longest}.",
        "en": "{blue} of {elapsed} days in the black; the longest streak was {longest}.",
        "fr": "{blue} jour sur {elapsed} dans le vert ; la plus longue série a duré {longest}.",
        "de": "{blue} von {elapsed} Tagen im Plus; die längste Serie dauerte {longest}.",
    },
    # ── Rule 8 · the list ─────────────────────────────────────────────────────
    "largest_entry": {
        "pt": "{count} lançamentos {period}. O maior gasto foi {merchant}, {amount}.",
        "nl": "{count} boekingen {period}. De grootste uitgave was {merchant}, {amount}.",
        "en": "{count} entries {period}. The largest expense was {merchant}, {amount}.",
        "fr": "{count} opérations {period}. La plus grosse dépense était {merchant}, {amount}.",
        "de": "{count} Buchungen {period}. Die größte Ausgabe war {merchant}, {amount}.",
    },
    # ── Rule 9 · biggest expenses ─────────────────────────────────────────────
    "top_share": {
        "pt": "{merchant} é {pct}% de tudo que saiu.",
        "nl": "{merchant} is {pct}% van alles wat eruit ging.",
        "en": "{merchant} is {pct}% of everything that went out.",
        "fr": "{merchant} représente {pct} % de tout ce qui est sorti.",
        "de": "{merchant} macht {pct} % von allem aus, was ausgegeben wurde.",
    },
    # ── Rule 10 · month against month ─────────────────────────────────────────
    "mom_driver": {
        "pt": "{category} subiu {delta} e explica {share}.",
        "nl": "{category} steeg met {delta} en verklaart {share}.",
        "en": "{category} rose by {delta} and accounts for {share}.",
        "fr": "{category} a augmenté de {delta} et explique {share}.",
        "de": "{category} stieg um {delta} und macht {share} aus.",
    },
    # ── Rule 11 · fixed against variable ──────────────────────────────────────
    "fixed_variable": {
        "pt": "Dos {total} que saíram, {fixed} são contas fixas e {variable} são gastos variáveis.",
        "nl": "Van de {total} die eruit ging, is {fixed} vast en {variable} variabel.",
        "en": "Of the {total} that went out, {fixed} is fixed bills and {variable} is variable spending.",
        "fr": "Sur les {total} sortis, {fixed} sont des charges fixes et {variable} des dépenses variables.",
        "de": "Von den {total}, die ausgegeben wurden, sind {fixed} Fixkosten und {variable} variable Ausgaben.",
    },
    # ── Rule 12 · spending per day ────────────────────────────────────────────
    "busiest_day": {
        "pt": "O dia {day} concentrou o maior gasto do período ({amount}). Dias sem gasto: {quiet}.",
        "nl": "Op {day} werd het meest uitgegeven ({amount}). Dagen zonder uitgaven: {quiet}.",
        "en": "{day} had the biggest spending of the period ({amount}). Days with no spending: {quiet}.",
        "fr": "Le {day} concentre la plus grosse dépense de la période ({amount}). Jours sans dépense : {quiet}.",
        "de": "Am {day} wurde am meisten ausgegeben ({amount}). Tage ohne Ausgaben: {quiet}.",
    },
    # ── Empty states: they teach the chat sentence ────────────────────────────
    "empty_ledger": {
        "pt": "Ainda não há lançamentos neste período. Registre um gasto pelo chat e ele aparece aqui.",
        "nl": "Er zijn nog geen boekingen in deze periode. Leg een uitgave vast in de chat en ze verschijnt hier.",
        "en": "There are no entries in this period yet. Record an expense in the chat and it shows up here.",
        "fr": "Il n'y a pas encore d'opérations sur cette période. Enregistre une dépense dans le chat et elle apparaît ici.",
        "de": "In diesem Zeitraum gibt es noch keine Buchungen. Erfasse eine Ausgabe im Chat, dann erscheint sie hier.",
    },
    "empty_budgets": {
        "pt": "Você ainda não definiu nenhum orçamento. Defina um pelo chat e o painel acompanha.",
        "nl": "Je hebt nog geen budget ingesteld. Stel er een in via de chat en het dashboard volgt het.",
        "en": "You have not set any budget yet. Set one in the chat and the panel tracks it.",
        "fr": "Tu n'as pas encore défini de budget. Définis-en un dans le chat et le tableau le suit.",
        "de": "Du hast noch kein Budget festgelegt. Lege im Chat eines fest, dann verfolgt das Panel es.",
    },
    "empty_upcoming": {
        "pt": "Nenhuma conta marcada para os próximos 30 dias. Cadastre uma conta fixa pelo chat.",
        "nl": "Geen rekeningen gepland voor de komende 30 dagen. Voeg een vaste last toe via de chat.",
        "en": "No bills scheduled for the next 30 days. Add a recurring bill in the chat.",
        "fr": "Aucune facture prévue pour les 30 prochains jours. Ajoute une charge fixe dans le chat.",
        "de": "Keine Rechnungen für die nächsten 30 Tage geplant. Lege im Chat eine Fixkosten-Position an.",
    },
    "empty_recurring": {
        "pt": "Você ainda não cadastrou contas fixas nem parcelas. Cadastre pelo chat.",
        "nl": "Je hebt nog geen vaste lasten of termijnen toegevoegd. Doe dat via de chat.",
        "en": "You have not added any recurring bills or instalments yet. Add them in the chat.",
        "fr": "Tu n'as pas encore ajouté de charges fixes ni de mensualités. Ajoute-les dans le chat.",
        "de": "Du hast noch keine Fixkosten oder Raten angelegt. Lege sie im Chat an.",
    },
    "empty_owed": {
        "pt": "Ninguém está devendo a você no momento. Registre pelo chat quando alguém ficar devendo.",
        "nl": "Op dit moment is niemand je iets schuldig. Leg het vast in de chat als iemand je iets schuldig wordt.",
        "en": "Nobody owes you anything right now. Record it in the chat when someone does.",
        "fr": "Personne ne te doit rien pour le moment. Enregistre-le dans le chat quand quelqu'un te doit.",
        "de": "Im Moment schuldet dir niemand etwas. Erfasse es im Chat, sobald jemand dir etwas schuldet.",
    },
}

# Commands the phrases suggest. Every one is deterministic in the chat router (no LLM involved)
# except ``balance_projection`` and ``log_expense``; a test runs the others through the router.
CHAT: dict[str, dict[str, str]] = {
    "balance_projection": {
        "pt": '"como fica meu mês?"',
        "nl": '"hoe ziet mijn maand eruit?"',
        "en": '"how does my month look?"',
        "fr": '"comment se présente mon mois ?"',
        "de": '"wie sieht mein Monat aus?"',
    },
    "budget": {
        "pt": '"meus orçamentos"',
        "nl": '"mijn budgetten"',
        "en": '"my budgets"',
        "fr": '"mon budget"',
        "de": '"meine Budgets"',
    },
    "mom": {
        "pt": '"mês contra mês"',
        "nl": '"maand tegen maand"',
        "en": '"month over month"',
        "fr": '"mois contre mois"',
        "de": '"Monat gegen Monat"',
    },
    "upcoming": {
        "pt": '"paguei {name}"',
        "nl": '"betaald {name}"',
        "en": '"paid {name}"',
        "fr": '"j\'ai payé {name}"',
        "de": '"ich habe {name} bezahlt"',
    },
    "owed": {
        "pt": '"{person} pagou {amount}"',
        "nl": '"{person} heeft {amount} betaald"',
        "en": '"{person} paid me {amount}"',
        "fr": '"{person} m\'a payé {amount}"',
        "de": '"{person} hat mir {amount} bezahlt"',
    },
    "blue_days": {
        "pt": '"dias no azul"',
        "nl": '"dagen in de plus"',
        "en": '"days in the black"',
        "fr": '"jours dans le vert"',
        "de": '"Tage im Plus"',
    },
    "entries": {
        "pt": '"últimas 10 despesas"',
        "nl": '"laatste 10 uitgaven"',
        "en": '"last 10 expenses"',
        "fr": '"dernières 10 dépenses"',
        "de": '"letzte 10 Ausgaben"',
    },
    "top": {
        "pt": '"top categorias"',
        "nl": '"top categorieën"',
        "en": '"top categories"',
        "fr": '"top catégories"',
        "de": '"top Kategorien"',
    },
    "fixed": {
        "pt": '"contas fixas"',
        "nl": '"vaste lasten"',
        "en": '"recurring bills"',
        "fr": '"charges fixes"',
        "de": '"Fixkosten"',
    },
    "log_expense": {
        "pt": '"gastei 25 no mercado"',
        "nl": '"25 euro uitgegeven bij de supermarkt"',
        "en": '"spent 25 at the supermarket"',
        "fr": '"j\'ai dépensé 25 au supermarché"',
        "de": '"25 Euro im Supermarkt ausgegeben"',
    },
    "set_budget": {
        "pt": '"orçamento restaurante 150"',
        "nl": '"budget restaurant 150"',
        "en": '"budget restaurant 150"',
        "fr": '"budget restaurant 150"',
        "de": '"Budget Restaurant 150"',
    },
    "add_recurring": {
        "pt": '"aluguel 1150 todo dia 1"',
        "nl": '"huur 1150 elke maand"',
        "en": '"rent 1150 every month"',
        "fr": '"loyer 1150 chaque mois"',
        "de": '"Miete 1150 jeden Monat"',
    },
    "add_owed": {
        "pt": '"Marta me deve 34,50"',
        "nl": '"Marta is mij 34,50 schuldig"',
        "en": '"Marta owes me 34,50"',
        "fr": '"Marta me doit 34,50"',
        "de": '"Marta schuldet mir 34,50"',
    },
}

# Fragments that phrases and API items compose. ``{month}``, ``{days}`` are filled by the caller.
LABELS: dict[str, dict[str, str]] = {
    "due_today": {"pt": "vence hoje", "nl": "vervalt vandaag", "en": "due today", "fr": "échéance aujourd'hui", "de": "heute fällig"},
    "due_tomorrow": {"pt": "vence amanhã", "nl": "vervalt morgen", "en": "due tomorrow", "fr": "échéance demain", "de": "morgen fällig"},
    "due_in": {"pt": "vence em {days}", "nl": "vervalt over {days}", "en": "due in {days}", "fr": "échéance dans {days}", "de": "fällig in {days}"},
    "overdue_by": {"pt": "atrasada há {days}", "nl": "{days} te laat", "en": "overdue by {days}", "fr": "en retard de {days}", "de": "seit {days} überfällig"},
    "in_month": {"pt": "em {month}", "nl": "in {month}", "en": "in {month}", "fr": "en {month}", "de": "im {month}"},
    "this_period": {"pt": "no período", "nl": "in de periode", "en": "in the period", "fr": "sur la période", "de": "im Zeitraum"},
    "prev_period": {"pt": "no período anterior", "nl": "in de vorige periode", "en": "in the previous period", "fr": "dans la période précédente", "de": "im vorherigen Zeitraum"},
    "share_most": {"pt": "a maior parte da diferença", "nl": "het grootste deel van het verschil", "en": "most of the difference", "fr": "l'essentiel de l'écart", "de": "den größten Teil des Unterschieds"},
    "share_almost_all": {"pt": "quase toda a diferença", "nl": "bijna het hele verschil", "en": "almost all of the difference", "fr": "presque tout l'écart", "de": "fast den ganzen Unterschied"},
}  # fmt: skip

UNITS: dict[str, dict[str, tuple[str, str]]] = {
    "day": {
        "pt": ("dia", "dias"),
        "nl": ("dag", "dagen"),
        "en": ("day", "days"),
        "fr": ("jour", "jours"),
        "de": ("Tag", "Tage"),
    },
    "bill": {
        "pt": ("conta", "contas"),
        "nl": ("rekening", "rekeningen"),
        "en": ("bill", "bills"),
        "fr": ("facture", "factures"),
        "de": ("Rechnung", "Rechnungen"),
    },
}

# Words a phrase must never contain: the panel describes the member's own records, it does not
# tell anyone what to buy, sell, invest in or cut, and it never gives a cause.
FORBIDDEN_STEMS: dict[str, tuple[str, ...]] = {
    "pt": ("invista", "compre", "venda", "aplique", "resgate", "recomendo", "porque", "por causa", "economize", "reduza", "evite", "deveria", "aconselho"),
    "nl": ("investeer", "koop ", "verkoop", "ik raad", "omdat", "bezuinig", "je moet", "je zou moeten", "ik adviseer"),
    "en": ("invest in", "buy ", "sell ", "i recommend", "because", "you should", "i advise", "cut back", "reduce your"),
    "fr": ("investis", "achète", "vends", "je recommande", "parce que", "tu devrais", "je conseille", "réduis", "évite"),
    "de": ("investiere", "kaufe", "verkaufe", "ich empfehle", "weil", "du solltest", "ich rate", "vermeide", "reduziere"),
}  # fmt: skip

# Thresholds of the rules (minimum data before a sentence is allowed).
MIN_BLUE_DAYS_ELAPSED = 7
MIN_ENTRIES_LARGEST = 5
MIN_ENTRIES_TOP_SHARE = 3
TOP_SHARE_PCT = 30
MIN_MOVE_EUR = 10
MIN_MOVE_PCT = 20
MIN_PREV_ENTRIES = 3
DRIVER_SHARE_PCT = 50
DRIVER_ALMOST_ALL_PCT = 80
MIN_OWED_DAYS = 7
OWED_ATTENTION_DAYS = 30
MIN_ENTRIES_BUSIEST = 5
MIN_DAYS_BUSIEST = 7
PACE_MAX_DAYS = 7


@dataclass(frozen=True)
class Phrase:
    key: str
    text: str
    chat: str | None
    severity: str  # "info" | "attention"


def _lang(lang: str | None) -> str:
    return lang if lang in LANGS else "pt"


def fmt_eur(value: float | Decimal, lang: str) -> str:
    """European money in every language: ``€ 1.234,56``; negatives ``− € 5,00``.

    ``lang`` is accepted for symmetry with the rules; the format is the same in all five.
    """
    rounded = round(float(value), 2)
    cents = f"{abs(rounded):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"{'− ' if rounded < 0 else ''}€ {cents}"


def unit(kind: str, n: int, lang: str) -> str:
    """``3 dias``, ``1 dia``: the count with the right singular/plural."""
    one, many = UNITS[kind][_lang(lang)]
    return f"{n} {one if abs(n) == 1 else many}"


def label(key: str, lang: str, **values) -> str:
    return LABELS[key][_lang(lang)].format(**values)


def month_word(day: date, lang: str) -> str:
    """Month name as it reads in a sentence: lower case in pt/nl/fr, capitalised in en/de."""
    name = month_name(day, _lang(lang))
    return name if _lang(lang) in ("en", "de") else name.lower()


def period_fragment(first_day: date | None, lang: str) -> str:
    """ "em outubro" for a calendar month, "no período" for a free range (``None``)."""
    if first_day is None:
        return label("this_period", lang)
    return label("in_month", lang, month=month_word(first_day, lang))


def prev_fragment(first_day: date | None, lang: str) -> str:
    """The compared period inside a sentence: "em setembro" or "no período anterior"."""
    if first_day is None:
        return label("prev_period", lang)
    return label("in_month", lang, month=month_word(first_day, lang))


def due_label(days: int, lang: str) -> str:
    """ "vence em 4 dias" / "vence hoje" / "atrasada há 2 dias"."""
    if days < 0:
        return label("overdue_by", lang, days=unit("day", -days, lang))
    if days == 0:
        return label("due_today", lang)
    if days == 1:
        return label("due_tomorrow", lang)
    return label("due_in", lang, days=unit("day", days, lang))


def _safe(text: str) -> str:
    """A value that goes inside a quoted chat command: no quotes, no line breaks."""
    return " ".join(text.replace('"', "").replace("“", "").replace("”", "").split())


def render(
    key: str,
    lang: str,
    chat_key: str | None = None,
    severity: str = "info",
    chat_args: dict[str, str] | None = None,
    **values,
) -> Phrase:
    lang = _lang(lang)
    chat = CHAT.get(chat_key or key, {}).get(lang)
    if chat and chat_args:
        chat = chat.format(**{k: _safe(v) for k, v in chat_args.items()})
    text = PHRASES[key][lang].format(**values)
    # A sentence built from a merchant's name ("renda é 99% ...") still starts with a capital.
    return Phrase(key=key, text=text[:1].upper() + text[1:], chat=chat, severity=severity)


def _pct(part: float, whole: float) -> int:
    return int((Decimal(str(part)) * 100 / Decimal(str(whole))).quantize(Decimal(1), ROUND_HALF_UP))


def budget_pct(spent: Decimal | float, limit: Decimal | float) -> int:
    """Percent of a budget used: whole percent, half up, kept inside its level's band.

    The level (80 / 100) is decided on the exact amounts, as the chat alerts do, and the number
    never contradicts it: 99.60 of 100 reads 99 % (not "100 %, over"), 79.50 reads 79 % (not "80 %").
    """
    spent, limit = Decimal(str(spent)), Decimal(str(limit))
    if limit <= 0:
        return 0
    pct = int((spent * 100 / limit).quantize(Decimal(1), ROUND_HALF_UP))
    if spent >= limit:
        return max(pct, 100)
    if spent * 100 >= limit * 80:
        return min(max(pct, 80), 99)
    return min(pct, 79)


# ── The 12 rules ──────────────────────────────────────────────────────────────


def rule_balance_vs_projection(
    balance: float,
    projected: float | None,
    lang: str,
    *,
    insufficient: bool = False,
    committed: float = 0.0,
) -> Phrase | None:
    """Rule 1. With a projection, the estimate sentence; with ``insufficient`` (too little history)
    the sentence that says data is missing; otherwise nothing. Never invents a value."""
    if projected is None:
        if not insufficient:
            return None
        return render(
            "projection_insufficient",
            lang,
            chat_key="log_expense",
            committed=fmt_eur(committed, lang),
        )
    return render("balance_projection", lang, projected=fmt_eur(projected, lang))


def rule_budget_over(category: str, spent: float, limit: float, lang: str) -> Phrase | None:
    """Rule 2. Silent below 80 % of the limit; "info" from 80 %, "attention" from 100 %."""
    if limit <= 0:
        return None
    pct = budget_pct(spent, limit)
    if pct < 80:
        return None
    over = pct >= 100
    return render(
        "budget_over" if over else "budget_near",
        lang,
        chat_key="budget",
        severity="attention" if over else "info",
        category=category,
        spent=fmt_eur(spent, lang),
        limit=fmt_eur(limit, lang),
        pct=pct,
    )


def rule_budget_pace(
    category: str, spent: float, limit: float, days_to_80: int | None, lang: str
) -> Phrase | None:
    """Rule 3. Below 80 % and, at the current pace, 80 % is reached within a week."""
    if days_to_80 is None or limit <= 0 or spent >= limit * 0.8:
        return None
    if not 0 < days_to_80 <= PACE_MAX_DAYS:
        return None
    return render(
        "budget_pace",
        lang,
        chat_key="budget",
        category=category,
        days=unit("day", days_to_80, lang),
    )


def rule_category_move(
    moves: list[tuple[str, float, float]], prev_entries: int, prev_when: str, lang: str
) -> Phrase | None:
    """Rule 4. The category that rose the most against the previous period; if none rose enough,
    the one that fell the most. A move needs at least 10 EUR and 20 % (any rise from zero counts),
    and the previous period must have at least 3 entries (otherwise it is no base)."""
    if prev_entries < MIN_PREV_ENTRIES:
        return None

    def big(cur: float, prev: float) -> bool:
        diff = abs(cur - prev)
        return diff >= MIN_MOVE_EUR and (prev <= 0 or diff / prev * 100 >= MIN_MOVE_PCT)

    rises = [(c - p, name) for name, c, p in moves if c > p and big(c, p)]
    if rises:
        delta, name = max(rises)
        return render(
            "categories_rise",
            lang,
            chat_key="mom",
            category=name,
            delta=fmt_eur(delta, lang),
            prev=prev_when,
        )
    falls = [(p - c, name) for name, c, p in moves if p > c and big(c, p)]
    if falls:
        delta, name = max(falls)
        return render(
            "categories_fall",
            lang,
            chat_key="mom",
            category=name,
            delta=fmt_eur(delta, lang),
            prev=prev_when,
        )
    return None


def rule_upcoming(
    n_pay: int,
    total_pay: float,
    n_overdue: int,
    total_overdue: float,
    first_name: str | None,
    lang: str,
) -> Phrase | None:
    """Rule 5. Overdue bills ("attention") come first; else the sum of the next 30 days. Nothing
    when there is nothing to say."""
    args = {"name": first_name or ""}
    if n_overdue > 0:
        return render(
            "upcoming_overdue",
            lang,
            chat_key="upcoming" if first_name else "fixed",
            chat_args=args if first_name else None,
            severity="attention",
            overdue=unit("bill", n_overdue, lang),
            late_total=fmt_eur(total_overdue, lang),
        )
    if n_pay <= 0:
        return None
    return render(
        "upcoming_ok",
        lang,
        chat_key="upcoming" if first_name else "fixed",
        chat_args=args if first_name else None,
        bills=unit("bill", n_pay, lang),
        total=fmt_eur(total_pay, lang),
    )


def rule_owed(person: str, amount: float, days: int, lang: str) -> Phrase | None:
    """Rule 6. Only for a debt open for a week or more; "attention" from 30 days."""
    if amount <= 0 or days < MIN_OWED_DAYS or not person:
        return None
    plain = f"{amount:.2f}".replace(".", ",")
    return render(
        "owed_open",
        lang,
        chat_key="owed",
        chat_args={"person": person, "amount": plain},
        severity="attention" if days >= OWED_ATTENTION_DAYS else "info",
        person=person,
        amount=fmt_eur(amount, lang),
        days=unit("day", days, lang),
    )


def rule_blue_days(blue: int, elapsed: int, longest: int, lang: str) -> Phrase | None:
    """Rule 7. Needs a week of the period behind it."""
    if elapsed < MIN_BLUE_DAYS_ELAPSED:
        return None
    return render(
        "blue_days_one" if blue == 1 else "blue_days",
        lang,
        chat_key="blue_days",
        blue=blue,
        elapsed=elapsed,
        longest=unit("day", longest, lang),
    )


def rule_largest_entry(
    count: int, merchant: str | None, amount: float, period: str, lang: str
) -> Phrase | None:
    """Rule 8. At least 5 entries in the list and a name for the biggest."""
    if count < MIN_ENTRIES_LARGEST or not merchant or amount <= 0:
        return None
    return render(
        "largest_entry",
        lang,
        chat_key="entries",
        count=count,
        period=period,
        merchant=merchant,
        amount=fmt_eur(amount, lang),
    )


def rule_top_share(
    merchant: str | None, amount: float, total: float, entries: int, lang: str
) -> Phrase | None:
    """Rule 9. The biggest expense is at least 30 % of everything spent (3 entries or more)."""
    if entries < MIN_ENTRIES_TOP_SHARE or not merchant or total <= 0:
        return None
    pct = _pct(amount, total)
    if pct < TOP_SHARE_PCT:
        return None
    return render("top_share", lang, chat_key="top", merchant=merchant, pct=pct)


def rule_mom_driver(
    category: str | None, rise: float, total_delta: float, lang: str
) -> Phrase | None:
    """Rule 10. Spending rose and one category explains at least half of the rise."""
    if not category or total_delta < MIN_MOVE_EUR or rise < MIN_MOVE_EUR:
        return None
    share = rise / total_delta * 100
    if share < DRIVER_SHARE_PCT:
        return None
    key = "share_almost_all" if share >= DRIVER_ALMOST_ALL_PCT else "share_most"
    return render(
        "mom_driver",
        lang,
        chat_key="mom",
        category=category,
        delta=fmt_eur(rise, lang),
        share=label(key, lang),
    )


def rule_fixed_variable(fixed: float, variable: float, lang: str) -> Phrase | None:
    """Rule 11. Both parts exist (a split of 100 % and 0 % says nothing)."""
    if fixed <= 0 or variable <= 0:
        return None
    return render(
        "fixed_variable",
        lang,
        chat_key="fixed",
        total=fmt_eur(fixed + variable, lang),
        fixed=fmt_eur(fixed, lang),
        variable=fmt_eur(variable, lang),
    )


def rule_busiest_day(
    day: date | None, amount: float, quiet_days: int, elapsed: int, entries: int, lang: str
) -> Phrase | None:
    """Rule 12. A week of the period and 5 entries behind it."""
    if day is None or amount <= 0 or elapsed < MIN_DAYS_BUSIEST or entries < MIN_ENTRIES_BUSIEST:
        return None
    return render(
        "busiest_day",
        lang,
        chat_key="entries",
        day=f"{day.day:02d}/{day.month:02d}",
        amount=fmt_eur(amount, lang),
        quiet=quiet_days,
    )


RULES: dict[str, Callable[..., Phrase | None]] = {
    "balance_vs_projection": rule_balance_vs_projection,
    "budget_over": rule_budget_over,
    "budget_pace": rule_budget_pace,
    "category_move": rule_category_move,
    "upcoming": rule_upcoming,
    "owed": rule_owed,
    "blue_days": rule_blue_days,
    "largest_entry": rule_largest_entry,
    "top_share": rule_top_share,
    "mom_driver": rule_mom_driver,
    "fixed_variable": rule_fixed_variable,
    "busiest_day": rule_busiest_day,
}

# Empty-state card kind -> (phrase key, chat key).
EMPTY: dict[str, tuple[str, str]] = {
    "ledger": ("empty_ledger", "log_expense"),
    "budgets": ("empty_budgets", "set_budget"),
    "upcoming": ("empty_upcoming", "add_recurring"),
    "recurring": ("empty_recurring", "add_recurring"),
    "owed": ("empty_owed", "add_owed"),
}


def empty_hint(kind: str, lang: str) -> Phrase:
    """The sentence an empty card shows: what is missing and the chat sentence that fills it."""
    key, chat_key = EMPTY[kind]
    return render(key, lang, chat_key=chat_key)
