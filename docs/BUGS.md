# Bugs e testes de regressão

Regra: **todo bug tem um ID e um teste que o impede de voltar.** O registro é
`alfred/tests/bugs.json`; `python scripts/bugs.py check` confere a regra e roda no CI
(passo "Bug registry") e em `tests/test_bug_registry.py`. O passo "Bug regressions" do CI
roda só os testes marcados `@pytest.mark.bug` (`pytest -m bug`) para o relatório ficar claro.

## Estados

| Estado | Significa | O que a regra exige |
|---|---|---|
| `open` | conhecido, não corrigido | bug novo: teste marcado + `xfail(strict=True)`; bug herdado (`legacy`) pode esperar o teste |
| `fixed` | corrigido | pelo menos um teste marcado, **sem** xfail |
| `manual` | só se confirma na Meta, no modelo ou em produção | campo `reason` |
| `wontfix` | decisão de não corrigir | campo `reason` |

IDs: `B-NNN` para bugs novos; os herdados mantêm o nome de origem (`A-NN` = leitura de código
de 07/10, `BUG-NN` = hard test de 28/09, `H…`/`P…` = hard tests de 30/09 a 04/10, `C-NN` =
correções pós hard test).

## Depois de cada hard test

1. `python scripts/hard_test.py` grava `hard_test_relatorio.json` com tudo que o webhook
   recusou (HTTP diferente de 200 ou erro de rede). Para abrir um bug por falha:
   `python scripts/bugs.py from-report hard_test_relatorio.json`.
2. O script só enxerga o HTTP: as respostas chegam no WhatsApp. O que você achar lendo as
   respostas (ou marcando "Diferente" no checklist manual) vira bug com
   `python scripts/bugs.py add --title "..." --message "texto exato" --severity alta`, ou o
   Claude lê as marcações e registra todas de uma vez com `from-report`.
3. Cada bug aberto nasce com um teste em `tests/regression/` que **falha de propósito**
   (`xfail` estrito). Preencha a verificação real antes de corrigir.
4. Corrigiu? O `xfail` passa a XPASS e o CI fica vermelho: remova o `xfail` e rode
   `python scripts/bugs.py fixed B-NNN`. Dali em diante o teste vigia o bug para sempre.

Nunca apague nem enfraqueça um teste marcado `bug` para fazer o CI passar. Se o
comportamento mudou de propósito, o bug passa a `wontfix` com o motivo.

## Comandos

```bash
python scripts/bugs.py check               # a regra (CI)
python scripts/bugs.py list --status open  # o que falta
python scripts/bugs.py add --title "..." --message "..." [--severity alta]
python scripts/bugs.py from-report hard_test_relatorio.json
python scripts/bugs.py fixed B-001
pytest -m bug                              # só as regressões de bugs
```
