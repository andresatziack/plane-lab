# Fase 5 — Bolsa de horas por work item

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 4.

## Objetivo

Permitir creditar um pool de horas em um work item específico, isolado do
contrato de suporte do cliente.

**Caso de uso:** cliente tem contrato fixo de 30h/mês de suporte e fecha à parte
um projeto de 40h. Abro um chamado para o projeto, credito 40h de bolsa nele, e
os apontamentos dos técnicos consomem essa bolsa — sem tocar nas 30h de suporte
mensal.

## Escopo

### 1. Entidade Bolsa de Horas

Vinculada a um work item. Campos:

- horas creditadas (Decimal)
- horas consumidas, saldo
- descrição / referência comercial do projeto
- status (aberta / encerrada)
- autoria e data do crédito

Permitir créditos adicionais ao longo da vida do chamado (aditivo de escopo),
com histórico de cada crédito e seu autor. O saldo é o total creditado menos o
consumido.

### 2. Precedência sobre o contrato

Regra R6 do contexto mestre, reforçada: se o work item tem bolsa ativa, todo
apontamento de rota `DEBIT_POOL` naquele chamado debita a bolsa e **nunca** o
pool do contrato. Sem débito parcial nos dois.

O apontamento persiste explicitamente qual origem foi debitada — bolsa do work
item ou período do contrato. Sem isso, os relatórios da Fase 9 não conseguem
separar receita de projeto de consumo de suporte.

### 3. Estouro da bolsa

Mesmo princípio adotado para o pool de contrato na Fase 4: **não bloquear**
apontamento de trabalho já executado. A bolsa fica com saldo negativo e o alerta
aparece.

Diferença crítica em relação ao contrato: ao estourar, o excedente **nunca**
migra para o pool do contrato do cliente, nem silenciosamente nem
automaticamente. Isso violaria a intenção do recurso, que existe justamente para
isolar o projeto do suporte contratado.

Ao encerrar a bolsa com déficit, o Admin decide explicitamente: creditar horas
adicionais (aditivo de escopo) ou faturar o excedente em R$, usando o mesmo
mecanismo de lançamento de excedente da Fase 6.

### 4. UI

- Ação para creditar horas no work item, restrita a Admin
- Indicador de saldo da bolsa destacado no work item: creditado, consumido,
  restante, e percentual de consumo
- Histórico de créditos

## Critérios herdados da Fase 4: um que só pode ser fechado aqui

| Critério                                                                                                       | O que faltava                                                                                                                                                                                                                                                                                                                                                                            | Status                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Fase 4, critério 17**, terceiro destino do saldo remanescente: "convertê-lo em bolsa de horas de um chamado" | A entidade de bolsa de horas, que é desta fase. A Fase 4 entregou os outros dois destinos (transferir, expirar) auditados, e deixou este preparado: o tipo de lançamento `CONVERTED_TO_ISSUE_ALLOWANCE` já existe no livro-caixa, `end_and_create_successor` já aceita `balance_destination="issue_allowance"` e `target_issue`, e devolvia `ISSUE_ALLOWANCE_NOT_AVAILABLE` com HTTP 501 | ✅ **Fechado** — `test_service_pool.py::test_converting_the_remaining_balance_into_a_work_item_allowance` e `test_service_contract_app.py::test_converting_to_a_work_item_allowance_credits_the_issue` |

Os três passos abaixo eram o que a fase precisava fazer para fechá-lo, e **os três foram
cumpridos** — inclusive o terceiro, que é o mais fácil de esquecer:

1. dar corpo ao ramo `ISSUE_ALLOWANCE` de
   `plane.utils.service_pool.end_and_create_successor`, creditando a bolsa do work item
   informado e escrevendo a linha `CONVERTED_TO_ISSUE_ALLOWANCE` no período de origem, com
   `actor` e `notes` — o critério 20 da Fase 4 exige auditoria em **todos** os caminhos, e
   este é o caminho onde as horas mais facilmente desaparecem sem registro;
2. um teste que atravesse o mesmo caminho do usuário: saldo remanescente num contrato que
   encerra, convertido em bolsa, com o saldo do período de origem indo a zero e a bolsa do
   chamado recebendo exatamente as mesmas horas;
