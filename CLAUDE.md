# Alfred — guia para o Claude

Assistente pessoal via WhatsApp para o mercado holandês: finanças, lembretes, treino,
saúde, metas/hábitos, notas/tarefas, viagens e dashboard web. Um único serviço FastAPI
no Railway, com PostgreSQL e a API da Anthropic (Claude Haiku 4.5).

Roadmap e estado dos módulos: `claude/alfred-status.md` no Projeto "Alfred" (claude.ai).

## Estrutura

```
alfred/                    ← raiz do serviço (Railway "Root Directory")
  src/alfred/
    main.py                FastAPI app, /health, /ready
    webhook.py             GET verificação Meta, POST mensagens (HMAC, idempotente)
    security.py            validação HMAC-SHA256 (fail-closed sem segredo)
    conversation.py        router de mensagens: consentimento, i18n, todos os handlers
    llm.py                 cliente AsyncAnthropic partilhado + extractors (JSON)
    models.py              ORM (13 tabelas)
    dashboard.py           GET /d/{token} (HTML) e /api/d/{token} (JSON)
    whatsapp.py            send_text / send_template (Graph API)
    settings.py / db.py    config via env vars / engine async
  scripts/daily_cron.py    lembretes + resumo semanal (cron Railway a cada 15 min)
  scripts/hard_test.py     teste de ponta a ponta contra o webhook real
  alembic/versions/        migrações (cadeia linear, head = e5f3a2d7c8b1)
  m5-templates/ m6-flow/   JSON para submeter no WhatsApp Manager
  tests/                   pytest (unit + integração com Postgres real)
docs/                      ARCHITECTURE.md, OPERATIONS.md
```

## Comandos (a partir de `alfred/`)

```bash
pip install -e ".[dev]"
pytest -q                                   # unit tests, sem DB
ALFRED_TEST_DB=1 DATABASE_URL=... pytest -q # + integração (Postgres migrado)
ruff check src scripts tests alembic && ruff format src scripts tests alembic
alembic revision -m "mXX — descrição"       # nova migração (escrever à mão)
alembic upgrade head
docker compose up                           # app + Postgres local
```

## Regras do projecto

- **Todo texto para o utilizador passa por `_t(key, lang)`** e tem as 5 línguas
  (pt, nl, en, fr, de) em `_STRINGS` (conversation.py). Nunca strings soltas.
- **Datas e horas em `settings.timezone` (Europe/Amsterdam)**, nunca "hoje" em UTC.
- **Nunca registar telefones, nomes ou conteúdo de mensagens em logs** — usar
  `member_id=str(member.id)`; no webhook, `_mask_phone()`.
- **Toda query filtra por `member_id`** (ou `household_id`). Sem excepções.
- **Ordem dos handlers em `handle_inbound` importa**: comandos exactos → regex
  específicos → extractors LLM → `generate_reply`. Um handler novo entra antes dos
  extractors LLM e deve fazer `return` depois de responder (uma resposta por mensagem).
- **Chamadas LLM só via `llm._get_client()`** (async, timeout, retries). O output JSON
  do LLM é validado antes de gravar.
- **Webhook responde sempre 200** à Meta; falhas do handler são revertidas (SAVEPOINT).
- Dinheiro: hoje é `Float` (dívida técnica, ver roadmap S0) — formatar com `_fmt_eur`.
- Testes: `SimpleNamespace` + `make_member/make_message/make_session` de
  `tests/conftest.py`; nunca chamar Meta ou Anthropic de verdade.

## Deploy

Push/merge em `master` → Railway faz build do `alfred/Dockerfile` e corre
`alembic upgrade head` no arranque (`start.sh`). Checklist: skill `/deploy-checklist`.
