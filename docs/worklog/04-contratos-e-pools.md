# Fase 4 — Contratos, pool de horas mensais e saldo acumulado

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 3.
> Leia a D18 do `DECISOES.md` antes de rodar esta fase — hierarquia
> matriz/filial pode alterar o motor de débito.

## Objetivo

Permitir que um Cliente tenha contrato de suporte com pool de horas mensais,
que os apontamentos debitem automaticamente desse pool, que o saldo não
utilizado acumule, e que o excedente seja tratado sem bloquear o trabalho.

## Escopo

### 1. Entidade Contrato

Vinculada a um Cliente. Campos:
- Identificação / número do contrato
- Horas mensais contratadas (Decimal)
- Data de início e data de fim
- **Política de acúmulo** — saldo não utilizado acumula para os meses seguintes.
  Campo opcional de validade do saldo transportado em meses (vazio = não expira)
- **Teto de acúmulo** (opcional) — limite do saldo transportável, absoluto ou
  como múltiplo das horas mensais (ex.: 2×). Saldo acima do teto não é
  transportado e é registrado como expirado por teto
- **Limite de alerta de alto consumo** e **de baixo consumo** — percentuais que
  disparam os avisos da seção 8
- **Política de excedente** — ver seção 4
- Valor/hora de excedente (Decimal, opcional) — usado ao faturar excedente.
  Padrão: o valor/hora base do Cliente
- Status (ativo / suspenso / encerrado)
- Contrato anterior (FK opcional) — para rastrear a cadeia de renovações
- Observações

### 1b. Múltiplos contratos por Cliente

Um Cliente **pode ter mais de um contrato ativo simultaneamente** — ex.: um
contrato de suporte 10h/mês e um contrato de infraestrutura 20h/mês, com pools
separados.

Modelagem:
- Cada Cliente tem um **contrato padrão** (flag no contrato ou FK no Cliente)
- Cada **project** pode fixar um contrato específico. Como o Cliente já vem do
  project (contexto mestre, seção 2b), fixar o contrato no project resolve o caso
  de `Marubeni – Suporte` e `Marubeni – Infra` debitarem pools distintos
- Resolução do contrato a debitar, em ordem: contrato fixado no project → contrato
  padrão do Cliente vigente na data do atendimento
- Se a resolução for ambígua ou vazia, **falhar de forma explícita** com mensagem
  clara. Nunca escolher um contrato arbitrariamente: debitar o pool errado é pior
  que bloquear o apontamento

Um Cliente também pode ter histórico de contratos encerrados. A restrição de não
sobreposição vale apenas entre contratos que disputam o mesmo escopo — dois
contratos ativos com escopos distintos são válidos.

**Contrato vencido ou não iniciado.** Coerente com a política de não bloquear
trabalho já executado (seção 4): o apontamento é **permitido**, com alerta
visível no formulário e no work item, e sinalizado nos relatórios como
apontamento fora de vigência contratual. Nunca descartar o registro por causa de
uma pendência comercial.

### 2. Período de competência (pool mensal)

Entidade que representa o pool de um mês específico de um contrato:
- contrato, mês/ano de competência
- horas contratadas do mês
- **horas transportadas** do período anterior (positivas ou negativas)
- horas concedidas = contratadas + transportadas
- horas consumidas
- saldo = concedidas − consumidas
- status (aberto / fechado)
- referência ao lançamento de excedente, quando houver

Os períodos devem ser gerados de forma determinística a partir da vigência do
contrato. Consumo e saldo devem ser consistentes mesmo com apontamentos
concorrentes — usar transação e lock adequado, nunca ler-somar-gravar sem
proteção.

### 3. Acúmulo de saldo

Ao fechar um período com saldo positivo, o saldo é transportado para o período
seguinte como horas transportadas.

Se o contrato definir validade do saldo transportado, o saldo expira após N
meses — exige rastrear a origem (competência) de cada parcela de saldo
transportado, para expirar a parcela correta. Se o campo estiver vazio, o saldo
não expira.

