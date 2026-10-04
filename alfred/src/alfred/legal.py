"""Public legal pages — privacy policy (GDPR/AVG art. 13) at /privacy.

Required by Meta to publish the WhatsApp app, and by the GDPR for any user.
Texts live in POLICY below (en / nl / pt); ``?lang=`` picks one, default en.
Update ``POLICY_VERSION`` and the "last updated" date whenever the text changes.
"""

# ruff: noqa: E501
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from alfred.settings import settings

router = APIRouter(tags=["legal"])

POLICY_VERSION = "1.3"
POLICY_DATE = "2026-10-04"
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
            (
                "Your dashboard link",
                [
                    "The link Alfred sends you to see your dashboard contains a personal access code. Anyone with the link can see your dashboard, so do not share it. Links expire after 7 days; ask for 'my dashboard' to get a new one.",
                ],
            ),
            (
                "Exercising your rights in the chat",
                [
                    "Send 'export my data' to receive a download link (valid for 15 minutes, works once), 'delete my data' to erase everything (you will be asked to confirm), or STOP to stop processing.",
                ],
            ),
            (
                "Error monitoring",
                [
                    "When enabled, an error-monitoring provider (Sentry) receives technical error reports to keep the service reliable. We configure it to avoid sending message contents.",
                ],
            ),
            (
                "More of what you can record",
                [
                    "Debts and loans: the name you give for the other person, the amount and a note. Please record only what concerns you; that person has the same rights over their name and can ask us to erase it. Training plans and the loads you lift, trip destinations, dates, itineraries, packing lists and trip budgets, monthly budgets, fixed bills and instalments, and analysis views you save are also kept. The dashboard shows all of this to anyone who has your link.",
                    "Questions you ask Alfred about your own numbers (for example 'how much did I spend on food in August?') are sent as text to the AI provider to understand the question; the figures themselves are calculated by us and are not sent to the AI provider.",
                    "A log of the messages Alfred sent you, with their delivery status, is kept for about three months and then erased.",
                ],
            ),
            (
                "Security",
                [
                    "Data travels over encrypted connections. Health information (what you log about medication, mood, sleep and water) is additionally encrypted inside our database. Access to the systems is limited to the operator.",
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
            (
                "Je dashboardlink",
                [
                    "De link die Alfred je stuurt voor je dashboard bevat een persoonlijke toegangscode. Iedereen met de link kan je dashboard zien, deel hem dus niet. Links verlopen na 7 dagen; vraag 'mijn dashboard' voor een nieuwe.",
                ],
            ),
            (
                "Je rechten uitoefenen in de chat",
                [
                    "Stuur 'exporteer mijn gegevens' voor een downloadlink (15 minuten geldig, werkt één keer), 'verwijder mijn gegevens' om alles te wissen (je moet dit bevestigen) of STOP om de verwerking te stoppen.",
                ],
            ),
            (
                "Foutmonitoring",
                [
                    "Indien ingeschakeld ontvangt een foutmonitoringdienst (Sentry) technische foutmeldingen om de dienst betrouwbaar te houden. We stellen die zo in dat berichtinhoud niet wordt meegestuurd.",
                ],
            ),
            (
                "Meer gegevens die je kunt vastleggen",
                [
                    "Schulden en leningen: de naam die je van de andere persoon opgeeft, het bedrag en een notitie. Leg alleen vast wat jou aangaat; die persoon heeft dezelfde rechten over zijn of haar naam en kan ons vragen die te wissen. Ook bewaren we trainingsschema's en de gewichten die je tilt, reisbestemmingen, data, routes, paklijsten en reisbudgetten, maandbudgetten, vaste lasten en termijnen, en analyseweergaven die je opslaat. Het dashboard toont dit alles aan iedereen die je link heeft.",
                    "Vragen die je Alfred stelt over je eigen cijfers (bijvoorbeeld 'hoeveel gaf ik in augustus uit aan eten?') worden als tekst naar de AI-aanbieder gestuurd om de vraag te begrijpen; de bedragen zelf rekenen wij uit en gaan niet naar de AI-aanbieder.",
                    "Een logboek van de berichten die Alfred je stuurde, met de bezorgstatus, bewaren we ongeveer drie maanden en wissen we daarna.",
                ],
            ),
            (
                "Beveiliging",
                [
                    "Gegevens gaan via versleutelde verbindingen. Gezondheidsgegevens (wat je vastlegt over medicatie, stemming, slaap en water) worden bovendien versleuteld in onze database opgeslagen. Toegang tot de systemen is beperkt tot de beheerder.",
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
                    f"Dúvidas ou pedidos sobre os seus dados: {CONTACT}.",
                ],
            ),
            (
                "Quais dados tratamos",
                [
                    "Seu número de WhatsApp e nome de perfil; as mensagens que você envia ao "
                    "Alfred e as respostas do Alfred.",
                    "O que você pede ao Alfred para registrar: despesas e receitas, lembretes, "
                    "treinos, metas e hábitos, notas e tarefas, viagens.",
                    "Se você escolher registrá-los: dados de saúde (medicação, humor, sono, "
                    "água). São dados de categoria especial; só os tratamos com o seu "
                    "consentimento explícito, que você pode retirar a qualquer momento.",
                ],
            ),
            (
                "Por quê e com que base legal",
                [
                    "Para prestar o serviço que você pede (RGPD art. 6.º, n.º 1, al. b)); dados "
                    "de saúde só com consentimento explícito (art. 9.º, n.º 2, al. a)).",
                    "Não vendemos os seus dados, não os usamos para publicidade nem para "
                    "treinar modelos de IA.",
                ],
            ),
            (
                "Inteligência artificial",
                [
                    "O Alfred é um sistema de IA, não uma pessoa (Regulamento Europeu da IA, "
                    "art. 50.º). Suas mensagens são processadas por um modelo de IA da "
                    "Anthropic para as compreender e responder. A Anthropic não usa estes "
                    "dados para treinar os seus modelos.",
                ],
            ),
            (
                "Quem mais trata os seus dados",
                [
                    "Meta Platforms (WhatsApp Business API) — entrega de mensagens.",
                    "Anthropic PBC — processamento de texto por IA.",
                    "Railway Corp. — hospedagem da aplicação e da base de dados.",
                    "Alguns destes fornecedores tratam dados fora da UE; essas transferências "
                    "estão cobertas pelo EU–US Data Privacy Framework e/ou por cláusulas "
                    "contratuais-tipo.",
                ],
            ),
            (
                "Durante quanto tempo",
                [
                    "Enquanto você usar o Alfred. Depois de pedir a exclusão da conta, os "
                    "seus dados são apagados em 30 dias, salvo obrigação legal de os manter.",
                ],
            ),
            (
                "Seus direitos",
                [
                    "Você pode pedir acesso, correção, exclusão, limitação ou uma cópia dos "
                    "seus dados (portabilidade) e se opor ao tratamento. Envie STOP no "
                    f"WhatsApp para parar todo o tratamento, ou escreva para {CONTACT}.",
                    "Você pode reclamar à autoridade neerlandesa, a Autoriteit "
                    "Persoonsgegevens (autoriteitpersoonsgegevens.nl).",
                ],
            ),
            (
                "Seu link do painel",
                [
                    "O link que o Alfred envia para você ver o painel contém um código pessoal de acesso. Quem tiver o link vê o seu painel, então não compartilhe. Os links expiram em 7 dias; peça 'meu dashboard' para receber um novo.",
                ],
            ),
            (
                "Exercendo seus direitos no chat",
                [
                    "Envie 'exportar meus dados' para receber um link de download (vale por 15 minutos e funciona uma vez), 'apagar meus dados' para excluir tudo (você precisará confirmar) ou STOP para parar o tratamento.",
                ],
            ),
            (
                "Monitoramento de erros",
                [
                    "Quando ativado, um provedor de monitoramento de erros (Sentry) recebe relatórios técnicos de falhas para manter o serviço confiável. Configuramos para não enviar o conteúdo das mensagens.",
                ],
            ),
            (
                "Mais dados que você pode registrar",
                [
                    "Dívidas e empréstimos: o nome que você informa da outra pessoa, o valor e uma nota. Registre apenas o que diz respeito a você; essa pessoa tem os mesmos direitos sobre o próprio nome e pode pedir que o apaguemos. Também guardamos planos de treino e as cargas que você usa, destinos, datas, roteiros, listas de mala e orçamentos de viagem, orçamentos mensais, contas fixas e parcelas, e as visões de análise que você salva. O painel mostra tudo isso a quem tiver o seu link.",
                    "As perguntas que você faz ao Alfred sobre os seus próprios números (por exemplo, 'quanto gastei com comida em agosto?') são enviadas como texto ao provedor de IA para entender a pergunta; os valores em si são calculados por nós e não vão para o provedor de IA.",
                    "Um registro das mensagens que o Alfred enviou a você, com o estado de entrega, é guardado por cerca de três meses e depois apagado.",
                ],
            ),
            (
                "Segurança",
                [
                    "Os dados trafegam por conexões criptografadas. As informações de saúde (o que você registra sobre medicação, humor, sono e água) ficam, além disso, criptografadas dentro do nosso banco de dados. O acesso aos sistemas é restrito ao operador.",
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
