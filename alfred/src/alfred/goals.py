# ruff: noqa: E501
"""Goals the way the member sees them: financial goals apart from routine goals, with day marks.

* A **financial goal** ("juntar 5000 euros até dezembro", "reserva de emergência 5000") is measured in
  money. Its progress is what the member says they put aside: "guardei 300 na reserva" adds,
  "já tenho 1200 na reserva" sets the balance. It never depends on the month's income and spending.
* A **routine goal** ("meditar todos os dias", "correr 10 km") is measured in days with a check-in:
  the week is drawn Monday to Sunday with one mark per day, like the trip packing list.

Rule-based, no LLM. The panel reads the same numbers through ``progress_for``.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.models import Goal, GoalContribution, HabitLog, Member
from alfred.parsing import strip_accents, to_amount
from alfred.training import Reply
from alfred.validation import MAX_AMOUNT

_MONEY_WORDS = re.compile(
    r"\b(?:juntar|poupar|poupanca|guardar|economizar|economia|reserva|fundo|save|saving|savings|emergency|sparen|spaar|sparen|noodfonds|reserve|epargn\w*|economis\w*|fonds|sparziel|notgroschen)\b"
)
_EURO = re.compile(r"€|\beuros?\b|\beur\b")
_AMOUNT = re.compile(r"(?:€\s*)?(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(k|mil)?\b")
_MONTHS = {
    "pt": ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"),
    "en": ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"),
    "nl": ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"),
    "fr": ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre", "novembre", "decembre"),
    "de": ("januar", "februar", "marz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "dezember"),
}  # fmt: skip
_MONTH_INDEX = {name: i + 1 for names in _MONTHS.values() for i, name in enumerate(names)}
_UNTIL = re.compile(
    r"\b(?:ate|ate o fim de|ate fim de|by|until|before|voor|tot|avant|jusqu'?a|bis|vor)\s+(?:(?:o\s+)?dia\s+)?(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})(?:[/.-](?P<y>\d{2,4}))?\b"
)
_UNTIL_MONTH = re.compile(
    rf"\b(?:ate|ate o fim de|ate fim de|by|until|before|end of|voor|tot|avant|jusqu'?a|bis|vor|en|em|in)\s+(?P<mon>{'|'.join(sorted(_MONTH_INDEX, key=len, reverse=True))})(?:\s+(?:de\s+)?(?P<y>\d{{4}}))?\b"
)
MAX_GOALS_SHOWN = 10


def _plain(text: str) -> str:
    return strip_accents((text or "").lower())


def _amount_in(text: str) -> float | None:
    """The first sensible amount in a goal title ("juntar 5.000 euros", "reserva 5k")."""
    plain = _plain(text)
    plain = _UNTIL.sub(" ", plain)  # a deadline date is not the target
    for m in _AMOUNT.finditer(plain):
        whole = m.group(1).replace(".", "").replace(" ", "")
        value = float(whole + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3):
            value *= 1000
        if 0 < value < MAX_AMOUNT:
            return round(value, 2)
    return None


def is_money_title(title: str) -> bool:
    plain = _plain(title)
    if _amount_in(plain) is None:
        return False
    return bool(_EURO.search(plain) or _MONEY_WORDS.search(plain))


def is_money_goal(goal: Goal) -> bool:
    unit = (goal.target_unit or "").lower()
    return "eur" in unit or "€" in unit or is_money_title(goal.title)


def target_amount(goal: Goal) -> float | None:
    raw = (goal.target_value or "").replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if 0 < value < 1e9:
        return round(value, 2)
    return _amount_in(goal.title) if is_money_goal(goal) else None


def deadline_of(goal: Goal, today: date) -> date | None:
    return goal.deadline or parse_deadline(goal.title, today)


def parse_deadline(title: str, today: date) -> date | None:
    plain = _plain(title)
    if m := _UNTIL.search(plain):
        year = int(m.group("y")) if m.group("y") else today.year
        year += 2000 if year < 100 else 0
        try:
            found = date(year, int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
        if not m.group("y") and found < today:
            found = found.replace(year=year + 1)
        return found
    if m := _UNTIL_MONTH.search(plain):
        month = _MONTH_INDEX[m.group("mon")]
        year = int(m.group("y")) if m.group("y") else today.year
        end = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        if not m.group("y") and end < today:
            end = (date(year + 1 + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        return end
    return None


def prepare_new_goal(goal: Goal, today: date) -> None:
    """Fill target and deadline from the words of a new financial goal."""
    if not is_money_title(goal.title):
        return
    amount = _amount_in(goal.title)
    if amount:
        goal.target_value = f"{amount:.2f}".rstrip("0").rstrip(".")
        goal.target_unit = "EUR"
    goal.deadline = parse_deadline(goal.title, today)


# ── progress ──────────────────────────────────────────────────────────────────


async def saved_amounts(
    session: AsyncSession, member_id: uuid.UUID, goal_ids: list[uuid.UUID]
) -> dict[uuid.UUID, float]:
    """Per goal: the last ``set`` balance plus every ``add`` after it."""
    if not goal_ids:
        return {}
    rows = (
        await session.execute(
            select(GoalContribution.goal_id, GoalContribution.kind, GoalContribution.amount)
            .where(GoalContribution.member_id == member_id, GoalContribution.goal_id.in_(goal_ids))
            .order_by(GoalContribution.created_at, GoalContribution.id)
            .limit(5000)
        )
    ).all()
    out: dict[uuid.UUID, float] = dict.fromkeys(goal_ids, 0.0)
    for gid, kind, amount in rows:
        out[gid] = float(amount) if kind == "set" else out[gid] + float(amount)
    return {k: round(max(v, 0.0), 2) for k, v in out.items()}


def progress_for(goal: Goal, saved: float, today: date) -> dict:
    target = target_amount(goal)
    due = deadline_of(goal, today)
    out: dict = {
        "saved": round(saved, 2),
        "target_amount": target,
        "deadline": due.isoformat() if due else None,
    }
    if target:
        out["saved_pct"] = min(int(saved * 100 / target), 100)
        out["remaining"] = round(max(target - saved, 0.0), 2)
        out["done"] = saved >= target
        if due and due >= today and saved < target:
            months = max((due.year - today.year) * 12 + due.month - today.month, 1)
            out["per_month"] = round((target - saved) / months, 2)
    return out


async def money_goals(session: AsyncSession, member_id: uuid.UUID) -> list[Goal]:
    goals = (
        (
            await session.execute(
                select(Goal)
                .where(Goal.member_id == member_id, Goal.active.is_(True))
                .order_by(Goal.created_at)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [g for g in goals if is_money_goal(g)]


# ── week marks for routine goals ─────────────────────────────────────────────

DONE, MISSED, LATER = "✅", "⬜", "▫️"


def week_days(today: date) -> list[date]:
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


def marks(done_days: set[date], today: date) -> str:
    return "".join(
        DONE if d in done_days else (LATER if d > today else MISSED) for d in week_days(today)
    )


async def week_logs(
    session: AsyncSession, member_id: uuid.UUID, goal_ids: list[uuid.UUID], today: date
) -> dict[uuid.UUID, set[date]]:
    if not goal_ids:
        return {}
    days = week_days(today)
    rows = (
        await session.execute(
            select(HabitLog.goal_id, HabitLog.log_date).where(
                HabitLog.member_id == member_id,
                HabitLog.goal_id.in_(goal_ids),
                HabitLog.log_date >= days[0],
                HabitLog.log_date <= days[-1],
            )
        )
    ).all()
    out: dict[uuid.UUID, set[date]] = {gid: set() for gid in goal_ids}
    for gid, d in rows:
        out[gid].add(d)
    return out


async def marks_line(
    session: AsyncSession, member: Member, goal: Goal, lang: str, today: date
) -> str:
    """ "\n✅✅⬜✅⬜▫️▫️ · 3 de 7 esta semana" for a goal that was just logged."""
    await session.flush()
    done = (await week_logs(session, member.id, [goal.id], today))[goal.id]
    return "\n" + _t("goal_week_line", lang, marks=marks(done, today), n=len(done))


# ── strings ───────────────────────────────────────────────────────────────────


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


def _t(key: str, lang: str, **kw) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


STRINGS: dict[str, dict[str, str]] = {
    "goal_week_line": _all(
        "{marks} · {n} de 7 dias esta semana",
        "{marks} · {n} van 7 dagen deze week",
        "{marks} · {n} of 7 days this week",
        "{marks} · {n} sur 7 jours cette semaine",
        "{marks} · {n} von 7 Tagen diese Woche",
    ),
    "goals_title": _all(
        "Suas metas ({n}):",
        "Je doelen ({n}):",
        "Your goals ({n}):",
        "Tes objectifs ({n}) :",
        "Deine Ziele ({n}):",
    ),
    "goals_money_head": _all(
        "Metas financeiras",
        "Financiële doelen",
        "Financial goals",
        "Objectifs financiers",
        "Finanzziele",
    ),
    "goals_routine_head": _all(
        "Rotina: {done} de {n} feitas hoje",
        "Routine: {done} van {n} vandaag gedaan",
        "Routine: {done} of {n} done today",
        "Routine : {done} sur {n} faits aujourd'hui",
        "Routine: {done} von {n} heute erledigt",
    ),
    "goals_legend": _all(
        "✅ feito · ⬜ faltou · ▫️ ainda por vir (segunda a domingo)",
        "✅ gedaan · ⬜ gemist · ▫️ nog te komen (maandag t/m zondag)",
        "✅ done · ⬜ missed · ▫️ still to come (Monday to Sunday)",
        "✅ fait · ⬜ manqué · ▫️ à venir (lundi à dimanche)",
        "✅ erledigt · ⬜ verpasst · ▫️ noch offen (Montag bis Sonntag)",
    ),
    "goal_money_row": _all(
        "• {title}: {saved} de {target} ({pct}%)\n  {bar}{rest}",
        "• {title}: {saved} van {target} ({pct}%)\n  {bar}{rest}",
        "• {title}: {saved} of {target} ({pct}%)\n  {bar}{rest}",
        "• {title} : {saved} sur {target} ({pct} %)\n  {bar}{rest}",
        "• {title}: {saved} von {target} ({pct} %)\n  {bar}{rest}",
    ),
    "goal_money_rest": _all(
        " faltam {left}", " nog {left}", " {left} to go", " il reste {left}", " noch {left}"
    ),
    "goal_money_due": _all(
        " · até {due}, cerca de {per} por mês",
        " · tot {due}, ongeveer {per} per maand",
        " · by {due}, about {per} a month",
        " · d'ici le {due}, environ {per} par mois",
        " · bis {due}, etwa {per} pro Monat",
    ),
    "goal_money_done": _all(
        " meta atingida", " doel bereikt", " goal reached", " objectif atteint", " Ziel erreicht"
    ),
    "goal_money_added": _all(
        "Anotei {amount} na meta *{title}*. Agora: {saved} de {target} ({pct}%).",
        "Genoteerd: {amount} bij doel *{title}*. Nu: {saved} van {target} ({pct}%).",
        "Added {amount} to *{title}*. Now: {saved} of {target} ({pct}%).",
        "{amount} ajouté à *{title}*. Maintenant : {saved} sur {target} ({pct} %).",
        "{amount} zu *{title}* notiert. Jetzt: {saved} von {target} ({pct} %).",
    ),
    "goal_money_set": _all(
        "Combinado: você tem {saved} na meta *{title}*, de {target} ({pct}%).",
        "Afgesproken: je hebt {saved} bij doel *{title}*, van {target} ({pct}%).",
        "Done: you have {saved} in *{title}*, out of {target} ({pct}%).",
        "C'est noté : tu as {saved} dans *{title}*, sur {target} ({pct} %).",
        "Notiert: Du hast {saved} bei *{title}*, von {target} ({pct} %).",
    ),
    "goal_money_notarget": _all(
        'Anotei {amount} na meta *{title}*. Total: {saved}. Para eu mostrar a barra, diga o alvo: "meta: juntar 5000 euros".',
        'Genoteerd: {amount} bij *{title}*. Totaal: {saved}. Noem het doelbedrag voor de balk: "doel: 5000 euro sparen".',
        'Added {amount} to *{title}*. Total: {saved}. Tell me the target to see the bar: "goal: save 5000 euros".',
        "{amount} ajouté à *{title}*. Total : {saved}. Dis-moi l'objectif pour la barre : « objectif : épargner 5000 euros ».",
        '{amount} zu *{title}* notiert. Summe: {saved}. Nenne das Ziel für den Balken: "Ziel: 5000 Euro sparen".',
    ),
    "goal_money_which": _all(
        'Qual meta financeira? Você tem: {names}. Diga, por exemplo: "guardei 300 na reserva".',
        'Welk financieel doel? Je hebt: {names}. Zeg bijvoorbeeld: "300 bij de reserve gedaan".',
        'Which financial goal? You have: {names}. Say, for example: "put 300 in the reserve".',
        "Quel objectif financier ? Tu as : {names}. Dis par exemple : « j'ai mis 300 dans la réserve ».",
        'Welches Finanzziel? Du hast: {names}. Sag zum Beispiel: "300 in die Reserve gelegt".',
    ),
    "goal_money_empty": _all(
        'Você ainda não tem meta financeira. Crie uma assim: "meta: reserva de emergência 5000 euros". Depois diga "guardei 300 na reserva" e eu acompanho.',
        'Je hebt nog geen financieel doel. Maak er een: "doel: noodfonds 5000 euro". Zeg daarna "300 bij het noodfonds gedaan".',
        'You have no financial goal yet. Create one: "goal: emergency fund 5000 euros". Then say "put 300 in the emergency fund" and I\'ll track it.',
        "Tu n'as pas encore d'objectif financier. Crée-en un : « objectif : fonds d'urgence 5000 euros ». Puis dis « j'ai mis 300 dans le fonds d'urgence ».",
        'Du hast noch kein Finanzziel. Lege eins an: "Ziel: Notgroschen 5000 Euro". Sag dann "300 in den Notgroschen gelegt".',
    ),
    "goal_money_hint": _all(
        'Para somar o que você guarda, diga: "guardei 300 na meta". Se você já tem um saldo, diga: "já tenho 1200 na meta".',
        'Om op te tellen wat je opzij zet, zeg: "300 bij het doel gedaan". Heb je al een saldo, zeg: "ik heb al 1200 bij het doel".',
        'To add what you put aside, say: "put 300 in the goal". If you already have a balance, say: "I already have 1200 in the goal".',
        "Pour ajouter ce que tu mets de côté, dis : « j'ai mis 300 dans l'objectif ». Si tu as déjà un solde : « j'ai déjà 1200 dans l'objectif ».",
        'Um dein Erspartes zu zählen, sag: "300 ins Ziel gelegt". Hast du schon etwas: "ich habe schon 1200 im Ziel".',
    ),
    "goal_workout_line": _all(
        "Conta para a meta: {goal}.",
        "Telt mee voor het doel: {goal}.",
        "Counts toward your goal: {goal}.",
        "Compte pour l'objectif : {goal}.",
        "Zählt für das Ziel: {goal}.",
    ),
    "goals_money_only_head": _all(
        "Suas metas financeiras ({n}):",
        "Je financiële doelen ({n}):",
        "Your financial goals ({n}):",
        "Tes objectifs financiers ({n}) :",
        "Deine Finanzziele ({n}):",
    ),
}


# ── rendering ─────────────────────────────────────────────────────────────────


def _bar(pct: int) -> str:
    filled = round(pct / 10)
    return "▰" * filled + "▱" * (10 - filled)


def _money_row(goal: Goal, saved: float, lang: str, today: date) -> str:
    from alfred.conversation import _fmt_eur

    prog = progress_for(goal, saved, today)
    target = prog["target_amount"]
    if not target:
        return f"• {goal.title}: {_fmt_eur(saved)}"
    pct = prog["saved_pct"]
    if prog["done"]:
        rest = _t("goal_money_done", lang)
    else:
        rest = _t("goal_money_rest", lang, left=_fmt_eur(prog["remaining"]))
        if prog.get("per_month") and prog["deadline"]:
            due = date.fromisoformat(prog["deadline"])
            rest += _t(
                "goal_money_due", lang, due=f"{due:%d/%m/%Y}", per=_fmt_eur(prog["per_month"])
            )
    return _t(
        "goal_money_row",
        lang,
        title=goal.title,
        saved=_fmt_eur(saved),
        target=_fmt_eur(target),
        pct=pct,
        bar=_bar(pct),
        rest=rest,
    )


async def render_goals(
    session: AsyncSession, member: Member, lang: str, today: date, goals: list[Goal]
) -> str:
    """ "minhas metas": financial goals first, then the routine goals with the week marks."""
    money = [g for g in goals if is_money_goal(g)]
    routine = [g for g in goals if not is_money_goal(g)]
    saved = await saved_amounts(session, member.id, [g.id for g in money])
    week = await week_logs(session, member.id, [g.id for g in routine], today)
    parts = [_t("goals_title", lang, n=len(goals))]
    if money:
        parts.append("\n" + _t("goals_money_head", lang))
        parts += [_money_row(g, saved.get(g.id, 0.0), lang, today) for g in money[:MAX_GOALS_SHOWN]]
    if routine:
        done_today = sum(1 for g in routine if today in week[g.id])
        parts.append("\n" + _t("goals_routine_head", lang, done=done_today, n=len(routine)))
        for g in routine[:MAX_GOALS_SHOWN]:
            tick = DONE if today in week[g.id] else "○"
            parts.append(f"{tick} {g.title}")
            parts.append(
                "   "
                + _t("goal_week_line", lang, marks=marks(week[g.id], today), n=len(week[g.id]))
            )
        parts.append("\n" + _t("goals_legend", lang))
    return "\n".join(parts)


# ── commands ──────────────────────────────────────────────────────────────────

_LIST = {
    "metas financeiras", "minhas metas financeiras", "as minhas metas financeiras", "meta financeira", "minha reserva",
    "financial goals", "my financial goals", "financiele doelen", "mijn financiele doelen", "mijn spaardoelen",
    "objectifs financiers", "mes objectifs financiers", "finanzziele", "meine finanzziele",
}  # fmt: skip
_AMT = r"(?:€\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|euros?|eur)?"
_ADD = re.compile(
    rf"^(?:guardei|poupei|juntei|separei|coloquei|botei|depositei|economizei|reservei|saved|put|added|set aside|ik heb|ik stortte|j'ai mis|j'ai epargne|ich habe|ich legte)\s+{_AMT}"
    r"(?:\s+(?:na|no|nas|nos|para a|para o|para|em|in|on|dans|auf|to|into|bij|naar|zu|in die|in den|de)\s+(?:(?:meta|reserva|goal|doel|objectif|ziel)\s+)?(?:(?:de|da|do|the|het|de|le|la|der|die|das)\s+)?(?P<goal>.+?))?"
    r"(?:\s+(?:gedaan|gespaard|opzij gezet|gelegt|gespart|mis de cote))?$"
)
_SET = re.compile(
    rf"^(?:ja tenho|tenho|ja guardei|o saldo d[aoe]|saldo d[aoe]|i already have|i have|i now have|ik heb al|ik heb nu|j'ai deja|j'ai maintenant|ich habe schon|ich habe jetzt)\s+{_AMT}"
    r"\s+(?:na|no|em|in|on|dans|auf|bij|de|do|da|of|van|von)\s+(?:(?:meta|reserva|goal|doel|objectif|ziel)\s+)?(?:(?:de|da|do|the|het|le|la|der|die|das)\s+)?(?P<goal>.+)$"
)


def _find(goals: list[Goal], text: str) -> list[Goal]:
    from alfred.conversation import _goal_for_activity

    text = (text or "").strip()
    if not text:
        return goals if len(goals) == 1 else []
    hit = _goal_for_activity(goals, text)
    return [hit] if hit else []


async def handle_goal_command(
    raw: str, body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is about a financial goal the member has (cheap exit first)."""
    from alfred.conversation import _fmt_eur

    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 160:
        return None
    is_list = plain in _LIST
    m_add = None if is_list else _ADD.match(plain)
    m_set = None if is_list or m_add else _SET.match(plain)
    if not (is_list or m_add or m_set):
        return None
    goals = await money_goals(session, member.id)
    if is_list:
        if not goals:
            return Reply(_t("goal_money_empty", lang))
        saved = await saved_amounts(session, member.id, [g.id for g in goals])
        rows = [_money_row(g, saved.get(g.id, 0.0), lang, today) for g in goals[:MAX_GOALS_SHOWN]]
        return Reply("\n".join([_t("goals_money_only_head", lang, n=len(goals)), *rows]))
    m = m_add or m_set
    if not goals:
        return None  # "guardei 20 no cofre" with no financial goal is a normal message
    amount = to_amount(m.group("amt"))
    if amount is None or not 0 < float(amount) <= MAX_AMOUNT:
        return None
    found = _find(goals, m.group("goal") or "")
    if not found:
        if m.group("goal") and not _find(goals, m.group("goal")):
            return None  # the words name something else ("guardei 50 no cofrinho do filho")
        return Reply(_t("goal_money_which", lang, names=", ".join(g.title for g in goals[:5])))
    goal = found[0]
    amt = round(float(amount), 2)
    session.add(
        GoalContribution(
            id=uuid.uuid4(),
            member_id=member.id,
            goal_id=goal.id,
            kind="add" if m_add else "set",
            amount=amt,
        )
    )
    await session.flush()
    saved = (await saved_amounts(session, member.id, [goal.id]))[goal.id]
    audit(session, "goal_contribution", member.id, kind="add" if m_add else "set")
    prog = progress_for(goal, saved, today)
    target = prog["target_amount"]
    if not target:
        return Reply(
            _t(
                "goal_money_notarget",
                lang,
                amount=_fmt_eur(amt),
                title=goal.title,
                saved=_fmt_eur(saved),
            )
        )
    key = "goal_money_added" if m_add else "goal_money_set"
    text = _t(
        key,
        lang,
        amount=_fmt_eur(amt),
        title=goal.title,
        saved=_fmt_eur(saved),
        target=_fmt_eur(target),
        pct=prog["saved_pct"],
    )
    return Reply(text + "\n" + _bar(prog["saved_pct"]))


