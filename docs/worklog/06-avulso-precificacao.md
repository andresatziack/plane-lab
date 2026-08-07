# Fase 6 — Cliente avulso, precificação em R$ e faturamento de excedente

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 3. Para a seção 6,
> depende também da Fase 4.

## Objetivo

Converter apontamentos em valor monetário para clientes sem contrato, e faturar
o excedente de clientes com contrato. A cobrança ocorre no início de cada mês,
referente aos atendimentos do mês anterior.

## Escopo

### 1. Tabela de preços por Cliente

Configurada no painel de administração do Cliente. Modelo definido:
- **Valor/hora base** do cliente (ex.: R$ 200,00)
- Valor efetivo de cada Tipo de Hora = valor base × multiplicador do tipo
  - Horário comercial (1.0) → R$ 200,00
  - Fora do expediente (1.5) → R$ 300,00
  - Domingos e feriados (2.0) → R$ 400,00
- Possibilidade de **sobrescrever** com valor absoluto para um Tipo de Hora
  específico daquele cliente, quando a regra fugir do multiplicador

A tela deve exibir a tabela efetiva resultante, para o admin conferir os valores
finais sem fazer a multiplicação de cabeça. Configurar um cliente novo deve
exigir mudar um único número.

### 2. Vigência de preços

Preços têm data de início de vigência. Reajuste anual não deve reescrever o
histórico. Combinado com a regra R4, o apontamento persiste o valor/hora vigente
no momento do registro.

### 3. Cálculo do valor

Para apontamento com rota de faturamento `BILL_AMOUNT`:

```
valor = horas_equivalentes × valor_hora_base_do_cliente
```

Ou, quando houver override absoluto para o Tipo de Hora:

```
valor = horas_apontadas × valor_absoluto_do_tipo
```

Regras:
- `Decimal`, duas casas, `ROUND_HALF_UP`
- Rota `NON_BILLABLE` (Garantia, Cortesia) → valor R$ 0,00 (regra R5)
- Persistir no apontamento: valor/hora aplicado e valor calculado (snapshot)

### 4. Exibição

- No work item de cliente avulso: valor por apontamento e **valor total**
- Em cliente de contrato: não exibir valores em R$, apenas horas — exceto para
  apontamentos com rota `BILL_AMOUNT` lançados fora do escopo do contrato, e
  para excedente faturado
- Valores visíveis apenas para papéis autorizados. Definir se técnico vê valor
  ou apenas Admin — recomendo que técnico veja apenas horas

### 5. Consolidado mensal para faturamento

Visão por Cliente e mês de competência, listando os apontamentos faturáveis, as
horas equivalentes e o valor total, com exportação em CSV. É o insumo do
faturamento do início do mês.

**Não construir pipeline de exportação novo.** O repositório já tem um, e já tem
uma vaga reservada com este nome:

```python
# apps/api/plane/db/models/exporter.py:26
("issue_worklogs", "Issue Worklogs")
```

Essa choice existe e nenhum código a usa. Implementar o handler dela no pipeline
existente (`plane/bgtasks/export_task.py`, `ExporterHistory`,
`plane/utils/exporters/formatters.py`) e ganhar de graça fila, histórico, status,
retry e download. Ver `docs/worklog/OPORTUNIDADES.md`, seção A2.

Respeitar a regra R7: a competência é a data do atendimento.

O consolidado deve separar claramente **quatro** origens de receita (D36):
- apontamentos avulsos
- apontamentos fora de escopo de clientes com contrato
- excedente de contrato faturado (seção 6)
- **excedente de bolsa de horas** faturado — não se mistura com o de contrato: excedente de
  contrato é suporte consumido acima do contratado, excedente de bolsa é projeto entregue
  acima do orçado. Fundi-los esconderia o indicador de qualidade da sua estimativa

Note que apontamentos podem cair em avulso por três motivos distintos (D33): o cliente é
avulso, o contrato está suspenso, ou o contrato venceu. O **motivo** precisa aparecer no
consolidado, porque os dois últimos são pendência comercial com prazo.

### 6. Faturamento de excedente de contrato

Completa o que a Fase 4 deixou preparado. Quando o Admin fecha um período de
contrato com déficit e escolhe faturar o excedente:

```
valor_excedente = horas_excedentes × valor_hora_de_excedente
```

Onde `valor_hora_de_excedente` é o campo do contrato, com fallback para o
valor/hora base do Cliente.

Regras:
- O lançamento de excedente entra no consolidado mensal do Cliente, identificado
  como excedente de contrato, não como apontamento avulso
- Persistir o valor/hora aplicado como snapshot (R4)
- Faturar o excedente zera o déficit e **não** reduz as horas dos meses
  seguintes
