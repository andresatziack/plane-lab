# Próxima sessão: Fase 6 — Avulso, precificação em R$ e excedente

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 6**: `docs/worklog/06-avulso-precificacao.md` — converter apontamentos
em valor monetário, e faturar o excedente que a Fase 4 gravou em horas.

**O modelo de dados ainda NÃO foi apresentado para esta fase.** A §7.1 do contexto mestre
vale integralmente: **apresente o modelo e as decisões, e aguarde OK antes de escrever
código.**

O briefing pede "modelo de dados e desenho da **vigência de preços** primeiro". Essa é a
decisão central da fase, e é a primeira vez nesta série que uma configuração precisa ser
versionada no tempo em vez de apenas auditada — leia a seção "Onde as fases anteriores já
prepararam o terreno" antes de propor qualquer coisa.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
   A **§4b** é a parte que esta fase mais usa: escala monetária `(12, 2)`, `ROUND_HALF_UP`,
   e a regra de que **o arredondamento acontece uma vez por apontamento, nunca no total**.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as quatro armadilhas.
   **Os scripts já existem no repositório. Não os recrie.**
4. **`docs/worklog/06-avulso-precificacao.md`** — o briefing, com 13 critérios de aceite.
5. **`docs/worklog/DECISOES.md`** — a **D6** é a espinha desta fase (preço = valor/hora base
   × multiplicador, com override absoluto opcional). Leia também a **D20** (existe
   exatamente um mecanismo para "não cobrar") e a **D22**, que avisa o preço que uma coluna
   **nullable** rastreada cobra da trilha de auditoria — o override opcional é o caso
   concreto que ela previu.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b, 3, 4 e 5 mescladas.** PRs #1 a #12 fechados, **nenhum PR aberto**.
- Última migração: **0129**. A sua é a **0130**.
- Baseline da suíte: **1913 passando, 0 falhando** — medido com `RECREATE_DB=1` na branch
  da Fase 5 antes do merge. Meça de novo antes de tocar em nada, para não herdar regressão
  alheia como se fosse sua.
- **Não existe CI.** As checagens locais são a única verificação.
- `ruff` **não vem instalado** no venv do sandbox. Instale com
  `/projects/sandbox/.plane-agent-sandbox/venv-api/bin/python -m pip install ruff`. Há
  **3 erros F401 pré-existentes** na `preview` (`app/views/issue/sub_issue.py` ×2,
  `app/views/project/invite.py`) — não são seus, e corrigi-los produz churn alheio.

### Armadilhas do sandbox, e uma que custou caro na Fase 5

- **`git checkout -- <arquivo>` restaura para o HEAD, não para o seu último estado.** Se o
  seu trabalho ainda não está commitado, ele é **destruído**. Na Fase 5 isso apagou um
  arquivo inteiro de domínio no meio da verificação por sabotagem, e ele teve de ser
  reescrito. **Commite antes de sabotar** — aí o `git checkout --` faz exatamente o que se
  espera. O README dos scripts avisa sobre não encadear a verificação com `&&`; isto é uma
  armadilha diferente e pior.
- **Sabotagem que falha pelo motivo errado não prova nada.** A primeira tentativa de uma das
  sabotagens da Fase 5 declarou um `from ... import X` dentro de uma função que já usava `X`
  do escopo do módulo, e o teste quebrou com `UnboundLocalError` antes de chegar ao código
  sob teste. Confira **a mensagem** de cada falha, não só a cor.
- **`/tmp` não persiste entre invocações de shell.** Rascunhos vão em `/projects/sandbox/`,
  fora do repositório, e são apagados depois.
- **Os serviços nativos são colhidos entre invocações de shell.** Um `psql` numa chamada não
  encontra o Postgres que a chamada anterior subiu — rode `./start-test-services.sh` na
  **mesma** invocação. `psql` está em `/usr/bin/psql`.
- **`gh` tem de rodar de `/projects/sandbox`**, não de dentro de `plane-lab`: o `.mise.toml`
  não é _trusted_ e o `mise` aborta o comando.
- **O hook de pre-commit precisa de `pnpm` no PATH.** Antes de commitar algo do frontend:
  `export PATH="$(ls -d /root/.nvm/versions/node/* | tail -1)/bin:$PATH"` e
  `corepack enable pnpm`. Sem isso o commit falha com `pnpm: command not found`.
