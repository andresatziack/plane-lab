# Próxima sessão: Fase 7 — Permissões de apontamento, delegação e auditoria

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 7**: `docs/worklog/07-permissoes-delegacao.md` — quem pode apontar por
quem, e o rastro que isso deixa.

**O modelo de dados ainda NÃO foi apresentado para esta fase.** A §7.1 do contexto mestre
vale integralmente: **apresente o modelo e as decisões, e aguarde OK antes de escrever
código.**

Por que a 7 e não a 8 ou a 9: ela depende só da Fase 3, está desbloqueada há quatro fases,
e é a única do núcleo que ainda bloqueia outra coisa — a Fase 8 (portal do cliente) depende
dela. A 9 depende de 4, 5 e 6, que agora estão todas prontas, então também está liberada;
se preferir a 9, o handoff serve, mas leia a seção "Se você for fazer a Fase 9 em vez da 7".

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
   Para esta fase a **R8** (autoria e quem digitou são dois fatos) e a **R11** (quem vê o
   quê) são as partes que mais importam.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as armadilhas.
   **Os scripts já existem. Não os recrie.**
4. **`docs/worklog/07-permissoes-delegacao.md`** — o briefing.
5. **`docs/worklog/DECISOES.md`** — e desta vez **leia até o fim**: a Fase 6 acrescentou
   **D37 a D44**, e a **D44** (rota escolhida × rota aplicada) e a **D43** (três classes de
   motivo) mudam o que um apontamento significa.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b, 3, 4, 5 e 6 mescladas.**
- Última migração: **0130**. A sua é a **0131**.
- Baseline da suíte: **2042 passando, 0 falhando** — medido com `RECREATE_DB=1` na branch
  da Fase 6. **Meça de novo antes de tocar em nada**, para não herdar regressão alheia
  como se fosse sua.
- **Não existe CI.** As checagens locais são a única verificação.
- `ruff` **não vem instalado** no venv do sandbox:
  `/projects/sandbox/.plane-agent-sandbox/venv-api/bin/python -m pip install ruff`. Há
  **3 erros F401 pré-existentes** na `preview` (`app/views/issue/sub_issue.py` ×2,
  `app/views/project/invite.py`) — não são seus, e corrigi-los produz churn alheio.
- Checagens do frontend: `./tools/agent-sandbox/run-web-checks.sh {i18n|types|lint}`.
  Depois de editar `packages/*/src`, use `REBUILD=1` ou os imports de `@plane/types`
  aparecem como TS2307 em arquivos que você nem tocou.

### Armadilhas do sandbox

- **`git checkout -- <arquivo>` restaura para o HEAD, não para o seu último estado.** Se o
  seu trabalho ainda não está commitado, ele é **destruído**. Na Fase 5 isso apagou um
  arquivo inteiro de domínio no meio da verificação por sabotagem. **Commite antes de
  sabotar.**
- **Sabotagem que falha pelo motivo errado não prova nada.** Confira **a mensagem** de cada
  falha, não só a cor.
- **`run-api-tests.sh` recebe argumentos do pytest direto**, não por variável de ambiente:
  `./run-api-tests.sh plane/tests/unit/utils/test_x.py -q`. Não existe
  `AGENT_SANDBOX_PYTEST_ARGS` — na Fase 6 uma tentativa disso rodou a suíte inteira duas
  vezes por engano, ~8 minutos jogados fora.
- **`RECREATE_DB=1` não é opcional depois de mexer em modelo.** `pytest.ini` tem
  `--reuse-db` **e** `--nomigrations`: o schema é construído dos modelos, mas o banco é
  reusado, então colunas novas simplesmente não existem e você recebe 200 erros de
  `column ... does not exist` que não têm nada a ver com o seu código.
- **`/tmp` não persiste entre invocações de shell.** Rascunhos vão em `/projects/sandbox/`,
  fora do repositório, e são apagados depois.
- **Os serviços nativos são colhidos entre invocações de shell.** Um `psql` numa chamada não
  encontra o Postgres que a anterior subiu — rode `./start-test-services.sh` na **mesma**
  invocação. `psql` está em `/usr/bin/psql`.
- **`gh` tem de rodar de `/projects/sandbox`**, não de dentro de `plane-lab`: o
  `.mise.toml` não é _trusted_ e o `mise` aborta o comando.
- **O hook de pre-commit precisa de `pnpm` no PATH.** Antes de commitar frontend:
  `export PATH="$(ls -d /root/.nvm/versions/node/* | tail -1)/bin:$PATH"` e
  `corepack enable pnpm`. O hook roda `oxlint --deny-warnings` nos arquivos em stage e
  **falha em qualquer advertência**, inclusive pré-existente. Linte seus caminhos antes do
  `git add`.
