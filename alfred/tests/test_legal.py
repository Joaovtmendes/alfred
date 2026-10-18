"""Public privacy policy page (/privacy) — required by Meta and the GDPR."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from alfred.legal import POLICY, PRIVACY_URLS


@pytest.mark.parametrize("lang", ["en", "nl", "pt"])
async def test_privacy_redirects_to_the_public_site_in_each_language(
    client: AsyncClient, lang: str
) -> None:
    resp = await client.get("/privacy", params={"lang": lang}, follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == PRIVACY_URLS[lang]
    assert resp.headers["location"].startswith("https://jofie.nl/privacy/")


async def test_privacy_unknown_language_falls_back_to_english(client: AsyncClient) -> None:
    resp = await client.get("/privacy", params={"lang": "xx"}, follow_redirects=False)
    assert resp.headers["location"] == PRIVACY_URLS["en"]


def test_every_language_has_the_same_sections() -> None:
    counts = {lang: len(p["sections"]) for lang, p in POLICY.items()}
    assert len(set(counts.values())) == 1, counts


def test_policy_1_6_covers_the_v2_data_categories_couple_and_statements_in_every_language() -> None:
    from alfred.legal import POLICY_VERSION

    assert POLICY_VERSION == "2.3"
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(w in text for w in ("bank statement", "bankafschrift", "extrato banc")), lang
        assert "iban" in text, lang
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(w in text for w in ("three months", "drie maanden", "três meses")), lang
        assert any(w in text for w in ("trip", "reis", "viage")), lang
        assert any(w in text for w in ("encrypted", "versleuteld", "criptografad")), lang
        assert any(w in text for w in ("partner", "par")), lang
        assert any(w in text for w in ("mark as shared", "als gedeeld", "como da casa")), lang


def test_policy_says_training_files_go_to_the_ai_provider_and_are_not_kept() -> None:
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(w in text for w in ("photo", "foto")), lang
        assert any(w in text for w in ("not kept", "niet bewaard", "não é guardado")), lang


def test_policy_1_7_covers_service_records_in_every_language() -> None:
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(
            w in text
            for w in ("invoice for a service", "factuur voor een dienst", "nota de serviço")
        ), lang
        assert "15 " in text, lang


def test_policy_1_8_covers_home_contracts_in_every_language() -> None:
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(
            w in text
            for w in ("contracts of your home", "contracten van je huis", "contratos da casa")
        ), lang
        assert "60" in text, lang


def test_policy_1_9_covers_deadline_reminders_in_every_language() -> None:
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert any(
            w in text for w in ("deadline reminders", "deadline-herinneringen", "avisos de prazos")
        ), lang
        assert any(w in text for w in ("quarterly btw", "kwartaal-btw", "btw trimestral")), lang


def test_policy_2_2_covers_google_calendar_limited_use_in_every_language() -> None:
    for lang, policy in POLICY.items():
        text = " ".join(" ".join(paras) for _, paras in policy["sections"]).lower()
        assert "google api services user data policy" in text, lang
        assert "limited use" in text or "uso limitado" in text, lang
