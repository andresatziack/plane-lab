# Fase 9 — Dashboards de consumo e relatórios de faturamento

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende das Fases 4, 5 e 6.

## Objetivo

Substituir os relatórios de Power BI externos que hoje concentram essas regras.
Este é o objetivo final do projeto: ter tudo em um único sistema.

## Escopo

### 1. Dashboard do Cliente com contrato

Comum a todos os dashboards de Cliente:

- **Distribuição por Tipo de Hora** — quanto do consumo foi comercial, fora do
  expediente, domingos e feriados
- **Distribuição por Tipo de Atendimento**
- **Horas não faturáveis** — total não cobrado no período, **separado por Tipo
  de Atendimento**. Nunca somar Garantia e Cortesia num único número: alta
  garantia indica problema de qualidade na entrega, alta cortesia indica desconto
  concedido — ações de gestão opostas (R5)
- Bolsas de horas ativas por chamado, com saldo de cada

Específico do Cliente com contrato:

- **Consumo do contrato no mês corrente** — horas contratadas vs. consumidas vs.
  saldo
- **Histórico mensal** — série temporal de consumo por competência, evidenciando
  meses de estouro
- **Saldo acumulado** — quanto de saldo transportado está disponível, e de quais
  competências ele vem
- **Meses com excedente** — separando os transportados como déficit dos faturados
  em R$, com o valor de cada
- Se o Cliente tem mais de um contrato, um dashboard **por contrato**, sem somar
  pools distintos

### 1b. Dashboard do Cliente avulso — em horas E em reais

Cliente sem contrato não tem pool, então os gráficos são estruturalmente
diferentes: não existe "contratado vs. consumido". Exibir, para cada período:

- **Horas consumidas** — série temporal mensal, em horas apontadas e em horas
  equivalentes
- **Valor faturado** — série temporal mensal em R$
- **Distribuição por Tipo de Hora**, com horas **e** valor em cada faixa (deixa
  visível quanto do custo vem de trabalho fora do expediente)
- **Ticket médio por chamado**, em horas e em R$
- Horas não faturáveis e o valor que **deixou de ser cobrado**, separado por
  Tipo de Atendimento — é o desconto real concedido, e precisa ser visível

Todo indicador do cliente avulso tem as duas leituras: horas e reais. Só horas
não permite discutir faturamento; só reais não permite discutir esforço.

### 1c. Visão consolidada de grupo econômico (opcional)

Se o campo Cliente pai estiver preenchido (Fase 1), oferecer ao Admin uma visão
que soma os Clientes do mesmo grupo — ex.: Marubeni e Terlogs juntas.

Restrições:

- É **apenas relatório**. Não afeta contrato, pool, débito nem faturamento
- Os contratos permanecem independentes e nunca são somados como se fossem um só
- Não expor essa visão aos usuários do portal do cliente nesta fase

### 2. Relatório de faturamento

Por Cliente e competência (mês da data do atendimento, regra R7):

- lista de apontamentos faturáveis com data, chamado, técnico, tempo, tipo de
  hora, horas equivalentes, valor
- totais consolidados
- visão pronta para conferência antes de emitir a nota
- exportação usando o pipeline existente, pela choice `issue_worklogs` já
  reservada em `db/models/exporter.py:26` — não construir export novo (ver
  `docs/worklog/OPORTUNIDADES.md`, seção A2)

### 3. Visão operacional (interna)

- Horas apontadas por técnico no período
- Horas por projeto e por chamado
- Apontamentos sem Cliente vinculado (para correção)
- Chamados com bolsa próxima do estouro
- Contratos com saldo crítico ou vencendo

### 3b. Painel de clientes que precisam de atenção

Consolida os alertas definidos na Fase 4, seção 9, em uma visão única para os
técnicos:

- **Alto consumo** — clientes com risco de estouro ou já negativos
- **Baixo consumo** — clientes com saldo acumulado excessivo, horas descartadas
  por teto, ou nenhum apontamento no mês

Os dois extremos merecem o mesmo destaque visual. O de baixo consumo é sinal de
risco de não renovação e costuma ser ignorado justamente porque não gera dor
operacional imediata — o painel existe para tornar isso visível a tempo.

Cada alerta deve levar direto ao cliente e ao período que o originou.

### 3c. Dashboard no portal do cliente

O usuário do cliente vê o dashboard de consumo do **Cliente do project ativo**
(ver Fase 8). Marcel, alternando entre `Marubeni` e `Terlogs`, vê o dashboard de
contrato de cada uma separadamente.

Restrições:

- Cliente com contrato vê horas, consumo e saldo. **Não vê valores em R$**
- Cliente avulso vê horas **e** valores, porque o valor é a própria fatura dele
- Nenhum cliente vê multiplicadores, tabela de preços, ou dados de outro Cliente

### 4. Filtros globais