- Nomes de índice do Django têm **limite de 30 caracteres** (`models.E034`).
- **Uniques parciais do Django viram índices únicos**, não `pg_constraint`. Se você for
  verificar DDL a mão, procure em `pg_indexes`.

---

# O que a Fase 6 mudou e você precisa saber

## Um apontamento agora tem duas rotas, e ler só uma dá a resposta errada

`ServiceLog.applied_billing_route` continua sendo a rota **escolhida** (do Tipo de
Atendimento). A nova `settled_billing_route` é a que foi **aplicada**, e
`route_deviation_reason` diz por que diferem. **D44.**

Elas diferem sempre que a D33 se aplica: um apontamento que escolheu "Contrato" mas cujo
cliente não tem contrato usável é faturado como avulso. Se a sua fase mostrar, filtrar ou
auditar rota de faturamento, ler `applied_billing_route` sozinha vai dizer "Contrato" sobre
um apontamento que foi faturado em reais.

## R$ 0,00 é verdade por cinco motivos, e eles são bugs diferentes

Dois não precisam de coluna: rota `NON_BILLABLE` (a rota diz) e pool pago
(`debited_period` / `debited_allowance` dizem). Os outros três estão em
`pricing_failure_reason`, e a **D43** os divide em três classes que um painel financeiro
não pode misturar:

| Classe | Alguém age? |
|---|---|
| pendência **comercial** (`route_deviation_reason`) | sim, com prazo. **É** receita |
| pendência de **cadastro** (`NO_PRICE_SHEET_*`) | sim. **Não** é receita ainda |
| **trabalho interno** (`INTERNAL_PROJECT_NO_CLIENT`) | **não.** Fora da receita |

As listas `PRICING_PENDENCY_FAILURES` e `INTERNAL_WORK_FAILURES` estão no nível do módulo
em `plane/db/models/service_log.py` justamente para que ninguém decida isso de novo.

## Dinheiro é ADMIN de workspace, e a barreira é de serializer

A R11 põe o valor em R$ fora do alcance do técnico, e a **R11(b)** diz que a restrição é do
serializer, não da interface: "esconder na interface e mandar no payload é vazamento".

O mecanismo: `ServiceLogSerializer.AMOUNT_FIELDS` (7 chaves) e
`ServiceIssueAllowanceSerializer.AMOUNT_FIELDS` (1) são **removidas do payload** no
`__init__` quando `context["can_see_amounts"]` é falso. O default é falso — a direção segura
para um campo de dinheiro é estar ausente até alguém provar o contrário.

**O vazamento que isso não pega sozinho, e que você vai encontrar de novo:** o payload do
Admin alimentava `IssueActivity` via `_record_activity`, e o feed de atividade de um work
item é legível por **qualquer Member do project**. Reusar o payload já serializado — a coisa
óbvia — poria o valor na frente exatamente de quem a R11 o esconde, a um salto do endpoint
que o omitiu. Por isso existe `_activity_payload`, que serializa **sem** contexto. Fixado
por `test_the_activity_trail_carries_no_money`.

**A Fase 7 mexe em permissões de apontamento e a Fase 8 abre o portal.** Se qualquer coisa
sua serializar um `ServiceLog` num contexto novo, decida explicitamente se aquele contexto
vê dinheiro, e escreva o teste que afirma a **ausência das chaves** — não que o valor é
zero, não que a tela não mostra.

## Onde a decisão de liquidação mora agora

`plane/utils/service_billing.py` é novo e tem **uma** responsabilidade:
`resolve_settlement` decide, para um apontamento persistido, se ele debita pool, é
precificado, ou nenhum dos dois. `settle_service_log_batch` é o que as views chamam —
`apply_batch_debit` **não é mais o ponto de entrada da API**.

Se a delegação da Fase 7 criar um caminho novo que reaplique débito, ele passa por aí. O
`apply_debit` continua existindo e com comportamento intacto (as Fases 4 e 5 têm testes
contra ele), mas ele é a metade do pool de uma decisão maior.

## Módulos novos e o que cada um garante

| Arquivo | O que garante |
|---|---|
| `plane/utils/service_money.py` | **Não importa Django.** Aritmética monetária pura: `quantize_money` com `ROUND_HALF_UP` explícito, e **duas** funções de preço com nomes de parâmetro diferentes de propósito |
| `plane/utils/service_pricing.py` | Vigência (`resolve_price_sheet`), tabela efetiva, preço de um apontamento, taxa de excedente. **O único lugar da fase que levanta exceção** é `resolve_overage_rate` |
| `plane/utils/service_billing.py` | A decisão de liquidação e o consolidado mensal (`consolidated_billing`) |
| `plane/db/models/service_pricing.py` | `ServiceClientPrice` (uma linha por vigência) + `ServiceClientHourTypeRate` (filha da folha) |

