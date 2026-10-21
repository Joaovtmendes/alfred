# ruff: noqa: E501
"""Google Calendar (optional): connect from the chat, mirror appointments, disconnect any time.

What this does and nothing more (the privacy policy, section "Google Calendar", promises the same):

* the member asks to connect; we send a link to Google's consent screen (scope
  ``calendar.events`` only, no profile, no e-mail);
* an appointment created in Jofie is also created in the member's primary calendar; moving or
  cancelling it there follows; events we did not create are never read, changed or deleted;
* "disconnect" (or erasing the data) revokes the grant at Google and deletes the stored token.

Failures never break the chat: Google being slow or down only means the mirror is skipped. The
link in the chat carries a signed, short-lived state; the refresh token never leaves the database
(it is excluded from the export and encrypted at rest when ``DATA_ENCRYPTION_KEY`` is set).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from html import escape
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.db import get_session
from alfred.models import Appointment, GoogleConnection, Member
from alfred.settings import settings
from alfred.web_security import limit_dashboard
from alfred.whatsapp import send_text

logger = structlog.get_logger()
router = APIRouter()

SCOPE = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
STATE_TTL_SECONDS = 900
EVENT_MINUTES = 60  # an appointment has a start only; the event lasts an hour
TIMEOUT = 6.0

# ── texts ────────────────────────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    "gcal_connect_body": {
        "pt": "Para ligar o Google Agenda, toque no botão e autorize. Eu só crio, mudo e apago os compromissos que eu mesmo criar para você; não leio o resto da sua agenda. O link vale 15 minutos. Para desligar quando quiser: *desconectar google agenda*.",
        "nl": "Tik op de knop en geef toestemming om Google Agenda te koppelen. Ik maak, wijzig en verwijder alleen afspraken die ik zelf voor je maak; de rest van je agenda lees ik niet. De link is 15 minuten geldig. Loskoppelen kan altijd: *ontkoppel google agenda*.",
        "en": "To connect Google Calendar, tap the button and give permission. I only create, change and delete appointments I made for you; I don't read the rest of your calendar. The link is valid for 15 minutes. To disconnect any time: *disconnect google calendar*.",
        "fr": "Pour relier Google Agenda, touche le bouton et autorise. Je crée, modifie et supprime seulement les rendez-vous que j'ai créés pour toi; je ne lis pas le reste de ton agenda. Le lien vaut 15 minutes. Pour déconnecter quand tu veux : *déconnecter google agenda*.",
        "de": "Tippe auf den Button und erlaube die Verbindung mit Google Kalender. Ich erstelle, ändere und lösche nur Termine, die ich selbst für dich angelegt habe; den Rest deines Kalenders lese ich nicht. Der Link gilt 15 Minuten. Trennen kannst du jederzeit: *google kalender trennen*.",
    },
    "gcal_connect_button": {
        "pt": "Ligar Google",
        "nl": "Google koppelen",
        "en": "Connect Google",
        "fr": "Relier Google",
        "de": "Google verbinden",
    },
    "gcal_unavailable": {
        "pt": "A ligação com o Google Agenda ainda não está disponível. Seus compromissos continuam funcionando normalmente aqui.",
        "nl": "De koppeling met Google Agenda is nog niet beschikbaar. Je afspraken werken hier gewoon door.",
        "en": "The Google Calendar connection isn't available yet. Your appointments keep working here as usual.",
        "fr": "La liaison avec Google Agenda n'est pas encore disponible. Tes rendez-vous continuent de fonctionner ici.",
        "de": "Die Verbindung mit Google Kalender ist noch nicht verfügbar. Deine Termine funktionieren hier wie gewohnt.",
    },
    "gcal_already": {
        "pt": "O Google Agenda já está ligado. Os compromissos novos vão para lá. Para desligar: *desconectar google agenda*.",
        "nl": "Google Agenda is al gekoppeld. Nieuwe afspraken komen daar ook. Loskoppelen: *ontkoppel google agenda*.",
        "en": "Google Calendar is already connected. New appointments go there too. To disconnect: *disconnect google calendar*.",
        "fr": "Google Agenda est déjà relié. Les nouveaux rendez-vous y vont aussi. Pour déconnecter : *déconnecter google agenda*.",
        "de": "Google Kalender ist bereits verbunden. Neue Termine landen auch dort. Trennen: *google kalender trennen*.",
    },
    "gcal_connected": {
        "pt": "Google Agenda ligado. A partir de agora, os compromissos que eu criar vão também para o seu Google Agenda.",
        "nl": "Google Agenda is gekoppeld. Afspraken die ik maak komen nu ook in je Google Agenda.",
        "en": "Google Calendar connected. From now on, appointments I create also go to your Google Calendar.",
        "fr": "Google Agenda est relié. Désormais, les rendez-vous que je crée vont aussi dans ton Google Agenda.",
        "de": "Google Kalender ist verbunden. Termine, die ich anlege, landen jetzt auch in deinem Google Kalender.",
    },
    "gcal_disconnected": {
        "pt": "Google Agenda desligado: revoguei a autorização e apaguei o acesso guardado. Os eventos que já estão no Google continuam lá.",
        "nl": "Google Agenda losgekoppeld: ik heb de toestemming ingetrokken en de opgeslagen toegang verwijderd. Bestaande events in Google blijven staan.",
        "en": "Google Calendar disconnected: I revoked the permission and deleted the stored access. Events already in Google stay there.",
        "fr": "Google Agenda déconnecté : j'ai révoqué l'autorisation et supprimé l'accès enregistré. Les événements déjà dans Google y restent.",
        "de": "Google Kalender getrennt: Ich habe die Berechtigung widerrufen und den gespeicherten Zugriff gelöscht. Bereits vorhandene Termine in Google bleiben bestehen.",
    },
    "gcal_not_connected": {
        "pt": "O Google Agenda não está ligado. Para ligar: *conectar google agenda*.",
        "nl": "Google Agenda is niet gekoppeld. Koppelen: *koppel google agenda*.",
        "en": "Google Calendar isn't connected. To connect: *connect google calendar*.",
        "fr": "Google Agenda n'est pas relié. Pour relier : *connecter google agenda*.",
        "de": "Google Kalender ist nicht verbunden. Verbinden: *google kalender verbinden*.",
    },
    "gcal_page_ok_h": {
        "pt": "Google Agenda ligado",
        "nl": "Google Agenda gekoppeld",
        "en": "Google Calendar connected",
        "fr": "Google Agenda relié",
        "de": "Google Kalender verbunden",
    },
    "gcal_page_ok": {
        "pt": "Pode voltar ao WhatsApp. Os compromissos que o Jofie criar vão também para o seu Google Agenda.",
        "nl": "Je kunt terug naar WhatsApp. Afspraken die Jofie maakt komen ook in je Google Agenda.",
        "en": "You can go back to WhatsApp. Appointments Jofie creates also go to your Google Calendar.",
        "fr": "Tu peux retourner sur WhatsApp. Les rendez-vous créés par Jofie vont aussi dans ton Google Agenda.",
        "de": "Du kannst zu WhatsApp zurückkehren. Termine, die Jofie anlegt, landen auch in deinem Google Kalender.",
    },
    "gcal_page_no_h": {
        "pt": "Não foi possível ligar",
        "nl": "Koppelen is niet gelukt",
        "en": "Couldn't connect",
        "fr": "Liaison impossible",
        "de": "Verbindung nicht möglich",
    },
    "gcal_page_no": {
        "pt": "Nada foi ligado. Se quiser tentar de novo, escreva *conectar google agenda* no WhatsApp e permita o acesso ao calendário.",
        "nl": "Er is niets gekoppeld. Wil je het opnieuw proberen, schrijf dan *koppel google agenda* in WhatsApp en geef toegang tot de agenda.",
        "en": "Nothing was connected. To try again, write *connect google calendar* on WhatsApp and allow calendar access.",
        "fr": "Rien n'a été relié. Pour réessayer, écris *connecter google agenda* sur WhatsApp et autorise l'accès à l'agenda.",
        "de": "Es wurde nichts verbunden. Zum erneuten Versuch schreibe *google kalender verbinden* in WhatsApp und erlaube den Kalenderzugriff.",
    },
    "gcal_page_gone_h": {
        "pt": "Este link expirou",
        "nl": "Deze link is verlopen",
        "en": "This link expired",
        "fr": "Ce lien a expiré",
        "de": "Dieser Link ist abgelaufen",
    },
    "gcal_page_gone": {
        "pt": "Escreva *conectar google agenda* no WhatsApp para receber um novo.",
        "nl": "Schrijf *koppel google agenda* in WhatsApp voor een nieuwe.",
        "en": "Write *connect google calendar* on WhatsApp to get a new one.",
        "fr": "Écris *connecter google agenda* sur WhatsApp pour en recevoir un nouveau.",
        "de": "Schreibe *google kalender verbinden* in WhatsApp, um einen neuen zu erhalten.",
    },
}

_CAL = r"(?:google\s+(?:agenda|calendar|calendrier|kalender)|(?:agenda|calendar|calendrier|kalender)\s+(?:do\s+|de\s+|van\s+|von\s+)?google)"
_FILL = r"(?:\s+(?:o|a|meu|minha|my|mijn|mon|ma|meinen|meinem|de|het|je|mijn)){0,2}"
CONNECT_RE = re.compile(
    r"^(?:conecta(?:r)?|liga(?:r)?|vincula(?:r)?|sincroniza(?:r)?|connect|link|sync|koppel(?:en)?|verbind(?:en)?|"
    r"connecter|relier|synchroniser|verbinde(?:n)?|verknupfe(?:n)?|verknuepfe(?:n)?|synchronisier(?:e|en))"
    + _FILL
    + r"\s+"
    + _CAL
    + r"\s*[.!]*$"
)
DISCONNECT_RE = re.compile(
    r"^(?:desconecta(?:r)?|desliga(?:r)?|desvincula(?:r)?|disconnect|unlink|ontkoppel(?:en)?|verbreek|"
    r"deconnecter|dissocier|trenne(?:n)?|entkoppel(?:e|n))" + _FILL + r"\s+" + _CAL + r"\s*[.!]*$"
)
# German and Dutch may put the object first: "google kalender verbinden", "google agenda koppelen"
CONNECT_DE_RE = re.compile(
    r"^" + _CAL + r"\s+(?:verbinden|verknupfen|verknuepfen|synchronisieren|koppelen)\s*[.!]*$"
)
DISCONNECT_DE_RE = re.compile(
    r"^" + _CAL + r"\s+(?:trennen|entkoppeln|ontkoppelen|loskoppelen)\s*[.!]*$"
)


def configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret.get_secret_value())


def redirect_uri() -> str:
    return settings.google_redirect_uri or f"{settings.base_url.rstrip('/')}/google/callback"


def _t(key: str, lang: str) -> str:
    table = STRINGS[key]
    return table.get(lang) or table["en"]


# ── signed state (the link in the chat) ───────────────────────────────────────────────────────


def _sig(member_hex: str, ts: int) -> str:
    key = settings.google_client_secret.get_secret_value().encode()
    return hmac.new(key, f"gcal|{member_hex}|{ts}".encode(), hashlib.sha256).hexdigest()[:40]


def make_state(member_id: uuid.UUID, now: float | None = None) -> str:
    ts = int(now if now is not None else time.time())
    return f"{member_id.hex}.{ts}.{_sig(member_id.hex, ts)}"


def read_state(state: str, now: float | None = None) -> uuid.UUID | None:
    """The member a state was issued for, or None (malformed, forged or older than 15 minutes)."""
    try:
        member_hex, ts_s, sig = state.split(".")
        ts = int(ts_s)
        member_id = uuid.UUID(member_hex)
    except (ValueError, AttributeError):
        return None
    if not configured() or not hmac.compare_digest(sig, _sig(member_hex, ts)):
        return None
    if (now if now is not None else time.time()) - ts > STATE_TTL_SECONDS or ts > time.time() + 60:
        return None
    return member_id


def connect_link(member_id: uuid.UUID) -> str:
    return f"{settings.base_url.rstrip('/')}/google/connect?" + urlencode(
        {"state": make_state(member_id)}
    )


# ── Google HTTP (one place, so tests swap the transport) ──────────────────────────────────────


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)


async def _token_request(data: dict[str, str]) -> tuple[int, dict]:
    async with _client() as c:
        r = await c.post(TOKEN_URL, data=data)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


async def _revoke(token: str) -> None:
    try:
        async with _client() as c:
            await c.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        logger.warning("gcal.revoke_failed")


async def _access_token(session: AsyncSession, conn: GoogleConnection) -> str | None:
    """A fresh access token, or None. A grant Google no longer honours deletes the connection."""
    try:
        status, body = await _token_request(
            {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "refresh_token": conn.refresh_token,
                "grant_type": "refresh_token",
            }
        )
    except httpx.HTTPError:
        logger.warning("gcal.token_unreachable")
        return None
    if status == 400 and body.get("error") == "invalid_grant":
        await session.execute(delete(GoogleConnection).where(GoogleConnection.id == conn.id))
        audit(session, "google_grant_lost", conn.member_id)
        logger.info("gcal.grant_lost", member_id=str(conn.member_id))
        return None
    token = body.get("access_token")
    return token if status == 200 and isinstance(token, str) else None


async def get_connection(session: AsyncSession, member_id: uuid.UUID) -> GoogleConnection | None:
    return await session.scalar(
        select(GoogleConnection).where(GoogleConnection.member_id == member_id)
    )


# ── connect / disconnect ──────────────────────────────────────────────────────────────────────


async def connect_reply(
    member: Member, lang: str, session: AsyncSession
) -> tuple[str, str | None, str | None]:
    """(text, button label, url) — the url is None when no link should be sent."""
    if not configured() or not settings.base_url:
        return _t("gcal_unavailable", lang), None, None
    if await get_connection(session, member.id) is not None:
        return _t("gcal_already", lang), None, None
    audit(session, "google_link_sent", member.id)
    return _t("gcal_connect_body", lang), _t("gcal_connect_button", lang), connect_link(member.id)


async def disconnect(member: Member, lang: str, session: AsyncSession) -> str:
    conn = await get_connection(session, member.id)
    if conn is None:
        return _t("gcal_not_connected", lang)
    await _revoke(conn.refresh_token)
    await session.execute(delete(GoogleConnection).where(GoogleConnection.id == conn.id))
    audit(session, "google_disconnected", member.id)
    return _t("gcal_disconnected", lang)


async def revoke_member(session: AsyncSession, member_id: uuid.UUID) -> None:
    """Called before erasing a member: the grant is revoked at Google, never left behind."""
    conn = await get_connection(session, member_id)
    if conn is not None:
        await _revoke(conn.refresh_token)


def match_command(body_plain: str) -> str | None:
    """'connect' | 'disconnect' | None for a (accent-free, lower-case) message."""
    plain = re.sub(r"\s+", " ", body_plain).strip(" .!?")
    if DISCONNECT_RE.match(plain) or DISCONNECT_DE_RE.match(plain):
        return "disconnect"
    if CONNECT_RE.match(plain) or CONNECT_DE_RE.match(plain):
        return "connect"
    return None


# ── appointment mirror (best effort) ──────────────────────────────────────────────────────────


def _event_body(appt: Appointment) -> dict:
    start = appt.starts_at
    end = start + timedelta(minutes=EVENT_MINUTES)
    tz = settings.timezone
    body: dict = {
        "summary": appt.title,
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": max(0, min(appt.remind_before_minutes, 40320))}
            ],
        },
    }
    if appt.notes:
        body["description"] = appt.notes
    return body


async def _call(
    method: str, url: str, token: str, json: dict | None = None
) -> httpx.Response | None:
    try:
        async with _client() as c:
            return await c.request(
                method, url, json=json, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError:
        logger.warning("gcal.api_unreachable", method=method)
        return None


async def _token_for(session: AsyncSession, member_id: uuid.UUID) -> str | None:
    if not configured():
        return None
    conn = await get_connection(session, member_id)
    return None if conn is None else await _access_token(session, conn)


async def on_created(session: AsyncSession, appt: Appointment) -> None:
    try:
        token = await _token_for(session, appt.member_id)
        if token is None:
            return
        r = await _call("POST", EVENTS_URL, token, _event_body(appt))
        if r is not None and r.status_code in (200, 201):
            event_id = r.json().get("id")
            if isinstance(event_id, str):
                appt.google_event_id = event_id[:1024]
                session.add(appt)
        else:
            logger.warning("gcal.create_failed", status=getattr(r, "status_code", None))
    except Exception:  # never break the chat for the mirror
        logger.exception("gcal.on_created_failed")


async def on_moved(session: AsyncSession, appt: Appointment) -> None:
    try:
        if not appt.google_event_id:
            return
        token = await _token_for(session, appt.member_id)
        if token is None:
            return
        body = _event_body(appt)
        r = await _call("PATCH", f"{EVENTS_URL}/{appt.google_event_id}", token, body)
        if r is None or r.status_code not in (200, 201):
            logger.warning("gcal.move_failed", status=getattr(r, "status_code", None))
    except Exception:
        logger.exception("gcal.on_moved_failed")


async def on_cancelled(session: AsyncSession, appt: Appointment) -> None:
    try:
        if not appt.google_event_id:
            return
        token = await _token_for(session, appt.member_id)
        if token is None:
            return
        r = await _call("DELETE", f"{EVENTS_URL}/{appt.google_event_id}", token)
        if r is not None and r.status_code in (200, 204, 404, 410):
            appt.google_event_id = None
            session.add(appt)
        else:
            logger.warning("gcal.cancel_failed", status=getattr(r, "status_code", None))
    except Exception:
        logger.exception("gcal.on_cancelled_failed")


# ── web pages and routes ──────────────────────────────────────────────────────────────────────


def _page(lang: str, head_key: str, text_key: str, status: int = 200) -> HTMLResponse:
    """M-24: the Google landing pages wear the brand (same shell as the sign-up page)."""
    from alfred.signup import brand_page

    lang = lang if lang in ("pt", "nl", "en", "fr", "de") else "en"
    return brand_page(
        lang,
        _t(head_key, lang),
        f"<h1>{escape(_t(head_key, lang))}</h1>"
        f"<p class='lead'>{escape(_t(text_key, lang).replace('*', ''))}</p>",
        status=status,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/google/connect", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def google_connect(state: str = Query(default="", max_length=200)):
    """The link sent in the chat: checks the state, then hands over to Google's consent screen."""
    if read_state(state) is None:
        return _page("en", "gcal_page_gone_h", "gcal_page_gone", 410)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return RedirectResponse(
        f"{AUTH_URL}?{urlencode(params)}", status_code=302, headers={"Cache-Control": "no-store"}
    )