Período, Cliente, projeto, técnico, tipo de hora, tipo de atendimento, incluir
ou excluir os não faturáveis. Todos os números exibidos precisam ser rastreáveis até a
lista de apontamentos que os originou — se o admin não puder clicar no total e
ver a lista, o relatório não substitui o Power BI.

### 4b. Stack de gráficos — usar o design system do Plane

**Não instalar biblioteca de gráficos nova. Não usar Recharts diretamente.**

O repositório já tem tudo (verificado — ver `docs/worklog/ACHADOS-DO-CODIGO.md`, seção 14):

- **Recharts `^2.15.1`**, declarado no catálogo do pnpm
  (`pnpm-workspace.yaml:159`), nunca com versão fixa nos `package.json`
- Encapsulado no design system **`@plane/propel`**, que já expõe wrappers prontos:
  `@plane/propel/charts/bar-chart`, `area-chart`, `line-chart`, `pie-chart`,
  `radar-chart`, `scatter-chart`, `tree-map`
  (fonte em `packages/propel/src/charts/`)
- Tipos em `packages/types/src/charts/` — `TChartData`, `TBarChartProps`,
  `TAxisChartProps`, `TChartLegend`, `TChartMargin`
- Constantes e classes de eixo em `packages/constants/src/chart.ts`
  (ex.: `AXIS_LABEL_CLASSNAME`)
- Internos compartilhados de tooltip, legenda e ticks em
  `packages/propel/src/charts/components/`

Os wrappers do propel já resolvem tooltip customizado, legenda interativa com
destaque de série, ticks customizados e `ResponsiveContainer`. Reimplementar isso
com Recharts cru produz gráficos visualmente inconsistentes com o resto do produto.

Se algum gráfico necessário não existir no propel, **adicione o wrapper ao
propel** seguindo o padrão dos existentes, em vez de usar Recharts direto na tela.

### 4c. Padrões de tela de analytics a reaproveitar

Já existe uma feature de analytics no produto. Copie os padrões dela em vez de
inventar:

- Componentes: `apps/web/core/components/analytics/` — `analytics-wrapper.tsx`,
  `analytics-section-wrapper.tsx`, `insight-card.tsx`, `total-insights.tsx`,
  `trend-piece.tsx` (indicador de tendência), `insight-table/` (tabela de
  detalhamento), `loaders.tsx`, `empty-state.tsx`, `export.ts`
- Seletor de período pronto: `analytics/select/duration.tsx`
- Rota com abas: `apps/web/app/(all)/[workspaceSlug]/(projects)/analytics/[tabId]/`
  com `page.tsx`, `layout.tsx`, `header.tsx`
- Backend: `plane/app/views/analytic/{base,advance,project_analytics}.py`,
  `plane/app/serializers/analytic.py`, `plane/app/urls/analytic.py`, e
  `plane/bgtasks/analytic_plot_export.py` como referência de exportação assíncrona

O requisito de "clicar no total e ver a lista de apontamentos" tem análogo direto
em `insight-table/` — reutilizar.

### 5. Performance

Os totais devem ser calculados por agregação no banco, não em Python iterando
apontamentos. Considerar visão materializada ou tabela de agregados por
competência se o volume exigir. Definir e documentar a estratégia.

## Critérios de aceite

1. Gráfico de consumo do mês corrente bate exatamente com a soma dos
   apontamentos do período
2. Horas não faturáveis aparecem separadas por Tipo de Atendimento e não inflam
   o consumo do contrato
3. Consumo de bolsa de work item não aparece como consumo do contrato
4. Saldo acumulado exibido bate com a soma dos saldos transportados dos períodos
   anteriores
5. Mês com excedente faturado aparece com déficit zerado e o valor faturado
   visível, e o mês seguinte aparece com as horas contratadas íntegras
6. Relatório de faturamento de cliente avulso bate com a soma dos valores dos
   apontamentos, ao centavo
7. Apontamento retroativo aparece na competência correta em todos os gráficos
8. Clicar em qualquer total leva à lista de apontamentos que o compõem
9. Exportação em CSV contém os mesmos números da tela
10. Dashboards respeitam permissões: técnico não vê valores em R$, cliente não
    acessa estes painéis

## Entregar

Desenho das agregações e da estratégia de performance primeiro. Depois
implementação, com teste comparando cada agregado contra a soma direta dos
apontamentos — se os dois divergirem, o relatório está errado.

---

## O que foi entregue

Decisões em `DECISOES.md`, **D48 a D57**, mais a **revisão da D35**. Só o que não é
óbvio a partir delas está aqui.

### As agregações

Tudo em `plane/utils/service_reports.py`, tudo `GROUP BY` no Postgres. Contagens de
query fixadas por teste, não afirmadas em docstring:

| Função                       | Queries       | Cresce com o range? |
| ---------------------------- | ------------- | ------------------- |
| `hours_series`               | 1             | não                 |
| `distribution`               | 1             | não                 |
| `headline_totals`            | 1             | não                 |
| `revenue_series`             | 4             | **não**             |
| `contract_balance_statement` | 2 (era 1 + N) | não                 |

