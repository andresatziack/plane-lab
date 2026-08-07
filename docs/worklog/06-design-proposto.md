# Fase 6 — modelo de dados e desenho proposto

> **Status: aprovado com quatro correções, e implementado.** Este documento é o desenho
> como foi **proposto**, mantido como registro do que se decidiu e por quê. A revisão
> mudou quatro coisas, e o texto abaixo **não** foi reescrito para esconder isso — as
> decisões finais estão em `DECISOES.md`, **D37 a D44**, e são elas que valem.
>
> | Onde | O que a revisão mudou |
> |---|---|
> | §B (excedente de bolsa) | Eu propus usar o valor base do Cliente. **Recusado**: a bolsa ganhou valor/hora próprio, nullable e rastreado. O argumento que eu mesmo usei para o `overage_hour_rate` do contrato — o momento do cadastro é o momento em que o valor é conhecido — vale igual para a bolsa, que é venda de projeto negociada à parte. **D39** |
> | §C (data do excedente) | Eu propus `period.ends_on`. Corrigido para `period.starts_on`, **e** a folha de preço passou a ser restrita ao dia 1º em DDL, o que torna a pergunta irrelevante por construção. **D40** |
> | §3.4 / §E (project sem Cliente) | Eu chamei de `NO_SERVICE_CLIENT_FOR_PROJECT` e listei como pendência. Renomeado para `INTERNAL_PROJECT_NO_CLIENT` e **excluído da receita**: não é falha, é trabalho interno, e ruído em painel financeiro custa a credibilidade do painel. **D43** |
> | §A (vigência) | Confirmado, mais um caso que eu não havia coberto: `worked_on` **anterior à primeira folha**. Não bloqueia; grava `NO_PRICE_SHEET_IN_FORCE`. **D37** |
>
> Baseline medido antes de qualquer coisa: **1913 passando, 0 falhando** (`RECREATE_DB=1`).
> Ao fim da fase: **2042 passando, 0 falhando**.

---

## 1. A decisão central: a vigência de preços

### 1.1 Duas tabelas, e a segunda é filha da vigência — não do Cliente

```
ServiceClientPrice            (a folha de preço: uma linha por vigência)
  └── ServiceClientHourTypeRate   (as exceções daquela folha)
```

