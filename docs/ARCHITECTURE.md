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
| 4e-0, 0b, 0c | lembretes M5: criar, listar, cancelar; cadência ("toda segunda", "dias úteis", "todo dia 5") | `scheduled_job`, `recurrence.py` |
| 4e-0f | orçamentos V2-01 | `budgets.py` |
| 4e-0h | resumo mensal (activar/desactivar/ver) V2-04 | `monthly_summary.py` |
| 4e-0l | "dias no azul", mês contra mês V2-15 | `insights.py` |
| 4e-0m | dívidas: "Pedro me deve 25", "Pedro pagou" V2-15 | `iou.py` |
| 4e-0o | "o que você me enviou" V2-18 | `outbox.py` |
| 4e-0n | a pagar / a receber / "paguei a luz" V2-16 | `ledger_status.py` |
| 4e-0g | contas fixas, assinaturas, parcelas V2-02 | `recurring.py` |
| 4e-0i | agenda V2-06 (ignorada se a mensagem começa por "nota:") | `agenda.py` |
| 4e-0p | plano de treino com cargas e plano de viagem (roteiro, mala, orçamento por categoria) V2-35, antes dos orçamentos | `training.py`, `tripplan.py` |
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
`iou`, `pending_batch` e, do V2-35, `workout_plan`, `workout_plan_day`,
`workout_plan_item`, `workout_load`, `trip_item`, `trip_pack_item`, `trip_budget_line` (único por
viagem+categoria) e `pending_action` (rascunho genérico, TTL de 15 min; o toque "confirmar" faz
`DELETE … RETURNING`, então um segundo toque não faz nada). Exportar e apagar dados (`privacy.py`) percorre `Base.metadata`: uma
tabela nova entra sozinha.
Migrações em `alfred/alembic/versions/` numa cadeia linear; o head actual é `d5e6f7a8b9c0`
(V2-35; antes dele `f2a3b4c5d6e7`, V2-18). Cada item V2 tem uma migração própria (`e5f6a7b8c9d0` orçamentos … `f2a3b4c5d6e7`
extrato de mensagens).

## Painel v2

O painel v1 (`/d/{token}`, Chart.js) continua o padrão. Um membro com `member.dashboard_v2 = true`
recebe a casca v2: HTML/CSS/JS próprios (`src/alfred/panel/`), sem Chart.js (barras em CSS e SVG
desenhados por `panel.js`) e sem nenhum host externo; as fontes (Hanken Grotesk, Bricolage
Grotesque, licença OFL) são servidas pelo próprio serviço. Só leitura.

```
GET  /d/{token}                 → v1 ou v2, conforme a flag do membro
GET  /api/d/{token}             → JSON da v1 (inalterado)
GET  /api/d/{token}/summary|money|health|agenda|trips
                                → JSON de uma aba; só a aba aberta chama a sua rota
GET  /api/d/{token}/export      → página de confirmação (links de prévia só fazem GET)
POST /api/d/{token}/export      → descarga, token de exportação de uso único
GET  /panel-assets/{nome}       → panel.css, panel.js e fontes (lista fixa, imutável, sem token)
```

| Escopo | Token | Validade | Pode |
|---|---|---|---|
| painel | `member.dashboard_token` | 7 dias, renovação deslizante (reemite com menos de metade; o anterior deixa de valer) | ler as abas |
| exportação | `member.export_token` | 15 min, uso único, só pelo comando "exportar meus dados" | descarregar os dados |

"Apagar meus dados" invalida os dois na mesma transação.

A renovação trava a linha do membro (`SELECT ... FOR UPDATE`): dois "meu dashboard" simultâneos
devolvem o mesmo link novo em vez de reemitir duas vezes. Link expirado ou desconhecido em
`/d/{token}` mostra uma página 404 em HTML, na língua do membro, que diz como pedir outro; as rotas
`/api/d/...` continuam a responder JSON 404. A mensagem "vale por até 7 dias" é de propósito: um
link reaproveitado tem entre 3,5 e 7 dias de validade restante.