O teto de acúmulo, quando definido, limita o transporte. O saldo descartado por
teto deve ser registrado e visível — ele é um indicador de que o cliente está
pagando por horas que não usa.

O tratamento do saldo remanescente no encerramento do contrato está na seção 8.

### 4. Saldo negativo e regularização de excedente

**Não bloquear apontamento por falta de saldo.** Trabalho já executado precisa
ser registrado; bloquear gera apontamento perdido, que é pior que saldo negativo.

Comportamento:
- O apontamento é salvo normalmente e o saldo do período fica negativo
- Alerta visível no work item, na tela do Cliente e no painel operacional
- Ao fechar o período com déficit, o Admin escolhe entre duas saídas:
  - **(a) Transportar o déficit** para o período seguinte, reduzindo as horas
    disponíveis do próximo mês
  - **(b) Faturar o excedente em R$** ao cliente — gera um lançamento de
    excedente daquele período, zera o déficit e **preserva integralmente as
    horas dos meses seguintes**

A opção (b) é o caso de uso explícito de "o cliente prefere pagar o excedente do
mês para não comprometer o resto do contrato".

O contrato pode ter uma preferência padrão, mas a escolha deve ser confirmável
período a período — a decisão é comercial e muda caso a caso.

Nesta fase, implementar o lançamento de excedente com as horas e a marcação de
regularização. **A conversão em valor monetário é implementada na Fase 6** —
deixe a interface preparada e documentada.

### 5. Motor de débito

Ao salvar um apontamento cuja rota de faturamento é `DEBIT_POOL`:
1. Resolver o Cliente a partir do **project** do work item (contexto mestre,
   seção 2b)
2. Resolver o contrato: contrato fixado no project → contrato padrão do Cliente,
   vigente na **data do atendimento** (regra R7 e seção 1b)
3. Resolver o período de competência do mês daquela data
4. Debitar `horas_equivalentes` do período
5. Persistir no apontamento a referência do período debitado e o multiplicador
   aplicado (regra R4)

Apontamento de rota `NON_BILLABLE` não debita (regra R5).
Editar ou excluir um apontamento deve estornar e reaplicar o débito de forma
transacional e idempotente. Nunca deixar saldo divergente.

### 6. Fechamento de período

Período fechado não aceita novos apontamentos nem alteração de apontamentos
existentes, exceto por Admin com registro em auditoria. Isso protege meses já
faturados. Não há workflow de aprovação de timesheet (decisão D10); o
travamento de período é o que protege o faturamento.

Fechar um período dispara: cálculo do saldo final, decisão de acúmulo ou de
excedente, e criação do período seguinte com as horas transportadas corretas.

### 7. Visibilidade do saldo

- No work item: quanto do pool do mês foi consumido pelo cliente daquele chamado,
  incluindo o saldo acumulado disponível
- Na tela do Cliente: saldo do mês corrente, saldo acumulado (com a competência
  de origem de cada parcela) e histórico por competência
- Alerta visual quando o saldo estiver abaixo do limite configurado, e alerta
  distinto quando estiver negativo

### 8. Renovação e encerramento de contrato

Ao chegar ao fim da vigência com saldo acumulado, o Admin escolhe entre três
caminhos, todos suportados:

**(a) Renovar mantendo o contrato** — nova data de fim e, opcionalmente, novas
horas mensais (tipicamente menores, justamente porque há saldo acumulado). O
saldo acumulado é transportado para o novo período. Mantém a continuidade do
histórico.

**(b) Renovar expirando o saldo** — o saldo remanescente é marcado como expirado
por encerramento, com registro de quantas horas e quando, para efeito de
histórico e de negociação futura.

