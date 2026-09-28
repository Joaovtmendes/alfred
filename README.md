# Alfred

Assistente pessoal pelo WhatsApp para quem vive nos Países Baixos. O utilizador escreve
em linguagem natural ("Jumbo 23,50", "corri 5 km", "lembrete: medicação às 09:00") e o
Alfred regista, responde e envia lembretes. Fala PT, NL, EN, FR e DE.

| Área | Módulos |
|---|---|
| Finanças | despesas e receitas, resumo, saldo, comparação, correcções, categorias que aprendem (M2–M4, M12) |
| Proactivo | lembretes e resumo semanal por template da Meta (M5) |
| Vida | treino (M7), saúde (M8), metas e hábitos com streak (M9), notas e tarefas (M10), viagens (M14) |
| Web | dashboard pessoal por link privado (M11) |
| Base | webhook WhatsApp, consentimento e disclosure EU AI Act (M1, M3) |

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async · PostgreSQL · Alembic ·
Anthropic (Claude Haiku 4.5) · WhatsApp Business Cloud API · Railway.

## Começar

```bash
cd alfred
cp .env.example .env            # preencher WHATSAPP_* e LLM_API_KEY
docker compose up -d db
pip install -e ".[dev]"
alembic upgrade head
uvicorn alfred.main:app --reload
pytest -q
```

## Documentação

| Documento | Para quê |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | como uma mensagem atravessa o sistema, módulos e dados |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | deploy, serviços Railway, env vars, cron, templates Meta, testes |
| [CLAUDE.md](CLAUDE.md) | regras do projecto (também lidas pelo Claude Code) |
| Projeto "Alfred" no claude.ai → `alfred-status.md` | roadmap e estado de cada módulo |
