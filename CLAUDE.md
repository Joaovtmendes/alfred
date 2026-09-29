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
    webhook.py             GET verificação Meta; POST grava + 200 já, processa em background
    security.py            validação HMAC-SHA256 (fail-closed sem segredo)
    conversation.py        router de mensagens: consentimento, i18n, todos os handlers
    parsing.py             regex/parsers puros e ancorados (viagem, correcção, orçamento, acentos)
    validation.py          sanitiza TODO o JSON devolvido pelo LLM antes de gravar
    clock.py               "hoje/semana/mês" em Europe/Amsterdam + nomes de meses/dias i18n
    llm.py                 cliente AsyncAnthropic partilhado + extractors (JSON)
    models.py              ORM (13 tabelas)
    dashboard.py           GET /d/{token} (HTML) e /api/d/{token} (JSON)
    whatsapp.py            send_text / send_template (Graph API)
    settings.py / db.py    config via env vars / engine async
  scripts/daily_cron.py    lembretes + resumo semanal (cron Railway a cada 15 min)
  scripts/hard_test.py     teste de ponta a ponta contra o webhook real
  alembic/versions/        migrações (cadeia linear, head = a1d5c7e9b3f2)
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
- **Datas e horas em `settings.timezone` (Europe/Amsterdam)** via `alfred.clock`
  (`today_local()`, `now_local()`, `day_start()`); nunca `datetime.now(UTC).date()` nem
  `strftime("%B")` (sai sempre em inglês — usar `month_name`/`weekday_abbr`).
- **Nunca registar telefones, nomes ou conteúdo de mensagens em logs** — usar
  `member_id=str(member.id)`; no webhook, `_mask_phone()`.
- **Toda query filtra por `member_id`** (ou `household_id`). Sem excepções.
- **Ordem dos handlers em `handle_inbound` importa**: comandos exactos → regex
  específicos → extractors LLM → `generate_reply`. Um handler novo entra antes dos
  extractors LLM e deve fazer `return` depois de responder (uma resposta por mensagem).
- **Chamadas LLM só via `llm._get_client()`** (async, timeout, retries). O output JSON
  do LLM passa por `validation.sanitize_*` antes de gravar (valor, categoria, moeda, dias).
- **Regex de comandos: ancorar** (`^…$`) e correr sobre `body_plain` (sem acentos, mesmo
  comprimento); texto capturado vem de `body` via `_grp`. Texto do utilizador em ILIKE
  passa por `like_escape` + `escape="\\"`. Um handler que apaga/altera dados nunca adivinha.
- **Webhook responde sempre 200 e logo** (~0,1 s): grava a mensagem, faz commit, e o
  handler corre em background com sessão própria (lock por membro, SAVEPOINT, `FOR UPDATE
  SKIP LOCKED`). `message.processed` marca o fim; o processo web re-tenta as não
  processadas (`webhook.recovery_loop`).
- Dinheiro: `expense.amount` é `NUMERIC(12,2)` na BD mas `float` em Python
  (`asdecimal=False`): somas em Python passam por `_cents()`; formatar com `_fmt_eur`.
- Testes: `SimpleNamespace` + `make_member/make_message/make_session` de
  `tests/conftest.py`; nunca chamar Meta ou Anthropic de verdade.

## Deploy

Push/merge em `master` → Railway faz build do `alfred/Dockerfile` e corre
`alembic upgrade head` no arranque (`start.sh`). Checklist: skill `/deploy-checklist`.