**CSP por nonce.** `SecurityHeadersMiddleware` gera um nonce por pedido (`request.state.csp_nonce`);
`script-src` e `style-src` só aceitam esse nonce (nunca `unsafe-inline`). A v1 mantém
`https://cdnjs.cloudflare.com` para o Chart.js (com SRI); a v2 marca `request.state.panel_v2` e a
CSP dela não nomeia host externo. Todo dado do utilizador entra no DOM por `textContent`
(`panel.js` não pode conter `innerHTML`; há teste). O JSON de configuração da página escapa `<`, `>`
e `&`.

**Filtro único.** `panel_filters.parse_filter` lê da URL só valores de lista fechada (mês/intervalo,
categorias, tipo, estado, viagem); `apply_expense_filter` serve cartões e lista. Texto livre
(comerciante, pessoa) filtra só no navegador e nunca vai à URL nem ao servidor.

**Frases.** `panel_phrases.py` é um motor de regras (sem LLM): no máximo uma frase por cartão, só
com facto relevante, e a sugestão é sempre um comando do chat. O catálogo nas 5 línguas passa pelo
`scripts/message_audit.py`.

### Resumo e Dinheiro (cartões)

`panel_calc.py` guarda os números (funções puras e uma consulta com `GROUP BY` por cartão, sem N+1),
`panel_phrases.py` as 12 regras de frase e `panel_api.py` monta o JSON. Toda consulta de lançamentos
passa por `apply_expense_filter`; um cartão que precisa de outro período (mês anterior, últimos 30
dias, próximos 30 dias) deriva o filtro com `dataclasses.replace` e continua a usá-lo, então o
membro e os demais filtros valem também ali. Dias são dias locais (`to_char(timezone(...))`,
`clock.day_start`), por isso 00:30 do dia 1º, virada de ano, 29/02 e mudança de horário caem no dia
certo (testes em `test_panel_calc.py` e `test_panel_cards.py`).

Envelope comum: `{"tab", "lang", "today", "filter": {start, end (exclusivo), categories, kind,
states, trip}, "cards": [...]}`. Todo cartão tem `id` estável, `empty`, `phrase` (`null` ou
`{key, text, chat, severity}`) e, se vazio, `hint` (a frase do chat que ensina). Dinheiro em EUR com
2 casas, datas ISO. O formato completo de cada cartão está na docstring de `panel_api.py`.

| Aba | Cartões (em ordem) |
|---|---|
| `summary` | `balance` (com `compare` e `projection`), `upcoming`, `categories`, `budgets`, `blue_days`, `owed` |
| `money` | `transactions` (50 por página, `?page=N`), `top_expenses`, `month_vs_month`, `fixed_variable`, `daily`, `categories`, `avg_ticket`, `recurring`, `owed` |

Definições que o front-end pode citar:

- **Projeção do fim do mês** (só no mês corrente e sem filtro de categoria, tipo, estado ou viagem;
  caso contrário `projection.available = false` com o motivo): saldo realizado + receita marcada
  "a receber" - contas até o fim do mês (lançamentos `to_pay` e itens fixos, semanais repetidos,
  atrasado conta uma vez) - gasto variável esperado (média diária dos últimos 30 dias, sem os
  lançamentos cujo comerciante é o nome de um item fixo, x dias que faltam). Precisa de 10 dias de
  histórico e 5 lançamentos variáveis; sem isso `sufficient = false`, `projected = null` e só
  `realized` e `committed` valem. A faixa `low`..`high` é um desvio-padrão do gasto diário somado
  nos dias restantes. Receita não registrada nunca é projetada.
- **Mês contra mês:** mês corrente contra os mesmos dias do mês anterior (31/10 contra 30/09 trava
  no fim de setembro); mês passado contra o mês anterior inteiro; intervalo livre contra o intervalo
  de mesmo tamanho logo antes. `delta_pct` é `null` sem base (período anterior em zero).
- **Orçamentos:** o mês de referência é o do último dia do período; 80% e 100% decididos nos valores
  exatos, como os alertas do chat (99,60 de 100 é 99%, nunca "estourou"); o número mostrado é
  arredondado meio para cima mas fica dentro da faixa do nível; `crossed_on` é o dia em que o acumulado passou do
  limite; `days_to_80` só dentro do mês corrente, com 5 dias e 3 lançamentos.
