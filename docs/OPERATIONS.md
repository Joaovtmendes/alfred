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
| `DASHBOARD_TOKEN_TTL_DAYS` | default 7; se o Railway ainda tiver 90 herdado, o link vale 90 dias (conferir) |
| `WHATSAPP_DISPLAY_NUMBER` | só dígitos do número exibido da WABA; sem ele o painel v2 não mostra o botão "Conversar no WhatsApp" |

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

## Endurecimento (Sprint 4a)

- `GET /webhook/whatsapp` (verificação da Meta) recusa em produção o token vazio ou o placeholder `dev_verify_token` e compara em tempo constante. Definir `WHATSAPP_VERIFY_TOKEN` (valor aleatório) no Railway e o mesmo valor no webhook da Meta.
- `/openapi.json` e `/docs` estão desligados em produção.
- Scan estático: Bandit limpo (semgrep depende de semgrep.dev, bloqueado no ambiente do Claude; correr localmente com `semgrep --config p/python --config p/security-audit --metrics=off`).

## Painel v2 (flag por membro)

Ligar só para um número (console SQL do Railway):

```sql
UPDATE member SET dashboard_v2 = true WHERE wa_phone = '31600000000';
```

Reverter (o próximo `/d/{token}` já devolve a v1; nada mais muda):

```sql
UPDATE member SET dashboard_v2 = false WHERE wa_phone = '31600000000';
```

Antes de ligar: conferir `DASHBOARD_TOKEN_TTL_DAYS` e `WHATSAPP_DISPLAY_NUMBER` (tabela acima).

Efeito do deploy nos links já emitidos: a validade passa de 90 para 7 dias, contada de
`dashboard_token_created_at` (a migração `b1c2d3e4f5a6` pôs 2026-09-30 nos links que já existiam).
Todo link com mais de 7 dias, inclusive os do histórico do WhatsApp que prometiam 90, deixa de abrir
no mesmo instante e mostra a página "Este link expirou"; quem escrever "meu dashboard" recebe um
novo. Links com menos de 7 dias continuam válidos e são reemitidos no
próximo pedido se tiverem menos de metade da validade. A migração `c4d5e6f7a8b9` só acrescenta
colunas (`dashboard_v2` com default false, `export_token`, `export_token_expires_at`): o código
anterior roda com o esquema novo, então o rollback de código não exige `alembic downgrade`
(o downgrade apaga os tokens de exportação pendentes e a flag).

Membro de demonstração (números dos mockups de outubro de 2026; só mexe no `31000000000`, pode
correr várias vezes):

```bash
cd alfred && python scripts/seed_demo_member.py     # imprime o link do painel
python scripts/contrast_check.py                    # contraste AA dos dois temas
python scripts/panel_shots.py --base http://127.0.0.1:8000 --out /tmp/panel-shots --check-v1
```

`panel_shots.py` abre o Chromium do Playwright, captura Resumo e Dinheiro (claro/escuro, 1280 e 390) e falha se houver violação de CSP, erro de página,
fonte que não carrega ou rolagem horizontal. Sem acesso ao cdnjs (sandbox), `--chartjs` serve um
`chart.umd.js` local no lugar do CDN para provar que a v1 corre sob a CSP por nonce.


## Segurança de dados em repouso (S1-06 e S1-09, 04/10)

**Saúde cifrada no app (S1-06).** `health_log.value` e `.notes` são guardados como token Fernet com prefixo `enc1:` quando `DATA_ENCRYPTION_KEY` existe. Passos para ligar, nesta ordem:
1. Gerar a chave: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Guardar a chave num gerenciador de senhas **e** na variável `DATA_ENCRYPTION_KEY` do serviço `alfred` (e do `alfred-cron`, que lê o banco). Perder a chave = perder os dados de saúde.
3. Depois do deploy (a migração `a1b2c3d4e5f6` só alarga a coluna), cifrar o que já existe: `railway run python -m alfred.crypto backfill` (idempotente).
4. Rotação: `DATA_ENCRYPTION_KEY="nova,antiga"` (a primeira cifra, todas decifram); rodar o backfill de novo não reescreve o que já está cifrado, então para trocar de vez a chave é preciso ler e regravar as linhas (script a escrever quando houver a primeira rotação).
Sem a chave o app grava texto puro e registra o aviso `crypto.no_key` no start; com valor cifrado e sem chave a leitura falha de forma explícita (nunca devolve o token como se fosse dado).

**Backup cifrado (S1-09).** `scripts/backup_db.sh backup ARQUIVO` faz `pg_dump` e cifra com AES-256 (senha em `BACKUP_PASSPHRASE`, nunca em disco nem na linha de comando); `restore` faz o inverso. Testado em 04/10 (ida e volta e senha errada falha). O agendamento diário e o lugar de guardar o arquivo dependem do plano Hobby/região UE (V1-01, V1-15). A senha do backup vai no gerenciador de senhas, **diferente** da chave de dados.

**2FA (S1-08).** Não existe login de administrador no app (só `/internal/metrics` com token). O que vale aqui é ligar 2FA nas contas que mandam no sistema: GitHub, Railway, Meta Business, Google (Gmail do projeto) e Sentry. Ação do J.

**RLS (S1-02), decisão pendente.** O app usa um único papel de banco; RLS só protege se cada request definir `app.member_id` e o papel não for dono das tabelas (`FORCE ROW LEVEL SECURITY`). Isso toca webhook, cron, painel e exportação, e um erro derruba a produção. Proposta: fazer depois do staging (V1-13), tabela por tabela começando por `health_log`, com testes de isolamento entre membros.