As 4 do `revenue_series` são: um scan dos apontamentos agrupado pelas entradas da
classificação, uma leitura de contratos para os conjuntos de cobertura por competência,
e uma por origem de excedente. **A classificação roda sobre as linhas agrupadas**, cuja
cardinalidade é meses × clientes × um punhado — é isso que permite ter a regra de
origem em um lugar só e ainda assim não iterar apontamento nenhum.

### A superfície nova

```
GET workspaces/<slug>/service-reports/consumption/   ADMIN, MEMBER
GET workspaces/<slug>/service-reports/operational/   ADMIN, MEMBER
GET workspaces/<slug>/service-reports/attention/     ADMIN, MEMBER
GET workspaces/<slug>/service-reports/billing/       ADMIN
GET workspaces/<slug>/service-reports/logs/          ADMIN, MEMBER   <- o único drill-down
POST workspaces/<slug>/service-issue-allowances/<pk>/dismiss-alert/  ADMIN, MEMBER
```

`consumption/` devolve `shape: "contract" | "standalone"` decidido **pelo dado**, e
troca o `competence_basis` sozinho — um frontend não consegue errar a D48.

Rota web em `/:workspaceSlug/service-reports/`, quatro abas, entrando pela de atenção.

### Os critérios, e onde cada um está fixado

| #     | Onde                                                                                             |
| ----- | ------------------------------------------------------------------------------------------------ |
| 1, 8  | `TestEveryBucketAgreesWithItsOwnDescriptor` — property test sobre o payload, nos 3 papéis        |
| 2     | `TestCriterion2NonBillableIsNeverOneNumber` + `non_billable_breakdown`                           |
| 3     | `TestCriterion3AllowanceAndContractStaySeparate`                                                 |
| 4     | `contract_balance_statement`, com as parcelas por competência de origem                          |
| 5     | herdado da Fase 6 (`close_period`), exibido na aba de consumo                                    |
| 6, 13 | `Sum` da coluna `amount` persistida em todo lugar; sabotagem de recomputação quebra 37 testes    |
| 7     | `TestD47TheTwoBasesDisagreeAndBothAreRight` e `TestD47OverHttp`                                  |
| 9     | `TestCriterion9TheCsvAndTheScreenComeFromOneDescriptor`                                          |
| 10    | `TestD50TheRoleProjectionIsAppliedAtTheAggregationBoundary` + `TestTheMemberShapeCarriesNoMoney` |

### O que a §4b pediu e não foi feito, com o motivo

O briefing autorizava acrescentar um wrapper ao propel se faltasse um tipo de gráfico.
**Nenhum foi acrescentado**, e um foi recusado de propósito: **eixo Y duplo** (horas à
esquerda, R$ à direita). Duas unidades num eixo é um gráfico que mente. A §1b pede as
duas leituras e as recebe como gráficos irmãos com escala própria mais uma tabela
carregando horas **e** valor na mesma linha — que é onde comparar as duas é honesto.

Se depois se decidir que o eixo duplo é necessário mesmo assim, o lugar é
`packages/propel/src/charts/`, não um Recharts direto num componente.

### As três dívidas do briefing

| Dívida                                                           | Estado                                                                     |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Alerta de bolsa não dispensável (`05-bolsa-por-workitem.md:191`) | **paga.** Migração 0132, D54                                               |
| Histórico de créditos da bolsa sem tela                          | **paga.** Painel expansível na aba de consumo; a API já devolvia `credits` |
| D35, carência de 30 dias                                         | **revisada, não implementada como varredura.** Ver a revisão da D35        |

### O §3c não foi implementado, e isso é deliberado

O dashboard do portal depende da Fase 8. A **projeção** GUEST foi construída e testada
no domínio (`ReportViewer.guest()`); a **rota** é critério herdado, registrado como
critério 22 em `08-portal-do-cliente.md`. Uma segunda projeção seria um segundo lugar
onde a R11 pode estar errada.

### Duas coisas que os testes acharam e a revisão não

1. **O slice "sem cliente" de uma distribuição por cliente mentia.** Não tinha como se
   descrever no descritor e caía no filtro não-estreitado: reportava as próprias horas
   apontando para todas as linhas da janela. O **número** estava certo — era o
   descritor. Nenhuma asserção sobre valores esperados pegaria isso.
2. **Os buckets de excedente ofereciam um descritor mais uma flag** dizendo para não
   usá-lo. Ver D56.

Ambas vieram da property test, e são o argumento para tê-la escrito como propriedade em
vez de exemplos.

### Limitação nomeada

**Bolsa de chamado CANCELADO nunca fica pendente de encerramento.** O Plane só marca
`completed_at` para o grupo `completed`. Tratar cancelamento como fechamento para fins
de faturamento é decisão comercial que a D35 não toma, e inventar uma segunda definição
de "fechado" na camada de relatórios é a divergência que a D49 evita. Tem teste de
caracterização, para que mudar seja deliberado.