3. remover a asserção de indisponibilidade que hoje fixa o comportamento
   (`test_converting_to_a_work_item_allowance_is_not_available_yet` e
   `test_converting_to_a_work_item_allowance_returns_not_implemented`), e marcar o critério
   17 da Fase 4 como fechado apontando para o teste novo.

Além disso, a Fase 4 deixou o **ponto de extensão da R6** pronto e vazio:
`plane.utils.service_pool._work_item_allowance(issue)` devolve `None` hoje, e é a primeira
coisa que `apply_debit` consulta. É uma função nomeada em vez de um comentário justamente
porque a R6 é uma **hierarquia** — bolsa primeiro, depois o pool do contrato, depois valor
em R$, e nunca dois. Esta fase substitui o corpo; nada mais no motor de débito precisa
mudar.

## Critérios de aceite

Abreviações dos arquivos de teste:

| Sigla | Arquivo                                                         |
| ----- | --------------------------------------------------------------- |
| `U`   | `plane/tests/unit/utils/test_service_allowance.py`              |
| `UM`  | `plane/tests/unit/models/test_service_issue_allowance_model.py` |
| `UA`  | `plane/tests/unit/utils/test_service_pool_alerts.py`            |
| `UP`  | `plane/tests/unit/utils/test_service_pool.py`                   |
| `C`   | `plane/tests/contract/app/test_service_issue_allowance_app.py`  |

1. ✅ Admin credita 40h em um chamado e o saldo aparece no work item —
   `U::test_crediting_forty_hours_shows_the_balance_on_the_work_item` afirma as **quatro**
   grandezas que a §4 pede (creditado, consumido, restante, percentual), e não só o total.
   Pelo endpoint que o Admin usa de fato,
   `C::test_an_admin_credits_forty_hours_and_the_balance_appears`
2. ✅ Técnico aponta 5h nesse chamado: bolsa cai para 35h e o pool mensal do contrato do
   cliente permanece **intacto** —
   `U::test_logging_five_hours_consumes_the_allowance_and_leaves_the_contract_intact` e
   `C::test_logging_time_consumes_the_allowance_and_leaves_the_contract_intact`. O "intacto"
   é afirmado contra um período **que existe e vale zero**, nunca contra a ausência de
   período: período faltando e período não tocado dão o mesmo saldo e são bugs opostos.
   **O par de herança** exige teste próprio, e tem:
   `U::test_a_log_on_a_sub_task_consumes_the_parents_allowance` e
   `C::test_a_sub_task_log_consumes_the_parents_allowance` — sem eles a herança não estaria
   verificada
3. ✅ O apontamento registra que a origem debitada foi a bolsa do work item — afirmado
   dentro do critério 2: `debited_allowance` preenchido, `debited_period` nulo, e a linha
   `DEBIT` do livro-caixa com `allowance` preenchido e `contract` nulo. A exclusividade das
   duas colunas é DDL: `UM::TestWorkLogOriginExclusivity`
4. ✅ Chamado sem bolsa continua debitando do contrato normalmente —
   `U::test_a_work_item_without_any_allowance_debits_the_contract`,
   `C::test_a_log_on_an_issue_without_an_allowance_still_debits_the_contract`. **O sentido
   do critério mudou com a herança**: "sem bolsa" passou a significar sem bolsa própria
   **nem herdada**, e é assim que o teste está escrito. Ele também é o **controle positivo**
   de todo teste de isolamento acima — sem ele, "o pool não se moveu" seria satisfeito por
   um motor de débito nunca ligado
5. ✅ Crédito adicional de 10h leva o saldo de 35h para 45h, com histórico dos dois
   créditos — `U::test_an_additional_credit_adds_up_and_keeps_both_in_the_history` afirma o
   histórico com autor e data de **cada** crédito, e que continua existindo **uma só**
   bolsa: uma segunda bolsa daria a mesma soma e perderia o registro do aditivo.
   `C::test_a_second_credit_adds_up_and_returns_both`
6. ✅ Excluir apontamento devolve as horas à bolsa, não ao contrato —
   `U::test_deleting_a_log_returns_the_hours_to_the_allowance_not_the_contract` afirma as
   **duas** metades: a bolsa voltou e o contrato não foi creditado. O mecanismo que garante
   isso é `U::test_the_reversal_reads_the_debited_amount_from_the_debit_row` — a origem sai
   do que o débito persistiu, nunca de re-resolver a R6
