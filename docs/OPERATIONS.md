# Operações

## Deploy

- **Produção:** Railway, projecto Alfred, a partir do GitHub `Joaovtmendes/alfred`,
  branch `master`, Root Directory `alfred/` (confirmar em Railway → Settings → Source).
- Cada deploy corre `alembic upgrade head` antes de arrancar o uvicorn (`start.sh`).
- Fluxo: branch → PR (o CI corre lint, migrações e testes) → merge → deploy automático.
- Antes e depois: skill `/deploy-checklist` no Claude Code.

## Serviços Railway

| Serviço | Start command | Schedule |
|---|---|---|
| `alfred-web` | `bash /app/start.sh` | — (healthcheck `/health`) |
| `alfred-cron` | `python /app/scripts/daily_cron.py` | `*/15 * * * *` |

Se ainda existir um serviço `alfred-worker`, pode ser apagado: o código procrastinate
foi removido.

## Variáveis de ambiente

A lista completa, com explicação, está em `alfred/.env.example`. As críticas:

| Variável | Sem ela |
|---|---|
| `WHATSAPP_APP_SECRET` | o webhook rejeita tudo (401), de propósito |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | o Alfred não consegue responder |
| `LLM_API_KEY` | respostas de erro no lugar de LLM |
| `BASE_URL` | o link do dashboard não é enviado |
| `TIMEZONE` | default `Europe/Amsterdam` |

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
- Submissão: WhatsApp Manager → Message Templates (depois da verificação do negócio).

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