- Um período não pode ter excedente faturado duas vezes
- Estornar o faturamento de excedente deve restaurar o déficit e o transporte de
  forma consistente

Ponto de atenção: as horas excedentes já carregam o multiplicador do Tipo de Hora
(são `horas_equivalentes`). Aplicar o multiplicador de novo aqui cobraria em
dobro. O valor/hora de excedente incide sobre as horas equivalentes já
calculadas.

## Critérios de aceite

1. Cliente avulso com base R$ 200,00: apontamento de `1h` em horário comercial
   gera R$ 200,00
2. O mesmo apontamento em Fora do expediente (1.5) gera R$ 300,00
3. Apontamento de `1h 15min` em horário comercial gera R$ 250,00
4. Apontamento de rota `NON_BILLABLE` gera R$ 0,00 e aparece na lista como não
   cobrado, com o Tipo de Atendimento identificado
5. Override absoluto de R$ 350,00 em Domingos e feriados prevalece sobre o
   cálculo por multiplicador
6. Reajustar o valor base do cliente não altera o valor de apontamentos já
   registrados
7. Cliente de contrato com déficit de 3h e valor de excedente R$ 250,00 gera
   lançamento de R$ 750,00 ao faturar o excedente
8. O excedente faturado não aplica o multiplicador duas vezes
9. Faturar excedente duas vezes no mesmo período é rejeitado
10. O consolidado mensal separa avulso, fora de escopo e excedente
11. O consolidado mensal soma corretamente e exporta em CSV
12. Apontamento retroativo entra na competência do mês do atendimento
13. Nenhum arredondamento monetário perde ou cria centavos na soma do
    consolidado

## Entregar

Modelo de dados e desenho da vigência de preços primeiro. Testes de
precificação, de faturamento de excedente e de arredondamento monetário são
obrigatórios.

---

## Status: implementada

Suíte: **2042 passando, 0 falhando** (baseline de entrada: 1913). Migração `0130`,
validada aplicando a cadeia, semeando dados, revertendo e reaplicando. Decisões desta
fase: **D37 a D44** em `DECISOES.md`. O desenho aprovado antes do código está em
`06-design-proposto.md`.

Prefixos dos testes: `M` = `unit/models/test_service_pricing_model.py`,
`$` = `unit/utils/test_service_money.py`, `P` = `unit/utils/test_service_pricing.py`,
`B` = `unit/utils/test_service_billing.py`, `AP` = `contract/app/test_service_pricing_app.py`.

| # | Critério | Onde fecha |
| - | -------- | ---------- |
| 1 | `1h` comercial a R$ 200,00 gera R$ 200,00 | `$::test_one_commercial_hour_at_two_hundred_is_two_hundred`, `P::test_a_commercial_hour_is_priced_from_the_base_rate` |
| 2 | Fora do expediente (1.5) gera R$ 300,00 | `$::test_one_after_hours_hour_is_three_hundred_through_the_hours_not_the_rate`, `P::test_an_after_hours_entry_is_priced_through_its_equivalent_hours` |
| 3 | `1h 15min` comercial gera R$ 250,00 | `$::test_one_hour_fifteen_of_commercial_time_is_two_hundred_and_fifty` |
| 4 | `NON_BILLABLE` gera R$ 0,00 e aparece identificado | `P::test_a_non_billable_route_never_reaches_pricing`, `M::test_a_non_billable_row_cannot_carry_money` |
| 5 | Override de R$ 350,00 prevalece | `P::test_an_override_prices_from_logged_hours_not_equivalent_hours`, `AP::test_an_override_prevails_in_the_effective_table` |
| 6 | Reajustar a base não altera apontamento já registrado | `P::test_a_readjustment_does_not_move_an_already_priced_log`, `AP::test_a_readjustment_is_a_new_vigency_not_an_edit` |
| 7 | Déficit de 3h a R$ 250,00 gera R$ 750,00 | `$::test_a_three_hour_overage_at_two_fifty_is_seven_hundred_and_fifty`, `B::test_billing_the_overage_writes_hours_and_reais_on_one_row` |
| 8 | O excedente não aplica o multiplicador duas vezes | `$::test_overage_amount_has_no_multiplier_parameter` (assinatura), `$::test_an_after_hours_overage_is_not_multiplied_again` |
| 9 | Faturar excedente duas vezes é rejeitado | `B::test_a_second_overage_billing_for_one_period_is_refused_by_the_database` — **DDL**, não `if` |
| 10 | O consolidado separa as origens | `B::test_an_avulso_client_lands_in_standalone`, `B::test_a_deviated_log_is_standalone_with_its_reason_beside_it`, `B::test_a_contract_overage_lands_in_the_periods_competency_not_the_closing_month` |
| 11 | O consolidado soma e exporta em CSV | `AP::test_it_reports_the_month_by_client_and_origin`, `AP::test_it_queues_a_row_of_the_reserved_type` |
| 12 | Retroativo entra na competência do atendimento | `P::test_a_retroactive_entry_prices_at_the_rate_of_its_service_date` |
| 13 | Nenhum arredondamento perde ou cria centavos | `$::test_summing_rounded_values_is_the_number_that_matches_the_invoice`, `B::test_the_total_is_a_sum_of_persisted_amounts` |

