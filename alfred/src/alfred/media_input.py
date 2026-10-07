# ruff: noqa: E501
"""A photo or a PDF sent on WhatsApp: read once, then routed by what it is.

Flow: type check → hourly cap → size-capped download (never stored) → the model reads it into JSON
and says whether it is a training plan or a receipt (the file is data, never instructions) → the
matching module cleans it and answers with a preview. Nothing is saved before the member taps.
CSV and other text files still go to ``statement``. The audit log keeps only the event.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import clock
from alfred.audit import audit
from alfred.home import home_reply
from alfred.llm import read_member_file
from alfred.models import AuditLog, Member
from alfred.receipt import receipt_reply
from alfred.services import service_reply
from alfred.training import Reply
from alfred.training_media import plan_reply
from alfred.whatsapp import MediaTooLarge, download_media

MAX_PER_HOUR = 10
MAX_IMAGE_BYTES = 5_000_000
MAX_PDF_BYTES = 10_000_000
IMAGE_MIME = ("image/jpeg", "image/png", "image/webp")
PDF_MIME = "application/pdf"


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def is_media_file(raw: dict) -> bool:
    """A photo, or a document that is a PDF (anything else goes to the statement reader)."""
    kind = raw.get("type")
    if kind == "image":
        return True
    if kind == "document":
        doc = raw.get("document") or {}
        name = str(doc.get("filename") or "").lower()
        return str(doc.get("mime_type") or "").lower() == PDF_MIME or name.endswith(".pdf")
    return False


async def handle_media_file(
    member: Member, message: object, lang: str, session: AsyncSession
) -> Reply:
    raw = getattr(message, "raw", None) or {}
    if raw.get("type") == "image":
        media = raw.get("image") or {}
        mime = str(media.get("mime_type") or "").lower().split(";")[0].strip()
        limit = MAX_IMAGE_BYTES
        if mime not in IMAGE_MIME:
            return Reply(_t("media_bad_type", lang))
    else:
        media = raw.get("document") or {}
        mime, limit = PDF_MIME, MAX_PDF_BYTES

    since = datetime.now(UTC) - timedelta(hours=1)
    recent = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.member_id == member.id,
            AuditLog.event == "media_upload",
            AuditLog.created_at >= since,
        )
    )
    if (recent or 0) >= MAX_PER_HOUR:
        return Reply(_t("media_rate", lang))
    audit(session, "media_upload", member.id)

    try:
        data = await download_media(str(media.get("id") or ""), limit)
    except MediaTooLarge:
        return Reply(_t("media_too_big", lang))
    if data is None:
        return Reply(_t("media_download_failed", lang))

    reading = await read_member_file(data, mime, lang)
    del data  # the file is never kept
    if not isinstance(reading, dict):
        return Reply(_t("media_unavailable", lang))
    kind = reading.get("kind")
    if kind == "workout_plan":
        plan = await plan_reply(reading, member, lang, session)
        if plan is not None:
            return plan
    elif kind == "receipt":
        return await receipt_reply(reading, member, lang, session)
    elif kind == "service_invoice":
        return await service_reply(reading, member, lang, session, clock.today_local())
    elif kind == "home_contract":
        return await home_reply(reading, member, lang, session, clock.today_local())
    return Reply(_t("media_unknown", lang))


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "media_bad_type": _all(
        "Só consigo ler foto (JPG, PNG ou WebP) ou PDF. Se preferir, escreva em texto.",
        "Ik kan alleen een foto (JPG, PNG of WebP) of pdf lezen. Je kunt het ook typen.",
        "I can only read a photo (JPG, PNG or WebP) or a PDF. You can also type it.",
        "Je peux seulement lire une photo (JPG, PNG ou WebP) ou un PDF. Tu peux aussi l'écrire en texte.",
        "Ich kann nur ein Foto (JPG, PNG oder WebP) oder eine PDF lesen. Du kannst es auch tippen.",
    ),
    "media_too_big": _all(
        "O arquivo é grande demais. Mande uma foto menor (até 5 MB) ou um PDF de até 10 MB.",
        "Het bestand is te groot. Stuur een kleinere foto (tot 5 MB) of een pdf tot 10 MB.",
        "The file is too big. Send a smaller photo (up to 5 MB) or a PDF up to 10 MB.",
        "Le fichier est trop gros. Envoie une photo plus petite (jusqu'à 5 Mo) ou un PDF jusqu'à 10 Mo.",
        "Die Datei ist zu groß. Sende ein kleineres Foto (bis 5 MB) oder eine PDF bis 10 MB.",
    ),
    "media_download_failed": _all(
        "Não consegui baixar o arquivo. Tente enviar de novo.",
        "Ik kon het bestand niet ophalen. Probeer het opnieuw te sturen.",
        "I couldn't download the file. Please send it again.",
        "Je n'ai pas pu télécharger le fichier. Envoie-le encore une fois.",
        "Ich konnte die Datei nicht laden. Sende sie bitte noch einmal.",
    ),
    "media_unavailable": _all(
        'Não consegui ler o arquivo agora. Tente de novo mais tarde ou escreva em texto ("45 mercado" ou "plano de treino: segunda - peito: supino 4x10 60kg").',
        'Ik kon het bestand nu niet lezen. Probeer het later opnieuw of typ het ("45 supermarkt" of "trainingsschema: maandag - borst: bankdrukken 4x10 60kg").',
        'I couldn\'t read the file right now. Try again later or type it ("45 groceries" or "training plan: monday - chest: bench press 4x10 60kg").',
        'Je n\'ai pas pu lire le fichier maintenant. Réessaie plus tard ou écris-le ("45 courses" ou "plan d\'entrainement : lundi - pectoraux : développé couché 4x10 60kg").',
        'Ich konnte die Datei gerade nicht lesen. Versuche es später noch einmal oder tippe es ("45 Supermarkt" oder "trainingsplan: montag - brust: bankdrücken 4x10 60kg").',
    ),
    "media_unknown": _all(
        "Não reconheci nesse arquivo um recibo, uma nota de serviço, um contrato da casa nem um plano de treino. Mande uma foto nítida de um deles ou escreva em texto.",
        "Ik herken in dit bestand geen bon, dienstfactuur, huiscontract of trainingsschema. Stuur een scherpe foto van een ervan of typ het.",
        "I couldn't recognise a receipt, a service invoice, a home contract or a training plan in that file. Send a clear photo of one of them or type it.",
        "Je n'ai reconnu ni reçu, ni facture de service, ni contrat de la maison, ni plan d'entraînement dans ce fichier. Envoie une photo nette de l'un d'eux ou écris-le.",
        "Ich habe in dieser Datei weder einen Beleg, eine Dienstleistungsrechnung, einen Hausvertrag noch einen Trainingsplan erkannt. Sende ein scharfes Foto von einem davon oder tippe es.",
    ),
    "media_rate": _all(
        "Você já enviou muitos arquivos nesta hora. Tente de novo daqui a pouco.",
        "Je hebt in dit uur al veel bestanden gestuurd. Probeer het zo meteen opnieuw.",
        "You've sent a lot of files this hour. Please try again in a little while.",
        "Tu as déjà envoyé beaucoup de fichiers cette heure-ci. Réessaie dans un moment.",
        "Du hast in dieser Stunde schon viele Dateien geschickt. Versuche es gleich noch einmal.",
    ),
}
