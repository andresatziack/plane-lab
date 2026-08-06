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

**O modelo de dados já foi apresentado e aprovado**, com as decisões registradas abaixo.
A §7 do contexto mestre está cumprida para esta fase: **não reapresente a proposta.**
Valide-a contra o código, e **avise** se algo não fechar — uma divergência entre este
documento e o código é notícia, não detalhe.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as quatro armadilhas.
   **Os scripts já existem no repositório. Não os recrie.**
4. **`docs/worklog/04-contratos-e-pools.md`** — o briefing, com os 24 critérios de aceite.
5. **`docs/worklog/DECISOES.md`** — **D18 é obrigatória**, o próprio briefing manda ler
   antes de começar: hierarquia matriz/filial pode alterar o motor de débito. Também
   **D3, D4, D9, D10, D15** e **D21/D22**.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b e 3 mescladas.** PRs #1 a #8 fechados.
- Última migração: **0127**. A sua é a **0128**.
- Baseline da suíte: **1689 passando, 0 falhando** — medido com `RECREATE_DB=1`. Meça de
  novo antes de tocar em nada, para não herdar regressão alheia como se fosse sua.
- **Não existe CI.** As checagens locais são a única verificação.

---

# Design aprovado da Fase 4

## D1. Livro-caixa de horas (_ledger_) append-only — **aprovado**

Quatro exigências do briefing são a mesma coisa: rastrear a competência de origem de cada
parcela para expirar a certa (critério 6), estorno idempotente (11, 12), "saldo nunca
desaparece sem registro de auditoria" (20) e concorrência sem corromper saldo (14). Um
journal de movimentos resolve as quatro; sem ele, cada uma precisa de mecanismo próprio.

```
ServiceHourLedgerEntry (append-only)
  contract, period, origin_period, hours (assinado), entry_type, service_log, actor, notes
```

`entry_type` ∈ `GRANT` · `CARRY_IN` · `CARRY_OUT` · `DEBIT` · `REVERSAL` ·
`OVERAGE_BILLED` · `EXPIRED_BY_VALIDITY` · `EXPIRED_BY_CAP` · `EXPIRED_BY_CONTRACT_END` ·
`TRANSFERRED_TO_CONTRACT` · `CONVERTED_TO_ISSUE_ALLOWANCE`

O critério 20 passa a ser satisfeito **por construção**: não existe caminho que reduza
saldo sem inserir linha, porque a redução _é_ a linha.

Não é a "segunda tabela de auditoria" que a §6 do steering proíbe. A divisão é explícita:
`ServiceConfigActivity` registra **configuração** (contrato criado, horas mensais
alteradas); o ledger é **movimento contábil** — é o dado, não o log do dado.

> **Restrição acrescentada na aprovação.** A unique parcial de idempotência tem de cobrir
> **`REVERSAL` além de `DEBIT`**. A cascata de exclusão roda em Celery, e Celery **repete
> task**: sem isso um retry estorna duas vezes e credita hora que nunca existiu — o
> espelho exato do débito duplicado que a unique existe para impedir.
>
> **Verificado, e é boa notícia:** `replace_service_log_batch` **apaga e recria** as linhas
> reusando o `batch_id` (leia o docstring). Cada linha nova tem PK nova, então a unique
> `(service_log, entry_type)` sobrevive a uma edição. Confirme lendo o código.

## D2. O período carrega o saldo; o ledger carrega o histórico — **aprovado**

Ler o ledger inteiro a cada débito não escala e complica o lock. Então o período tem os
totais e é a linha que se tranca (`select_for_update`), o ledger tem os movimentos, e os
dois são escritos **na mesma transação, sempre**.

`reconcile_period()` afirma que a soma do ledger bate com o período, com **teste dedicado**
e um management command de reparo. Sem reconciliação testada, dois números que deveriam ser
iguais divergem em silêncio e ninguém descobre até o cliente contestar.

## D3. `GeneratedField` — **RECUSADO.** Use `@property` + `annotate()`

O argumento contra armazenar valor derivado está certo, mas `annotate()` resolve igual
**sem armazenar nada e sem novidade**:

```python
.annotate(balance=F("contracted_hours") + F("carried_hours") - F("consumed_hours"))
 .filter(balance__lt=0)
```

