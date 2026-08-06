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

Ao salvar um apontamento cuja rota de faturamento é `DEBITA_POOL`:
1. Resolver o Cliente a partir do **project** do work item (contexto mestre,
   seção 2b)
2. Resolver o contrato: contrato fixado no project → contrato padrão do Cliente,
   vigente na **data do atendimento** (regra R7 e seção 1b)
3. Resolver o período de competência do mês daquela data
4. Debitar `horas_equivalentes` do período
5. Persistir no apontamento a referência do período debitado e o multiplicador
   aplicado (regra R4)

Apontamento com Garantia não debita (regra R5).
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

1. Contrato de 30h/mês com vigência de 12 meses gera períodos de competência
   corretos
2. Apontamento de `2h` com multiplicador 1.0 reduz o saldo do mês de 30h para
   28h
3. Apontamento de `2h` com multiplicador 2.0 reduz o saldo em 4h, não 2h
4. Apontamento com Garantia não altera o saldo
5. Cliente consome 22h de 30h em janeiro: fevereiro abre com 38h concedidas
6. Com validade de saldo definida em 2 meses, o saldo de janeiro não utilizado
   expira no momento correto e não infla os meses seguintes
7. Cliente com 2h de saldo recebe apontamento de 5h: o apontamento é salvo, o
   saldo fica em −3h, e o alerta aparece
8. Fechando o período com déficit e escolhendo transportar, o mês seguinte abre
   com 27h em vez de 30h
9. Fechando o período com déficit e escolhendo faturar o excedente, o mês
   seguinte abre com 30h íntegras e o lançamento de excedente de 3h é registrado
10. Apontamento com data de atendimento no mês anterior debita o período do mês
    anterior, não o corrente
11. Excluir um apontamento devolve exatamente as horas ao período correto
12. Editar o tempo de um apontamento de `2h` para `3h` deixa o saldo consistente,
    sem débito duplicado
13. Período fechado rejeita novo apontamento com mensagem clara
14. Dois apontamentos salvos simultaneamente no mesmo período não corrompem o
    saldo
15. Com teto de acúmulo de 2× e contrato de 30h/mês, o saldo transportado nunca
    passa de 60h, e o excesso descartado fica registrado
16. Renovar o contrato mantendo-o (caminho a) transporta o saldo acumulado para o
    novo período
17. Encerrar e criar novo contrato (caminho c) permite transferir o saldo,
    expirá-lo, ou convertê-lo em bolsa de horas de um chamado — com auditoria em
    todos os casos
18. Cliente com consumo de 90% do pool no dia 10 aparece no painel de alto
    consumo
19. Cliente sem nenhum apontamento no mês aparece no painel de baixo consumo
20. Saldo nunca desaparece sem registro de auditoria em nenhum dos fluxos de
    renovação, expiração ou teto
21. Cenário de referência: chamado no project `Marubeni` debita o contrato de
    10h/mês da Marubeni; chamado no project `Terlogs` debita o de 30h/mês da
    Terlogs. Os dois pools são independentes e a relação societária entre as
    empresas não os mistura
22. Cliente com dois contratos ativos e dois projects debita cada project no
    contrato fixado nele
23. Cliente com dois contratos ativos e nenhum fixado no project debita o contrato
    padrão
24. Resolução de contrato ambígua ou vazia falha com mensagem explícita, sem
    escolher contrato arbitrariamente

## Entregar

Modelo de dados e desenho do motor de débito primeiro, incluindo a estratégia
de concorrência, de estorno, e de rastreamento das parcelas de saldo
transportado. Testes de acúmulo, estorno, excedente e concorrência são
obrigatórios — é aqui que o dinheiro vaza.
