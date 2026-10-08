# ruff: noqa: E501
"""Sign-up page (mandatory since 08/10/2026): the only way to open an account.

A number that writes to Alfred for the first time gets one message with a button. The button opens
``/cadastro/{token}``: one screen where the person picks the language, says what to call them and
accepts the privacy notice (and, separately and optionally, health data and deadline reminders).
Only then does the account become active and the chat starts. Nothing is stored about the person
before that except the phone number and the first message's language.

* The token is a secret, single use, valid 30 minutes, tied to one member (so to one phone number).
* The page needs no JavaScript and no external host; the form posts to the same address.
* Each number is its own member with its own household and its own panel link, so five testers
  never see each other's data.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from html import escape
from urllib.parse import parse_qs

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.db import get_session
from alfred.legal import POLICY_VERSION
from alfred.models import CalendarOptIn, Member
from alfred.panel_tokens import ensure_panel_token
from alfred.settings import settings
from alfred.web_security import limit_dashboard
from alfred.whatsapp import send_cta_url, send_text

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["signup"])

LANGS = ("pt", "nl", "en", "fr", "de")
LANG_NAMES = {
    "pt": "Português",
    "nl": "Nederlands",
    "en": "English",
    "fr": "Français",
    "de": "Deutsch",
}
TOKEN_TTL = timedelta(minutes=30)
REUSE_IF_LEFT = timedelta(minutes=10)  # a still-fresh link is sent again instead of a new one
NAME_RE = re.compile(r"^[\w .'’-]{1,40}$")
MAX_BODY = 4096

# The states in which a number is waiting for its sign-up. ``pending_language`` and
# ``pending_response`` are the old chat flow's states: someone caught in the middle of it
# when this shipped gets the link like everybody else.
WAITING_STATES = ("pending", "pending_language", "pending_response", "pending_signup")

# ── WhatsApp texts (joined to the router's catalogue; audited like every other message) ───────

STRINGS: dict[str, dict[str, str]] = {
    "signup_invite": {
        "pt": "Oi! Eu sou o *Alfred*, um assistente pessoal pelo WhatsApp. Sou uma inteligência artificial, não uma pessoa.\n\nPara começar, crie a sua conta: leva um minuto, você escolhe o idioma e o que quer que eu guarde. O link vale por 30 minutos.",
        "nl": "Hoi! Ik ben *Alfred*, een persoonlijke assistent via WhatsApp. Ik ben een kunstmatige intelligentie, geen mens.\n\nMaak om te beginnen je account aan: het duurt een minuut en je kiest je taal en wat ik mag bewaren. De link is 30 minuten geldig.",
        "en": "Hi! I'm *Alfred*, a personal assistant on WhatsApp. I'm an artificial intelligence, not a person.\n\nTo start, create your account: it takes a minute, and you choose your language and what I may keep. The link is valid for 30 minutes.",
        "fr": "Salut ! Je suis *Alfred*, un assistant personnel sur WhatsApp. Je suis une intelligence artificielle, pas une personne.\n\nPour commencer, crée ton compte : ça prend une minute, tu choisis ta langue et ce que je peux garder. Le lien est valable 30 minutes.",
        "de": "Hallo! Ich bin *Alfred*, ein persönlicher Assistent auf WhatsApp. Ich bin eine künstliche Intelligenz, kein Mensch.\n\nLege zum Start dein Konto an: Das dauert eine Minute, du wählst deine Sprache und was ich speichern darf. Der Link gilt 30 Minuten.",
    },
    "signup_button": {
        "pt": "Criar minha conta",
        "nl": "Account maken",
        "en": "Create account",
        "fr": "Créer mon compte",
        "de": "Konto erstellen",
    },
    "signup_again": {
        "pt": "Antes de conversar, preciso que você crie a sua conta. É rápido e o link vale por 30 minutos.",
        "nl": "Voordat we kunnen praten, maak je eerst je account aan. Het is snel en de link is 30 minuten geldig.",
        "en": "Before we chat, please create your account first. It's quick and the link is valid for 30 minutes.",
        "fr": "Avant de discuter, crée d'abord ton compte. C'est rapide et le lien est valable 30 minutes.",
        "de": "Bevor wir chatten, lege bitte zuerst dein Konto an. Das geht schnell und der Link gilt 30 Minuten.",
    },
    "signup_welcome": {
        "pt": "Olá, {name}. Sua conta está ativa.\n\n",
        "nl": "Hoi {name}, je account is actief.\n\n",
        "en": "Hi {name}, your account is active.\n\n",
        "fr": "Salut {name}, ton compte est actif.\n\n",
        "de": "Hallo {name}, dein Konto ist aktiv.\n\n",
    },
    "signup_panel_text": {
        "pt": "Este é o seu painel pessoal: só você tem este link. Ele mostra tudo o que você me contar.",
        "nl": "Dit is je persoonlijke dashboard: alleen jij hebt deze link. Het toont alles wat je me vertelt.",
        "en": "This is your personal dashboard: only you have this link. It shows everything you tell me.",
        "fr": "Voici ton tableau de bord personnel : toi seul as ce lien. Il montre tout ce que tu me racontes.",
        "de": "Das ist dein persönliches Dashboard: nur du hast diesen Link. Es zeigt alles, was du mir erzählst.",
    },
    "signup_panel_button": {
        "pt": "Abrir meu painel",
        "nl": "Dashboard openen",
        "en": "Open my dashboard",
        "fr": "Ouvrir mon tableau",
        "de": "Dashboard öffnen",
    },
}

# ── page texts ────────────────────────────────────────────────────────────────────────────────

PAGE: dict[str, dict[str, str]] = {
    "pt": {
        "title": "Criar conta no Alfred",
        "h1": "Seu assistente pessoal no WhatsApp",
        "lead": "Anote gastos, compromissos, hábitos e treinos conversando. Crie a sua conta em um minuto.",
        "feat": "Dinheiro|Agenda|Hábitos e treino|Casa e prazos",
        "language": "Idioma",
        "name": "Como devo chamar você?",
        "name_hint": "Até 40 caracteres.",
        "privacy": 'Li o <a href="{url}">aviso de privacidade</a> e entendo que o Alfred é uma inteligência artificial e pode errar. (obrigatório)',
        "health": "Autorizo o Alfred a guardar dados de saúde que eu contar (sono, humor, remédio, água). Posso retirar quando quiser. (opcional)",
        "deadlines": "Quero avisos dos prazos holandeses (seguro-saúde, imposto, BTW). (opcional)",
        "submit": "Criar conta e voltar ao WhatsApp",
        "note": "Sem senha: a conta fica ligada ao número de WhatsApp que pediu este link.",
        "err_name": "Escreva um nome de 1 a 40 caracteres, sem símbolos.",
        "err_privacy": "Marque a caixa do aviso de privacidade para continuar.",
        "done_h1": "Conta criada, {name}",
        "done_text": "Pode voltar ao WhatsApp: mandei lá as boas-vindas e o link do seu painel.",
        "done_open": "Abrir o WhatsApp",
        "done_health_on": "Dados de saúde: autorizados.",
        "done_health_off": "Dados de saúde: não autorizados (você pode autorizar depois, no chat).",
        "done_deadlines_on": "Avisos de prazos: ligados.",
        "gone_h1": "Este link não vale mais",
        "gone_text": "Ele expirou ou já foi usado. Mande oi de novo no WhatsApp para receber um novo.",
    },
    "nl": {
        "title": "Account maken bij Alfred",
        "h1": "Je persoonlijke assistent op WhatsApp",
        "lead": "Noteer uitgaven, afspraken, gewoontes en trainingen door te praten. Maak je account in een minuut.",
        "feat": "Geld|Agenda|Gewoontes en training|Huis en deadlines",
        "language": "Taal",
        "name": "Hoe mag ik je noemen?",
        "name_hint": "Maximaal 40 tekens.",
        "privacy": 'Ik heb de <a href="{url}">privacyverklaring</a> gelezen en begrijp dat Alfred een kunstmatige intelligentie is die fouten kan maken. (verplicht)',
        "health": "Ik geef Alfred toestemming om gezondheidsgegevens te bewaren die ik vertel (slaap, stemming, medicijn, water). Ik kan dit altijd intrekken. (optioneel)",
        "deadlines": "Ik wil herinneringen aan Nederlandse deadlines (zorgverzekering, belasting, btw). (optioneel)",
        "submit": "Account maken en terug naar WhatsApp",
        "note": "Geen wachtwoord: het account hoort bij het WhatsApp-nummer dat deze link heeft aangevraagd.",
        "err_name": "Vul een naam in van 1 tot 40 tekens, zonder symbolen.",
        "err_privacy": "Vink het vakje van de privacyverklaring aan om door te gaan.",
        "done_h1": "Account gemaakt, {name}",
        "done_text": "Je kunt terug naar WhatsApp: daar staat het welkomstbericht met de link naar je dashboard.",
        "done_open": "WhatsApp openen",
        "done_health_on": "Gezondheidsgegevens: toegestaan.",
        "done_health_off": "Gezondheidsgegevens: niet toegestaan (je kunt dit later in de chat doen).",
        "done_deadlines_on": "Deadline-herinneringen: aan.",
        "gone_h1": "Deze link werkt niet meer",
        "gone_text": "Hij is verlopen of al gebruikt. Stuur opnieuw hallo op WhatsApp voor een nieuwe.",
    },
    "en": {
        "title": "Create your Alfred account",
        "h1": "Your personal assistant on WhatsApp",
        "lead": "Log spending, appointments, habits and workouts just by chatting. Create your account in a minute.",
        "feat": "Money|Calendar|Habits and training|Home and deadlines",
        "language": "Language",
        "name": "What should I call you?",
        "name_hint": "Up to 40 characters.",
        "privacy": 'I have read the <a href="{url}">privacy notice</a> and understand that Alfred is an artificial intelligence and can make mistakes. (required)',
        "health": "I allow Alfred to keep the health data I share (sleep, mood, medication, water). I can withdraw at any time. (optional)",
        "deadlines": "I want reminders for Dutch deadlines (health insurance, income tax, VAT). (optional)",
        "submit": "Create account and go back to WhatsApp",
        "note": "No password: the account is tied to the WhatsApp number that asked for this link.",
        "err_name": "Enter a name of 1 to 40 characters, without symbols.",
        "err_privacy": "Tick the privacy notice box to continue.",
        "done_h1": "Account created, {name}",
        "done_text": "You can go back to WhatsApp: the welcome message and your dashboard link are waiting there.",
        "done_open": "Open WhatsApp",
        "done_health_on": "Health data: allowed.",
        "done_health_off": "Health data: not allowed (you can allow it later in the chat).",
        "done_deadlines_on": "Deadline reminders: on.",
        "gone_h1": "This link no longer works",
        "gone_text": "It expired or was already used. Say hi again on WhatsApp to get a new one.",
    },
    "fr": {
        "title": "Créer un compte Alfred",
        "h1": "Ton assistant personnel sur WhatsApp",
        "lead": "Note tes dépenses, rendez-vous, habitudes et entraînements en discutant. Crée ton compte en une minute.",
        "feat": "Argent|Agenda|Habitudes et sport|Maison et échéances",
        "language": "Langue",
        "name": "Comment dois-je t'appeler ?",
        "name_hint": "40 caractères au maximum.",
        "privacy": "J'ai lu l'<a href=\"{url}\">avis de confidentialité</a> et je comprends qu'Alfred est une intelligence artificielle qui peut se tromper. (obligatoire)",
        "health": "J'autorise Alfred à garder les données de santé que je donne (sommeil, humeur, médicament, eau). Je peux retirer cet accord à tout moment. (facultatif)",
        "deadlines": "Je veux des rappels des échéances néerlandaises (assurance maladie, impôts, TVA). (facultatif)",
        "submit": "Créer le compte et retourner sur WhatsApp",
        "note": "Pas de mot de passe : le compte est lié au numéro WhatsApp qui a demandé ce lien.",
        "err_name": "Écris un nom de 1 à 40 caractères, sans symboles.",
        "err_privacy": "Coche la case de l'avis de confidentialité pour continuer.",
        "done_h1": "Compte créé, {name}",
        "done_text": "Tu peux retourner sur WhatsApp : le message de bienvenue et le lien de ton tableau de bord t'y attendent.",
        "done_open": "Ouvrir WhatsApp",
        "done_health_on": "Données de santé : autorisées.",
        "done_health_off": "Données de santé : non autorisées (tu peux les autoriser plus tard dans le chat).",
        "done_deadlines_on": "Rappels d'échéances : activés.",
        "gone_h1": "Ce lien ne fonctionne plus",
        "gone_text": "Il a expiré ou a déjà été utilisé. Dis à nouveau salut sur WhatsApp pour en recevoir un nouveau.",
    },
    "de": {
        "title": "Alfred-Konto anlegen",
        "h1": "Dein persönlicher Assistent auf WhatsApp",
        "lead": "Erfasse Ausgaben, Termine, Gewohnheiten und Training einfach im Chat. Lege dein Konto in einer Minute an.",
        "feat": "Geld|Kalender|Gewohnheiten und Training|Haus und Fristen",
        "language": "Sprache",
        "name": "Wie soll ich dich nennen?",
        "name_hint": "Höchstens 40 Zeichen.",
        "privacy": 'Ich habe die <a href="{url}">Datenschutzerklärung</a> gelesen und verstehe, dass Alfred eine künstliche Intelligenz ist und Fehler machen kann. (erforderlich)',
        "health": "Ich erlaube Alfred, die Gesundheitsdaten zu speichern, die ich mitteile (Schlaf, Stimmung, Medikament, Wasser). Ich kann das jederzeit widerrufen. (optional)",
        "deadlines": "Ich möchte Erinnerungen an niederländische Fristen (Krankenversicherung, Steuer, MwSt.). (optional)",
        "submit": "Konto anlegen und zurück zu WhatsApp",
        "note": "Kein Passwort: Das Konto gehört zur WhatsApp-Nummer, die diesen Link angefordert hat.",
        "err_name": "Gib einen Namen mit 1 bis 40 Zeichen ein, ohne Symbole.",
        "err_privacy": "Setze den Haken bei der Datenschutzerklärung, um fortzufahren.",
        "done_h1": "Konto angelegt, {name}",
        "done_text": "Du kannst zurück zu WhatsApp: Dort warten die Willkommensnachricht und der Link zu deinem Dashboard.",
        "done_open": "WhatsApp öffnen",
        "done_health_on": "Gesundheitsdaten: erlaubt.",
        "done_health_off": "Gesundheitsdaten: nicht erlaubt (du kannst das später im Chat erlauben).",
        "done_deadlines_on": "Frist-Erinnerungen: an.",
        "gone_h1": "Dieser Link funktioniert nicht mehr",
        "gone_text": "Er ist abgelaufen oder wurde schon benutzt. Schreib auf WhatsApp erneut Hallo, um einen neuen zu bekommen.",
    },
}

_CSS = """
:root{--bg:#fbfbf9;--fg:#1d2327;--muted:#5b646b;--line:#e3e5e2;--accent:#1f6f5c;--on:#fff;--err:#a3281d}
@media (prefers-color-scheme:dark){:root{--bg:#141719;--fg:#e8eaeb;--muted:#9aa3a9;--line:#2a2f33;
--accent:#6fc3a9;--on:#0d1411;--err:#ff8a7d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:30rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.5rem;line-height:1.25;margin:.5rem 0;text-wrap:balance}
.lead{color:var(--muted);margin:0 0 1rem}
.feat{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 1.5rem;padding:0;list-style:none}
.feat li{border:1px solid var(--line);border-radius:999px;padding:.15rem .7rem;font-size:.9rem}
nav{display:flex;flex-wrap:wrap;gap:.5rem 1rem;font-size:.9rem;margin-bottom:1rem}
nav a{color:var(--accent)} nav b{font-weight:600}
label{display:block;font-weight:600;margin:1rem 0 .25rem}
.hint{color:var(--muted);font-size:.85rem;margin:0}
input[type=text]{width:100%;font:inherit;padding:.65rem .75rem;border:1px solid var(--line);border-radius:.5rem;background:transparent;color:var(--fg)}
.check{display:flex;gap:.6rem;align-items:flex-start;margin:.9rem 0;font-weight:400}
.check input{margin-top:.3rem;width:1.1rem;height:1.1rem;flex:none}
.check a{color:var(--accent)}
button,.btn{display:block;width:100%;text-align:center;font:inherit;font-weight:600;margin-top:1.4rem;padding:.8rem 1rem;border:0;border-radius:.6rem;background:var(--accent);color:var(--on);text-decoration:none;cursor:pointer}
.err{color:var(--err);margin:.75rem 0 0;font-weight:600}
.note{color:var(--muted);font-size:.85rem;margin-top:1rem}
ul.sum{padding-left:1.1rem;color:var(--muted)}
"""


def _norm_lang(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def _page(lang: str, title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>"
    )
    return HTMLResponse(html, status_code=status)


def render_gone(lang: str | None) -> HTMLResponse:
    lang = _norm_lang(lang)
    p = PAGE[lang]
    return _page(
        lang,
        p["gone_h1"],
        f"<h1>{escape(p['gone_h1'])}</h1><p class='lead'>{escape(p['gone_text'])}</p>",
        status=404,
    )


def render_form(
    lang: str,
    token: str,
    *,
    name: str = "",
    health: bool = False,
    deadlines: bool = False,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    lang = _norm_lang(lang)
    p = PAGE[lang]
    nav = " ".join(
        f"<b>{n}</b>" if code == lang else f'<a href="?lang={code}">{n}</a>'
        for code, n in LANG_NAMES.items()
    )
    feats = "".join(f"<li>{escape(x)}</li>" for x in p["feat"].split("|"))
    privacy = p["privacy"].replace("{url}", f"/privacy?lang={lang}")
    err = f'<p class="err" role="alert">{escape(p[error])}</p>' if error else ""
    body = (
        f"<nav>{nav}</nav><h1>{escape(p['h1'])}</h1><p class='lead'>{escape(p['lead'])}</p>"
        f'<ul class="feat">{feats}</ul>'
        f'<form method="post" action="/cadastro/{escape(token)}" autocomplete="off">'
        f'<input type="hidden" name="lang" value="{lang}">'
        f'<label for="n">{escape(p["name"])}</label>'
        f'<input id="n" type="text" name="name" maxlength="40" required value="{escape(name)}" autocomplete="given-name">'
        f'<p class="hint">{escape(p["name_hint"])}</p>'
        f'<label class="check"><input type="checkbox" name="privacy" value="1" required><span>{privacy}</span></label>'
        f'<label class="check"><input type="checkbox" name="health" value="1"{" checked" if health else ""}><span>{escape(p["health"])}</span></label>'
        f'<label class="check"><input type="checkbox" name="deadlines" value="1"{" checked" if deadlines else ""}><span>{escape(p["deadlines"])}</span></label>'
        f'{err}<button type="submit">{escape(p["submit"])}</button></form>'
        f'<p class="note">{escape(p["note"])}</p>'
    )
    return _page(lang, p["title"], body, status=status)


def render_done(lang: str, name: str, health: bool, deadlines: bool) -> HTMLResponse:
    lang = _norm_lang(lang)
    p = PAGE[lang]
    items = [p["done_health_on"] if health else p["done_health_off"]]
    if deadlines:
        items.append(p["done_deadlines_on"])
    summary = "".join(f"<li>{escape(i)}</li>" for i in items)
    link = ""
    if settings.whatsapp_display_number:
        number = re.sub(r"\D", "", settings.whatsapp_display_number)
        link = f'<a class="btn" href="https://wa.me/{number}">{escape(p["done_open"])}</a>'
    body = (
        f"<h1>{escape(p['done_h1'].format(name=name))}</h1>"
        f"<p class='lead'>{escape(p['done_text'])}</p><ul class='sum'>{summary}</ul>{link}"
    )
    return _page(lang, p["title"], body)


# ── tokens and the invite message ─────────────────────────────────────────────────────────────


def guess_language(phone: str) -> str:
    """First guess when the first message shows nothing: by country code (the page can change it)."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("55"):
        return "pt"
    if digits.startswith(("31", "32")):
        return "nl"
    if digits.startswith("33"):
        return "fr"
    if digits.startswith(("49", "43")):
        return "de"
    return "en"


def issue_token(member: Member, now: datetime | None = None) -> uuid.UUID:
    """Give ``member`` a sign-up token (the current one when it still has 10+ minutes left)."""
    now = now or datetime.now(UTC)
    if (
        member.signup_token is not None
        and member.signup_token_expires_at is not None
        and member.signup_token_expires_at - now > REUSE_IF_LEFT
    ):
        return member.signup_token
    member.signup_token = uuid.uuid4()
    member.signup_token_expires_at = now + TOKEN_TTL
    return member.signup_token


def signup_url(token: uuid.UUID) -> str:
    return f"{settings.base_url.rstrip('/')}/cadastro/{token}"


async def send_invite(member: Member, lang: str, *, again: bool = False) -> None:
    """The one message a not-yet-registered number gets: what Alfred is, and the button."""
    lang = _norm_lang(lang)
    token = issue_token(member)
    key = "signup_again" if again else "signup_invite"
    await send_cta_url(
        member.wa_phone, STRINGS[key][lang], STRINGS["signup_button"][lang], signup_url(token)
    )


# ── routes ────────────────────────────────────────────────────────────────────────────────────


async def _find(token_str: str, session: AsyncSession, *, lock: bool = False) -> Member | None:
    """The member holding a live sign-up token (None for unknown, malformed or expired ones)."""
    try:
        token = uuid.UUID(token_str)
    except ValueError:
        return None
    stmt = select(Member).where(Member.signup_token == token)
    if lock:
        stmt = stmt.with_for_update()
    member = (await session.execute(stmt)).scalar_one_or_none()
    if member is None or member.signup_token_expires_at is None:
        return None
    if member.signup_token_expires_at < datetime.now(UTC):
        return None
    return member


@router.get("/cadastro/{token}", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def signup_page(
    token: str, lang: str | None = None, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    member = await _find(token, session)
    if member is None:
        return render_gone(lang)
    return render_form(lang or member.language, token)


def _form(raw: bytes) -> dict[str, str]:
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=20)
    return {k: v[0] for k, v in parsed.items() if v}


@router.post("/cadastro/{token}", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def signup_submit(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return render_gone(None)
    member = await _find(token, session, lock=True)  # the row lock: a double tap signs up once
    if member is None:
        return render_gone(None)
    data = _form(raw)
    lang = _norm_lang(data.get("lang"))
    name = " ".join(data.get("name", "").split())
    health = data.get("health") == "1"
    deadlines = data.get("deadlines") == "1"
    if not NAME_RE.match(name):
        return render_form(
            lang, token, name=name, health=health, deadlines=deadlines, error="err_name", status=400
        )
    if data.get("privacy") != "1":
        return render_form(
            lang,
            token,
            name=name,
            health=health,
            deadlines=deadlines,
            error="err_privacy",
            status=400,
        )

    now = datetime.now(UTC)
    member.language = lang
    member.preferred_name = name
    member.consent_state = "accepted"
    member.disclosure_accepted_at = now
    member.disclosure_version = POLICY_VERSION
    member.health_consent_at = now if health else None
    member.signup_token = None  # single use
    member.signup_token_expires_at = None
    if deadlines:
        exists = await session.scalar(
            select(CalendarOptIn.id).where(CalendarOptIn.member_id == member.id)
        )
        if exists is None:
            session.add(CalendarOptIn(member_id=member.id))
    audit(
        session,
        "signup_completed",
        member.id,
        policy=POLICY_VERSION,
        health=health,
        deadlines=deadlines,
    )
    await ensure_panel_token(session, member)
    panel_token = member.dashboard_token
    phone = member.wa_phone
    await session.commit()  # the account exists before the welcome goes out

    try:
        await _welcome(phone, lang, name, panel_token)
    except Exception:  # the page already confirmed the account; a failed send must not undo it
        logger.exception("signup.welcome_failed")
    return render_done(lang, name, health, deadlines)


async def _welcome(phone: str, lang: str, name: str, panel_token: uuid.UUID | None) -> None:
    from alfred.conversation import _t  # late: conversation imports this module

    text = STRINGS["signup_welcome"][lang].format(name=name) + _t("consent_accepted", lang)
    await send_text(phone, text)
    if panel_token is not None:
        url = f"{settings.base_url.rstrip('/')}/d/{panel_token}"
        await send_cta_url(
            phone, STRINGS["signup_panel_text"][lang], STRINGS["signup_panel_button"][lang], url
        )