**(c) Encerrar e criar contrato novo** — o contrato antigo é desativado e um novo
é criado, referenciando o anterior. Ao criar, o Admin decide o destino do saldo
remanescente:
  - transferir para o novo contrato
  - expirar
  - **converter em bolsa de horas de um work item** — o cliente usa as horas que
   sobraram em um projeto pontual, em vez de perdê-las. Usa o mecanismo da
   Fase 5; deixe a interface preparada e documentada se a Fase 5 ainda não
   estiver implementada

Em todos os caminhos: registrar em auditoria quem decidiu, quando, e o destino
das horas. Nunca fazer o saldo desaparecer sem registro.

### 9. Alertas de clientes que precisam de atenção

Painel visível aos técnicos e ao Admin, sinalizando clientes que exigem ação nos
**dois extremos** de consumo:

**Alto consumo** — risco de estouro e de trabalho não remunerado:
- consumo acima do limite configurado antes do meio do mês
- saldo do mês negativo
- tendência que projeta estouro antes do fim do mês

**Baixo consumo** — risco comercial de não renovação:
- consumo abaixo do limite configurado ao fim do mês
- saldo acumulado acima de N meses de horas contratadas
- horas descartadas por teto de acúmulo
- contrato sem nenhum apontamento no mês

O baixo consumo é um sinal de churn: o cliente está pagando por um contrato que
não usa e vai questionar a renovação. Tratar esse alerta como oportunidade de
atendimento proativo, não como informação secundária — é tão acionável quanto o
alerta de estouro.

Alertas devem ser configuráveis por contrato (os limites) e dispensáveis por
período, para não virar ruído.

## Critérios de aceite

Abreviações dos arquivos de teste:

| Sigla | Arquivo                                                             |
| ----- | ------------------------------------------------------------------- |
| `U`   | `plane/tests/unit/utils/test_service_pool.py`                        |
| `UA`  | `plane/tests/unit/utils/test_service_pool_alerts.py`                 |
| `UC`  | `plane/tests/unit/utils/test_service_pool_concurrency.py`            |
| `UM`  | `plane/tests/unit/models/test_service_contract_model.py`             |
| `UD`  | `plane/tests/unit/bg_tasks/test_service_pool_deletion_cascade.py`    |
| `C`   | `plane/tests/contract/app/test_service_contract_app.py`              |

1. ✅ Contrato de 30h/mês com vigência de 12 meses gera períodos de competência
   corretos — `U::test_a_twelve_month_contract_generates_twelve_periods` afirma os
   **limites reais** de janeiro e dezembro além da contagem: doze períodos podem ser doze
   pelos motivos errados. Também `C::test_an_admin_creates_a_contract_and_its_periods`, e
   `U::test_a_partial_first_month_is_clipped_to_the_vigency` para o mês parcial
2. ✅ Apontamento de `2h` com multiplicador 1.0 reduz o saldo do mês de 30h para
   28h — `U::test_two_hours_at_one_times_leaves_twenty_eight` e, ponta a ponta pelo
   endpoint, `C::test_a_two_hour_log_leaves_twenty_eight`
3. ✅ Apontamento de `2h` com multiplicador 2.0 reduz o saldo em 4h, não 2h —
   `U::test_two_hours_at_two_times_takes_four_not_two`, `C::test_a_double_multiplier_takes_four_hours`
4. ✅ Apontamento de rota `NON_BILLABLE` (Garantia ou Cortesia) não altera o saldo —
   `U::test_a_non_billable_route_does_not_move_the_balance_and_says_why`. A ausência é
   afirmada por **três** vias (rota gravada, `debited_hours` zero, nenhuma linha `DEBIT`) e
   com controle positivo na mesma fixture. Vale **por construção**: o débito lê
   `debited_hours`, e a constraint `service_log_debited_hours_follows_billing_route`
   garante zero nessa coluna
5. ✅ Cliente consome 22h de 30h em janeiro: fevereiro abre com 38h concedidas —
   `U::test_twenty_two_of_thirty_leaves_february_with_thirty_eight`
