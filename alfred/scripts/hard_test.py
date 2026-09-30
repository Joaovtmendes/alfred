#!/usr/bin/env python3
"""
Alfred Hard Test — cobertura completa A–L
Simula mensagens WhatsApp reais via POST ao webhook com assinatura HMAC válida.
As respostas chegam ao teu WhatsApp em tempo real.

Uso:
  python scripts/hard_test.py
  python scripts/hard_test.py --blocos A,K
  python scripts/hard_test.py --blocos B --delay 6

Notas:
  - Cada run usa um run_id único → sem colisões de wa_message_id
  - O número destino (+31631152144) recebe todas as respostas normalmente
  - Respeita a ordem sequencial dos casos dentro de cada bloco
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time

# ── Load .env ──
from pathlib import Path

import httpx

env_file = Path(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            os.environ[key.strip()] = val.strip().strip("\"'")


# ── config ──────────────────────────────────────────────────────────────────
WEBHOOK_URL = os.environ.get(
    "HARD_TEST_WEBHOOK_URL",
    "https://alfred-production-5d54.up.railway.app/webhook/whatsapp",
)
APP_SECRET = os.environ["WHATSAPP_APP_SECRET"]
FROM_NUMBER = "31631152144"  # teu número — recebe respostas
PHONE_NUMBER_ID = "1293756070490097"
WABA_ID = "3869329083209695"

DEFAULT_DELAY = 4.0  # segundos entre mensagens


# ── helpers ──────────────────────────────────────────────────────────────────
def _payload(text: str, msg_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551795431",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [{"profile": {"name": "Joao"}, "wa_id": FROM_NUMBER}],
                            "messages": [
                                {
                                    "from": FROM_NUMBER,
                                    "id": msg_id,
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": text},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


_seq_counter = 0


def send(label: str, text: str, run_id: str, delay: float = DEFAULT_DELAY) -> int:
    global _seq_counter
    _seq_counter += 1
    msg_id = f"wamid.ht.{run_id}.{_seq_counter:04d}"

    body = json.dumps(_payload(text, msg_id)).encode()
    sig = _sign(body)

    try:
        resp = httpx.post(
            WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
            timeout=15,
        )
        status = "✅" if resp.status_code == 200 else f"❌ {resp.status_code}"
        extra = f"  ← {resp.text[:120]}" if resp.status_code != 200 else ""
        print(f"  [{_seq_counter:03d}] {status}  [{label}]  {repr(text)}{extra}")
    except Exception as exc:
        print(f"  [{_seq_counter:03d}] ❌ ERRO  [{label}]  {repr(text)}  → {exc}")

    time.sleep(delay)
    return getattr(resp, "status_code", 0) if "resp" in dir() else 0


def _tap_payload(button_id: str, msg_id: str) -> dict:
    """Same envelope as a text message, but the message is a reply-button tap."""
    payload = _payload("", msg_id)
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del msg["text"]
    msg["type"] = "interactive"
    msg["interactive"] = {
        "type": "button_reply",
        "button_reply": {"id": button_id, "title": "teste"},
    }
    return payload


def send_tap(label: str, button_id: str, run_id: str, delay: float = DEFAULT_DELAY) -> int:
    """Simulate tapping a reply button (Meta sends ``interactive.button_reply``)."""
    global _seq_counter
    _seq_counter += 1
    msg_id = f"wamid.ht.{run_id}.{_seq_counter:04d}"
    body = json.dumps(_tap_payload(button_id, msg_id)).encode()
    code = 0
    try:
        resp = httpx.post(
            WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
            timeout=15,
        )
        code = resp.status_code
        status = "✅" if code == 200 else f"❌ {code}"
        print(f"  [{_seq_counter:03d}] {status}  [{label}]  TAP {button_id!r}")
    except Exception as exc:
        print(f"  [{_seq_counter:03d}] ❌ ERRO  [{label}]  TAP {button_id!r}  → {exc}")
    time.sleep(delay)
    return code


# ══════════════════════════════════════════════════════════════════════════════
# BLOCOS
# ══════════════════════════════════════════════════════════════════════════════


def bloco_a(run_id, delay):
    """Core Financeiro — despesas, receitas, correcção, queries temporais."""
    print("\n══ Bloco A — Core Financeiro ══")

    # --- registo de despesa ---
    send("A01 despesa com merchant", "Jantar Restaurante Flores 45€", run_id, delay)
    send("A02 despesa com descrição", "fui ao supermercado e gastei 23€", run_id, delay)
    send("A03 despesa valor alto (≥200€)", "comprei um computador por 1200€", run_id, delay)
    send("A04 despesa só montante", "50€", run_id, delay)
    send("A05 despesa sem montante", "compra", run_id, delay)

    # --- receitas ---
    send("A06 receita salário", "recebi salário 3000€", run_id, delay)
    send("A07 receita bónus", "recebi bónus da empresa 500€", run_id, delay)

    # --- correcção de valor (BUG-04 fix) ---
    send("A08 despesa para corrigir", "Mercado 80€", run_id, delay)
    send("A09 correcção valor válida", "errei foram 42€", run_id, delay)
    # edge case: sem despesa recente para corrigir
    send("A10 apagar última", "apaga", run_id, delay)
    send("A11 correcção sem despesa", "errei foram 99€", run_id, delay)

    # --- apagar ---
    send("A12 despesa nova", "Spotify 10€", run_id, delay)
    send("A13 apagar última", "apaga", run_id, delay)

    # --- saldo ---
    send("A14 saldo", "qual é o meu saldo?", run_id, delay)
    send("A15 saldo alternativo", "saldo", run_id, delay)

    # --- resumo e queries temporais (BUG-03 fix) ---
    send("A16 quanto hoje (BUG-03)", "quanto gastei hoje?", run_id, delay)
    send("A17 gastos hoje variante", "gastos de hoje", run_id, delay)
    send("A18 quanto ontem", "quanto gastei ontem?", run_id, delay)
    send("A19 esta semana", "quanto gastei esta semana?", run_id, delay)
    send("A20 gastos semana passada", "gastos da semana passada", run_id, delay)
    send("A21 resumo mês actual", "gastos deste mês", run_id, delay)
    send("A22 resumo mês passado", "gastos do mês passado", run_id, delay)
    send("A23 resumo setembro", "resumo de setembro", run_id, delay)
    send("A24 comparação meses", "setembro vs agosto", run_id, delay)

    # --- queries avançadas ---
    send("A25 últimas N", "últimas 5 despesas", run_id, delay)
    send("A26 top categorias", "top categorias este mês", run_id, delay)
    send("A27 year-to-date", "quanto gastei este ano?", run_id, delay)


def bloco_b(run_id, delay):
    """Viagem (M14) — criar, despesas auto-tag, saldo, terminar, listar."""
    print("\n══ Bloco B — Viagem ══")

    # limpar viagem activa anterior
    send("B00 fechar viagem anterior", "voltei", run_id, delay)

    # criar viagem
    send("B01 iniciar viagem PT", "criar viagem Portugal €500 de 1 a 7 de outubro", run_id, delay)
    send("B02 confirmar", "sim", run_id, delay)

    # despesas durante viagem (auto-tag)
    send("B03 despesa hotel", "hotel Lisboa 120€", run_id, delay)
    send("B04 despesa jantar viagem", "jantar Porto 45€", run_id, delay)
    send("B05 despesa transporte", "uber Porto 15€", run_id, delay)

    # consultas de viagem
    send("B06 saldo viagem", "quanto gastei na viagem?", run_id, delay)
    send("B07 resumo viagem", "resumo da viagem", run_id, delay)

    # terminar viagem
    send("B08 terminar viagem", "voltei", run_id, delay)

    # listar
    send("B09 listar viagens", "as minhas viagens", run_id, delay)

    # multilíngue
    send("B10 EN criar trip", "trip: Amsterdam", run_id, delay)
    send("B11 EN confirmar", "yes", run_id, delay)
    send("B12 EN despesa trip", "train ticket 35€", run_id, delay)
    send("B13 EN encerrar trip", "back home", run_id, delay)
    send("B14 NL criar reis", "op reis naar Berlijn €300", run_id, delay)
    send("B15 NL confirmar", "ja", run_id, delay)
    send("B16 NL despesa reis", "hotel Amsterdam 90€", run_id, delay)
    send("B17 NL encerrar reis", "terug", run_id, delay)


def bloco_c(run_id, delay):
    """Lembretes (M5) — criar, listar, cancelar."""
    print("\n══ Bloco C — Lembretes ══")

    send(
        "C01 lembrete diário",
        "configura lembrete: tomar medicação às 09:00 todos os dias",
        run_id,
        delay,
    )
    send("C02 lembrete semanal", "lembrete: pagar renda toda a segunda às 10:00", run_id, delay)
    send("C03 lembrete único", "lembrete: ligar ao dentista amanhã às 14h", run_id, delay)
    send(
        "C04 lembrete dias úteis", "lembrete: reunião de equipa às 09:30 dias úteis", run_id, delay
    )

    send("C05 listar lembretes", "os meus lembretes", run_id, delay)

    send("C06 cancelar medicação", "cancela lembrete de medicação", run_id, delay)
    send("C07 cancelar renda", "cancela lembrete de renda", run_id, delay)

    send("C08 listar após cancelar", "os meus lembretes", run_id, delay)


def bloco_d(run_id, delay):
    """Treino (M7) — registo, retroactivo, queries."""
    print("\n══ Bloco D — Treino ══")

    send("D01 corrida hoje", "corri 5km em 30 minutos hoje", run_id, delay)
    send(
        "D02 multi-actividade",
        "fiz 45 minutos de corrida e 20 minutos de musculação",
        run_id,
        delay,
    )
    send("D03 corrida retroactiva", "corri 8km ontem", run_id, delay)
    send("D04 natação", "nadei 1km hoje", run_id, delay)
    send("D05 ciclismo com distância", "andei 25km de bicicleta hoje", run_id, delay)

    send("D06 query semanal", "os meus treinos desta semana", run_id, delay)
    send("D07 query mensal", "treinos deste mês", run_id, delay)
    send("D08 contagem por actividade", "quantas vezes corri esta semana?", run_id, delay)
    send("D09 contagem musculação", "quantas vezes fiz musculação este mês?", run_id, delay)

    send("D10 apagar treino hoje", "apaga o treino de hoje", run_id, delay)
    send("D11 apagar último treino", "apaga o último treino", run_id, delay)


def bloco_e(run_id, delay):
    """Saúde & Bem-estar (M8) — medicação, humor, sono, água, queries."""
    print("\n══ Bloco E — Saúde ══")

    send("E01 medicação", "tomei omeprazol", run_id, delay)
    send("E02 medicação com hora", "tomei vitamina D às 08:00", run_id, delay)
    send("E03 humor", "humor hoje 8/10", run_id, delay)
    send("E04 humor variante", "hoje sinto-me um 6 em 10", run_id, delay)
    send("E05 sono", "dormi 7 horas", run_id, delay)
    send("E06 sono retroactivo", "ontem dormi 6h30", run_id, delay)
    send("E07 água", "bebi 1.5L de água", run_id, delay)
    send("E08 água extra", "mais 500ml de água agora", run_id, delay)
    send("E09 peso e pressão", "peso 75kg pressão 12/8", run_id, delay)

    send("E10 query humor semana", "como foi o meu humor esta semana?", run_id, delay)
    send("E11 query sono médio", "quantas horas dormi em média?", run_id, delay)
    send("E12 query medicação adherência", "tomei a medicação todos os dias?", run_id, delay)
    send("E13 query água hoje", "bebi água suficiente hoje?", run_id, delay)


def bloco_f(run_id, delay):
    """Metas & Hábitos (M9) — criar, log, streak, listar, concluir."""
    print("\n══ Bloco F — Metas & Hábitos ══")

    send("F01 meta poupança", "meta: quero poupar 5000€ até dezembro", run_id, delay)
    send("F02 meta treino", "meta: correr 3x por semana", run_id, delay)
    send("F03 meta hábito", "meta: meditar todos os dias", run_id, delay)

    send("F04 log hábito keyword", "meditei hoje", run_id, delay)
    send("F05 log hábito free-form", "fiz yoga durante 20 minutos", run_id, delay)
    send("F06 log hábito retroactivo", "meditei ontem", run_id, delay)

    send("F07 streak hábito", "quantos dias seguidos meditei?", run_id, delay)
    send("F08 frequência hábito", "quantas vezes meditei esta semana?", run_id, delay)
    send("F09 listar metas", "as minhas metas", run_id, delay)

    send("F10 concluir meta", "meta de corrida concluída", run_id, delay)
    send("F11 listar após concluir", "as minhas metas", run_id, delay)


def bloco_g(run_id, delay):
    """Produtividade (M10) — notas, tarefas, concluir, apagar."""
    print("\n══ Bloco G — Produtividade ══")

    send("G01 nota simples", "nota: ideia para o Alfred — notificações push", run_id, delay)
    send(
        "G02 nota longa",
        "nota: reunião com cliente segunda às 14h sobre proposta comercial — levar exemplos de campanha",
        run_id,
        delay,
    )
    send("G03 listar notas", "as minhas notas", run_id, delay)

    send("G04 tarefa com prazo", "tarefa: enviar relatório até sexta", run_id, delay)
    send("G05 tarefa sem prazo", "tarefa: comprar presente para o aniversário", run_id, delay)
    send("G06 tarefa urgente", "tarefa: confirmar reserva de hotel até amanhã", run_id, delay)

    send("G07 listar tarefas", "as minhas tarefas", run_id, delay)
    send("G08 concluir tarefa por número", "feito: 1", run_id, delay)
    send("G09 concluir tarefa por texto", "feito: relatório", run_id, delay)
    send("G10 apagar tarefa", "apaga tarefa de aniversário", run_id, delay)
    send("G11 listar após mudanças", "as minhas tarefas", run_id, delay)


def bloco_h(run_id, delay):
    """Dashboard (M11) — link, re-pedir."""
    print("\n══ Bloco H — Dashboard ══")

    send("H01 pedir dashboard PT", "meu dashboard", run_id, delay)
    send("H02 pedir dashboard EN", "my dashboard", run_id, delay)
    send("H03 pedir dashboard NL", "mijn dashboard", run_id, delay)


def bloco_i(run_id, delay):
    """Aprendizagem de Categorias (M12) — override, re-uso, múltiplos merchants."""
    print("\n══ Bloco I — Categorias M12 ══")

    # merchant desconhecido — deve pedir categoria? (BUG-09)
    send("I01 merchant desconhecido", "Zara 89€", run_id, delay)
    send("I02 override Zara", "na verdade Zara é roupa", run_id, delay)
    send("I03 re-registar Zara (override)", "Zara 55€", run_id, delay)

    send("I04 merchant desconhecido 2", "Jumbo 47€", run_id, delay)
    send("I05 override Jumbo", "Jumbo é supermercado", run_id, delay)
    send("I06 re-registar Jumbo", "Jumbo 30€", run_id, delay)

    send("I07 merchant desconhecido 3", "Decathlon 120€", run_id, delay)
    send("I08 override Decathlon", "Decathlon is sports", run_id, delay)
    send("I09 re-registar Decathlon", "Decathlon 60€", run_id, delay)


def bloco_j(run_id, delay):
    """i18n — EN, NL além do PT base."""
    print("\n══ Bloco J — i18n ══")

    # EN
    send("J01 EN despesa", "spent 30 euros at Lidl", run_id, delay)
    send("J02 EN receita", "received salary 3000 euros", run_id, delay)
    send("J03 EN resumo", "my summary", run_id, delay)
    send("J04 EN hoje (BUG-03 EN)", "how much did I spend today?", run_id, delay)
    send("J05 EN saldo", "what is my balance?", run_id, delay)
    send("J06 EN dashboard", "my dashboard", run_id, delay)

    # NL
    send("J07 NL despesa", "30 euro bij Albert Heijn", run_id, delay)
    send("J08 NL receita", "salaris ontvangen 2800 euro", run_id, delay)
    send("J09 NL resumo", "mijn overzicht", run_id, delay)
    send("J10 NL hoje", "hoeveel heb ik vandaag uitgegeven?", run_id, delay)
    send("J11 NL saldo", "mijn saldo", run_id, delay)


def bloco_k(run_id, delay):
    """Edge Cases — gibberish, emoji, valores extremos, duplicado, ambíguo."""
    print("\n══ Bloco K — Edge Cases ══")

    # gibberish (BUG-08 fix) — palavra única → deve dizer "não entendi"
    send("K01 gibberish 1 palavra", "asdfjklqwerty", run_id, delay)
    send("K02 gibberish 2 palavras", "xzq pqr", run_id, delay)
    send("K03 só número", "12345", run_id, delay)

    # emoji
    send("K04 emoji só", "🙂", run_id, delay)
    send("K05 emoji + texto", "🍕 pizza 12€", run_id, delay)

    # valores extremos
    send("K06 valor muito alto", "paguei 99999€ de renda", run_id, delay)
    send("K07 valor zero", "café 0€", run_id, delay)
    send("K08 valor negativo", "reembolso -15€", run_id, delay)
    send("K09 múltiplas moedas", "gastei 50 dollars", run_id, delay)

    # input ambíguo
    send("K10 sem montante", "compra", run_id, delay)
    send("K11 palavra comum", "gastos", run_id, delay)
    send(
        "K12 mensagem muito longa",
        "hoje fui ao supermercado e gastei 23 euros, depois fui ao ginásio, almocei no restaurante por 18 euros, e à tarde comprei umas coisas na farmácia por 8 euros e um livro por 15 euros",
        run_id,
        delay,
    )

    # duplicado — mesmo msg_id envia duas vezes
    unique_dup_id = f"wamid.ht.dup.{run_id}"
    body1 = json.dumps(_payload("Mercado 25€", unique_dup_id)).encode()
    sig1 = _sign(body1)
    r1 = httpx.post(
        WEBHOOK_URL,
        content=body1,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig1},
        timeout=15,
    )
    print(
        f"  [K13] {'✅' if r1.status_code == 200 else '❌'}  [K13 duplicado 1ª vez]  (id={unique_dup_id})"
    )
    time.sleep(delay)

    r2 = httpx.post(
        WEBHOOK_URL,
        content=body1,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig1},
        timeout=15,
    )
    print(
        f"  [K14] {'✅ (esperado 200 mas sem resposta)' if r2.status_code == 200 else '❌'}  [K14 duplicado 2ª vez — deve ser silenciado]"
    )
    time.sleep(delay)

    # assinatura inválida — deve retornar 4xx
    bad_body = json.dumps(_payload("tentativa inválida 99€", f"wamid.ht.bad.{run_id}")).encode()
    bad_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"
    r_bad = httpx.post(
        WEBHOOK_URL,
        content=bad_body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": bad_sig},
        timeout=15,
    )
    expected = (
        "✅ (esperado 4xx)"
        if r_bad.status_code in (400, 401, 403)
        else f"⚠️  retornou {r_bad.status_code} (esperado 4xx)"
    )
    print(f"  [K15] {expected}  [K15 assinatura inválida]")
    time.sleep(delay)


def bloco_l(run_id, delay):
    """Sprint 4d/4b/5 — botões, valores inválidos, links (dashboard/exportação).

    Só o que é seguro: nunca envia "apagar meus dados" nem toca num botão que apague de
    verdade (os botões reais têm o id do gasto, que só o WhatsApp conhece). Os toques aqui
    usam ids falsos/antigos para provar que o webhook os trata sem efeito colateral.
    """
    print("\n══ Bloco L — Botões, valores inválidos, links ══")
    import uuid as _uuid

    send("L01 gasto normal → resposta com [Editar][Desfazer]", "Padaria 6,40", run_id, delay)
    send(
        "L02 valor alto → 'tens a certeza do valor?' + [Está certo][Desfazer]",
        "Televisão 1500",
        run_id,
        delay,
    )
    send("L03 apaga (limpa a televisão)", "apaga", run_id, delay)
    send("L04 apaga (limpa a padaria)", "apaga", run_id, delay)
    send("L05 zero + negativo → 'Valor inválido'", "Café 0 e reembolso -15", run_id, delay)
    send("L06 link do dashboard", "meu dashboard", run_id, delay)
    send("L07 exportar dados → link JSON", "exportar meus dados", run_id, delay)
    send_tap(
        "L08 toque Desfazer com id inexistente → 'já não existe'",
        f"undo:{_uuid.uuid4()}",
        run_id,
        delay,
    )
    send_tap("L09 toque Cancelar → 'não apaguei nada'", "keep:0", run_id, delay)
    send_tap(
        "L10 toque Apagar tudo expirado → 'expirou' (não apaga)",
        f"wipe:{int(time.time()) - 7200}",
        run_id,
        delay,
    )
    send_tap("L11 toque com id lixo → silêncio (sem resposta)", "lixo", run_id, delay)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

BLOCOS = {
    "A": bloco_a,
    "B": bloco_b,
    "C": bloco_c,
    "D": bloco_d,
    "E": bloco_e,
    "F": bloco_f,
    "G": bloco_g,
    "H": bloco_h,
    "I": bloco_i,
    "J": bloco_j,
    "K": bloco_k,
    "L": bloco_l,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alfred Hard Test")
    parser.add_argument(
        "--blocos",
        default=",".join(BLOCOS.keys()),
        help="Blocos a correr (ex: A,B,K). Default: todos.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Segundos entre mensagens (default: {DEFAULT_DELAY})",
    )
    args = parser.parse_args()

    run_id = str(int(time.time()))
    selecionados = [b.strip().upper() for b in args.blocos.split(",") if b.strip()]
    desconhecidos = [b for b in selecionados if b not in BLOCOS]
    if desconhecidos:
        print(f"Blocos desconhecidos: {desconhecidos}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print(f"Alfred Hard Test — run_id={run_id}")
    print(f"Blocos: {', '.join(selecionados)}")
    print(f"Webhook: {WEBHOOK_URL}")
    print(f"Delay: {args.delay}s entre mensagens")
    print(f"Respostas → WhatsApp +{FROM_NUMBER}")
    print("=" * 60)

    t_start = time.time()

    for bloco in selecionados:
        BLOCOS[bloco](run_id, args.delay)

    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"Concluído em {elapsed:.0f}s — {_seq_counter} mensagens enviadas")
    print("Verifica o teu WhatsApp e os Railway Logs para as respostas.")
    print("=" * 60)