- O hook roda `oxlint --deny-warnings` nos arquivos em stage e **falha em qualquer
  advertência**, inclusive uma que já existia. Linte seus caminhos antes de dar `git add`.
- Nomes de índice do Django têm **limite de 30 caracteres** (`models.E034`).

---

# Onde as fases anteriores já prepararam o terreno para você

## O campo que a Fase 4 gravou sem consumidor

`ServiceContract.overage_hour_rate` existe, é auditado, tem escala monetária `(12, 2)` e
**nada o lê**. Foi gravado então porque é _termo do contrato_: o momento em que um contrato é
cadastrado é o momento em que o valor é conhecido, e adicionar coluna auditada depois
significa back-fill de trilha de auditoria que não se faz honestamente.

A §6 do seu briefing é o consumidor dele, com fallback para o valor/hora base do Cliente —
que **não existe ainda e é seu**. A Fase 4 deliberadamente não criou um valor/hora em
`ServiceClient` para não invadir esta fase.

## O excedente já está em horas, e há um jeito de cobrar em dobro

`ServiceContractPeriod.overage_hours` e `ServiceIssueAllowance.overage_hours` guardam
excedente **em horas**, e as duas são preenchidas no fechamento com uma linha
`OVERAGE_BILLED` no livro-caixa.

**Essas horas já são equivalentes**, ou seja, já levaram o multiplicador do Tipo de Hora.
Aplicar o multiplicador outra vez na precificação cobra em dobro — o briefing chama isso de
ponto de atenção e o critério 8 existe só para isso. O valor/hora de excedente incide sobre
as horas equivalentes já calculadas.

Note que **existem duas fontes de excedente agora**, não uma: o período de contrato e a
bolsa de horas do chamado, que a Fase 5 entregou. O consolidado da §5 pede três origens de
receita separadas; a bolsa é uma quarta pergunta que o briefing não fez, e você vai precisar
decidir se o excedente de bolsa aparece como excedente de contrato, como receita de projeto,
ou como origem própria. **É cláusula comercial, não escolha de implementação — pergunte.**

## O livro-caixa é onde o dinheiro se move, e ele já tem forma

A **D23** e a **D29** valem sem alteração para esta fase. Se qualquer coisa aqui mover saldo
de horas, tem de passar por `plane.utils.service_pool.write_ledger_entry`, que é o único
lugar que cria linha — e o único que sabe que uma linha aponta para um período **ou** para
uma bolsa, nunca para os dois.

Um `update()` direto em `consumed_hours`, `credited_hours` ou `overage_hours` reabre a classe
de bug que `reconcile_service_periods` existe para consertar.

**Se você precisar de um journal de dinheiro**, e provavelmente vai, leia a D29 antes de
criar um segundo: o argumento que decidiu a Fase 5 foi que reusar o journal existente
transformou um critério de aceite numa recusa do banco. Ver se o mesmo se aplica a valores em
R$ é a primeira pergunta do seu desenho, não a última.

## Onde o código está

| Camada  | Arquivo                                                                                  |
| ------- | ---------------------------------------------------------------------------------------- |
| Modelos | `plane/db/models/service_contract.py`, `service_issue_allowance.py`, `service_client.py` |
| Domínio | `plane/utils/service_pool.py`, `service_allowance.py`, `service_log_time.py`             |
| Alertas | `plane/utils/service_pool_alerts.py`                                                     |
| API     | `plane/app/{serializers,views,urls}/service_*`                                           |
| Reparo  | `plane/db/management/commands/reconcile_service_periods.py`                              |

`plane/utils/service_log_time.py` **não importa Django** de propósito, e é onde toda
aritmética pura de hora vive (incluindo `quantize_hours`, que a Fase 5 moveu para lá quando
os dois domínios precisaram do mesmo quantizador). Se a sua aritmética monetária for pura,
ela pertence a um módulo com essa mesma propriedade.

## Coisas das fases anteriores que vão te morder

- **A rota de faturamento é snapshot no apontamento** (`applied_billing_route`), não uma
  consulta ao catálogo. É isso que faz o critério 6 — reajustar o valor base não altera
  apontamento existente — ser possível. Sua precificação tem de snapshotar do mesmo jeito.
- **`ServiceLog.debited_period` e `debited_allowance` são mutuamente exclusivos por
  constraint**, e um apontamento não faturável não pode reivindicar nenhum dos dois. Se você
  adicionar coluna de valor, decida a relação dela com essas duas antes de migrar.
