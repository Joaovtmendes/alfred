# ADR-0001 — Painel v2: tokens por escopo, CSP por nonce e saúde sem barreira extra

- Data: 2026-10-01
- Estado: aceito
- Spec: `claude/alfred-dashboard-v2-design.md` (projeto Alfred no claude.ai)

## Contexto

O painel web é aberto por um link com token UUID no caminho (`/d/{token}`), por membro. O mesmo token abre o painel e a exportação completa (`/api/d/{token}/export`). A v2 acrescenta abas de saúde, agenda e viagens, ou seja, mais dados sensíveis (GDPR art. 9) atrás do mesmo link. A CSP atual aceita `script-src 'unsafe-inline'`, e a v2 renderiza muito mais dados do usuário (comerciantes, notas, tarefas, nomes).

## Decisão

1. **Dois escopos de token:** painel (7 dias, só leitura, renovação deslizante: reemite se faltar menos da metade da validade) e exportação (token separado, uso único, 15 minutos, só pelo comando "exportar meus dados"). "Apagar meus dados" invalida todos os tokens na mesma transação.
2. **CSP por nonce** (sem `unsafe-inline`); Chart.js segue no CDN com SRI. Todo dado do usuário entra no DOM por `textContent`; teste de XSS no CI.
3. **Aba de saúde sem PIN nem barreira extra**, igual às outras abas, com abertura registrada no `AuditLog`. Revisitar quando existir o site público com login.
4. Painel **somente leitura** e **individual** (sem visão da casa até haver consentimento por membro).

## Racional

Separar os escopos limita o dano de um link vazado (o painel não baixa tudo). A CSP por nonce dá quase a mesma proteção da CSP estrita com bem menos mudança. Um PIN na aba de saúde adicionaria estado, tela e comando a um painel que é só leitura, e o risco é o mesmo do dinheiro enquanto não houver conta de usuário.

## Consequências

- Risco aceito e registrado: quem tem o link de 7 dias vê também a saúde.
- Novo estado: tabela/coluna para o token de exportação e a lógica de renovação deslizante.
- O comando de exportação passa a emitir um link próprio; links antigos de exportação deixam de valer.
- Reversível: o painel v2 entra atrás de uma flag por membro.