@router.get("/google/callback", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def google_callback(
    state: str = Query(default="", max_length=200),
    code: str = Query(default="", max_length=2000),
    error: str = Query(default="", max_length=100),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    member_id = read_state(state)
    member = await session.get(Member, member_id) if member_id else None
    if member is None:
        return _page("en", "gcal_page_gone_h", "gcal_page_gone", 410)
    lang = member.language or "en"
    if error or not code:
        audit(session, "google_denied", member.id)
        return _page(lang, "gcal_page_no_h", "gcal_page_no")
    try:
        status, body = await _token_request(
            {
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            }
        )
    except httpx.HTTPError:
        logger.warning("gcal.exchange_unreachable")
        return _page(lang, "gcal_page_no_h", "gcal_page_no", 502)
    refresh = body.get("refresh_token")
    scope = str(body.get("scope") or "")
    if status != 200 or not isinstance(refresh, str) or SCOPE not in scope.split():
        # no refresh token, or the member unticked the calendar permission: keep nothing
        for t in (refresh, body.get("access_token")):
            if isinstance(t, str):
                await _revoke(t)
        audit(session, "google_denied", member.id)
        return _page(lang, "gcal_page_no_h", "gcal_page_no")
    existing = await get_connection(session, member.id)
    if existing is None:
        session.add(GoogleConnection(member_id=member.id, refresh_token=refresh, scope=scope[:300]))
    else:
        existing.refresh_token, existing.scope = refresh, scope[:300]
        existing.connected_at = datetime.now(UTC)
        session.add(existing)
    audit(session, "google_connected", member.id)
    await session.commit()
    try:
        await send_text(member.wa_phone, _t("gcal_connected", lang))
    except Exception:
        logger.warning("gcal.confirm_send_failed")
    return _page(lang, "gcal_page_ok_h", "gcal_page_ok")