Isso atende o painel de alertas. O `GeneratedField` seria o **primeiro do repositório**,
introduzido na fase que mexe com dinheiro, e interage com coisas que esta fase usa:
`F()` + `refresh_from_db`, `update()` que precisa excluir a coluna, e o risco sob
`--nomigrations`. O ganho é um índice numa tabela com **uma linha por contrato por mês** —
3.000 linhas em cinco anos de operação. Não paga a novidade.

Se um dia aparecer query lenta, aí sim, com evidência.

## D4. Sentinela `"UNSET"` na trilha de auditoria — **aprovado**

É aqui que o aviso deixado pela Fase 2b é cobrado. `svc_cfg_activity_shape_matches_verb`
exige `old_value` e `new_value` não nulos quando `verb='updated'`, e o contrato tem três
campos nullable que são exatamente o que a trilha existe para registrar: teto de acúmulo,
validade do saldo transportado e valor/hora de excedente.

Das três saídas documentadas no comentário da constraint, a escolhida é a **sentinela**:
uma linha em `serialize_config_value`, `CONFIG_VALUE_UNSET = "UNSET"`. Não `""` (ambíguo
com texto legitimamente vazio) nem `"None"` (repr de Python vazando para dado). `UNSET`
segue o `UPPER_SNAKE` dos códigos de erro e não colide com nada que a função produza:
decimais quantizados, datas ISO, booleanos minúsculos, enums minúsculos.

Recusadas: excluir os campos de `TRACKED_FIELDS` (o docstring de `ServiceConfigActivity`
nomeia "accrual cap, overage hour rate" como razão de existir da tabela) e relaxar a
constraint (enfraquece a garantia DDL de _todas_ as linhas, inclusive as dos catálogos,
para resolver um problema de três colunas).

Implementada **uma vez, no helper compartilhado**, para a Fase 6 herdar em vez de
redescobrir. Dois testes obrigatórios:

1. a transição `NULL → 60.00` grava linha que **satisfaz** a constraint (hoje estouraria);
2. **nenhum campo em `TRACKED_FIELDS` é texto livre.** A segurança da sentinela depende
   disso, e hoje isso é um comentário. Comentário apodrece; teste falha. É a lição do
   falso-verde aplicada a uma premissa de design.

## D5. Teto de acúmulo: enum + valor — **aprovado**

`accrual_cap_hours` e `accrual_cap_multiple` ambos nullable admitem estado sem significado
(os dois preenchidos, com qual vencendo?).

```python
accrual_cap_mode  = CharField(choices=[NONE, ABSOLUTE, MULTIPLE], default=NONE)
accrual_cap_value = DecimalField(10, 4, null=True)
```

`CheckConstraint`: `mode=NONE ⟺ value IS NULL`, e `value > 0` nos outros dois. O estado
incoerente deixa de ser **representável**, em vez de ser recusado por validação que alguém
esquece de chamar.

## D6. Consumo FIFO, parcela mais antiga primeiro — **aprovado**

Regra que o briefing não enuncia e sem a qual o critério 6 é indeterminado. A cota
contratada do mês é uma parcela com origem no próprio mês, e o consumo come sempre da mais
antiga. Consequência: o transportado é consumido antes, e **hora nenhuma expira podendo ter
sido usada**. A ordem inversa faria o cliente perder saldo com o pool cheio — e ele
reclamaria com razão.

## D7. `Project.service_contract` — **aprovado**

FK nullable `DO_NOTHING` em `project.py`, com precedente no mesmo arquivo
(`Project.service_client`, D19). Resolução, com falha explícita em vez de escolha
arbitrária:

```
project.service_contract → contrato is_default do cliente, vigente em worked_on → erro
```

Códigos: `NO_SERVICE_CLIENT_FOR_PROJECT`, `NO_CONTRACT_FOR_CLIENT`,
`AMBIGUOUS_CONTRACT_RESOLUTION`. E `CONTRACT_OUT_OF_VIGENCY` como **alerta, não bloqueio**
(D9).

## D8. A Fase 4 não guarda dinheiro — **aprovado**