**`ServiceClientPrice`** é a folha de preço de um Cliente a partir de uma data. Carrega
**um número**: `base_hour_rate`. É isso que faz a §1 ("configurar um cliente novo deve
exigir mudar um único número") ser verdade por construção.

**`ServiceClientHourTypeRate`** é o override absoluto de um Tipo de Hora, e é **filha da
folha, não do Cliente**. Essa é a parte do desenho que quero justificar, porque a
alternativa é a óbvia e está errada:

Se o override pendurasse no Cliente, um reajuste criaria uma folha nova e o override
**sobreviveria intacto** — e override é valor absoluto, logo ele silenciosamente **deixaria
de ser reajustado**. Pior: não haveria como *remover* um override num reajuste, nem como
tê-lo em 2026 e não em 2027. Sendo filha da folha, **cada vigência é uma folha de preço
completa e autossuficiente**: base + suas exceções. Reajustar é copiar a folha para a
frente e editar. Ler um preço é uma busca da folha vigente e as exceções dela — sem
nenhuma regra de mesclagem entre vigências para errar.

### 1.2 Não existe `ends_on`. A vigência é resolvida por "maior `starts_on` que não passa da data"

```python
ServiceClientPrice.objects.filter(
    service_client_id=..., starts_on__lte=<data>
).order_by("-starts_on").first()
```

Par `(starts_on, ends_on)` são duas verdades que podem discordar, e discordam de duas
formas: **buraco** (nenhuma folha cobre a data) e **sobreposição** (duas cobrem). Com a
regra do maior `starts_on`, buraco e sobreposição ficam **irrepresentáveis** — sobreposição
pelo índice único `(service_client, starts_on)`, buraco porque a folha anterior vale até a
seguinte começar. Mesmo raciocínio da D3 da Fase 4 (saldo é propriedade, não coluna): não
guardar o que pode ser derivado sem ambiguidade.

O único caso aberto é **data anterior à primeira folha**, que é ausência de configuração e
está tratado em §3.4.

### 1.3 A vigência é selecionada por `worked_on`, não pela data de digitação

Aqui a §2 do briefing e a R7 se tensionam. A §2 diz "o apontamento persiste o valor/hora
vigente **no momento do registro**"; a R7 e o critério 12 dizem que a competência é a **data
do atendimento**.

Proponho **`worked_on`**, e o argumento é a coerência da fatura: um apontamento de março
digitado em maio, depois de um reajuste em abril, precisa entrar na **fatura de março**
(critério 12) — com o preço de março. Selecionar pela data de digitação colocaria um preço
de maio numa fatura de março, e faria dois apontamentos do mesmo dia de março digitados com
um mês de diferença terem preços diferentes. A fatura de março deixaria de ser reproduzível.

O critério 6 ("reajustar a base não altera apontamento já registrado") é satisfeito pelo
snapshot da R4 **de qualquer forma** — ele não decide esta questão. É a vigência por
`worked_on` que resolve o retroativo, e é por isso que a tabela de vigência existe e o
snapshot sozinho não bastaria. **Confirmar — muda fatura (pergunta A).**

### 1.4 Campos

**`ServiceClientPrice(ChangeTrackerMixin, WorkspaceBaseModel)`** — `db_table = "service_client_prices"`

| Campo | Tipo | Notas |
|---|---|---|
| `service_client` | FK `db.ServiceClient`, **`DO_NOTHING`**, `related_name="prices"` | regra da casa: FK de configuração usada por apontamento nunca é CASCADE |
| `starts_on` | `DateField()` | início da vigência |
| `base_hour_rate` | `DecimalField(12, 2)`, `MinValueValidator(Decimal("0.01"))` | escala monetária da §4b |
| `notes` | `TextField(blank=True)` | |

- `TRACKED_FIELDS = ["starts_on", "base_hour_rate"]`, `CONFIG_ENTITY_NAME = ServiceConfigEntity.CLIENT_PRICE`
- **Nenhum campo rastreado é nullable** → nenhum encosta em `CONFIG_VALUE_UNSET`. Ver §1.5.
- `config_summary()` → `"Vigência de 01/01/2026: R$ 200,00/h"`

Constraints:
```python
unique_together = [["service_client", "starts_on", "deleted_at"]]
UniqueConstraint(["service_client", "starts_on"], condition=Q(deleted_at__isnull=True),
                 name="service_client_price_unique_start_when_deleted_at_null")
CheckConstraint(condition=Q(base_hour_rate__gt=0),
                name="service_client_price_base_rate_is_positive")
indexes = [Index(["service_client", "-starts_on"], name="svc_client_price_vigency_idx")]
```

`base_hour_rate > 0` **não é higiene, é a D20**: valor zero seria um segundo mecanismo de
"não cobrar", e existe exatamente um (a rota `NON_BILLABLE`). É o mesmo argumento pelo qual
`ServiceHourType.multiplier` recusa zero.

**`ServiceClientHourTypeRate(ChangeTrackerMixin, WorkspaceBaseModel)`** — `db_table = "service_client_hour_type_rates"`

| Campo | Tipo | Notas |
|---|---|---|
| `price` | FK `db.ServiceClientPrice`, **`DO_NOTHING`**, `related_name="hour_type_rates"` | |
| `hour_type` | FK `db.ServiceHourType`, **`DO_NOTHING`**, `related_name="client_rates"` | |
| `absolute_rate` | `DecimalField(12, 2)`, `MinValueValidator(Decimal("0.01"))` | **não nullable** |

- `TRACKED_FIELDS = ["absolute_rate"]` — **`hour_type` fica fora de propósito**: o
  `ChangeTrackerMixin` faz `getattr` no `__init__`, e essa FK `DO_NOTHING` pode apontar para
  um Tipo de Hora soft-deletado, o que levantaria `DoesNotExist` só ao carregar a linha. É
  exatamente a armadilha que a `ServiceClassificationWindow` já documenta.
- `config_summary()` lê `ServiceHourType.all_objects`, para renderizar tipo inativo.

```python
unique_together = [["price", "hour_type", "deleted_at"]]
UniqueConstraint(["price", "hour_type"], condition=Q(deleted_at__isnull=True),
                 name="svc_client_hour_rate_unique_type_when_deleted_at_null")
CheckConstraint(condition=Q(absolute_rate__gt=0),
                name="svc_client_hour_rate_is_positive")
```

### 1.5 Isto responde à advertência da D22, em vez de pagar o preço dela

A D22 avisou que rastrear uma coluna **nullable** cobra da trilha de auditoria, porque
`svc_cfg_activity_shape_matches_verb` exige `old_value`/`new_value` não nulos num `updated`,
e nomeou "o preço sobrescrito opcional da Fase 6" como o caso concreto.

**O override aqui é uma linha, não uma coluna nullable.** Cadastrar um override é `CREATED`,
remover é `DELETED` — os dois verbos que a D22 adicionou justamente para eventos em que
nenhum campo de linha existente muda. `absolute_rate` é não nullable, então o caminho
`updated` também é limpo. O sentinel `CONFIG_VALUE_UNSET` continua existindo e continua
sendo usado por `ServiceContract.overage_hour_rate`, que não muda de forma.

---

## 2. O dinheiro no apontamento: seis colunas, todas obrigação de auditoria

A R4 exige snapshot de tudo que decide dinheiro, e o `applied_multiplier` /
`applied_billing_route` são o molde. Nenhuma das seis abaixo é conveniência — testei cada
uma contra "consigo derivar?".

| Coluna | Tipo | Por que é irredutível |
|---|---|---|
| `settled_billing_route` | `CharField(20, choices=BillingRoute, default=DEBIT_POOL)` | a rota **aplicada** (D33) |
| `route_deviation_reason` | `CharField(32, choices, null=True)` | o **motivo** do desvio (D33) |
| `applied_hour_rate` | `DecimalField(12, 2, null=True)` | snapshot R4 do valor/hora — critério 6 |
| `applied_rate_basis` | `CharField(20, choices, null=True)` | `BASE_MULTIPLIER` \| `ABSOLUTE_OVERRIDE`. Ver §3.2 |
| `amount` | `DecimalField(12, 2, default=Decimal("0.00"))` | o valor. Critério 13 exige somar valores persistidos |
| `pricing_failure_reason` | `CharField(32, choices, null=True)` | por que um apontamento que devia ter valor tem zero |

### 2.1 `applied_billing_route` não é tocada

A D33 pede que **o escolhido e o aplicado** sejam recuperáveis. A tentação é redefinir
`applied_billing_route` para "a rota aplicada" — e isso perderia o escolhido, que é
exatamente a informação que permite ao consolidado distinguir "avulso porque o cliente é
avulso" de "avulso porque o contrato venceu".

Então: `applied_billing_route` mantém o significado (rota do Tipo de Atendimento escolhido,
snapshotada — só o docstring é corrigido para dizer "escolhida"), e `settled_billing_route`
é nova. Três fatos, três colunas, e uma constraint que os mantém coerentes. Nenhuma das três
check constraints existentes de `ServiceLog` muda de comportamento.

**Alternativa recusada:** derivar a rota aplicada de `route_deviation_reason` (não nulo ⟹
aplicada = `BILL_AMOUNT`). Funciona e economiza uma coluna, mas põe o `GROUP BY` do
consolidado em cima de um `CASE`, e a D33 pediu os dois explicitamente. Uma coluna indexável
custa menos que uma regra replicada em cada relatório.

### 2.2 A constraint que importa mais

```python
# Um apontamento debita um pool OU é faturado em R$. Nunca os dois.
CheckConstraint(
    condition=(
        ~Q(settled_billing_route=BillingRoute.BILL_AMOUNT)
        | Q(debited_period__isnull=True, debited_allowance__isnull=True)
    ),
    name="service_log_billed_debits_no_pool",
)
```

Este é o análogo monetário do que a **D29** conseguiu para horas: cobrar em R$ *e* debitar
o pool pelo mesmo apontamento passa a ser **recusado pelo banco**, não pela ordem de um
`if`. Espelha `service_log_debits_at_most_one_origin`, que já existe.

As demais:

```python
# R5 / critério 4: valor zero sai da rota, não de um `if`.
CheckConstraint(
    condition=(
        ~Q(applied_billing_route=BillingRoute.NON_BILLABLE)
        | Q(amount=0, applied_hour_rate__isnull=True, applied_rate_basis__isnull=True,
            settled_billing_route=BillingRoute.NON_BILLABLE, route_deviation_reason__isnull=True)
    ),
    name="service_log_non_billable_carries_no_amount",
)

# O único desvio legal é o da D33: escolheu Contrato, saiu avulso.
CheckConstraint(
    condition=(
        Q(route_deviation_reason__isnull=True,
          settled_billing_route=F("applied_billing_route"))
        | Q(route_deviation_reason__isnull=False,
            applied_billing_route=BillingRoute.DEBIT_POOL,
            settled_billing_route=BillingRoute.BILL_AMOUNT)
    ),
    name="service_log_route_deviation_is_coherent",
)

# Taxa, base e valor andam juntos, e sem taxa não há valor.
CheckConstraint(
    condition=(
        Q(applied_hour_rate__isnull=True, applied_rate_basis__isnull=True, amount=0)
        | Q(applied_hour_rate__isnull=False, applied_rate_basis__isnull=False, amount__gt=0)
    ),
    name="service_log_amount_requires_a_rate",
)

# Pool pago não carrega dinheiro.
CheckConstraint(
    condition=(
        ~Q(settled_billing_route=BillingRoute.DEBIT_POOL)
        | Q(amount=0, applied_hour_rate__isnull=True)
    ),
    name="service_log_pool_route_carries_no_amount",
)

# Falha de precificação só existe onde havia dinheiro a fazer.
CheckConstraint(
    condition=(
        Q(pricing_failure_reason__isnull=True)
        | Q(settled_billing_route=BillingRoute.BILL_AMOUNT,
            applied_hour_rate__isnull=True, amount=0)
    ),
    name="service_log_pricing_failure_means_no_amount",
)
```

Note que `amount__gt=0` só é seguro porque `base_hour_rate > 0` e `absolute_rate > 0` são
constraints (§1.4) e `equivalent_hours > 0` sempre. É a mesma cadeia da D20: um mecanismo
para "não cobrar".

Índice novo: `Index(["workspace", "settled_billing_route", "worked_on"],
name="svc_log_ws_settled_worked_idx")` — 29 caracteres, dentro do limite de 30 do
`models.E034`.

---

## 3. O cálculo

### 3.1 Aritmética monetária pura vai num módulo novo sem Django

`plane/utils/service_log_time.py` não importa Django de propósito, e é onde vive a
aritmética pura de hora. A aritmética de dinheiro tem a mesma propriedade mas não é hora, e
o nome do módulo passaria a mentir. Então: **`plane/utils/service_money.py`**, mesma regra
(nenhum import de Django, nem de models).

```python
MONEY_SCALE = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")

def quantize_money(value):
    return Decimal(value).quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)
```

**`ROUND_HALF_UP` explícito.** A §4b manda, e vale registrar que `quantize_hours` **não**
passa `rounding=` — ele confia no contexto (`ROUND_HALF_EVEN` por padrão), o que é inofensivo
lá porque o valor é exato por construção. Aqui não é, então é explícito.

### 3.2 Duas funções, porque são duas fórmulas com **grandezas de hora diferentes**

```python
def amount_from_base_rate(*, equivalent_hours, base_hour_rate):
    return quantize_money(Decimal(equivalent_hours) * Decimal(base_hour_rate))

def amount_from_absolute_rate(*, logged_hours, absolute_rate):
    return quantize_money(Decimal(logged_hours) * Decimal(absolute_rate))
```

Isto é o ponto mais fácil de errar da fase. A §3 do briefing usa **`horas_equivalentes`** com
a base (porque o multiplicador ainda não entrou no preço) e **`horas_apontadas`** com o
override (porque o valor absoluto **já embute** o multiplicador — ele *substitui*
`base × multiplicador`).

Critério 5, na prática: override de R$ 350,00 em Domingos e feriados (2.0), 1h num domingo.
`logged = 1.0`, `equivalent = 2.0`. Caminho certo: `1.0 × 350 = R$ 350,00`. Usando
`equivalent` por engano: `2.0 × 350 = R$ 700,00` — **cobrança em dobro, a mesma doença do
critério 8, num lugar onde o briefing não avisou**.

Por isso são **duas funções com parâmetros nomeados diferentes e keyword-only**, não uma
função com um flag. Passar a grandeza errada exige escrever o nome errado.

Consequência: **não posso colapsar `applied_hour_rate` em "a taxa por hora apontada"**
(que daria `base × multiplicador` e uma fórmula só). Além de perder a informação de se
houve override, introduziria um **segundo arredondamento** (`base × multiplicador`
quantizado, depois multiplicado), e a §4b exige **um arredondamento por apontamento**.
`applied_rate_basis` existe para que o snapshot diga qual fórmula rodou.

### 3.3 Onde a precificação entra no fluxo

O cálculo das grandezas de hora tem um único lugar de derivação
(`service_log.compute_hour_quantities`), e o snapshot da R4 acontece em `build_batch_rows`.
O dinheiro entra nos mesmos dois pontos, e em nenhum outro:

```
serializer
  └── _build_rows
        ├── build_segments        (classificação, Fase 2b)
        ├── resolve_segment_hour_type
        └── compute_hour_quantities            ← horas
  └── create/replace_service_log_batch          (persiste)
  └── settle_service_log_batch                  ← NOVO orquestrador
        ├── resolve_settlement(log)              (decide pool | avulso | não faturável)
        ├── apply_debit(log)                     quando pool   — Fase 4/5, comportamento intacto
        └── price_service_log(log)               quando avulso  — Fase 6
```

O preço **não** pode ser calculado em `build_batch_rows` junto do multiplicador, porque
depende da resolução do contrato (D33: o pool pode não resolver e virar avulso), e a
resolução precisa da linha persistida. Então a precificação mora no mesmo passo do débito,
que é o passo que já decide para onde a hora vai.

### 3.4 Os motivos, e o controle positivo

A doutrina "todo teste de ausência afirma o código do motivo" tem um alvo concreto aqui:
**valor R$ 0,00 é verdade por cinco motivos diferentes**, e são bugs diferentes.

| Situação | `settled_billing_route` | `amount` | Motivo registrado em |
|---|---|---|---|
| Garantia / Cortesia | `NON_BILLABLE` | 0 | — (a rota diz) |
| Pool pagou (período ou bolsa) | `DEBIT_POOL` | 0 | `debited_period` / `debited_allowance` |
| Project sem Cliente | `BILL_AMOUNT` | 0 | `pricing_failure_reason = NO_SERVICE_CLIENT_FOR_PROJECT` |
| Cliente sem nenhuma folha de preço | `BILL_AMOUNT` | 0 | `pricing_failure_reason = NO_PRICE_SHEET_FOR_CLIENT` |
| Folhas existem, todas começam depois de `worked_on` | `BILL_AMOUNT` | 0 | `pricing_failure_reason = NO_PRICE_IN_EFFECT_ON_DATE` |

As duas últimas são **bugs opostos** — "nunca cadastraram" e "cadastraram tarde demais" — e
por isso são códigos distintos. Nenhuma delas bloqueia o apontamento: é trabalho já
executado, e a D27, a D9 e a D21 dizem a mesma coisa três vezes. Elas não são levantadas
como exceção, são **gravadas**, então `NON_BLOCKING_RESOLUTION_FAILURES` não muda.

`route_deviation_reason` (D33), separado e ortogonal:

| Situação | Código |
|---|---|
| Cliente sem contrato | `NO_CONTRACT_FOR_CLIENT` |
| Contrato suspenso | `CONTRACT_SUSPENDED` |
| Contrato com `status = ENDED` | `CONTRACT_ENDED` |
| `worked_on` depois de `ends_on`, sem renovação | `CONTRACT_EXPIRED` |

São **duas colunas porque os dois motivos coexistem**: contrato vencido (desvio) *e* sem
folha de preço (falha) é um estado real, e o operador precisa das duas metades. Uma coluna
só perderia uma.

`AMBIGUOUS_CONTRACT_RESOLUTION` continua **bloqueando** — a D27 não é tocada, e a D33 não
pediu que fosse: ali existe um pool errado a debitar.

---

## 4. O excedente: o livro-caixa existente ganha dinheiro, e nenhum journal novo nasce

O handoff manda ler a D29 antes de criar um segundo journal, e o resultado é claro:

Dos três eventos monetários da fase, **dois já são linhas do livro-caixa**. Precificar um
apontamento não é movimento de pool (o journal dele é a própria tabela `service_logs`); e o
excedente de contrato e o de bolsa **já gravam uma linha `OVERAGE_BILLED`** hoje, carregando
só as horas. Criar uma tabela paralela seria gravar o R$ de um evento em outro lugar que o
evento.

### 4.1 E a reutilização *paga*, como pagou na Fase 5

O critério 9 ("faturar excedente duas vezes no mesmo período é rejeitado") é hoje garantido
por um `if`: `close_period` levanta `PERIOD_ALREADY_CLOSED`. O índice único existente é
`(service_log, entry_type)` **condicionado a `service_log IS NOT NULL`**, e
`OVERAGE_BILLED` tem `service_log` nulo — então **duas linhas `OVERAGE_BILLED` para o mesmo
período são representáveis hoje**.

Com o dinheiro morando na linha, isso deixa de ser aceitável, e a correção é a mesma que a
Fase 5 aplicou ao critério 9 dela: **virar DDL.**

```python
UniqueConstraint(fields=["period"],
                 condition=Q(entry_type=OVERAGE_BILLED, period__isnull=False),
                 name="service_ledger_unique_overage_billed_per_period")
UniqueConstraint(fields=["allowance"],
                 condition=Q(entry_type=OVERAGE_BILLED, allowance__isnull=False),
                 name="service_ledger_unique_overage_billed_per_allowance")
```

Critério 9 passa a ser recusa do banco. É a sabotagem que o handoff listou, e ela vai ficar
vermelha por `UniqueViolation`, não por `AssertionError` num `if`.

### 4.2 Colunas novas em `ServiceHourLedgerEntry`

| Campo | Tipo |
|---|---|
| `amount` | `DecimalField(12, 2, null=True, blank=True)` |
| `applied_hour_rate` | `DecimalField(12, 2, null=True, blank=True)` |

```python
CheckConstraint(
    condition=(
        Q(amount__isnull=True, applied_hour_rate__isnull=True)
        | Q(amount__isnull=False, applied_hour_rate__isnull=False,
            amount__gte=0, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED)
    ),
    name="service_ledger_amount_only_on_overage_billed",
)
```

`write_ledger_entry` ganha `amount=None` e `applied_hour_rate=None` keyword-only, e
quantiza o `amount` ali — ele continua sendo **o único lugar que cria linha**.

### 4.3 Critério 8 é resolvido pela assinatura, não pelo teste

```python
def overage_amount(*, overage_hours, overage_hour_rate):
    return quantize_money(Decimal(overage_hours) * Decimal(overage_hour_rate))
```

`period.overage_hours` e `allowance.overage_hours` **já são equivalentes** — o multiplicador
já entrou. A função **não tem parâmetro de multiplicador**, então aplicá-lo de novo exige
alterar a assinatura. O teste de sabotagem continua existindo, mas a primeira linha de
defesa é não haver por onde passar o valor.

Critério 7 confere: déficit 3h, `overage_hour_rate = 250` → `3 × 250 = R$ 750,00`.

### 4.4 Resolução do valor/hora de excedente, e o único lugar onde eu **bloqueio**

`ServiceContract.overage_hour_rate` → fallback para `base_hour_rate` da folha vigente do
Cliente. Se **nenhum dos dois** resolve, proponho **recusar** o fechamento com
`settlement = BILLED`, com o código `OVERAGE_RATE_NOT_CONFIGURED`.

Isso parece contrariar a D27/D9/D30, e não contraria: aquelas protegem **trabalho já
executado por um técnico**, que não pode ser perdido por pendência comercial. Fechar período
faturando excedente é **ato administrativo deliberado, num momento de escolha** — e o admin
tem a outra saída pronta e legítima (`CARRIED`). Faturar R$ 0,00 aqui **zeraria um déficit
real**, destruindo a dívida em silêncio; recusar devolve a decisão a quem pode tomá-la. É o
mesmo formato da D30 (bolsa encerrada bloqueia porque é trava administrativa, não ausência).
**Confirmar (pergunta D).**

### 4.5 Limitação aceita, declarada: não existe estorno de `OVERAGE_BILLED`

O critério 6 da §6 do briefing pede que "estornar o faturamento de excedente restaure o
déficit e o transporte de forma consistente". **Hoje não existe estorno de `OVERAGE_BILLED`
nem reabertura de período** — verifiquei: nada no repositório escreve linha reversora desse
tipo, e não há `reopen_period`. A Fase 4 fechou o critério sem esse caminho.

Duas saídas, e preciso da sua:
- **(i)** implementar `reverse_overage_billing(period)` nesta fase: linha reversora nova
  (exigindo um `entry_type` novo, que entra numa das três listas de sinal), reabertura do
  período, restauração do `carried_hours` do período seguinte. É trabalho real e mexe no
  motor da Fase 4;
- **(ii)** deixar como **dívida nomeada** para a fase que fizer travamento/reabertura de
  período, e fixar o estado atual com teste de caracterização.

Inclino-me por **(ii)**, porque reabrir período é um mecanismo que a Fase 4 deliberadamente
não construiu e cuja ausência hoje é coerente (período fechado recusa débito, estorno,
fechamento e edição de cota). Construí-lo dentro da fase do R$ é invasão de escopo do
mesmo tipo que a D35 recusou. **Confirmar (pergunta F).**

Limitação relacionada e menor: linhas `OVERAGE_BILLED` **já existentes** são história
pré-precificação e não têm valor. Por isso a constraint de §4.2 é unidirecional
(`amount` não nulo ⟹ `OVERAGE_BILLED`), e não "`OVERAGE_BILLED` ⟹ `amount` não nulo":
back-fill honesto é impossível e inventar valor de auditoria é o que as convenções proíbem.
O invariante forte fica no domínio e num teste.

---

## 5. D31 — nenhum período fora da vigência

Ponto único de mudança: **`resolve_period`**, imediatamente depois do early-return de leitura.

```python
if existing is not None or not materialize:
    return existing

# D31: a vigência começa no dia 1º e não há pro-rata. Apontamento com data anterior ao
# início debita o PRIMEIRO período do contrato -- 30h/mês por 12 meses são 360h, e
# antecipar horas não altera o total. Materializar o mês de fevereiro num contrato que
# começa em março criaria um 13º período com cota própria: 390h em vez de 360h.
competence = max(worked_on, contract.starts_on)
```

`worked_on` **posterior** a `ends_on` nunca chega aqui: a D33 desvia para avulso antes.

Clampar dentro de `resolve_period` — e não no chamador — mantém leitura e escrita
concordando: `issue_pool_snapshot`, que chama com `materialize=False`, passa a mostrar o
primeiro período para a mesma data em que o débito o usaria.

**Código que morre e vai ser removido, não deixado mentindo:** o ramo de `_period_bounds`
que devolve as fronteiras naturais para um mês que a vigência não toca. O docstring dele diz
que existe "para um apontamento retroativo ou tardio, que a D9 permite" — a D31 acabou de
tornar essa afirmação falsa, e `materialize_contract_periods` só caminha dentro da vigência.
As convenções mandam corrigir o texto antigo em vez de conviver com ele.

**O aviso `CONTRACT_OUT_OF_VIGENCY` se parte em dois**, porque agora as duas metades têm
destinos opostos: vigência **futura** clampa e segue avisando (`CONTRACT_OUT_OF_VIGENCY`);
vigência **passada** desvia para avulso (`CONTRACT_EXPIRED`). Todo código novo entra em
`_SEVERITY_BY_CODE`, sob pena de `KeyError` no `add()`.

---

## 6. O consolidado — quatro origens (D36) e o motivo (D33)

| Origem | Como é identificada | Competência (R7) |
|---|---|---|
| `STANDALONE_LOG` — avulso | `settled = BILL_AMOUNT` e (`applied = BILL_AMOUNT` sem contrato **ou** `route_deviation_reason` não nulo) | mês de `worked_on` |
| `OUT_OF_SCOPE_LOG` — fora de escopo | `applied = settled = BILL_AMOUNT` e o Cliente **tem** contrato na competência | mês de `worked_on` |
| `CONTRACT_OVERAGE` | `OVERAGE_BILLED` com `period` | competência do período |
| `ALLOWANCE_OVERAGE` | `OVERAGE_BILLED` com `allowance` | mês de `closed_at` |

Dentro de `STANDALONE_LOG`, quebra por `route_deviation_reason` — nulo é "o cliente é
avulso, é o modelo de negócio dele"; não nulo é **pendência comercial com prazo**, que é
exatamente a distinção que a D33 exigiu que aparecesse.

`OUT_OF_SCOPE_LOG` depende de "o Cliente tinha contrato naquela competência", que é
**derivado no relatório**, não snapshotado. Isso é deliberado: essa informação não decide
dinheiro (o valor já está persistido), ela só classifica receita — então a R4 não a alcança,
e contratos não são apagados. Snapshotá-la seria a segunda verdade que o comentário de
`ServiceLog` sobre a ausência de `applied_contract` já recusou uma vez.

**Critério 13 (nenhum centavo perdido ou criado):** todo total é `Sum("amount")` sobre
valores persistidos, no banco. Não existe caminho que recalcule valor a partir de horas num
agregado. A classificação das quatro origens fica numa função de domínio única
(`revenue_origin_of(...)` + a anotação de queryset equivalente), pelo mesmo motivo que
`compute_hour_quantities` é única: quatro relatórios não podem cada um ter a sua versão da
regra.

**Exportação:** implementar a vaga `("issue_worklogs", "Issue Worklogs")` que já existe em
`exporter.py:26`, no pipeline existente — `ExporterHistory`, `create_zip_file`,
`upload_to_s3` e o stack de `plane/utils/exporters/` (`ExportSchema` + `formatters.py`), que
é o que o briefing nomeia e o que dá controle explícito de cabeçalho e ordem de coluna.
Ganha fila, histórico, status, retry, download e expiração de graça. **Um ajuste
obrigatório:** o `GET` de `ExportIssuesEndpoint` filtra `type="issue_exports"` fixo, então
sem mexer nele a exportação de apontamentos ficaria invisível no histórico.

**Permissão: `[ROLE.ADMIN], level="WORKSPACE"`.** Não ADMIN+MEMBER como o export de issues,
porque a R11 diz que técnico não vê valor.

---

## 7. R11 — o dinheiro é ADMIN, e a restrição é de serializer

A R11 diz que técnico **não vê** valor em R$, e a R11(b) diz que a restrição é de
serializer: "esconder na interface e mandar no payload é vazamento". O briefing recomenda o
mesmo na §4.

`ServiceLogClientSerializer` já existe e é o precedente. Mas `ServiceLogSerializer` atende
técnico **e** admin no mesmo endpoint, então proponho remoção dinâmica de campo no
`__init__` a partir de `context["can_see_amounts"]` (verdadeiro só para ADMIN de workspace),
com **teste de contrato afirmando que o payload de um MEMBER não tem as chaves de dinheiro**
— não que a UI não as mostra.

`ServiceLogClientSerializer` **não** ganha valor nesta fase. A R11 diz que o cliente vê
valor "só se avulso", e o portal do cliente é a Fase 8. Fica como **critério herdado**,
registrado no documento da Fase 6 e a fechar na Fase 8, pelo mecanismo das convenções.

---

## 8. Migração 0130

1. `ServiceClientPrice`, `ServiceClientHourTypeRate`
2. 6 colunas + 6 constraints + 1 índice em `service_logs`
3. 2 colunas + 1 check + 2 uniques em `service_hour_ledger_entries`
4. Back-fill `settled_billing_route = applied_billing_route` — **fato exato, não palpite**:
   antes desta fase não havia desvio possível, então o aplicado *era* o escolhido
5. `RunPython` **antes** dos `AddConstraint`, no método da 0129: conta e **nomeia os IDs**
   das linhas violadoras de (a) `OVERAGE_BILLED` duplicado por período/bolsa, (b)
   `overage_hour_rate` negativo, (c) apontamento `BILL_AMOUNT` com origem de pool
6. Constraint nova em coluna existente: `overage_hour_rate` nulo ou `> 0` — a Fase 4 a
   deixou sem guarda porque nada lia o campo; agora esta fase lê

Reversível, e validada à mão com `make-migration.sh` contra `POSTGRES_DB=migcheck`
(aplicar cadeia → reverter 0130 → reaplicar), afirmando **os dados** e que o reverso
realmente removeu colunas e constraints.

---

## 9. Critérios de aceite → onde cada um é provado

| # | Como fecha |
|---|---|
| 1 | `1.0 × 200 = R$ 200,00` — `amount_from_base_rate` |
| 2 | `1.5 (equiv) × 200 = R$ 300,00` — o multiplicador entra pela hora, não pelo preço |
| 3 | `1.25 × 200 = R$ 250,00` |
| 4 | check `service_log_non_billable_carries_no_amount`; lista mostra Tipo de Atendimento |
| 5 | `amount_from_absolute_rate(logged_hours=…)` — §3.2, e o override é linha da folha |
| 6 | snapshot `applied_hour_rate` + `amount`; nova folha não toca linha existente |
| 7 | `3 × 250 = R$ 750,00` em `OVERAGE_BILLED.amount` |
| 8 | assinatura sem multiplicador (§4.3) + sabotagem |
| 9 | `service_ledger_unique_overage_billed_per_period` — DDL (§4.1) |
| 10 | quatro origens + `route_deviation_reason` (§6) |
| 11 | `Sum("amount")` + handler `issue_worklogs` |
| 12 | vigência por `worked_on` + competência por `worked_on` (§1.3) |
| 13 | um arredondamento por apontamento; totais só somam persistido (§3.1, §6) |

Herdado desta fase para a Fase 8: cliente avulso vê valores (R11). Herdado/dívida:
estorno de excedente (§4.5, pendente da sua resposta F).

## 10. Sabotagens planejadas

Além das cinco do handoff, três que o mapeamento revelou:

| Sabotagem | Tem de quebrar |
|---|---|
| aplicar o multiplicador de novo no excedente | 8 |
| arredondar no total em vez de por apontamento | 13 |
| ler o preço do cliente em vez do snapshot | 6 |
| ignorar o override absoluto | 5 |
| faturar o excedente duas vezes | 9 — e por `UniqueViolation` |
| **usar `equivalent_hours` no caminho do override** | 5 — cobra em dobro (§3.2) |
| **selecionar a vigência pela data de digitação** | 12 |
| **materializar período fora da vigência** | D31 — 390h num contrato de 360h |

E: cada teste de ausência afirma o **código** do motivo, com controle positivo na mesma
fixture — §3.4 mostra que R$ 0,00 é verdade por cinco motivos diferentes.