`service_log_time.py` e `service_money.py` são os dois módulos sem Django. Se a sua
aritmética for pura, ela pertence a um deles.

## Um footgun que você vai encontrar

Qualquer criação de `ServiceLog` **fora** de `build_batch_rows` precisa setar
`settled_billing_route`, senão o default `DEBIT_POOL` viola
`service_log_route_deviation_is_coherent` numa linha `non_billable` ou `bill_amount`. Falha
com `IntegrityError` — alto, nunca em silêncio. `ServiceLogFactory` já resolve com
`SelfAttribute("applied_billing_route")`.

---

# Coisas das fases anteriores que continuam valendo

- **`ServiceLog.delete()` foi sobrescrito** e estorna o débito de horas antes de apagar,
  porque um apontamento é excluído por quatro portas. **O valor em R$ não precisa de
  estorno** e isso é consequência de onde ele mora: o valor está na própria linha, então
  soft-delete o remove de todo total por removê-lo de `objects`. Se a delegação criar uma
  quinta porta, ela tem de passar por `delete()`.
- **O livro-caixa é o único lugar onde saldo se move** (D23, D29).
  `write_ledger_entry` é o único que cria linha, e desde a Fase 6 ele também carrega
  `amount` e `applied_hour_rate` — que só a linha `OVERAGE_BILLED` pode ter, por constraint.
- **Rota `NON_BILLABLE` tem `debited_hours = 0` por check constraint.** Valor R$ 0,00 sai da
  rota, não de um `if`.
- **`reconcile_service_periods`** é o comando de reparo, e desde a Fase 6 ele também checa
  que o valor de um excedente faturado é igual a horas × taxa.

## Verificação por sabotagem: continue fazendo

Quatro fases seguidas encontraram algo com isso. A Fase 6 encontrou **três**:

- o critério 5 é um segundo "cobrar em dobro" que o briefing não avisava (override absoluto
  multiplica horas **apontadas**, não equivalentes);
- empates de meio centavo são alcançáveis, e `quantize_hours` herda `ROUND_HALF_EVEN` do
  contexto — por isso `quantize_money` passa `rounding` explicitamente;
- o critério 9 era um `if`, e o índice único existente **não cobria** linhas
  `OVERAGE_BILLED` porque é condicionado a `service_log__isnull=False`. Duas cobranças do
  mesmo excedente eram representáveis. Agora é recusa do banco.

Para a Fase 7, os pontos onde a permissão vaza:

| Sabotagem | Tem de quebrar |
|---|---|
| deixar qualquer membro apontar como outro sem a permissão de delegação | o critério de delegação |
| gravar `author` sem gravar quem digitou | R8 |
| serializar dinheiro num contexto novo sem gate | R11(b) |
| permitir que a delegação reaplique débito por fora do domínio | D23 |

E **todo teste de ausência afirma o código do motivo, com controle positivo na mesma
fixture.**

## Dívidas nomeadas em aberto, por fase que as herda

| Fase | Dívida | Onde está registrada |
|---|---|---|
| **Própria** | **Estorno de excedente faturado não existe.** Exigiria entry type reversor, reabertura de período e cascateamento do transporte | **D42**, com roteiro completo |
| **8** | Cliente avulso ver os próprios valores. `ServiceLogClientSerializer` não ganhou campos monetários | `06-avulso-precificacao.md` |
| **9** | Tela do consolidado mensal (a API e o CSV estão prontos) | `06-avulso-precificacao.md` |
| **9** | Alerta de bolsa não é dispensável, ao contrário do de período | `05-bolsa-por-workitem.md` |
| **9** | Histórico de créditos da bolsa não tem tela | `05-bolsa-por-workitem.md` |
| **A definir** | Bolsa encerrada com 30 dias de carência — exige tarefa periódica | **D35** |

## Se você for fazer a Fase 9 em vez da 7

Ela está liberada: depende de 4, 5 e 6, e as três estão prontas. O que já existe e ela **não
deve reconstruir**:

- `consolidated_billing` entrega o mês por Cliente e por origem, com as quatro origens da
  D36, as pendências separadas em duas listas e o trabalho interno fora da receita;
- `workspace_alert_panel` e `workspace_allowance_alerts` entregam os alertas;
- `contract_balance_statement` entrega o extrato por período;
- a exportação CSV de apontamentos já usa o pipeline existente
  (`type="issue_worklogs"` em `ExporterHistory`, que era uma vaga sem implementação e agora
  tem `service_log_export_task`).

A Fase 9 é, em boa medida, dar tela ao que já existe em API.