- **Próximos pagamentos:** itens fixos ativos e lançamentos pendentes de hoje a 30 dias, mais os
  atrasados; um lançamento pendente com o mesmo nome do item fixo substitui o item (nunca conta
  duas vezes). Olham para frente: o período da URL não vale; categoria, tipo e estado valem.
- **Fixo x variável:** despesa paga cujo comerciante é o nome de um item fixo (`RecurringItem`) é
  fixa; o resto é variável.
- **Lista:** soma do dia = lançamentos pagos/recebidos do dia inteiro (lançamentos pendentes
  aparecem na lista, com `settled: false`, e não entram na soma); a soma do cartão, da lista, das
  categorias, dos maiores gastos, do gasto por dia e do mês contra mês é sempre o mesmo número
  (teste com 13 combinações de filtro).
- **Gasto por dia:** um ponto por dia até 62 dias; períodos maiores viram semanas ISO.

Frases: no máximo uma por cartão, só com dado mínimo (cada regra documenta o seu limiar). A
sugestão é um comando que o roteador do chat entende sem LLM (um teste roda todas, nas 5 línguas),
exceto "como fica meu mês?" e o exemplo de registrar um gasto, que passam pelo LLM.

### Front-end de Resumo e Dinheiro (`panel.js`, `panel.css`)

Fiel aos mockups aprovados (`Main`, `Mobile`, `Dinheiro`, `MobileDinheiro`); as capturas
(`scripts/panel_shots.py`) são comparadas com eles a cada mudança visual.

- **Grade.** Desktop (>= 900 px): 12 colunas, cartões `s7`/`s5`/`s6`/`s12` (Resumo: saldo 7 +
  próximos pagamentos 5, categorias 7 + orçamentos 5, dias no azul 6 + quem te deve 6; Dinheiro:
  lançamentos 7 + maiores gastos 5, mês contra mês 7 + fixos e variáveis 5, gasto por dia 12,
  categorias 7 + ticket médio 5, recorrências 7 + quem te deve 5). Celular: 1 coluna; no Resumo a
  ordem visual é a do mockup móvel (saldo, categorias, orçamentos, dias no azul, próximos
  pagamentos, quem te deve) via `order` no CSS, sem mexer na ordem do DOM.
- **Um desenho por cartão** em `renderers[card.id]`; cartão `empty` mostra o `hint` do servidor
  (texto + chip "Peça no chat"). Cartões desconhecidos são ignorados (as outras abas chegam depois).
- **Gráficos** (CSS/SVG, sem biblioteca): barra empilhada da projeção (realizado, contas marcadas,
  receita a receber, gasto variável esperado em listras) com a legenda em linha no desktop e em
  coluna no celular, e o "fim do mês (estimativa)" ABAIXO da legenda no celular; faixa de
  estimativa em texto; barras de categoria com Δ (▲ aumento = quente, ▼ queda, "= igual", "novo");
  barras de orçamento com marcas em 80% e 100% (80-99% listrado, 100% ou mais sólido quente, para
  não depender só da cor); barras por dia (pico em destaque); fixo x variável. `sparkline()` existe
  como primitiva, mas nenhum cartão atual recebe uma série que a justifique.
- **"Ver como tabela"** (`<details>` com `<table>`, cabeçalhos `scope=col`) em todo gráfico, aberto
  por teclado.
- **Filtros** (`#filter-bar`): mês (setas no Resumo, lista de 13 meses no Dinheiro), categoria
  (as 10 do `labels.py`, enviadas na configuração), tipo e estado são `<select>` nativos; escolher
  escreve `?month=&categories=&kind=&state=` com `history.pushState` e rebusca a aba (a
  resposta fica em cache por aba + query). Só valores de lista fechada são lidos e escritos
  (`readFilters` descarta o resto, inclusive parâmetros desconhecidos). A busca por comerciante
  (Dinheiro) é um `<input>` sem `name` e fora de formulário: filtra as linhas no navegador, não toca
  na URL nem na rede. Paginação (`?page=N`) só na aba Dinheiro e só mexe nesse número.
