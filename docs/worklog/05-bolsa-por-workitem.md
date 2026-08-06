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

| Critério                                  | O que faltava                                                                                                                                                                                                     | Status                              |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Fase 4, critério 17**, terceiro destino do saldo remanescente: "convertê-lo em bolsa de horas de um chamado" | A entidade de bolsa de horas, que é desta fase. A Fase 4 entregou os outros dois destinos (transferir, expirar) auditados, e deixou este preparado: o tipo de lançamento `CONVERTED_TO_ISSUE_ALLOWANCE` já existe no livro-caixa, `end_and_create_successor` já aceita `balance_destination="issue_allowance"` e `target_issue`, e hoje devolve `ISSUE_ALLOWANCE_NOT_AVAILABLE` com HTTP 501 | **Aberto — fechar nesta fase**      |

O que esta fase precisa fazer para fechá-lo, e **fechar significa provar**:

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