6. ✅ Com validade de saldo definida em 2 meses, o saldo de janeiro não utilizado
   expira no momento correto e não infla os meses seguintes —
   `U::test_a_parcel_expires_after_its_carryover_validity`. O teste **consome 4h em
   fevereiro**, de propósito: sem consumo o FIFO não é exercido e o critério viraria uma
   conferência de aritmética de datas. Percorre os três fechamentos e afirma que a parcela
   sobrevive aos dois primeiros. A ordem FIFO que o torna determinado é
   `U::test_consumption_eats_the_oldest_parcel_first` (D6)
7. ✅ Cliente com 2h de saldo recebe apontamento de 5h: o apontamento é salvo, o
   saldo fica em −3h, e o alerta aparece —
   `U::test_a_negative_balance_is_allowed_and_never_blocks` (o apontamento **existe** e o
   saldo é −3h) e `UA::test_a_negative_balance_raises_its_own_alert`
8. ✅ Fechando o período com déficit e escolhendo transportar, o mês seguinte abre
   com 27h em vez de 30h — `U::test_carrying_a_deficit_opens_the_next_month_short`,
   `C::test_closing_with_a_carried_deficit`
9. ✅ Fechando o período com déficit e escolhendo faturar o excedente, o mês
   seguinte abre com 30h íntegras e o lançamento de excedente de 3h é registrado —
   `U::test_billing_the_overage_keeps_the_next_month_whole`, `C::test_closing_by_billing_the_overage`
10. ✅ Apontamento com data de atendimento no mês anterior debita o período do mês
    anterior, não o corrente — `U::test_a_backdated_log_debits_the_backdated_month`, que
    afirma **os dois** meses: "março mexeu" é metade da alegação
11. ✅ Excluir um apontamento devolve exatamente as horas ao período correto —
    `U::test_deleting_a_log_returns_exactly_its_hours`, `C::test_deleting_a_log_returns_its_hours`
12. ✅ Editar o tempo de um apontamento de `2h` para `3h` deixa o saldo consistente,
    sem débito duplicado — `C::test_editing_a_log_from_two_to_three_hours_leaves_no_double_debit`
    pelo endpoint de edição de batch, que é onde o débito duplicado realmente aconteceria.
    `U::test_the_reversal_returns_the_debited_amount_not_a_recomputed_one` prova o
    mecanismo: o estorno lê a linha `DEBIT` em vez de recalcular
13. ✅ Período fechado rejeita novo apontamento com mensagem clara —
    `C::test_a_closed_period_rejects_a_new_log_and_rolls_the_rows_back`, que afirma também
    que **as linhas voltam atrás**: uma recusa que deixasse o apontamento gravado sem pool
    seria pior que nenhuma recusa. Controle positivo:
    `C::test_a_log_in_an_open_month_still_works_after_another_is_closed`
14. ✅ Dois apontamentos salvos simultaneamente no mesmo período não corrompem o
    saldo — `UC::test_concurrent_debits_do_not_corrupt_the_balance`, com oito threads reais
    sob `django_db(transaction=True)`. Ver a nota sobre **o que a sabotagem revelou** na
    seção de verificação abaixo: o teste só fica vermelho quando o lock **e** o `F()` são
    removidos, e então perde sete débitos de oito.
    `UC::test_a_debit_racing_a_close_cannot_land_in_the_closed_month` cobre o que só o lock
    protege, e `UC::test_concurrent_materialisation_of_one_competency_creates_one_period` a
    materialização concorrente
15. ✅ Com teto de acúmulo de 2× e contrato de 30h/mês, o saldo transportado nunca
    passa de 60h, e o excesso descartado fica registrado —
    `U::test_the_accrual_cap_limits_the_carry_and_records_the_discard`. A **ordem** do
    descarte é uma ambiguidade que o briefing não fecha e está fixada por
    `U::test_the_cap_discards_the_newest_parcels_first`
