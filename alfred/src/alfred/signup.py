# ruff: noqa: E501
"""Sign-up page (mandatory since 08/10/2026): the only way to open an account.

The invite and the page start in English; the person chooses the language on the page.

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
EMAIL_RE = re.compile(r"^[^@\s<>\"]{1,64}@[^@\s<>\"]{1,185}\.[^@\s<>\".]{2,}$")
MAX_BODY = 4096

# The states in which a number is waiting for its sign-up. ``pending_language`` and
# ``pending_response`` are the old chat flow's states: someone caught in the middle of it
# when this shipped gets the link like everybody else.
WAITING_STATES = ("pending", "pending_language", "pending_response", "pending_signup")

# ── WhatsApp texts (joined to the router's catalogue; audited like every other message) ───────

STRINGS: dict[str, dict[str, str]] = {
    "signup_invite": {
        "pt": "Oi! Eu sou o *Alfred*, seu assistente pessoal de IA no WhatsApp. Para começar, crie a sua conta clicando no botão abaixo.",
        "nl": "Hoi! Ik ben *Alfred*, je persoonlijke AI-assistent op WhatsApp. Maak om te beginnen je account aan via de knop hieronder.",
        "en": "Hi! I'm *Alfred*, your personal AI assistant on WhatsApp. To start, please create your account by clicking the button below.",
        "fr": "Salut ! Je suis *Alfred*, ton assistant personnel IA sur WhatsApp. Pour commencer, crée ton compte en cliquant sur le bouton ci-dessous.",
        "de": "Hallo! Ich bin *Alfred*, dein persönlicher KI-Assistent auf WhatsApp. Lege zum Start dein Konto an, indem du unten auf den Button tippst.",
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

_NEW: dict[str, dict] = {
    "en": {
        "h1": "Alfred, your personal assistant on WhatsApp",
        "form_h": "Create your account",
        "email": "Email",
        "email_hint": "Used only for your account.",
        "phone": "WhatsApp number",
        "phone_hint": "The number that opened this link. It can't be changed here.",
        "lang_hint": "Alfred will talk to you in this language.",
        "err_email": "Enter a valid email address.",
        "cards": [
            ("Financial", "“Uber 12,50”"),
            ("Calendar", "“dentist Friday 15:00”"),
            ("Habits and training", "“bench press 60 kg”"),
            ("Home and deadlines", "“energy contract ends 1 March”"),
        ],
        "chat": [
            "Jumbo 45,20, Uber 12,50, salary 2800",
            "I see 3 entries:\n1. Jumbo: €45,20\n2. Uber: €12,50\n3. salary: +€2.800,00\n\nConfirm and I'll add them. Nothing has been added yet.",
            "Confirm",
            "Recorded 3 transactions:\n• Jumbo: €45,20\n• Uber: €12,50\n• salary: +€2.800,00",
            "Example conversation",
        ],
    },
    "pt": {
        "h1": "Alfred, seu assistente pessoal no WhatsApp",
        "form_h": "Crie a sua conta",
        "email": "E-mail",
        "email_hint": "Usado só para a sua conta.",
        "phone": "Número de WhatsApp",
        "phone_hint": "O número que abriu este link. Não dá para mudar aqui.",
        "lang_hint": "O Alfred vai falar com você neste idioma.",
        "err_email": "Escreva um e-mail válido.",
        "cards": [
            ("Financeiro", "“Uber 12,50”"),
            ("Agenda", "“dentista sexta 15h”"),
            ("Hábitos e treino", "“supino 60 kg”"),
            ("Casa e prazos", "“contrato de energia termina 1º de março”"),
        ],
        "chat": [
            "Jumbo 45,20, Uber 12,50, salário 2800",
            "Vejo 3 lançamentos:\n1. Jumbo: €45,20\n2. Uber: €12,50\n3. salário: +€2.800,00\n\nConfirme e eu registro. Nada foi registrado ainda.",
            "Confirmar",
            "Registrei 3 lançamentos:\n• Jumbo: €45,20\n• Uber: €12,50\n• salário: +€2.800,00",
            "Exemplo de conversa",
        ],
    },
    "nl": {
        "h1": "Alfred, je persoonlijke assistent op WhatsApp",
        "form_h": "Maak je account aan",
        "email": "E-mail",
        "email_hint": "Alleen gebruikt voor je account.",
        "phone": "WhatsApp-nummer",
        "phone_hint": "Het nummer dat deze link opende. Het kan hier niet worden gewijzigd.",
        "lang_hint": "Alfred praat met je in deze taal.",
        "err_email": "Vul een geldig e-mailadres in.",
        "cards": [
            ("Financieel", "“Uber 12,50”"),
            ("Agenda", "“tandarts vrijdag 15:00”"),
            ("Gewoontes en training", "“bankdrukken 60 kg”"),
            ("Huis en deadlines", "“energiecontract eindigt 1 maart”"),
        ],
        "chat": [
            "Jumbo 45,20, Uber 12,50, salaris 2800",
            "Ik zie 3 posten:\n1. Jumbo: €45,20\n2. Uber: €12,50\n3. salaris: +€2.800,00\n\nBevestig en ik voeg ze toe. Er is nog niets toegevoegd.",
            "Bevestigen",
            "3 transacties vastgelegd:\n• Jumbo: €45,20\n• Uber: €12,50\n• salaris: +€2.800,00",
            "Voorbeeldgesprek",
        ],
    },
    "fr": {
        "h1": "Alfred, ton assistant personnel sur WhatsApp",
        "form_h": "Crée ton compte",
        "email": "E-mail",
        "email_hint": "Utilisé uniquement pour ton compte.",
        "phone": "Numéro WhatsApp",
        "phone_hint": "Le numéro qui a ouvert ce lien. Il ne peut pas être changé ici.",
        "lang_hint": "Alfred te parlera dans cette langue.",
        "err_email": "Saisis une adresse e-mail valide.",
        "cards": [
            ("Finances", "« Uber 12,50 »"),
            ("Agenda", "« dentiste vendredi 15h »"),
            ("Habitudes et sport", "« développé couché 60 kg »"),
            ("Maison et échéances", "« contrat d'énergie fini le 1er mars »"),
        ],
        "chat": [
            "Jumbo 45,20, Uber 12,50, salaire 2800",
            "Je vois 3 écritures :\n1. Jumbo : €45,20\n2. Uber : €12,50\n3. salaire : +€2.800,00\n\nConfirme et je les ajoute. Rien n'a encore été ajouté.",
            "Confirmer",
            "3 opérations enregistrées :\n• Jumbo : €45,20\n• Uber : €12,50\n• salaire : +€2.800,00",
            "Exemple de conversation",
        ],
    },
    "de": {
        "h1": "Alfred, dein persönlicher Assistent auf WhatsApp",
        "form_h": "Lege dein Konto an",
        "email": "E-Mail",
        "email_hint": "Nur für dein Konto verwendet.",
        "phone": "WhatsApp-Nummer",
        "phone_hint": "Die Nummer, die diesen Link geöffnet hat. Sie kann hier nicht geändert werden.",
        "lang_hint": "Alfred spricht in dieser Sprache mit dir.",
        "err_email": "Gib eine gültige E-Mail-Adresse ein.",
        "cards": [
            ("Finanzen", "„Uber 12,50“"),
            ("Kalender", "„Zahnarzt Freitag 15:00“"),
            ("Gewohnheiten und Training", "„Bankdrücken 60 kg“"),
            ("Haus und Fristen", "„Energievertrag endet am 1. März“"),
        ],
        "chat": [
            "Jumbo 45,20, Uber 12,50, Gehalt 2800",
            "Ich sehe 3 Einträge:\n1. Jumbo: €45,20\n2. Uber: €12,50\n3. Gehalt: +€2.800,00\n\nBestätige, dann trage ich sie ein. Noch nichts wurde eingetragen.",
            "Bestätigen",
            "3 Buchungen erfasst:\n• Jumbo: €45,20\n• Uber: €12,50\n• Gehalt: +€2.800,00",
            "Beispielgespräch",
        ],
    },
}
for _l, _extra in _NEW.items():
    PAGE[_l].update(_extra)

_CSS = """
:root{color-scheme:light;--bg:#f3f5f8;--card:#fff;--fg:#101828;--muted:#556070;--line:#e1e5eb;--field:#c7ced9;
--accent:#1d4ed8;--tint:#e6edff;--on:#fff;--err:#b3203a;--chat:#e9efe3;--out:#d9fdd3;--link:#027eb5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:30rem;margin:0 auto;padding:1rem 1rem 2rem;display:flex;flex-direction:column;gap:1.25rem}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:700;font-size:1.25rem}
.logo{width:2rem;height:2rem;border-radius:.55rem;background:var(--accent);color:var(--on);display:grid;place-items:center}
h1{font-size:2rem;line-height:1.12;margin:0;text-wrap:balance}
h2{font-size:1.4rem;margin:0}
form{background:var(--card);border:1px solid var(--line);border-radius:1.1rem;padding:1.25rem 1rem;display:flex;flex-direction:column;gap:1rem}
.f{display:flex;flex-direction:column;gap:.35rem}
label.l{font-weight:600}
.hint{color:var(--muted);font-size:.85rem;margin:0}
input[type=text],input[type=email],select{width:100%;height:3rem;font:inherit;padding:0 .85rem;border:1px solid var(--field);border-radius:.6rem;background:var(--card);color:var(--fg)}
input[readonly]{background:#edf0f5;color:var(--muted)}
hr{border:0;border-top:1px solid var(--line);margin:0}
.check{display:flex;gap:.7rem;align-items:flex-start;font-size:.9rem}
.check input{margin-top:.2rem;width:1.35rem;height:1.35rem;flex:none;accent-color:var(--accent)}
.check a{color:var(--accent)}
.req{color:var(--err);font-weight:600}
.opt{color:var(--muted)}
button,.btn{display:block;width:100%;text-align:center;font:inherit;font-weight:600;padding:.9rem 1rem;border:0;border-radius:.75rem;background:var(--accent);color:var(--on);text-decoration:none;cursor:pointer}
.err{color:var(--err);margin:0;font-weight:600}
.note{color:var(--muted);font-size:.85rem;margin:0;text-align:center}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:.9rem;padding:.9rem;display:flex;flex-direction:column;gap:.4rem}
.ic{width:2.25rem;height:2.25rem;border-radius:.6rem;background:var(--tint);color:var(--accent);display:grid;place-items:center}
.card b{font-weight:600}
.card span{font-size:.85rem;color:var(--muted)}
.chat{background:var(--chat);border-radius:.9rem;padding:.9rem;display:flex;flex-direction:column;gap:.5rem;font-size:.9rem;color:#111b21}
.chat div{padding:.5rem .75rem;border-radius:.75rem;max-width:92%;white-space:pre-line;background:#fff}
.chat .me{align-self:flex-end;background:var(--out)}
.chat small{color:var(--muted)}
.foot{color:var(--muted);font-size:.8rem;margin:0;text-align:center}
ul.sum{padding-left:1.1rem;color:var(--muted)}
.lead{color:var(--muted);margin:0}
@media (min-width:62rem){main{max-width:67rem;display:grid;grid-template-columns:1fr 26rem;column-gap:3rem;
row-gap:1.25rem;align-items:start;grid-template-rows:auto auto auto auto 1fr}
.brand{grid-column:1/-1}h1{grid-column:1;grid-row:2;font-size:3.2rem}.cards{grid-column:1;grid-row:3}
.chat{grid-column:1;grid-row:4;max-width:29rem}form{grid-column:2;grid-row:2/6}}
"""

_ICONS = (
    '<path d="M3 7h15a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7z"/><path d="M3 7l12-3v3"/>',
    '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    '<path d="M3 12h4l2-5 4 10 2-5h6"/>',
    '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
)


def _norm_lang(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def _page(lang: str, title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
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


def _mask(phone: str) -> str:
    d = re.sub(r"\D", "", phone)
    return f"+{d[:2]} {d[2:3]}•• ••• ••{d[-2:]}" if len(d) >= 6 else "+•• ••• •••"


def _brand() -> str:
    return '<div class="brand"><div class="logo">A</div>Alfred</div>'


def render_form(
    lang: str,
    token: str,
    *,
    phone: str = "",
    name: str = "",
    email: str = "",
    health: bool = False,
    deadlines: bool = False,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    lang = _norm_lang(lang)
    p = PAGE[lang]
    opts = "".join(
        f'<option value="{code}"{" selected" if code == lang else ""}>{n}</option>'
        for code, n in LANG_NAMES.items()
    )
    privacy = p["privacy"].replace("{url}", f"/privacy?lang={lang}")
    err = f'<p class="err" role="alert">{escape(p[error])}</p>' if error else ""
    cards = "".join(
        f'<div class="card"><div class="ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ic}</svg></div>'
        f"<b>{escape(label)}</b><span>{escape(ex)}</span></div>"
        for ic, (label, ex) in zip(_ICONS, p["cards"], strict=True)
    )
    c = p["chat"]
    chat = (
        f'<div class="chat"><div class="me">{escape(c[0])}</div><div>{escape(c[1])}</div>'
        f'<div class="me">{escape(c[2])}</div><div>{escape(c[3])}</div><small>{escape(c[4])}</small></div>'
    )
    form = (
        f'<form method="post" action="/cadastro/{escape(token)}" autocomplete="off">'
        f"<h2>{escape(p['form_h'])}</h2>"
        f'<div class="f"><label class="l" for="n">{escape(p["name"])}</label>'
        f'<input id="n" type="text" name="name" maxlength="40" required value="{escape(name)}" autocomplete="given-name">'
        f'<p class="hint">{escape(p["name_hint"])}</p></div>'
        f'<div class="f"><label class="l" for="e">{escape(p["email"])}</label>'
        f'<input id="e" type="email" name="email" maxlength="254" required value="{escape(email)}" autocomplete="email">'
        f'<p class="hint">{escape(p["email_hint"])}</p></div>'
        f'<div class="f"><label class="l" for="p">{escape(p["phone"])}</label>'
        f'<input id="p" type="text" value="{escape(_mask(phone))}" readonly tabindex="-1">'
        f'<p class="hint">{escape(p["phone_hint"])}</p></div>'
        f'<div class="f"><label class="l" for="l">{escape(p["language"])}</label>'
        f'<select id="l" name="lang" required>{opts}</select>'
        f'<p class="hint">{escape(p["lang_hint"])}</p></div><hr>'
        f'<label class="check"><input type="checkbox" name="privacy" value="1" required><span>{privacy}</span></label>'
        f'<label class="check"><input type="checkbox" name="health" value="1"{" checked" if health else ""}><span>{escape(p["health"])}</span></label>'
        f'<label class="check"><input type="checkbox" name="deadlines" value="1"{" checked" if deadlines else ""}><span>{escape(p["deadlines"])}</span></label>'
        f'{err}<button type="submit">{escape(p["submit"])}</button>'
        f'<p class="note">{escape(p["note"])}</p></form>'
    )
    body = f"{_brand()}<h1>{escape(p['h1'])}</h1>{form}<div class='cards'>{cards}</div>{chat}"
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
    return render_form(lang or member.language, token, phone=member.wa_phone)


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
    email = data.get("email", "").strip()
    health = data.get("health") == "1"
    deadlines = data.get("deadlines") == "1"
    again = {
        "phone": member.wa_phone,
        "name": name,
        "email": email,
        "health": health,
        "deadlines": deadlines,
        "status": 400,
    }
    if not NAME_RE.match(name):
        return render_form(lang, token, error="err_name", **again)
    if len(email) > 254 or not EMAIL_RE.match(email):
        return render_form(lang, token, error="err_email", **again)
    if data.get("privacy") != "1":
        return render_form(lang, token, error="err_privacy", **again)

    now = datetime.now(UTC)
    member.language = lang
    member.preferred_name = name
    member.email = email
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
