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
    W->>DB: cria Member no 1º contacto; INSERT Message ON CONFLICT DO NOTHING
    alt mensagem repetida (retry da Meta)
        W-->>M: 200 (ignorada)
    else nova
        W->>C: handle_inbound() dentro de SAVEPOINT + lock por membro
        C->>C: consentimento → comandos → regex dos módulos
        C->>L: extractors (despesa, hábito, saúde, treino, query) se preciso
        C->>DB: grava registo + mensagem de saída
        C->>M: send_text()
        W-->>M: 200
    end
    M->>U: resposta
```

**Garantias:** a Meta recebe sempre 200 (senão reenvia). Uma entrega repetida não é
processada duas vezes (UNIQUE em `wa_message_id`). Se o handler falhar a meio, as
suas escritas são revertidas pelo SAVEPOINT, mas a mensagem recebida fica guardada.

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
módulos seguintes (ver bugs abertos de viagem e correcção de categoria no roadmap).

## Dados

Tudo pertence a um `member` (e este a um `household`). Tabelas: `household`, `member`,
`message`, `expense`, `merchant_category_overrides`, `scheduled_job`,
`workout_session`, `health_log`, `goal`, `habit_log`, `note`, `task`, `trip`.
Migrações em `alfred/alembic/versions/` numa cadeia linear
(M1 → M2 → M4a → M5 → M12 → M7 → M8 → M9 → M10 → M11 → M14).

## Chamadas ao LLM

`llm.py` tem um único `AsyncAnthropic` por processo (timeout 20 s, 2 retries).
Uma mensagem de conversa livre pode gerar até 4 chamadas sequenciais (extractor do
módulo → despesa → classificação de query → resposta); juntar isto numa chamada com
tool use é o próximo ganho de custo e latência (roadmap S0).

## Processos

| Processo | O quê | Onde |
|---|---|---|
| web | FastAPI (webhook, dashboard, health) | Railway `alfred-web`, `start.sh` corre migrações |
| cron | `scripts/daily_cron.py` a cada 15 min | Railway `alfred-cron` |