16. ✅ Renovar o contrato mantendo-o (caminho a) transporta o saldo acumulado para o
    novo período — `U::test_renewing_in_place_carries_the_accumulated_balance`,
    `C::test_renewing_in_place_extends_the_vigency`
17. ⚠️ **Parcialmente atendido — o terceiro destino é fechado na Fase 5.** Transferir e
    expirar estão implementados e auditados
    (`U::test_a_successor_can_receive_the_transferred_balance`,
    `U::test_a_successor_can_expire_the_balance_with_an_audit_row`,
    `C::test_creating_a_successor_transfers_the_balance`). **Converter em bolsa de horas de
    um chamado depende da entidade da Fase 5**, que não existe: a interface, o tipo de
    lançamento `CONVERTED_TO_ISSUE_ALLOWANCE` e o argumento `target_issue` já estão
    prontos, e a chamada devolve `ISSUE_ALLOWANCE_NOT_AVAILABLE` com HTTP 501 — um código
    explícito, não um no-op silencioso
    (`U::test_converting_to_a_work_item_allowance_is_not_available_yet`,
    `C::test_converting_to_a_work_item_allowance_returns_not_implemented`). Registrado como
    critério herdado em `05-bolsa-por-workitem.md`
18. ✅ Cliente com consumo de 90% do pool no dia 10 aparece no painel de alto
    consumo — `UA::test_ninety_percent_on_the_tenth_raises_the_high_consumption_alert` e
    `C::test_high_consumption_before_midmonth_appears`, com `reference_date` explícito para
    que o teste não passe ou falhe segundo o dia em que roda. Controle negativo com motivo:
    `UA::test_the_same_consumption_late_in_the_month_does_not_raise_it_but_raises_another`
19. ✅ Cliente sem nenhum apontamento no mês aparece no painel de baixo consumo —
    `UA::test_a_month_with_no_work_logs_raises_the_churn_alert`,
    `C::test_a_contract_with_no_logs_appears_in_low_consumption`
20. ✅ Saldo nunca desaparece sem registro de auditoria em nenhum dos fluxos de
    renovação, expiração ou teto — atendido **por construção**, não por diligência: não
    existe caminho que reduza saldo sem inserir linha no livro-caixa, porque a redução *é*
    a linha. O invariante que o prova é
    `U::test_a_closed_period_ledger_sums_to_exactly_zero` (um período fechado soma zero:
    tudo saiu, foi faturado ou foi baixado, e cada um desses é uma linha). Cada caminho de
    renovação afirma `actor` e `notes` nas linhas que gera
21. ✅ Cenário de referência Marubeni / Terlogs —
    `U::test_two_clients_have_independent_pools`, que monta a relação societária
    (`ServiceClient.parent`) de propósito e afirma que o pool da matriz **não** se move
    (D18: o campo não carrega comportamento)
22. ✅ Cliente com dois contratos ativos e dois projects debita cada project no
    contrato fixado nele — `U::test_a_project_pinned_contract_wins`
23. ✅ Cliente com dois contratos ativos e nenhum fixado no project debita o contrato
    padrão — `U::test_the_default_contract_is_used_when_the_project_pins_none`
24. ⚠️ **Atendido para o caso ambíguo; deliberadamente NÃO atendido ao pé da letra para o
    caso vazio.** Ambiguidade falha explicitamente e nomeia os candidatos
    (`U::test_an_ambiguous_resolution_fails_explicitly`), e um pin para o contrato de outro
    cliente falha com código próprio
    (`U::test_a_pin_to_another_clients_contract_is_refused`). **Mas uma resolução _vazia_
    não custa ao técnico o apontamento dele.** A justificativa do próprio critério —
    "debitar o pool errado é pior que bloquear o apontamento" — só vale quando existe um
    pool errado a debitar, e a §4, a D4 e a D9 dizem três vezes que trabalho já executado
    nunca é descartado por pendência comercial. A resolução continua falhando **alto**: o
    apontamento fica com `debited_period` nulo e o painel do chamado informa o código.
    Ver `U::test_an_absent_contract_does_not_cost_the_technician_their_work_log` e
    `U::test_an_ambiguous_resolution_still_blocks_the_work_log`. **Isto é regra de negócio,
    não escolha de implementação, e está sinalizado para confirmação.**

