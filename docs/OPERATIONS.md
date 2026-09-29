# Operações

## Deploy

- **Produção:** Railway, projecto Alfred, a partir do GitHub `Joaovtmendes/alfred`,
  branch `master`, Root Directory `alfred/` (confirmar em Railway → Settings → Source).
- Cada deploy corre `alembic upgrade head` antes de arrancar o uvicorn (`start.sh`).
- Fluxo: branch → PR (o CI corre lint, migrações e testes) → merge → deploy automático.
- Antes e depois: skill `/deploy-checklist` no Claude Code.

## Serviços Railway

Projecto `alert-empathy`, ambiente `production`. Configuração feita **no painel**
(o Railway descontinuou o config-as-code; `alfred/railway.toml` só vale até 01/12/2026).

| Serviço | Start command | Schedule | Variáveis |
|---|---|---|---|
| `alfred` (web) | CMD do Dockerfile (`start.sh`) | — | todas as do `.env.example` |
| `alfred-cron` | `python /app/scripts/daily_cron.py` | Every 15 minutes | `DATABASE_URL=${{alfred.DATABASE_URL}}`, `WHATSAPP_TOKEN=${{alfred.WHATSAPP_TOKEN}}`, `WHATSAPP_PHONE_NUMBER_ID=${{alfred.WHATSAPP_PHONE_NUMBER_ID}}` |
| `Postgres` | gerido | — | — |

Ambos os serviços têm Root Directory `alfred` e activar **Wait for CI** (deploy só
depois do CI verde).

Pendentes de infra: região actual é **US West** (dados de utilizadores UE → mover
para EU West/Amsterdã); plano actual é trial (upgrade para Hobby antes de acabar).

## Variáveis de ambiente

A lista completa, com explicação, está em `alfred/.env.example`. As críticas:

| Variável | Sem ela |
|---|---|
| `WHATSAPP_APP_SECRET` | o webhook rejeita tudo (401), de propósito |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | o Alfred não consegue responder |
| `LLM_API_KEY` | respostas de erro no lugar de LLM |
| `BASE_URL` | o link do dashboard não é enviado |
| `TIMEZONE` | default `Europe/Amsterdam` |

## Processamento de mensagens

- O webhook grava a mensagem e responde 200 em ~0,1 s; o handler corre depois, em
  background. Se o processo morrer a meio, a mensagem fica com `processed = false` e o
  serviço web re-tenta-a (a cada minuto, mensagens com 2–20 min).
- Alerta recomendado: log `webhook.stale_unprocessed_message` (mensagem que ficou mais de
  20 min sem resposta) e `webhook.handle_inbound_failed`.
- Deploy: a migração `a1d5c7e9b3f2` marca as mensagens antigas como processadas e converte
  valores inválidos (NaN/inf/≥1e10) em 0,00 em vez de falhar. Numa segunda instância em
  paralelo (deploy rolling), a linha da mensagem é reservada com `SKIP LOCKED`.
- Ordem das respostas: quem obtém o lock do membro processa a mensagem mais antiga ainda por
  responder (não necessariamente a sua), até chegar à sua. Uma mensagem que falha é tentada
  uma vez por rajada e não bloqueia as seguintes.

## Lembretes (cron)

- Horas dos lembretes = hora local de Amsterdão, como o utilizador escreveu.
- O cron corre a cada 15 min e envia os jobs cuja hora passou há menos de 25 min e
  que ainda não foram enviados hoje (`last_sent_at`). Re-execuções são seguras.
- Resumo semanal: segundas às 09:00 locais, excepto para quem já tem o seu próprio
  job `weekly_summary`.
- Só envia **templates aprovados** na Meta (`m5-templates/`). Enquanto não forem
  aprovados, o envio falha e aparece `cron.send_failed` nos logs.

## Templates e Flows da Meta

- `alfred/m5-templates/`: 4 templates UTILITY (lembretes, resumo semanal).
- `alfred/m6-flow/`: Flow de onboarding + template de boas-vindas. O handler já
  está integrado em `conversation.handle_flow_onboarding`.
- Submissão: `python scripts/submit_templates.py` (dry run) e depois `--apply`, no teu
  Mac (usa `WHATSAPP_TOKEN` e `WHATSAPP_WABA_ID` do `.env`). Re-executar mostra o estado.
- Enviar templates exige **forma de pagamento** na WABA (Business Settings → WhatsApp
  → Configurações de pagamento).

## Testes

```bash
cd alfred
pytest -q                                             # unit (sem DB)
docker compose up -d db && alembic upgrade head
ALFRED_TEST_DB=1 pytest -q                            # + integração
python scripts/hard_test.py --blocos A,K --delay 2    # E2E contra produção
```

`hard_test.py` envia mensagens assinadas para o webhook de produção, e as respostas
chegam ao WhatsApp de teste. Corre-o **no Terminal do teu Mac**: dentro das sessões
do Claude o proxy de rede bloqueia o domínio do Railway. Esse era o "403" registado
em 28/09 — não era um firewall do Railway.

## Regras de registo (Sprint 3)

- Só euros: uma despesa em USD/GBP não é gravada (a resposta pede conversão). Somar moedas misturadas seria errado.
- Mensagem com 2+ valores ("mercado 20 e farmácia 10") passa por `extract_expenses_multi`; cada item é gravado e os não-EUR são listados como ignorados.
- Valores >= `HIGH_VALUE_THRESHOLD` (default 1000) recebem um aviso com o caminho de desfazer ("apaga" / "errei foram X"); não há bloqueio nem estado pendente.
- "errei foram X" só corrige uma despesa criada nas últimas 2 h (`CORRECTION_WINDOW_HOURS`).
- Fallback do LLM: se a resposta afirma "registada/saved/…", é substituída por `fallback_no_record` (nada foi gravado nesse caminho; o log tem `conversation.llm_false_record_claim`).
- `SECRET_KEY` foi removida: nada a usava (o dashboard usa tokens UUID por membro).
