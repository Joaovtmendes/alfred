---
name: deploy-checklist
description: Checklist antes e depois de fazer merge/deploy do Alfred no Railway.
disable-model-invocation: true
---

# Deploy Alfred (Railway)

Antes do merge em `master` (corre os comandos em `alfred/`):

1. `ruff check src scripts tests alembic` e `pytest -q` passam — e o CI do PR está verde.
2. Migrações novas? `alembic heads` mostra uma só head; `upgrade`/`downgrade -1`
   testados em Postgres local. Nada destrutivo sem backup.
3. Novos env vars estão em `.env.example` **e** definidos no Railway
   (serviço web e, se usados no cron, também no `alfred-cron`).
4. Texto novo tem as 5 línguas.
5. Mensagens proactivas novas usam templates **aprovados** na Meta.

Depois do deploy:

6. `curl https://alfred-production-5d54.up.railway.app/health` → 200, e o log de
   arranque mostra `webhook_secret_configured=True`.
7. Nos logs: `alembic upgrade head` sem erros.
8. Smoke test pelo WhatsApp: "olá", uma despesa ("Jumbo 12,50"), "saldo".
9. Se algo falhar: Railway → Deployments → *Redeploy* do deploy anterior
   (as migrações não revertem sozinhas — `alembic downgrade` manual se preciso).
