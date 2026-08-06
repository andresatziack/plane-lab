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

## Critérios de aceite

1. Admin credita 40h em um chamado e o saldo aparece no work item
2. Técnico aponta 5h nesse chamado: bolsa cai para 35h e o pool mensal do
   contrato do cliente permanece **intacto**
3. O apontamento registra que a origem debitada foi a bolsa do work item
4. Chamado sem bolsa continua debitando do contrato normalmente
5. Crédito adicional de 10h leva o saldo de 35h para 45h, com histórico dos dois
   créditos
6. Excluir apontamento devolve as horas à bolsa, não ao contrato
7. Estourar a bolsa não debita do contrato em nenhuma circunstância
8. Apontamento com multiplicador 2.0 consome o dobro da bolsa
9. Apontamento de rota `NON_BILLABLE` não consome bolsa

## Entregar

Modelo de dados primeiro, com atenção especial a como a precedência de origem é
resolvida e persistida. Testes cobrindo o isolamento entre bolsa e contrato.
