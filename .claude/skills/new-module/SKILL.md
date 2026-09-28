---
name: new-module
description: Passo a passo para adicionar um módulo novo ao Alfred (ex. M15 pagamentos recorrentes, M17 orçamento) — modelo, migração, i18n, handler, testes e roadmap.
disable-model-invocation: true
---

# Novo módulo Alfred (Mxx)

Segue por esta ordem e marca cada passo:

1. **Especificação curta** — frases de exemplo do utilizador em PT/NL/EN, respostas
   esperadas, e o que é gravado. Confirma com o Joao antes de codar.
2. **Modelo** em `src/alfred/models.py`: FK `member_id` com `ondelete="CASCADE"`,
   índice `(member_id, <data>)`, dinheiro em `Numeric(12, 2)`, datas `timezone=True`.
3. **Migração** `alembic revision -m "Mxx — ..."` escrita à mão; `down_revision` = head
   actual (`alembic heads`). Testa `upgrade` e `downgrade -1` num Postgres local.
4. **Textos** em `_STRINGS` com as 5 línguas (pt, nl, en, fr, de). Nada fora de `_t()`.
5. **Detecção**: regex/keywords ancoradas (evita apanhar mensagens de outros
   módulos — ver bugs de trip/correcção de categoria). Se precisar do LLM, uma
   função em `llm.py` usando `_get_client()` e validando o JSON.
6. **Handler** em `handle_inbound`, antes dos extractors LLM genéricos; responde uma
   vez, grava com `_save_outbound`, `return`.
7. **Testes** — usa o agente `test-writer`: casos felizes nas 3 línguas principais,
   mensagens de outros módulos que NÃO podem ser apanhadas, e regressões.
8. **Segurança** — corre o agente `security-reviewer` sobre o diff.
9. **Docs** — actualiza `claude/alfred-status.md` no Projeto (estado do módulo) e o
   bloco correspondente em `scripts/hard_test.py`.
10. **Templates Meta** se houver mensagens proactivas (fora da janela de 24h).