O critério 6 da **§6** ("estornar o faturamento de excedente") **não** foi implementado.
É dívida nomeada com justificativa e roteiro em **D42**, compensada por confirmação
explícita de irreversibilidade e pelo índice único que impede o erro mais provável.

## As três armadilhas que o briefing não avisava

**1. O critério 5 é um segundo "cobrar em dobro".** A §3 usa `horas_equivalentes` com a
base, mas `horas_apontadas` com o override — porque o valor absoluto **já embute** o
multiplicador, ele *substitui* `base × multiplicador`. Override de R$ 350 num domingo
(2.0): o certo é `1.0 × 350 = R$ 350`; usando `equivalent` sai R$ 700. É a doença do
critério 8 vestida de critério 5. A defesa é estrutural: **duas funções keyword-only com
nomes de parâmetro diferentes** (`amount_from_base_rate` / `amount_from_absolute_rate`),
não uma função com flag — a flag errada é invisível na chamada, o nome errado levanta
`TypeError`. Fixado por `$::test_the_two_formulas_are_separate_functions_with_separate_parameter_names`.

**2. Empates de meio centavo são alcançáveis e comuns.** Horas são múltiplos de 0,25 e
uma taxa tem duas casas, então o produto cai exatamente no meio centavo: `0,25h ×
R$ 180,50 = 45,125`. `ROUND_HALF_UP` dá R$ 45,13; o padrão do contexto decimal
(`ROUND_HALF_EVEN`, que `quantize_hours` herda) dá R$ 45,12 e arredondaria metade dos
empates **contra** o fornecedor. Por isso `quantize_money` passa `rounding` explicitamente.
Fixado por `$::test_a_half_cent_tie_rounds_up_not_to_even`, com controle positivo
mostrando que half-even de fato difere.

**3. O critério 9 era um `if`, e o índice existente não o cobria.** Detalhado em D42 e no
"o que a Fase 6 ganhou de estrutural" das decisões.

## Limitações aceitas, com o teste que as fixa

- **Não existe estorno de excedente faturado.** D42, com o roteiro do que exigiria.
- **`service_ledger_amount_only_on_overage_billed` é unidirecional.** Ela garante que só
  `OVERAGE_BILLED` carrega valor, mas **não** que todo `OVERAGE_BILLED` carrega valor.
  Linhas escritas pelas Fases 4 e 5 são história real de antes de existirem preços, e não
  há valor honesto com que back-fillá-las — inventar valor de auditoria é o que as
  convenções proíbem. O invariante forte vive em `_bill_period_overage`, que recusa sem
  taxa, e em `B::test_billing_without_a_resolvable_rate_is_refused`.
- **A classificação "fora de escopo" é derivada no relatório, não snapshotada.** "O
  Cliente tinha contrato naquela competência" não decide dinheiro — o valor já está
  persistido — ela só classifica receita, então a R4 não a alcança e contratos não são
  apagados. Snapshotá-la seria a segunda verdade que a ausência de `applied_contract` em
  `ServiceLog` já recusou uma vez.
- **A tela rica do consolidado é da Fase 9.** Esta fase entrega a API e o CSV, que é o que
  o critério 11 pede.
- **Cliente avulso ver os próprios valores é critério herdado da Fase 8.** A R11 diz que o
  cliente vê valor "só se avulso", e o portal é a Fase 8 com seu próprio serializer.
  `ServiceLogClientSerializer` **não** ganhou campos monetários nesta fase.
- **`applied_billing_route` não é mais autoexplicativa sozinha.** Desde a D44 ela é a rota
  *escolhida*; quem lê um apontamento precisa de `settled_billing_route` ao lado. O
  docstring do campo diz isso.

## O footgun conhecido

Qualquer criação de `ServiceLog` **fora** de `build_batch_rows` precisa setar
`settled_billing_route`, senão o default `DEBIT_POOL` viola
`service_log_route_deviation_is_coherent` numa linha `non_billable` ou `bill_amount`.
Falha alto, com `IntegrityError` — nunca em silêncio. `ServiceLogFactory` resolve com
`SelfAttribute("applied_billing_route")`, que é o mesmo fato que a migração 0130
back-fillou para a história: antes desta fase, o aplicado *era* o escolhido.
