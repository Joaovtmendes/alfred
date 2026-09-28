"""Public legal pages — privacy policy (GDPR/AVG art. 13) at /privacy.

Required by Meta to publish the WhatsApp app, and by the GDPR for any user.
Texts live in POLICY below (en / nl / pt); ``?lang=`` picks one, default en.
Update ``POLICY_VERSION`` and the "last updated" date whenever the text changes.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from alfred.settings import settings

router = APIRouter(tags=["legal"])

POLICY_VERSION = "1.0"
POLICY_DATE = "2026-09-28"
CONTROLLER = "JVM Solutions, Biesbosch 179, 1181 JB Amstelveen, Nederland"
# Public contact for data requests — set PRIVACY_CONTACT_EMAIL in the environment.
CONTACT = settings.privacy_contact_email or "[privacy contact e-mail]"

# Each policy: list of (heading, paragraphs). Plain text, escaped on render.
POLICY: dict[str, dict] = {
    "en": {
        "title": "Alfred — Privacy policy",
        "updated": "Last updated",
        "sections": [
            (
                "Who we are",
                [
                    f"Alfred is a personal assistant on WhatsApp, operated by {CONTROLLER} "
                    "(the data controller).",
                    f"Questions or requests about your data: {CONTACT}.",
                ],
            ),
            (
                "What we process",
                [
                    "Your WhatsApp phone number and profile name; the messages you send "
                    "to Alfred and Alfred's replies.",
                    "What you ask Alfred to record: expenses and income, reminders, "
                    "workouts, goals and habits, notes and tasks, trips.",
                    "If you choose to log it: health information (medication, mood, sleep, "
                    "water). This is special-category data; we only process it because you "
                    "give explicit consent by sending it, and you can stop at any time.",
                ],
            ),
            (
                "Why, and on what legal basis",
                [
                    "To provide the service you asked for (GDPR art. 6(1)(b)); health "
                    "data only with your explicit consent (art. 9(2)(a)).",
                    "We do not sell your data, use it for advertising, or use it to "
                    "train AI models.",
                ],
            ),
            (
                "Artificial intelligence",
                [
                    "Alfred is an AI system, not a human (EU AI Act art. 50). Your messages "
                    "are processed by an AI model from Anthropic to understand and answer "
                    "them. Anthropic does not use this data to train its models.",
                ],
            ),
            (
                "Who else processes your data",
                [
                    "Meta Platforms (WhatsApp Business API) — message delivery.",
                    "Anthropic PBC — AI processing of message text.",
                    "Railway Corp. — hosting of the application and database.",
                    "Some of these providers process data outside the EU; transfers are "
                    "covered by the EU–US Data Privacy Framework and/or Standard "
                    "Contractual Clauses.",
                ],
            ),
            (
                "How long we keep it",
                [
                    "As long as you use Alfred. After you ask us to delete your account, "
                    "your data is deleted within 30 days, except where the law requires "
                    "us to keep it longer.",
                ],
            ),
            (
                "Your rights",
                [
                    "You can ask for access, correction, deletion, restriction or a copy "
                    "of your data (portability), and object to processing. Send STOP in "
                    f"WhatsApp to stop all processing, or write to {CONTACT} for any request.",
                    "You can complain to the Dutch data protection authority, the "
                    "Autoriteit Persoonsgegevens (autoriteitpersoonsgegevens.nl).",
                ],
            ),
        ],
    },
    "nl": {
        "title": "Alfred — Privacyverklaring",
        "updated": "Laatst bijgewerkt",
        "sections": [
            (
                "Wie wij zijn",
                [
                    f"Alfred is een persoonlijke assistent op WhatsApp, beheerd door "
                    f"{CONTROLLER} (verwerkingsverantwoordelijke).",
                    f"Vragen of verzoeken over je gegevens: {CONTACT}.",
                ],
            ),
            (
                "Welke gegevens",
                [
                    "Je WhatsApp-nummer en profielnaam; de berichten die je naar Alfred "
                    "stuurt en de antwoorden van Alfred.",
                    "Wat je Alfred laat vastleggen: uitgaven en inkomsten, herinneringen, "
                    "trainingen, doelen en gewoontes, notities en taken, reizen.",
                    "Als je dat zelf kiest: gezondheidsgegevens (medicatie, stemming, slaap, "
                    "water). Dit zijn bijzondere persoonsgegevens; we verwerken ze alleen met "
                    "je uitdrukkelijke toestemming, die je altijd kunt intrekken.",
                ],
            ),
            (
                "Waarom en op welke grondslag",
                [
                    "Om de dienst te leveren waar je om vraagt (AVG art. 6 lid 1 sub b); "
                    "gezondheidsgegevens alleen met uitdrukkelijke toestemming (art. 9 lid 2 "
                    "sub a).",
                    "We verkopen je gegevens niet, gebruiken ze niet voor advertenties en "
                    "niet om AI-modellen te trainen.",
                ],
            ),
            (
                "Kunstmatige intelligentie",
                [
                    "Alfred is een AI-systeem, geen mens (EU AI-verordening art. 50). Je "
                    "berichten worden verwerkt door een AI-model van Anthropic om ze te "
                    "begrijpen en te beantwoorden. Anthropic traint zijn modellen niet met "
                    "deze gegevens.",
                ],
            ),
            (
                "Wie je gegevens nog meer verwerkt",
                [
                    "Meta Platforms (WhatsApp Business API) — bezorging van berichten.",
                    "Anthropic PBC — AI-verwerking van berichttekst.",
                    "Railway Corp. — hosting van de applicatie en database.",
                    "Sommige leveranciers verwerken gegevens buiten de EU; die doorgifte valt "
                    "onder het EU-VS Data Privacy Framework en/of standaardcontractbepalingen.",
                ],
            ),
            (
                "Bewaartermijn",
                [
                    "Zolang je Alfred gebruikt. Nadat je vraagt je account te verwijderen, "
                    "worden je gegevens binnen 30 dagen gewist, tenzij de wet langer bewaren "
                    "verplicht.",
                ],
            ),
            (
                "Je rechten",
                [
                    "Je kunt inzage, correctie, verwijdering, beperking of een kopie "
                    "(dataportabiliteit) vragen en bezwaar maken. Stuur STOP in WhatsApp om "
                    f"alle verwerking te stoppen, of mail {CONTACT} voor elk verzoek.",
                    "Je kunt een klacht indienen bij de Autoriteit Persoonsgegevens "
                    "(autoriteitpersoonsgegevens.nl).",
                ],
            ),
        ],
    },
    "pt": {
        "title": "Alfred — Política de privacidade",
        "updated": "Última atualização",
        "sections": [
            (
                "Quem somos",
                [
                    f"O Alfred é um assistente pessoal no WhatsApp, operado por {CONTROLLER} "
                    "(responsável pelo tratamento).",
                    f"Dúvidas ou pedidos sobre os teus dados: {CONTACT}.",
                ],
            ),
            (
                "Que dados tratamos",
                [
                    "O teu número de WhatsApp e nome de perfil; as mensagens que envias ao "
                    "Alfred e as respostas do Alfred.",
                    "O que pedes ao Alfred para registar: despesas e receitas, lembretes, "
                    "treinos, metas e hábitos, notas e tarefas, viagens.",
                    "Se escolheres registá-los: dados de saúde (medicação, humor, sono, "
                    "água). São dados de categoria especial; só os tratamos com o teu "
                    "consentimento explícito, que podes retirar a qualquer momento.",
                ],
            ),
            (
                "Porquê e com que base legal",
                [
                    "Para prestar o serviço que pedes (RGPD art. 6.º, n.º 1, al. b)); dados "
                    "de saúde só com consentimento explícito (art. 9.º, n.º 2, al. a)).",
                    "Não vendemos os teus dados, não os usamos para publicidade nem para "
                    "treinar modelos de IA.",
                ],
            ),
            (
                "Inteligência artificial",
                [
                    "O Alfred é um sistema de IA, não uma pessoa (Regulamento Europeu da IA, "
                    "art. 50.º). As tuas mensagens são processadas por um modelo de IA da "
                    "Anthropic para as compreender e responder. A Anthropic não usa estes "
                    "dados para treinar os seus modelos.",
                ],
            ),
            (
                "Quem mais trata os teus dados",
                [
                    "Meta Platforms (WhatsApp Business API) — entrega de mensagens.",
                    "Anthropic PBC — processamento de texto por IA.",
                    "Railway Corp. — alojamento da aplicação e da base de dados.",
                    "Alguns destes fornecedores tratam dados fora da UE; essas transferências "
                    "estão cobertas pelo EU–US Data Privacy Framework e/ou por cláusulas "
                    "contratuais-tipo.",
                ],
            ),
            (
                "Durante quanto tempo",
                [
                    "Enquanto usares o Alfred. Depois de pedires a eliminação da conta, os "
                    "teus dados são apagados em 30 dias, salvo obrigação legal de os manter.",
                ],
            ),
            (
                "Os teus direitos",
                [
                    "Podes pedir acesso, retificação, eliminação, limitação ou uma cópia dos "
                    "teus dados (portabilidade), e opor-te ao tratamento. Envia STOP no "
                    f"WhatsApp para parar todo o tratamento, ou escreve para {CONTACT}.",
                    "Podes apresentar queixa à autoridade neerlandesa, a Autoriteit "
                    "Persoonsgegevens (autoriteitpersoonsgegevens.nl).",
                ],
            ),
        ],
    },
}

_CSS = """
:root{--bg:#fbfbf9;--fg:#1d2327;--muted:#5b646b;--line:#e3e5e2;--accent:#1f6f5c}
@media (prefers-color-scheme:dark){:root{--bg:#141719;--fg:#e8eaeb;--muted:#9aa3a9;
--line:#2a2f33;--accent:#6fc3a9}}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:42rem;margin:0 auto;padding:2.5rem 1rem 4rem}
h1{font-size:1.6rem;line-height:1.25;margin:0 0 .25rem;text-wrap:balance}
h2{font-size:1.05rem;margin:2rem 0 .5rem}
p{margin:.4rem 0;color:var(--fg)}
.meta{color:var(--muted);font-size:.9rem;margin-bottom:1.5rem}
nav{display:flex;gap:.75rem;font-size:.9rem;margin-bottom:1.5rem}
nav a{color:var(--accent)}
"""


def render_policy(lang: str) -> str:
    """Full HTML page for one language (unknown languages fall back to English)."""
    policy = POLICY.get(lang, POLICY["en"])
    lang = lang if lang in POLICY else "en"
    parts = [
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{escape(policy['title'])}</title><style>{_CSS}</style></head><body><main>",
        '<nav><a href="?lang=en">English</a><a href="?lang=nl">Nederlands</a>'
        '<a href="?lang=pt">Português</a></nav>',
        f"<h1>{escape(policy['title'])}</h1>",
        f'<p class="meta">{escape(policy["updated"])}: {POLICY_DATE} · v{POLICY_VERSION}</p>',
    ]
    for heading, paragraphs in policy["sections"]:
        parts.append(f"<h2>{escape(heading)}</h2>")
        parts.extend(f"<p>{escape(p)}</p>" for p in paragraphs)
    parts.append("</main></body></html>")
    return "".join(parts)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy(lang: str = Query(default="en", max_length=5)) -> HTMLResponse:
    return HTMLResponse(
        render_policy(lang.lower()), headers={"Cache-Control": "public, max-age=3600"}
    )
