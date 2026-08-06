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

O consolidado deve separar claramente três origens de receita:
- apontamentos avulsos
- apontamentos fora de escopo de clientes com contrato
- excedente de contrato faturado (seção 6)

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
