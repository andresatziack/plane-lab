# Próxima sessão: Fase 4 — Contratos e pools de horas

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 4**: `docs/worklog/04-contratos-e-pools.md` — contratos, pool de horas
mensal, acúmulo de saldo, excedente, renovação e alertas.

**Antes de escrever código, apresente o modelo de dados proposto** (campos, tipos,
relações, constraints, índices) e as decisões de arquitetura, **e aguarde OK** — §7 do
contexto mestre.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as quatro armadilhas.
4. **`docs/worklog/04-contratos-e-pools.md`** — o briefing da fase.
5. **`docs/worklog/DECISOES.md`** — **D18 é obrigatória**, o próprio briefing manda ler
   antes de começar: hierarquia matriz/filial pode alterar o motor de débito. Também
   **D3, D4, D9, D10, D15** (acúmulo, saldo negativo, contrato vencido, ausência de
   workflow de aprovação, renovação) e **D21/D22**, decididas na 2b.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b e 3 mescladas.** PRs #1 a #7 fechados.
- Última migração: **0127**. A sua é a **0128**.
- Baseline da suíte: **1689 passando, 0 falhando.** Número menor é regressão.
- **Não existe CI.** As checagens locais são a única verificação — ver
  `CONVENCOES-DE-TRABALHO.md`.

## Uma dívida da Fase 2b vence nesta fase

A `CheckConstraint` `svc_cfg_activity_shape_matches_verb` exige `old_value` **e**
`new_value` não nulos quando `verb='updated'`. Isso é seguro hoje porque todo
`TRACKED_FIELDS` existente é campo de modelo **não nulo**.

O briefing da Fase 4 tem pelo menos três campos **opcionais** — teto de acúmulo, validade
do saldo transportado e valor/hora de excedente. Se algum deles entrar em `TRACKED_FIELDS`,
a escrita viola a constraint.

As três saídas possíveis estão listadas no comentário da constraint, em
`plane/db/models/service_config_activity.py`. **Decida qual usar e diga qual, antes de
implementar.** Não descubra isso num traceback.

## O que as fases anteriores deixaram pronto para esta

- **A trilha de configuração tem `verb`** (`created` / `updated` / `deleted`). O docstring
  de `ServiceConfigActivity` traz o ponto de extensão dizendo que as Fases 4 e 6 devem usar
  os três: contrato criado e contrato encerrado são eventos financeiros, exatamente como
  feriado e janela. Use `create_with_config_activity` / `delete_with_config_activity`.
- **`validate_catalog_delete`** (`plane/utils/service_catalog.py`) tem ponto de extensão
  documentado para novas guardas de exclusão.
- **O apontamento já snapshota** `applied_multiplier` e `applied_billing_route` (R4). O
  débito da Fase 4 provavelmente precisa do seu próprio snapshot — considere e justifique.
- **O motor de classificação** (`plane/utils/service_calendar.py`) é total: nunca levanta
  exceção, devolve `suggested_hour_type=None` com motivo. A Fase 4 não deve quebrar isso —
  contrato vencido **permite** o apontamento, com alerta (D9 e seção 4 do briefing).
- **`service_client_change_alert`** existe e está sem uso, esperando quem construa o move
  de work item. O critério 10 da Fase 1 segue **em aberto** por isso.

## Escopo

Só a Fase 4. Encontrando dependência das Fases 5, 6 ou 9: deixe a interface preparada,
documente o ponto de extensão e **avise** — não implemente.

Ao terminar: marque os **24** critérios da Fase 4 um por um; se algum critério de fase anterior
só puder ser fechado aqui, use o mecanismo de **critérios herdados**; reescreva este arquivo
apontando para a fase seguinte; e abra o PR.

## Depois da Fase 4

Ordem do `README.md`: **5** (bolsa por work item, depende da 4), **6** (precificação,
depende de 3 e 4), **9** (dashboards, depende de 4, 5 e 6).

A **Fase 7** (permissões e delegação) depende só da 3 e **já está desbloqueada** — pode ser
feita em paralelo, se preferir trocar de assunto.