`ServiceClient` não tem valor/hora e criá-lo aqui invadiria a Fase 6. `OVERAGE_BILLED`
registra **horas**; `overage_hour_rate` fica no contrato como override opcional, sem
consumidor nesta fase. A conversão em R$ é ponto de extensão documentado. Atende o
critério 9 sem inventar precificação.

---

# As duas decisões que estavam bloqueadas

## A. Cascata de exclusão do work item — **opção 1, com três restrições**

O bug é real e foi verificado: o catch-all de `soft_delete_related_objects`
(`plane/bgtasks/deletion_task.py`) **atribui `deleted_at` e chama `.save()`**, sem passar
por `SoftDeleteModel.delete()`. E `ServiceLog.issue` é `CASCADE`. Logo, um estorno
pendurado no `delete()` do apontamento não roda quando um work item é excluído: o saldo
fica debitado por horas de um chamado que não existe mais.

Os caminhos normais já estão cobertos — `delete_service_log_batch` chama `row.delete()`
linha a linha, com comentário dizendo que faz isso **justamente prevendo o débito da
Fase 4**.

Corrija com um branch explícito antes do catch-all, respeitando:

1. **O branch é só para `ServiceLog`.** Não altere o catch-all para chamar `.delete()` em
   todo modelo com `deleted_at`: isso passaria a executar o `delete()` customizado de
   dezenas de modelos do core, e é mudança de comportamento grande demais para esta fase.
2. **Some a reconciliação da opção 2** — o command de reparo e um teste que prova a
   divergência. Não é redundância: o branch fecha _este_ caminho, e qualquer
   `.update(deleted_at=...)` futuro reabre a mesma classe de bug. O command é seguro barato.
3. **Documente que o estorno é assíncrono.** `soft_delete_related_objects` roda em task
   Celery, então o pool não volta na mesma transação da exclusão do work item. Se a task
   falhar, o saldo fica errado até a reconciliação rodar. Isso vai no docstring, não é para
   ser descoberto depois.

## B. As quatro ambiguidades

**B1 — Mês parcial: nem pro-rata nem "sempre integral".** `contracted_hours` já é snapshot
por período. Torne-o **editável enquanto o período está `OPEN`**, com default
`monthly_hours`. Pro-rata deixa de ser regra que o sistema inventa e passa a ser dado que o
admin informa quando o contrato diz. Sem campo de política e sem chute. Obrigatório: a
edição vai para a trilha de auditoria e é **recusada** em período `CLOSED`.

**B2 — Déficit sem teto, só alerta.** Confirmado, coerente com a D4. Mas
`ServiceContractAlertDismissal` com unique `(period, alert_code)` tem um furo: dispensar o
alerta a −5h o silencia a −200h. **Grave o saldo no momento da dispensa e re-dispare quando
piorar além da próxima faixa.** Um alerta que só toca uma vez, no dia mais barato, não é
barreira nenhuma. Registre o não-teto como limitação aceita.

**B3 — Excedente faturado tira o déficit do pool.** Confirmado: liquidação `BILLED` move o
déficit para `OVERAGE_BILLED` em horas, e o mês seguinte abre com as horas contratadas
íntegras, como diz o critério 9.

**B4 — Contrato suspenso debita e alerta**, igual a vencido (D9), **mas com código de
alerta distinto**, para o relatório separar os dois. Registre explicitamente que, com isso,
`SUSPENDED` não muda comportamento nenhum além do alerta. "Suspenso bloqueia trabalho novo
mas aceita lançamento retroativo" seria regra nova e não está escrita em lugar algum.

---

# Modelo de dados aprovado

Toda coluna de hora é `DecimalField(max_digits=10, decimal_places=4)`, conforme §4b do
contexto mestre.

## `ServiceContract` — `service_contracts`

`ChangeTrackerMixin` + `WorkspaceBaseModel`.