## Critérios que a Fase 4 deixa herdados

| Critério                             | O que falta                                                                             | Onde fecha            |
| ------------------------------------ | --------------------------------------------------------------------------------------- | --------------------- |
| 17, terceiro destino do saldo        | A entidade de bolsa de horas por work item. Interface, tipo de lançamento e argumento `target_issue` prontos; hoje devolve `ISSUE_ALLOWANCE_NOT_AVAILABLE` | **Fase 5**, seção 3   |

Nenhum critério de fase anterior pôde ser fechado aqui. O **critério 10 da Fase 1**
(mover um chamado com apontamentos entre clientes) permanece em aberto pelo mesmo motivo
registrado na Fase 3: não existe caminho de *move* de work item no backend. A Fase 4 não o
cria, então a detecção continua escrita e testada em `service_client_change_alert` sem
consumidor.

## Verificação por sabotagem

Os cinco pontos onde o dinheiro vaza, quebrados de propósito, com o resultado real:

| Sabotagem                                        | Ficou vermelho?                                                            |
| ------------------------------------------------ | -------------------------------------------------------------------------- |
| remover `select_for_update`                      | **Não sozinho** — ver a nota abaixo                                        |
| remover o `F()` do `consumed_hours`              | **Não sozinho** — ver a nota abaixo                                        |
| remover **os dois** ao mesmo tempo               | Sim: 8h viraram 1h, sete débitos de oito perdidos                          |
| remover a unique parcial do `DEBIT`              | Sim, 2 testes (débito duplicado)                                           |
| inverter o FIFO                                  | Sim, 3 testes (critérios 6 e 15)                                           |
| quebrar o snapshot de `contracted_hours`         | Sim, 3 testes                                                              |
| remover o estorno                                | Sim, 7 testes (critérios 11 e 12)                                          |

**O que a sabotagem do critério 14 revelou, e é a notícia mais importante desta seção.**
O plano previa que o lock e o `F()` fossem "duas metades de uma garantia" e que um teste
sabotasse cada metade independentemente. **Isso é falso, e uma versão anterior do docstring
de `_lock_period` afirmava exatamente isso.** As duas são *independentemente suficientes*
para a aritmética: sem o lock, o `F()` faz o incremento no SQL; sem o `F()`, o lock
serializa o ler-somar-gravar. Nenhum teste pode distinguir as duas, porque com qualquer uma
presente o comportamento está correto. O teste original passava com cada uma removida — era
um falso-verde do tipo que as convenções descrevem, e só a sabotagem o encontrou. Duas
correções foram feitas: o docstring passou a dizer a verdade, e
`UC::test_a_debit_racing_a_close_cannot_land_in_the_closed_month` foi acrescentado para
cobrir o que **só** o lock protege — a sequência ler-`status`-depois-debitar, na qual um
`close_period` concorrente cabe no meio.

A mesma sabotagem revelou dois testes mais fracos do que se anunciavam, ambos corrigidos:
o do snapshot afirmava só a **coluna** `contracted_hours` e passava com `granted_hours`
lendo o contrato ao vivo; e o da validade de acúmulo percorria um pool intocado, sem
exercer o FIFO que o critério 6 depende.

## Entregar

Modelo de dados e desenho do motor de débito primeiro, incluindo a estratégia
de concorrência, de estorno, e de rastreamento das parcelas de saldo
transportado. Testes de acúmulo, estorno, excedente e concorrência são
obrigatórios — é aqui que o dinheiro vaza.
