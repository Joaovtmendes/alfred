# ruff: noqa: E501
"""V2-05 — import a bank statement (CSV) sent as a WhatsApp document.

Flow: the member sends the file → we download it (size-capped, never stored), read it with
``statement_csv`` (rules only, no LLM) → a *draft* (``ImportBatch``) with the normalised lines →
a preview with counts and buttons → on confirmation the lines become ``Expense`` rows
(``source="csv"``). Nothing is written to the ledger before the member taps.

Safety: exact repeats are skipped through ``Expense.external_id`` (unique per member); lines that
look like an entry the member typed by hand (same amount and sign, ±2 days, shared name word) are
"possible duplicates" and left out unless the member chooses "importar tudo"; "desfazer importação"
removes the last import. A cap of ``MAX_PER_HOUR`` files per member and hour; file size and rows
are capped by the reader; the file itself is dropped as soon as it is read.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import clock
from alfred import statement_csv as csvp
from alfred.accounting import default_scope
from alfred.audit import audit
from alfred.couple import Reply, is_home_by_default
from alfred.labels import category_label
from alfred.models import AuditLog, Expense, ImportBatch, Member
from alfred.parsing import strip_accents
from alfred.whatsapp import MediaTooLarge, download_media

DRAFT_HOURS = 24
MAX_PER_HOUR = 10
_TEXT_EXT = (".csv", ".txt", ".tsv")
_TEXT_MIME = ("text/csv", "text/plain", "text/tab-separated-values", "application/csv")
_BANKS = {"ing": "ING", "rabobank": "Rabobank", "abn": "ABN AMRO", "bunq": "bunq"}

_HELP_RE = re.compile(
    r"^(?:importar|import|importeer|importer|importieren)\s+(?:(?:o|meu|um|my|a|het|mijn|een|mon|un|ein|mein|den|einen|de|el)\s+)*"
    r"(?:extrato|extract|statement|afschrift|bankafschrift|releve|kontoauszug|csv)\b"
)
_UNDO_RE = re.compile(
    r"^(?:desfaz(?:er)?\s+(?:a\s+|ultima\s+)*importacao"
    r"|undo\s+(?:the\s+|last\s+)*import"
    r"|(?:maak\s+)?(?:de\s+|laatste\s+)*import(?:atie)?\s+ongedaan"
    r"|annuler\s+(?:l['’]\s*|le\s+|dernier\s+)*import"
    r"|import(?:ieren)?\s+ruckgangig)"
)


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _fmt(value: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def _d(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _tokens(*texts: str) -> set[str]:
    return {w for t in texts for w in re.findall(r"[a-z0-9]{3,}", strip_accents(t.lower()))}


# ── entry points ──────────────────────────────────────────────────────────────


async def handle_statement_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> Reply | None:
    """``importar extrato`` (how to) and ``desfazer importação``; None for anything else."""
    text = body_plain.strip()
    if _UNDO_RE.match(text):
        return await _undo(member, lang, session)
    if _HELP_RE.match(text):
        return Reply(_t("imp_help", lang), [])
    return None


async def handle_document(
    member: Member, message: object, lang: str, session: AsyncSession
) -> Reply:
    """A file arrived: validate, download, read, store a draft, answer with the preview."""
    doc = (getattr(message, "raw", None) or {}).get("document") or {}
    name = str(doc.get("filename") or "")[:120]
    mime = str(doc.get("mime_type") or "").lower()
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not (ext in _TEXT_EXT or (not ext and mime in _TEXT_MIME)):
        return Reply(_t("imp_bad_type", lang), [])

    since = datetime.now(UTC) - timedelta(hours=1)
    recent = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.member_id == member.id,
            AuditLog.event == "statement_upload",
            AuditLog.created_at >= since,
        )
    )
    if (recent or 0) >= MAX_PER_HOUR:
        return Reply(_t("imp_rate", lang), [])
    audit(session, "statement_upload", member.id)

    try:
        data = await download_media(str(doc.get("id") or ""), csvp.MAX_BYTES)
    except MediaTooLarge:
        return Reply(_t("imp_too_big", lang), [])
    if data is None:
        return Reply(_t("imp_download_failed", lang), [])

    parsed = csvp.parse_statement(data, clock.today_local())
    del data  # the raw file is never kept
    if parsed.error:
        key = {"too_big": "imp_too_big", "too_many": "imp_too_many", "empty": "imp_empty"}
        return Reply(_t(key.get(parsed.error, "imp_format"), lang), [])
    return await _make_draft(member, lang, session, parsed, name)


async def handle_statement_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> Reply:
    """``imp_ok|imp_all|imp_no:<batch id>``."""
    try:
        ident = uuid.UUID(raw_id)
    except ValueError:
        return Reply(_t("imp_gone", lang), [])
    batch = await session.scalar(
        select(ImportBatch).where(ImportBatch.id == ident, ImportBatch.member_id == member.id)
    )
    if batch is None or batch.status != "draft":
        return Reply(_t("imp_gone", lang), [])
    if batch.expires_at < datetime.now(UTC):
        batch.status, batch.items = "expired", []
        return Reply(_t("imp_expired", lang), [])
    if action == "imp_no":
        batch.status, batch.items = "cancelled", []
        return Reply(_t("imp_cancelled", lang), [])
    return await _commit(member, lang, session, batch, include_possible=action == "imp_all")


# ── draft ─────────────────────────────────────────────────────────────────────


async def _make_draft(
    member: Member, lang: str, session: AsyncSession, parsed: csvp.Parsed, filename: str
) -> Reply:
    from alfred.conversation import _load_merchant_overrides

    rows = parsed.rows
    ids = [r.external_id for r in rows]
    known = set(
        (
            await session.scalars(
                select(Expense.external_id).where(
                    Expense.member_id == member.id, Expense.external_id.in_(ids)
                )
            )
        ).all()
    )
    lo = clock.day_start(min(r.day for r in rows)) - timedelta(days=3)
    hi = clock.day_start(max(r.day for r in rows)) + timedelta(days=4)
    manual = list(
        (
            await session.scalars(
                select(Expense).where(
                    Expense.member_id == member.id,
                    Expense.source == "manual",
                    Expense.expense_date >= lo,
                    Expense.expense_date <= hi,
                )
            )
        ).all()
    )
    taken: set[uuid.UUID] = set()
    overrides = await _load_merchant_overrides(member, session)

    items: list[dict] = []
    for r in rows:
        kind = "income" if r.amount > 0 else "expense"
        cat = csvp.categorize(r.counterparty, r.description, r.amount, overrides)
        flag = "new"
        if r.external_id in known:
            flag = "dup"
        else:
            words = _tokens(r.counterparty, r.description)
            for e in manual:
                if (
                    e.id in taken
                    or e.transaction_type != kind
                    or Decimal(str(e.amount)) != abs(r.amount)
                    or abs((clock.to_local(e.expense_date).date() - r.day).days) > 2
                    or not (words & _tokens(e.merchant or "", e.description or ""))
                ):
                    continue
                taken.add(e.id)
                flag = "maybe"
                break
        items.append(
            {
                "d": r.day.isoformat(),
                "a": str(r.amount),
                "c": r.counterparty,
                "x": r.description,
                "e": r.external_id,
                "k": cat,
                "f": flag,
            }
        )

    await session.execute(
        delete(ImportBatch).where(ImportBatch.member_id == member.id, ImportBatch.status == "draft")
    )
    counts = Counter(i["f"] for i in items)
    batch = ImportBatch(
        member_id=member.id,
        bank=parsed.bank or "generic",
        filename=filename or None,
        status="draft",
        rows_total=len(items),
        rows_new=counts["new"],
        rows_duplicate=counts["dup"],
        rows_possible=counts["maybe"],
        items=items,
        expires_at=datetime.now(UTC) + timedelta(hours=DRAFT_HOURS),
    )
    session.add(batch)
    await session.flush()

    if counts["new"] == 0 and counts["maybe"] == 0:
        batch.status, batch.items = "cancelled", []
        return Reply(_t("imp_nothing_new", lang), [])

    fresh = [i for i in items if i["f"] != "dup"]
    outgoing = sum((-Decimal(i["a"]) for i in fresh if Decimal(i["a"]) < 0), Decimal(0))
    incoming = sum((Decimal(i["a"]) for i in fresh if Decimal(i["a"]) > 0), Decimal(0))
    per_cat: Counter[str] = Counter()
    for i in fresh:
        if Decimal(i["a"]) < 0:
            per_cat[i["k"]] += -Decimal(i["a"])
    top = ", ".join(f"{category_label(c, lang)} {_fmt(v)}" for c, v in per_cat.most_common(3))
    other = sum(1 for i in fresh if i["k"] == "overig" and Decimal(i["a"]) < 0)
    days = [date.fromisoformat(i["d"]) for i in items]
    text = _t(
        "imp_preview",
        lang,
        bank=_BANKS.get(batch.bank) or _t("imp_generic_bank", lang),
        total=len(items),
        d1=_d(min(days)),
        d2=_d(max(days)),
        new=counts["new"],
        dup=counts["dup"],
        maybe_line=_t("imp_maybe_line", lang, maybe=counts["maybe"]) if counts["maybe"] else "",
        out=_fmt(outgoing),
        inc=_fmt(incoming),
        cats=top or "-",
        other_line=_t("imp_other_line", lang, other=other) if other else "",
    )
    buttons: list[tuple[str, str]] = []
    if counts["new"]:
        buttons.append((f"imp_ok:{batch.id}", _t("imp_btn_ok", lang, n=counts["new"])))
    if counts["maybe"]:
        buttons.append(
            (f"imp_all:{batch.id}", _t("imp_btn_all", lang, n=counts["new"] + counts["maybe"]))
        )
    buttons.append((f"imp_no:{batch.id}", _t("imp_btn_no", lang)))
    return Reply(text, buttons)


# ── confirm / undo ────────────────────────────────────────────────────────────


async def _commit(
    member: Member, lang: str, session: AsyncSession, batch: ImportBatch, *, include_possible: bool
) -> Reply:
    wanted = {"new", "maybe"} if include_possible else {"new"}
    items = [i for i in (batch.items or []) if i.get("f") in wanted]
    ids = [i["e"] for i in items]
    known = set(
        (
            await session.scalars(
                select(Expense.external_id).where(
                    Expense.member_id == member.id, Expense.external_id.in_(ids)
                )
            )
        ).all()
    )
    made = 0
    for i in items:
        if i["e"] in known:
            continue
        amount = Decimal(i["a"])
        income = amount > 0
        cat = i["k"]
        session.add(
            Expense(
                member_id=member.id,
                household_id=member.household_id,
                transaction_type="income" if income else "expense",
                amount=float(abs(amount)),
                currency="EUR",
                merchant=(i["c"] or i["x"])[:255] or None,
                category=cat,
                description=i["x"] or None,
                expense_date=clock.day_start(date.fromisoformat(i["d"])) + timedelta(hours=12),
                status="received" if income else "paid",
                shared=(not income) and await is_home_by_default(session, member, cat),
                scope=default_scope(member, cat, "income" if income else "expense"),
                source="csv",
                external_id=i["e"],
                import_batch_id=batch.id,
            )
        )
        known.add(i["e"])
        made += 1
    batch.status, batch.items, batch.rows_new = "done", [], made
    audit(session, "statement_imported", member.id, rows=made, bank=batch.bank)
    return Reply(_t("imp_done", lang, n=made), [])


async def _undo(member: Member, lang: str, session: AsyncSession) -> Reply:
    batch = await session.scalar(
        select(ImportBatch)
        .where(ImportBatch.member_id == member.id, ImportBatch.status == "done")
        .order_by(ImportBatch.created_at.desc())
        .limit(1)
    )
    if batch is None:
        return Reply(_t("imp_undo_none", lang), [])
    result = await session.execute(
        delete(Expense).where(
            Expense.member_id == member.id,
            Expense.import_batch_id == batch.id,
            Expense.source == "csv",
        )
    )
    batch.status = "undone"
    removed = result.rowcount or 0
    audit(session, "statement_undone", member.id, rows=removed)
    return Reply(_t("imp_undone", lang, n=removed), [])


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "imp_help": _all(
        "Para importar o extrato do banco, exporte o arquivo em *CSV* no app ou site do banco (ING, Rabobank, ABN AMRO, bunq…) e envie aqui como documento. Eu mostro um resumo e só importo depois do seu OK. Linhas já importadas ou que você já registrou à mão não duplicam. Para desfazer: *desfazer importação*.",
        "Om je bankafschrift te importeren, exporteer je het bestand als *CSV* in de app of site van je bank (ING, Rabobank, ABN AMRO, bunq…) en stuur je het hier als document. Ik laat eerst een samenvatting zien en importeer pas na je OK. Regels die al zijn geïmporteerd of die je zelf hebt ingevoerd worden niet dubbel. Ongedaan maken: *import ongedaan maken*.",
        "To import your bank statement, export it as *CSV* from your bank's app or website (ING, Rabobank, ABN AMRO, bunq…) and send it here as a document. I show a summary first and only import after your OK. Lines already imported, or that you entered by hand, are not duplicated. To undo: *undo import*.",
        "Pour importer ton relevé bancaire, exporte-le en *CSV* depuis l'appli ou le site de ta banque (ING, Rabobank, ABN AMRO, bunq…) et envoie-le ici comme document. Je montre d'abord un résumé et je n'importe qu'après ton OK. Les lignes déjà importées ou saisies à la main ne sont pas dupliquées. Pour annuler : *annuler l'import*.",
        "Um deinen Kontoauszug zu importieren, exportiere ihn als *CSV* in der App oder auf der Website deiner Bank (ING, Rabobank, ABN AMRO, bunq…) und sende ihn hier als Dokument. Ich zeige erst eine Zusammenfassung und importiere erst nach deinem OK. Bereits importierte oder von dir von Hand eingetragene Zeilen werden nicht doppelt angelegt. Zum Rückgängigmachen: *import rückgängig*.",
    ),
    "imp_bad_type": _all(
        "Esse arquivo não é um CSV. Exporte o extrato como *CSV* (ou TXT) no app do banco e envie de novo.",
        "Dit bestand is geen CSV. Exporteer het afschrift als *CSV* (of TXT) in de app van je bank en stuur het opnieuw.",
        "That file is not a CSV. Export the statement as *CSV* (or TXT) from your bank's app and send it again.",
        "Ce fichier n'est pas un CSV. Exporte le relevé en *CSV* (ou TXT) depuis l'appli de ta banque et renvoie-le.",
        "Diese Datei ist keine CSV. Exportiere den Auszug als *CSV* (oder TXT) in der App deiner Bank und sende ihn erneut.",
    ),
    "imp_rate": _all(
        "Você já enviou muitos arquivos nesta hora. Tente de novo daqui a pouco.",
        "Je hebt dit uur al veel bestanden gestuurd. Probeer het straks opnieuw.",
        "You have already sent many files this hour. Please try again in a little while.",
        "Tu as déjà envoyé beaucoup de fichiers cette heure. Réessaie dans un moment.",
        "Du hast in dieser Stunde schon viele Dateien gesendet. Versuche es gleich noch einmal.",
    ),
    "imp_download_failed": _all(
        "Não consegui baixar o arquivo. Pode enviar de novo?",
        "Ik kon het bestand niet downloaden. Kun je het opnieuw sturen?",
        "I could not download the file. Could you send it again?",
        "Je n'ai pas pu télécharger le fichier. Peux-tu le renvoyer ?",
        "Ich konnte die Datei nicht herunterladen. Kannst du sie noch einmal senden?",
    ),
    "imp_too_big": _all(
        "O arquivo é grande demais (limite de 1 MB). Exporte um período menor, por exemplo um ano por vez.",
        "Het bestand is te groot (limiet 1 MB). Exporteer een kortere periode, bijvoorbeeld één jaar per keer.",
        "The file is too big (1 MB limit). Export a shorter period, for example one year at a time.",
        "Le fichier est trop gros (limite de 1 Mo). Exporte une période plus courte, par exemple une année à la fois.",
        "Die Datei ist zu groß (Limit 1 MB). Exportiere einen kürzeren Zeitraum, zum Beispiel ein Jahr auf einmal.",
    ),
    "imp_too_many": _all(
        "O arquivo tem linhas demais (limite de 2000). Exporte um período menor.",
        "Het bestand heeft te veel regels (limiet 2000). Exporteer een kortere periode.",
        "The file has too many lines (limit 2000). Export a shorter period.",
        "Le fichier a trop de lignes (limite de 2000). Exporte une période plus courte.",
        "Die Datei hat zu viele Zeilen (Limit 2000). Exportiere einen kürzeren Zeitraum.",
    ),
    "imp_empty": _all(
        "Não achei lançamentos nesse arquivo.",
        "Ik vond geen transacties in dit bestand.",
        "I found no transactions in that file.",
        "Je n'ai trouvé aucune transaction dans ce fichier.",
        "Ich habe in dieser Datei keine Buchungen gefunden.",
    ),
    "imp_format": _all(
        "Não reconheci o formato desse arquivo. Preciso de um CSV do banco com data, valor e descrição. Se puder, exporte de novo pelo app do banco.",
        "Ik herken het formaat van dit bestand niet. Ik heb een CSV van je bank nodig met datum, bedrag en omschrijving. Exporteer het zo nodig opnieuw via de app van je bank.",
        "I did not recognise the format of that file. I need a CSV from your bank with date, amount and description. If you can, export it again from the bank's app.",
        "Je n'ai pas reconnu le format de ce fichier. Il me faut un CSV de ta banque avec date, montant et libellé. Si possible, exporte-le à nouveau depuis l'appli de ta banque.",
        "Ich habe das Format dieser Datei nicht erkannt. Ich brauche eine CSV deiner Bank mit Datum, Betrag und Beschreibung. Exportiere sie wenn möglich noch einmal in der App deiner Bank.",
    ),
    "imp_generic_bank": _all("extrato", "afschrift", "statement", "relevé", "Kontoauszug"),
    "imp_preview": _all(
        "Li seu extrato ({bank}): {total} linhas, de {d1} a {d2}.\n\n• Novas: {new}\n• Já importadas antes: {dup}{maybe_line}\n\nSaídas {out} · Entradas {inc}\nMaiores categorias: {cats}{other_line}\n\nImportar agora?",
        "Ik heb je afschrift gelezen ({bank}): {total} regels, van {d1} tot {d2}.\n\n• Nieuw: {new}\n• Al eerder geïmporteerd: {dup}{maybe_line}\n\nUitgaven {out} · Inkomsten {inc}\nGrootste categorieën: {cats}{other_line}\n\nNu importeren?",
        "I read your statement ({bank}): {total} lines, from {d1} to {d2}.\n\n• New: {new}\n• Already imported: {dup}{maybe_line}\n\nSpending {out} · Income {inc}\nBiggest categories: {cats}{other_line}\n\nImport now?",
        "J'ai lu ton relevé ({bank}) : {total} lignes, du {d1} au {d2}.\n\n• Nouvelles : {new}\n• Déjà importées : {dup}{maybe_line}\n\nDépenses {out} · Revenus {inc}\nPrincipales catégories : {cats}{other_line}\n\nImporter maintenant ?",
        "Ich habe deinen Auszug gelesen ({bank}): {total} Zeilen, vom {d1} bis {d2}.\n\n• Neu: {new}\n• Schon importiert: {dup}{maybe_line}\n\nAusgaben {out} · Einnahmen {inc}\nGrößte Kategorien: {cats}{other_line}\n\nJetzt importieren?",
    ),
    "imp_maybe_line": _all(
        "\n• Possíveis duplicados de lançamentos seus: {maybe}",
        "\n• Mogelijk dubbel met je eigen invoer: {maybe}",
        "\n• Possible duplicates of your own entries: {maybe}",
        "\n• Doublons possibles avec tes saisies : {maybe}",
        "\n• Mögliche Dubletten deiner eigenen Einträge: {maybe}",
    ),
    "imp_other_line": _all(
        "\n{other} saídas ficaram sem categoria (*overig*); você pode ajustar depois.",
        "\n{other} uitgaven hebben nog geen categorie (*overig*); je kunt dat later aanpassen.",
        "\n{other} expenses have no category yet (*overig*); you can adjust that later.",
        "\n{other} dépenses n'ont pas encore de catégorie (*overig*) ; tu pourras ajuster ensuite.",
        "\n{other} Ausgaben haben noch keine Kategorie (*overig*); du kannst das später anpassen.",
    ),
    "imp_btn_ok": _all(
        "Importar {n}", "Importeer {n}", "Import {n}", "Importer {n}", "{n} importieren"
    ),
    "imp_btn_all": _all(
        "Importar tudo ({n})",
        "Alles ({n})",
        "Import all ({n})",
        "Tout importer ({n})",
        "Alle ({n})",
    ),
    "imp_btn_no": _all("Cancelar", "Annuleren", "Cancel", "Annuler", "Abbrechen"),
    "imp_nothing_new": _all(
        "Tudo isso já estava importado ou registrado por você; não há nada novo.",
        "Dit stond al allemaal in je administratie; er is niets nieuws.",
        "All of this was already imported or entered by you; there is nothing new.",
        "Tout cela était déjà importé ou saisi par toi ; il n'y a rien de nouveau.",
        "Das war alles schon importiert oder von dir eingetragen; es gibt nichts Neues.",
    ),
    "imp_done": _all(
        "Pronto: {n} lançamentos importados. Para desfazer: *desfazer importação*.",
        "Klaar: {n} transacties geïmporteerd. Ongedaan maken: *import ongedaan maken*.",
        "Done: {n} entries imported. To undo: *undo import*.",
        "C'est fait : {n} écritures importées. Pour annuler : *annuler l'import*.",
        "Fertig: {n} Buchungen importiert. Zum Rückgängigmachen: *import rückgängig*.",
    ),
    "imp_cancelled": _all(
        "Cancelado, não importei nada.",
        "Geannuleerd, ik heb niets geïmporteerd.",
        "Cancelled, I imported nothing.",
        "Annulé, je n'ai rien importé.",
        "Abgebrochen, ich habe nichts importiert.",
    ),
    "imp_expired": _all(
        "Esse resumo expirou. Envie o arquivo de novo.",
        "Deze samenvatting is verlopen. Stuur het bestand opnieuw.",
        "That summary has expired. Please send the file again.",
        "Ce résumé a expiré. Renvoie le fichier.",
        "Diese Zusammenfassung ist abgelaufen. Sende die Datei noch einmal.",
    ),
    "imp_gone": _all(
        "Essa importação não está mais aberta. Envie o arquivo de novo se quiser importar.",
        "Deze import staat niet meer open. Stuur het bestand opnieuw als je wilt importeren.",
        "That import is no longer open. Send the file again if you want to import.",
        "Cet import n'est plus ouvert. Renvoie le fichier si tu veux importer.",
        "Dieser Import ist nicht mehr offen. Sende die Datei erneut, wenn du importieren möchtest.",
    ),
    "imp_undone": _all(
        "Desfeito: {n} lançamentos da última importação foram removidos.",
        "Ongedaan gemaakt: {n} transacties van de laatste import zijn verwijderd.",
        "Undone: {n} entries from the last import were removed.",
        "Annulé : {n} écritures du dernier import ont été supprimées.",
        "Rückgängig gemacht: {n} Buchungen des letzten Imports wurden entfernt.",
    ),
    "imp_undo_none": _all(
        "Não há importação para desfazer.",
        "Er is geen import om ongedaan te maken.",
        "There is no import to undo.",
        "Il n'y a aucun import à annuler.",
        "Es gibt keinen Import zum Rückgängigmachen.",
    ),
}