| Campo                                     | Tipo                                     | Notas                                          |
| ----------------------------------------- | ---------------------------------------- | ---------------------------------------------- |
| `service_client`                          | FK → ServiceClient, **DO_NOTHING**       | `related_name="contracts"`                     |
| `code`                                    | CharField(100)                           | único por cliente, padrão duplo de soft delete |
| `name`                                    | CharField(255)                           | ex.: "Suporte", "Infraestrutura"               |
| `monthly_hours`                           | Decimal(10,4)                            | > 0                                            |
| `starts_on` / `ends_on`                   | DateField                                | `ends_on >= starts_on`                         |
| `carryover_months`                        | PositiveSmallInt **null**                | vazio = não expira                             |
| `accrual_cap_mode` / `accrual_cap_value`  | choices / Decimal(10,4) **null**         | D5                                             |
| `high_consumption_threshold_pct`          | Decimal(5,2), default 80                 | não nullable, fora de `TRACKED_FIELDS`         |
| `low_consumption_threshold_pct`           | Decimal(5,2), default 30                 | idem                                           |
| `overage_policy`                          | choices `CARRY_DEFICIT` / `BILL_AMOUNT`  | preferência, confirmada por período            |
| `overage_hour_rate`                       | Decimal(12,2) **null**                   | Fase 6 consome                                 |
| `status`                                  | choices `ACTIVE` / `SUSPENDED` / `ENDED` |                                                |
| `previous_contract`                       | FK self, **DO_NOTHING**, null            | cadeia de renovação                            |
| `is_default`                              | Boolean                                  | partial unique **por cliente**                 |
| `notes`, `external_source`, `external_id` |                                          | padrão da casa                                 |

`TRACKED_FIELDS = [monthly_hours, starts_on, ends_on, status, accrual_cap_mode,
accrual_cap_value, carryover_months, overage_policy, overage_hour_rate]` ·
`CONFIG_ENTITY_NAME = ServiceConfigEntity.CONTRACT` · implementa `config_summary()`.

> **`is_default` aqui NÃO copia `ServiceCatalogBaseModel`.** Lá o default é por
> _workspace_; aqui é por _cliente_. Herdar a base traria a constraint errada — verificado
> em `service_catalog.py`, onde a condição é `fields=["workspace"]`. Por isso
> `ServiceContract` estende `WorkspaceBaseModel` direto.

## `ServiceContractPeriod` — `service_contract_periods`

| Campo                                  | Tipo                                | Notas                                                                 |
| -------------------------------------- | ----------------------------------- | --------------------------------------------------------------------- |
| `contract`                             | FK, **DO_NOTHING**                  |                                                                       |
| `competence_year` / `competence_month` | SmallInt                            | unique com `contract`                                                 |
| `starts_on` / `ends_on`                | DateField                           | limites reais, podem ser parciais                                     |
| `contracted_hours`                     | Decimal(10,4)                       | **snapshot** de `monthly_hours` (R4). Editável em período `OPEN` — B1 |
| `carried_hours`                        | Decimal(10,4), default 0            | **pode ser negativo** (déficit)                                       |
| `consumed_hours`                       | Decimal(10,4), default 0            | acumulador, sempre sob lock                                           |
| `discarded_by_cap_hours`               | Decimal(10,4), default 0            | critério 15                                                           |
| `overage_hours`                        | Decimal(10,4), default 0            | critério 9                                                            |
| `overage_settlement`                   | choices null / `CARRIED` / `BILLED` |                                                                       |
| `status`                               | `OPEN` / `CLOSED`                   |                                                                       |
| `closed_at`, `closed_by`               | DateTime, FK User **SET_NULL**      |                                                                       |

`granted_hours` e `balance_hours` são **`@property` + `annotate()`**, não colunas (D3).

O snapshot de `contracted_hours` é o que impede que alterar as horas mensais do contrato
reescreva meses já faturados. Índices: `(contract, competence_year, competence_month)` e
`(contract, status)`.

## `ServiceHourLedgerEntry` — `service_hour_ledger_entries`

Append-only, sem update. `contract`, `period`, `origin_period` (null), `hours` (assinado),
`entry_type`, `service_log` FK **DO_NOTHING** null, `actor` FK **SET_NULL** null, `notes`.

Unique parcial `(service_log, entry_type)` para **`DEBIT` e `REVERSAL`** → idempotência
garantida pelo banco, não por `if` na aplicação.

## `ServiceContractAlertDismissal` — `service_contract_alert_dismissals`

`period`, `alert_code`, `dismissed_by`, `dismissed_at`, **e o saldo no momento da dispensa**
(B2). Unique `(period, alert_code)`.

