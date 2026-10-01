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

Ficheiro: `alfred/src/alfred/conversation.py`. Os passos têm um comentário `# 4e-0x.` no
código (procura por esse prefixo; os números de linha mudam, a ordem não). `body` é o texto
em minúsculas; `body_plain` é o mesmo sem acentos e com o mesmo comprimento.

**Antes de tudo:** resposta de WhatsApp Flow (onboarding M6) → consentimento: `pending` →
disclosure; `pending_response` → sim/não; `rejected` → ignora, excepto `START`.

**Já com consentimento (`accepted`), por esta ordem:**

| Passo | O quê | Módulo / função |
|---|---|---|
| 4-0 | toque num botão (`undo:` `edit:` `ok:` `appt_*` `batch_*` `wipe:` `keep:`) | `_handle_button_reply` |
| 4-0aa | resposta a um rascunho de 2+ lançamentos ("sim", "cancela", "tira o segundo", "o primeiro foi 4") | `batch.py` `handle_batch_text` (só consulta a BD se o texto parece uma resposta) |
| 4-0a | "sim" solto sem rascunho → "não há nada a confirmar" | `_BARE_YES_RE` |
| 4-0b | RGPD: apagar / exportar dados | `privacy.py` |
| 4a–4d | stop, saldo, ajuda, resumo | comandos exactos |
| 4e-0, 0b, 0c | lembretes M5: criar, listar, cancelar | `scheduled_job` |
| 4e-0f | orçamentos V2-01 | `budgets.py` |
| 4e-0h | resumo mensal (activar/desactivar/ver) V2-04 | `monthly_summary.py` |
| 4e-0l | "dias no azul", mês contra mês V2-15 | `insights.py` |
| 4e-0m | dívidas: "Pedro me deve 25", "Pedro pagou" V2-15 | `iou.py` |
| 4e-0o | "o que você me enviou" V2-18 | `outbox.py` |
| 4e-0n | a pagar / a receber / "paguei a luz" V2-16 | `ledger_status.py` |
| 4e-0g | contas fixas, assinaturas, parcelas V2-02 | `recurring.py` |
| 4e-0i | agenda V2-06 | `agenda.py` |
| 4e-0j | nota de saúde V2-09 | `score.py` |
| 4e-0k | visões guardadas V2-14 | `analysis.py` `handle_view_command` |
| 4e-1 | correcção de categoria ("Jumbo é supermarkt") | `parsing.py` |
| 4e-2 … 4e-5d | notas, tarefas, dashboard | M10, M11 |
| 4e-6 … 4e-8b | metas e hábitos (regex e LLM) | M9 |
| 4e-9 … 4e-9e | saúde: medicação, humor, sono, água | M8 |
| 4e-10 … 4e-11d | treinos | M7 |
| 4e-0c(2), 4e-0d | comandos reais que o LLM improvisava; correcção de valor ("errei, foram 42") | |
| 4e-0e | 2+ lançamentos numa mensagem → **rascunho** com Confirmar/Ajustar/Cancelar (V2-17); com 1 sobrando, grava directo | `batch.py` `create_draft` |
| — | 1 despesa/receita → grava directo com [Desfazer] | `llm.extract_expense` |
| 4f | consulta financeira ("gastei em restaurantes este mês") | `classify_query` |
| 4f-2 | análise livre V2-14 (especificação fechada validada, limite diário) | `analysis.py` |
| 4g | conversa livre | `generate_reply` (últimas 20 mensagens) |

**Regras que evitam roubo de mensagens:**
- "paguei X" passa por três donos, por esta ordem: dívida aberta (`iou`), item pendente
  (`ledger_status`), conta fixa (`recurring`). Cada um só responde se existir o alvo; senão
  devolve `None` e a mensagem segue.
- Os módulos V2 respondem só a frases ancoradas e devolvem `None` caso contrário; os testes
  incluem "o que NÃO deve apanhar".
- Os padrões de viagem/correcção vivem em `parsing.py`; os comandos de listagem usam
  `_is_bare_command` ("gastos" é o resumo; "gastos em restaurante" vai para a query por categoria).
- Handlers novos entram **antes** de `# 4e-1` e mantêm o seu texto em `STRINGS` no próprio módulo
  (`_STRINGS.update(...)` em `conversation.py`); os módulos importam `_t` / `_fmt_eur` dentro das
  funções para evitar importações circulares.
- Nada que o utilizador escreve vai para SQL: o LLM só devolve enums fechados, validados em
  `validation.py`.

**Saída:** `send_text` / `send_buttons` e `_save_outbound`. O id da Meta do envio é guardado na
linha `message` (V2-18) para o estado de entrega chegar depois pelo webhook (`statuses[]`).

## Dados

Tudo pertence a um `member` (e este a um `household`). Tabelas: `household`, `member`,
`message` (inclui `kind`, `delivery_status`), `expense` (inclui `status`: paid / to_pay /
received / to_receive; relatórios só contam paid e received), `merchant_category_overrides`,
`scheduled_job`, `workout_session`, `health_log`, `goal`, `habit_log`, `note`, `task`, `trip`,
`llm_usage`, `audit_log` e, do V2, `budget`, `recurring_item`, `appointment`, `saved_view`,
`iou`, `pending_batch`. Exportar e apagar dados (`privacy.py`) percorre `Base.metadata`: uma
tabela nova entra sozinha.
Migrações em `alfred/alembic/versions/` numa cadeia linear; o head actual é `f2a3b4c5d6e7`
(V2-18). Cada item V2 tem uma migração própria (`e5f6a7b8c9d0` orçamentos … `f2a3b4c5d6e7`
extrato de mensagens).

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