7. ✅ Estourar a bolsa não debita do contrato em nenhuma circunstância —
   `U::test_overflowing_the_allowance_never_reaches_the_contract` (saldo vai a −15h, o
   apontamento fica gravado, o pool não se move). E, mais forte que qualquer teste de
   comportamento, `U::test_one_work_log_can_never_carry_two_debits`: o índice único
   `(service_log, entry_type)` faz o débito duplo ser **recusado pelo banco**, não por um
   `if`. Foi por isso que a bolsa reusa o livro-caixa do contrato
8. ✅ Apontamento com multiplicador 2.0 consome o dobro da bolsa —
   `U::test_a_multiplier_of_two_consumes_double_the_allowance`. Vale sem ramificação: o
   débito lê `debited_hours`, que a R4 já gravou como apontadas × multiplicador
9. ✅ Apontamento de rota `NON_BILLABLE` não consome bolsa —
   `U::test_a_non_billable_log_consumes_no_allowance`, que afirma **o motivo** da ausência
   (rota gravada, `debited_hours` zero, nenhuma origem reivindicada, nenhuma linha no
   livro-caixa) com controle positivo na mesma fixture. Vale três vezes: a ordem das guardas
   em `apply_debit`, a constraint da Fase 3, e a constraint nova
   `service_log_non_billable_debits_no_origin` — `UM::TestNonBillableDebitsNoOrigin`

## Além dos critérios: a herança e seus guarda-corpos

A bolsa é **herdada pela árvore de work items, ancestral mais próximo primeiro**. O briefing
não diz isso, e a decisão está registrada como **D28** no `DECISOES.md`: um projeto de 40h é
quebrado em sub-tarefas por qualquer um que o execute, e sem herança o pai ficaria com 40h
que ninguém aponta enquanto cada sub-tarefa debitava o pool de suporte — o isolamento que a
feature existe para dar, perdido em silêncio.

Os guarda-corpos, todos com teste:

| Guarda-corpo                                                                                             | Teste                                                                    |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Limite de profundidade de 10 ancestrais, e estourar = **ausência de bolsa**, nunca erro                  | `U::test_an_ancestor_beyond_the_depth_limit_counts_as_no_allowance`      |
| Pai **e** filho com bolsa: o filho vence (caracterização)                                                | `U::test_the_nearest_allowance_wins_when_parent_and_child_both_have_one` |
| Bolsa encerrada no ancestral **bloqueia**, e não cai para o contrato nem para um ancestral mais distante | `U::test_a_closed_ancestor_allowance_blocks_and_does_not_fall_back`      |
| Ciclo de `parent` não trava o débito                                                                     | `U::test_a_parent_cycle_does_not_hang_the_debit`                         |
| O painel diz que a bolsa é herdada, e de qual chamado                                                    | `U::test_the_panel_says_the_allowance_is_inherited_and_from_where`       |

## Limitações aceitas, com o teste que as fixa

- **O alerta `NO_SERVICE_LOGS_IN_MONTH` dispara para um cliente atendido só por bolsas.**
  Está correto — o alerta é sobre consumo _do contrato_, e um cliente cujo trabalho todo vai
  para bolsas de projeto de fato não consome o suporte que paga, o que é sinal legítimo de
  renovação. O que seria errado é a **leitura**: "cliente sem atendimento" sobre um cliente
  com 40h de projeto faz alguém ligar para o cliente errado. Então o alerta carrega
  `allowance_hours_in_period` e a interface diz "sem apontamentos no contrato (Nh em bolsas
  de projeto)". Fixado por
  `UA::test_the_churn_alert_still_fires_for_a_client_served_only_through_allowances`
- **Não existe reabrir bolsa encerrada.** Creditar numa bolsa encerrada é recusado
  (`U::test_crediting_a_closed_allowance_is_refused`), porque seria reabrir pela porta de
  trás. As duas saídas da §3 para um déficit acontecem **antes** do encerramento
- **Alerta de bolsa não é dispensável**, ao contrário do alerta de período.
  `ServiceContractAlertDismissal` tem FK obrigatória para período, e reusá-la exigiria mais
  um par nullable com constraint própria para algo que a §3 não pede — ela pede que o alerta
  apareça. Dívida nomeada da **Fase 9**

## Entregar

Modelo de dados primeiro, com atenção especial a como a precedência de origem é
resolvida e persistida. Testes cobrindo o isolamento entre bolsa e contrato.