- **Rota `NON_BILLABLE` tem `debited_hours = 0` por check constraint**, mas
  `equivalent_hours` continua calculado (R11a). Valor R$ 0,00 sai da rota, não de um `if`.
- **`ServiceLog.delete()` foi sobrescrito** e estorna o débito antes de apagar, porque um
  apontamento é excluído por quatro portas. Se valor em R$ passar a exigir estorno, ele
  pertence ao mesmo lugar.

## Verificação por sabotagem: continue fazendo

Três fases seguidas encontraram algo com isso. A Fase 4 descobriu um falso-verde no próprio
teste de concorrência. A Fase 5 confirmou por sabotagem que o débito duplo é recusado pelo
**banco** e não por um `if` — o que virou o argumento da D29 — e que a constraint nova do
critério 9 pega um caminho que os testes de comportamento não pegavam.

Os pontos onde o dinheiro vaza nesta fase, para começar a lista:

| Sabotagem                                                | Tem de quebrar |
| -------------------------------------------------------- | -------------- |
| aplicar o multiplicador de novo no excedente             | critério 8     |
| arredondar no total em vez de por apontamento            | critério 13    |
| ler o preço do cliente em vez do snapshot do apontamento | critério 6     |
| ignorar o override absoluto                              | critério 5     |
| permitir faturar o excedente duas vezes                  | critério 9     |

E **todo teste de ausência afirma o código do motivo, com controle positivo na mesma
fixture**: "valor zero" é verdade para Garantia, para preço não cadastrado e para um motor de
precificação nunca ligado, e esses são bugs diferentes.

---

# A dívida de processo: agora ela bloqueia

As Fases 4 e 5 responderam **oito** ambiguidades com defaults razoáveis e testes de
caracterização. Todas são **cláusulas contratuais**, não decisões de software, e as respostas
estão nos contratos reais com os clientes.

Isto deixou de ser recomendação: **a Fase 6 é a fase do dinheiro em R$**, e várias destas
mudam o valor de uma fatura.

| Ambiguidade                            | O que foi assumido                                                 |
| -------------------------------------- | ------------------------------------------------------------------ |
| Pro-rata do primeiro mês               | Nenhum. `contracted_hours` é editável em período aberto (D26)      |
| Teto de déficit                        | Não existe teto; só alerta, com re-disparo a cada 5h de piora      |
| O que "suspenso" significa             | Nada além de um código de alerta distinto                          |
| Ordem de descarte pelo teto de acúmulo | Descarta as parcelas mais novas (D25)                              |
| Bloquear apontamento sem contrato      | Não bloqueia (D27)                                                 |
| Bolsa herdada por sub-tarefa           | Herda, ancestral mais próximo primeiro, limite de 10 (D28)         |
| Pai e filho ambos com bolsa            | O filho vence                                                      |
| Saldo positivo de bolsa encerrada      | Baixado e perdido; **nunca** vai para o pool do contrato (§3, D30) |

Extraí-las para o `DECISOES.md` com a resposta real antes de desenhar a precificação evita
que a fase pergunte as mesmas coisas de novo — e evita defaults razoáveis sendo propostos
para regras que já existem em papel.

## Depois da Fase 6

Ordem do `README.md`: **9** (dashboards, depende de 4, 5 e 6), **8** (portal do cliente).

A **Fase 7** (permissões e delegação) depende só da 3 e está desbloqueada há três fases —
pode ser feita em paralelo, e **também não teve o passo de aprovação de modelo**. A seção de
delegação dela fala de reaplicação de débito: qualquer caminho novo que mexa em
`consumed_hours` ou `credited_hours` **tem** de passar pelos módulos de domínio.

A **Fase 9** herdou duas dívidas nomeadas da Fase 5, e elas estão registradas em
`05-bolsa-por-workitem.md`, não escondidas em comentário de código:

- **alerta de bolsa não é dispensável**, ao contrário do alerta de período —
  `ServiceContractAlertDismissal` tem FK obrigatória para período;
- **o histórico de créditos da bolsa não tem tela.** A API já o devolve em `credits`, com
  autor, data e a competência de origem quando as horas vieram de um contrato encerrado; a
  §3b da Fase 9 já prevê "bolsas de horas ativas por chamado, com saldo de cada", que é onde
  ele pertence.