## Alterações em modelo existente

- `Project.service_contract` — FK null `DO_NOTHING` (D7)
- `ServiceLog.debited_period` — FK null `DO_NOTHING`. Pedido explícito da seção 5, passo 5.
  **Não** adicionar `applied_contract`: o período já navega para o contrato, e um segundo
  snapshot seria uma segunda verdade
- `ServiceConfigEntity` += `CONTRACT`, `CONTRACT_PERIOD`
- `serialize_config_value` += sentinela (D4)
- `validate_catalog_delete` — nova guarda, no ponto de extensão que já existe

## Camada de domínio — `plane/utils/service_pool.py`

Pura e testável, no padrão de `service_log.py`:

```
resolve_contract(issue, worked_on)      -> (contract, warning_code | None) | erro explícito
resolve_period(contract, worked_on, *, materialize=True)
apply_debit(service_log, actor)         # idempotente pela unique parcial
reverse_debit(service_log, actor)
close_period(period, settlement, actor)
expire_parcels(period)                  # FIFO, validade e teto
reconcile_period(period)                # ledger vs. período
renew_in_place / renew_expiring_balance / end_and_create_successor
```

- **Concorrência:** `transaction.atomic()` + `select_for_update()` na linha do período,
  `consumed_hours` com `F()`, nunca ler-somar-gravar. Materialização concorrente de período
  resolve pela unique parcial, com retry.
- **Hierarquia R6:** `apply_debit` checa bolsa do work item primeiro. Fase 5 não existe →
  ponto de extensão documentado, **e avise** (§7.4).
- **Batch em competências diferentes** (31/01 23:00 → 01/02 01:00): cada segmento debita o
  **seu** período. Se qualquer um estiver fechado, o batch inteiro é recusado — meio batch
  gravado é pior que batch recusado, mesmo argumento de `create_service_log_batch`.

## Testes

Além do que as convenções já exigem, **verificação por sabotagem nos cinco pontos onde o
dinheiro vaza**. Cada um tem de ficar vermelho quando quebrado de propósito:

| Sabotagem                                | Tem de quebrar              |
| ---------------------------------------- | --------------------------- |
| remover `select_for_update`              | critério 14                 |
| remover a unique parcial do `DEBIT`      | débito duplicado            |
| inverter o FIFO                          | critério 6                  |
| quebrar o snapshot de `contracted_hours` | mês fechado sendo reescrito |
| remover o estorno                        | critérios 11 e 12           |

Todo teste de ausência afirma **o código do motivo** e vem com controle positivo na mesma
fixture: "não debitou" tem de dizer _por que_, porque `NON_BILLABLE` e "não existe
contrato" produzem o mesmo saldo e são bugs opostos.

O critério 14 exige `django_db(transaction=True)` com threads reais — `--reuse-db` roda em
transação e o lock não se manifesta. Marque e isole: é o teste mais lento e o mais fácil de
ficar flaky.

---

## Escopo

Só a Fase 4. Encontrando dependência das Fases 5, 6 ou 9: deixe a interface preparada,
documente o ponto de extensão e **avise** — não implemente.

Ao terminar: marque os **24** critérios da Fase 4 um por um; se algum critério de fase
anterior só puder ser fechado aqui, use o mecanismo de **critérios herdados**; reescreva
este arquivo apontando para a fase seguinte; e abra o PR.

## Depois da Fase 4

Ordem do `README.md`: **5** (bolsa por work item, depende da 4), **6** (precificação,
depende de 3 e 4), **9** (dashboards, depende de 4, 5 e 6).

A **Fase 7** (permissões e delegação) depende só da 3 e já está desbloqueada — pode ser
feita em paralelo.

## Uma dívida de processo, para você resolver antes da Fase 6

Três das quatro ambiguidades acima (pro-rata do primeiro mês, teto de déficit, o que
"suspenso" significa) são **cláusulas contratuais**, não decisões de software. As respostas
estão nos contratos reais com os clientes. Extraí-las para o `DECISOES.md` antes da Fase 6
evita que a fase de precificação pergunte as mesmas coisas de novo — e evita defaults
razoáveis sendo propostos para regras que já existem em papel.
