# Arquitectura

## Uma mensagem, do WhatsApp à resposta

```mermaid
sequenceDiagram
    participant U as Utilizador (WhatsApp)
    participant M as Meta Cloud API
    participant W as webhook.py
    participant C as conversation.py
    participant L as llm.py (Anthropic)
    participant DB as PostgreSQL
    U->>M: "Jumbo 23,50"
    M->>W: POST /webhook/whatsapp (X-Hub-Signature-256)
    W->>W: verifica HMAC (security.py) — 401 se inválido
    W->>DB: cria Member no 1º contacto; INSERT Message ON CONFLICT DO NOTHING; COMMIT
    alt mensagem repetida (retry da Meta)
        W-->>M: 200 (ignorada)
    else nova
        W-->>M: 200 (~0,1 s, antes de processar)
        Note over W,C: background task, sessão própria
        W->>C: handle_inbound() — lock por membro, FOR UPDATE SKIP LOCKED, SAVEPOINT
        C->>C: consentimento → comandos → regex dos módulos
        C->>L: extractors (despesa, hábito, saúde, treino, query) se preciso
        L-->>C: JSON → validation.sanitize_*
        C->>DB: grava registo + mensagem de saída; message.processed = true
        C->>M: send_text()
    end
    M->>U: resposta
```

**Garantias:** a Meta recebe sempre 200, e só depois de a mensagem estar gravada
(commit) — o LLM já não pode atrasar a resposta e provocar reenvios. Uma entrega
repetida não é processada duas vezes (UNIQUE em `wa_message_id`). Se o handler falhar a
meio, as suas escritas são revertidas pelo SAVEPOINT, mas a mensagem fica guardada com
`processed = false`; o processo web (`recovery_loop`, de minuto a minuto) re-tenta
mensagens com 2–20 min. `FOR UPDATE SKIP LOCKED` na linha da mensagem impede dois
processos (deploy rolling) de a tratarem duas vezes; no máximo 8 handlers em paralelo.
Mensagens que ficam sem resposta depois de 20 min geram o log de erro
`webhook.stale_unprocessed_message` (ligar um alerta a essa linha).

## Ordem de decisão em `handle_inbound`

1. Resposta de WhatsApp Flow (onboarding M6)
2. Estado de consentimento: `pending` → disclosure; `pending_response` → sim/não;
   `rejected` → ignora, excepto `START`
3. Comandos exactos: stop, saldo, resumo, ajuda, dashboard…
4. Padrões por módulo (regex): lembretes, correcção de categoria, notas/tarefas,
   metas/hábitos, saúde, treino, viagem
5. Extractors LLM: despesa/receita → query financeira
6. Conversa livre: `generate_reply` (histórico das últimas 20 mensagens)

A ordem importa: um padrão demasiado largo num passo anterior "rouba" mensagens de
módulos seguintes. Por isso os padrões de viagem/correcção vivem em `parsing.py`,
ancorados e com testes de "o que NÃO deve apanhar"; os comandos são comparados sobre
`body_plain` (sem acentos) e os de listagem usam `_is_bare_command` ("gastos" é o
resumo; "gastos em restaurante" vai para a query por categoria).

## Dados

Tudo pertence a um `member` (e este a um `household`). Tabelas: `household`, `member`,
`message`, `expense`, `merchant_category_overrides`, `scheduled_job`,
`workout_session`, `health_log`, `goal`, `habit_log`, `note`, `task`, `trip`.
Migrações em `alfred/alembic/versions/` numa cadeia linear
(M1 → M2 → M4a → M5 → M12 → M7 → M8 → M9 → M10 → M11 → M14 → S1: `a1d5c7e9b3f2`,
que passa `expense.amount` a `NUMERIC(12,2)`, cria índices e `trip.budget`).

## Chamadas ao LLM

`llm.py` tem um único `AsyncAnthropic` por processo (timeout 20 s, 2 retries).
Uma mensagem de conversa livre pode gerar até 4 chamadas sequenciais (extractor do
módulo → despesa → classificação de query → resposta); juntar isto numa chamada com
tool use é o próximo ganho de custo e latência (roadmap S0).

## Processos

| Processo | O quê | Onde |
|---|---|---|
| web | FastAPI (webhook, dashboard, health) | Railway `alfred`, `start.sh` corre migrações |
| cron | `scripts/daily_cron.py` a cada 15 min | Railway `alfred-cron` |