async def forget_contributions(session: AsyncSession, goal_id: uuid.UUID) -> None:
    await session.execute(delete(GoalContribution).where(GoalContribution.goal_id == goal_id))


# ── workouts count for the routine goal they belong to ───────────────────────

_ACTIVITY_STEMS = {
    "running": ("corr", "run", "loop", "cour", "lauf", "jog"),
    "walking": ("caminh", "walk", "wandel", "march", "geh", "passo", "step"),
    "cycling": ("bici", "cicl", "pedal", "bike", "cycl", "fiets", "velo", "rad"),
    "swimming": ("nata", "nad", "swim", "zwem", "nage", "schwimm"),
    "strength": ("muscul", "forca", "forc", "strength", "gym", "kracht", "krafttr"),
    "yoga": ("yoga",),
    "football": ("futebol", "football", "voetbal", "fussball", "foot"),
}  # fmt: skip


def goal_for_workout(goals: list[Goal], activity_type: str) -> Goal | None:
    stems = _ACTIVITY_STEMS.get((activity_type or "").lower(), ())
    key = _plain(activity_type)
    for g in goals:
        if is_money_goal(g):
            continue
        title = unicodedata.normalize("NFKD", _plain(g.title))
        words = re.findall(r"[a-z]+", title)
        if any(w.startswith(s) for w in words for s in stems) or (key and key in words):
            return g
    return None