- **Tema:** segue o sistema até o membro escolher no botão do topo; a escolha fica só no
  `localStorage` do navegador (com `try/catch`) e vira `data-theme` + `data-theme-locked`. O
  `scripts/contrast_check.py` cobre os pares novos (tokens `--warm-text` para texto sobre
  `--warm-soft`).
- **Barra de abas no celular:** decisão: continua em uma linha rolável (como no mockup), com
  degradê na borda que ainda tem abas (`fade-l`/`fade-r`, atualizado ao rolar) e a aba selecionada
  é rolada para a vista. Quebrar em duas linhas foi descartado porque empurra o conteúdo e foge
  do mockup.
- **Segurança e acessibilidade:** zero `innerHTML` (teste existente), dados do membro só por
  `textContent`/`dataset`, tamanhos dinâmicos por CSSOM, nada de host externo. Foco visível em tudo
  (inclusive o `<select>` dentro da pílula, via `:has`), abas por teclado (setas, Home, End),
  `prefers-reduced-motion` desliga transições e rolagens suaves. O painel não tem formulário nem
  botão que altere dados; "CSV" não existe no backend: a cópia dos dados é o link de "exportar meus
  dados" (JSON), citado num texto do cartão de lançamentos.
- **Testes:** `tests/test_panel_js.py` roda `panel.js` no Chromium com rede falsa e os payloads de
  `tests/panel_fixtures.py` (formato real da API, números dos mockups): um teste por grupo de
  cartões, XSS, estados vazios, filtros só na URL, URL adulterada, busca local, paginação, tema,
  teclado, ordem móvel, legenda no celular, overflow e CSP.

**Abas.** Resumo, Dinheiro, Agenda, Hábitos, Viagens. A rota `health` (aba Hábitos) é
própria, carregada só ao abrir a aba, e cada abertura vai para o `AuditLog`. O botão do chat
("Abrir meu painel") é uma mensagem `cta_url` com plano B em texto (`whatsapp.send_cta_url`).

**Agenda, Hábitos e Viagens** (`panel_tabs.py`, contrato de cada cartão no docstring do módulo).
Sem filtros: cada cartão tem janela fixa e nomeada. Agenda: próximos 7 dias (`Appointment`
ativos), tarefas (atrasadas/prazos), mapa de 4 semanas, lembretes (`ScheduledJob` ativos), notas.
Hábitos: água, treinos, plano de treino com cargas e evolução (`training_card`), hábitos e metas;
sono, humor e medicação saíram da aba por decisão de 02/10. Frases só descrevem (média, contagem),
nunca julgam, e a aba mostra "não é conselho médico". Viagens: viagem ativa, senão a próxima,
senão a última (orçamento, gasto, categorias, dias) e as anteriores com "dentro/acima do
orçamento". Só gastos liquidados (`SETTLED`) com `trip_id` do próprio membro. Os cartões
`itinerary`, `packing` e `plan_budget` só aparecem com viagem ativa ou próxima, pela mesma escolha
de viagem do chat (`tripplan.target_trip`); cada um tem frase (`rule_training`, `rule_packing`,
`rule_plan_budget`) com comando de chat equivalente.

**Treino e plano de viagem no chat (V2-35).** `training.py` e `tripplan.py` não usam LLM:
`parse_plan` lê "plano de treino: Segunda - Peito: Supino 4x10 60kg"; o rascunho fica em
`PendingAction` (`workout_plan`) até o botão `plan_ok`/`plan_cancel`; cargas ("supino 62 kg")
vão para `WorkoutLoad` e alimentam a evolução. Orçamento por categoria usa `resolve_category` de
`budgets.py` (hospedagem e sinônimos → `wonen`).

**Lembretes recorrentes (`recurrence.py`).** `parse_recurrence` tira a cadência do texto e devolve
máscara de dias (Seg=1 … Dom=64) mais dia do mês opcional, guardado em `payload["day_of_month"]`
(máscara 127); `scripts/daily_cron.py::is_job_due(day_of_month=…)` o respeita. `cadence_label` dá a
frase da confirmação e da listagem nas 5 línguas.

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
