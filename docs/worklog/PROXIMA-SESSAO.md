# Próxima sessão: Fase 5 — Bolsa de horas por work item

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 5**: `docs/worklog/05-bolsa-por-workitem.md` — creditar um pool de
horas num work item específico, isolado do contrato de suporte do cliente.

**O modelo de dados ainda NÃO foi apresentado para esta fase.** A §7.1 do contexto mestre
vale integralmente: **apresente o modelo e as decisões, e aguarde OK antes de escrever
código.** As fases 1 a 4 tiveram esse passo cumprido em sessões anteriores; esta não teve.

O briefing pede explicitamente "modelo de dados primeiro, com atenção especial a como a
precedência de origem é resolvida e persistida". Essa é a decisão central da fase — leia a
seção "Onde a Fase 4 já preparou o terreno" abaixo antes de propor qualquer coisa, porque
metade dela já está escrita e a outra metade tem um lugar reservado.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as quatro armadilhas.
   **Os scripts já existem no repositório. Não os recrie.**
4. **`docs/worklog/05-bolsa-por-workitem.md`** — o briefing, com 9 critérios de aceite
   **mais um critério herdado da Fase 4** que só pode ser fechado aqui.
5. **`docs/worklog/DECISOES.md`** — a **R6 é a espinha desta fase**, então leia
   **D23 a D27** (as da Fase 4) inteiras, e a **D20** (existe exatamente um mecanismo para
   "não cobrar"). A **D27** está adotada mas é revisável; ver o aviso ao fim deste arquivo.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b, 3 e 4 mescladas.** PRs #1 a #10 fechados, **nenhum PR aberto**.
- Última migração: **0128**. A sua é a **0129**.
- Baseline da suíte: **1824 passando, 0 falhando** — medido com `RECREATE_DB=1` **na
  `preview` já mesclada**. Meça de novo antes de tocar em nada, para não herdar regressão
  alheia como se fosse sua.
- **Não existe CI.** As checagens locais são a única verificação.
- `ruff` **não vem instalado** no venv do sandbox. Instale com
  `/projects/sandbox/.plane-agent-sandbox/venv-api/bin/python -m pip install ruff` antes de
  rodar `ruff check apps/api`. Há **3 erros F401 pré-existentes** na `preview`
  (`app/views/issue/sub_issue.py` ×2, `app/views/project/invite.py`) — não são seus, e
  corrigi-los produz churn alheio à mudança.

### Armadilhas do sandbox que custaram tempo na Fase 4

- **`/tmp` não persiste entre invocações de shell.** Escreva arquivos de rascunho (corpo de
  PR, por exemplo) em `/projects/sandbox/`, fora do repositório, e apague depois.
- **`gh` tem de rodar de `/projects/sandbox`, não de dentro de `plane-lab`**: o
  `.mise.toml` não é *trusted* e o `mise` aborta o comando.
- **`git checkout --` não restaura arquivo novo** (untracked). Depois de sabotar um arquivo
  criado pela sua própria fase, restaure com `sed` e **verifique num comando separado** —
  encadear com `&&` é a armadilha que o README dos scripts descreve.
- Nomes de índice do Django têm **limite de 30 caracteres** (`models.E034`).

---

# Onde a Fase 4 já preparou o terreno para você

Leia isto antes de desenhar o modelo. **Metade da Fase 5 já está escrita**, e propor um
desenho que a ignore vai colidir com invariantes que já têm teste.

## Os dois invariantes da Fase 4 que a Fase 5 não pode quebrar

1. **O período carrega o saldo; o livro-caixa carrega o histórico** (D23). O período tem os
   totais e é a linha que `select_for_update()` tranca; o livro-caixa append-only tem os
   movimentos; os dois são escritos **na mesma transação, sempre**.
2. **Nenhum saldo se move sem uma linha.** Um período **fechado soma exatamente zero** no
   livro-caixa. `reconcile_period` verifica coluna por coluna.

**Decisão de arquitetura que você tem de tomar e justificar:** a bolsa reusa
`ServiceHourLedgerEntry` ou ganha o seu próprio livro-caixa? Os dois caminhos são
defensáveis e o briefing não decide. Reusar mantém um só lugar onde horas se movem, mas o
livro-caixa atual tem FK obrigatória para `contract` e `period`. Um segundo journal duplica
o mecanismo. **Apresente a escolha com o custo de cada lado**; não a tome no meio do
código.

## O ponto de extensão da R6, pronto e vazio

`plane.utils.service_pool._work_item_allowance(issue)` devolve `None` hoje e é **a primeira
coisa que `apply_debit` consulta**. É função nomeada em vez de comentário justamente porque
a R6 é uma **hierarquia** — bolsa primeiro, depois o pool do contrato, depois valor em R$, e
nunca dois.

Você substitui o corpo. **Nada mais no motor de débito precisa mudar**, e se você estiver
alterando `apply_debit` além disso, pare e releia: provavelmente está criando o débito
parcial nos dois que o critério 7 proíbe.

## O critério herdado, e o que "fechar" exige

O **critério 17 da Fase 4** está parcialmente atendido: transferir e expirar o saldo
remanescente de um contrato encerrado funcionam e são auditados; **converter em bolsa de
horas de um chamado depende da entidade desta fase**. Já existe:

- o tipo de lançamento `CONVERTED_TO_ISSUE_ALLOWANCE` no livro-caixa;
- `end_and_create_successor(..., balance_destination="issue_allowance", target_issue=...)`;
- hoje devolve `ISSUE_ALLOWANCE_NOT_AVAILABLE` com **HTTP 501**, não um no-op silencioso.

Os três passos para fechá-lo estão em `05-bolsa-por-workitem.md`, seção "Critérios herdados
da Fase 4". O terceiro é fácil de esquecer: **remover as duas asserções de indisponibilidade
que hoje fixam o comportamento** e marcar o critério 17 da Fase 4 como fechado apontando
para o teste novo.

## Onde o código da Fase 4 está

| Camada    | Arquivo                                                        |
| --------- | -------------------------------------------------------------- |
| Modelos   | `plane/db/models/service_contract.py`                          |
| Domínio   | `plane/utils/service_pool.py`                                  |
| Alertas   | `plane/utils/service_pool_alerts.py`                           |
| API       | `plane/app/{serializers,views,urls}/service_contract*`          |
| Reparo    | `plane/db/management/commands/reconcile_service_periods.py`     |

## Coisas da Fase 4 que vão te morder se você não souber

- **`ServiceLog.delete()` foi sobrescrito** e estorna o débito antes de apagar. O estorno
  mora ali porque um apontamento é excluído por **quatro portas** —
  `delete_service_log_batch`, `replace_service_log_batch` (que é uma *edição*), o endpoint de
  batch, e a cascata do work item. O critério 6 desta fase ("excluir apontamento devolve as
  horas à bolsa, não ao contrato") passa por esse mesmo método: **a decisão de para onde
  devolver tem de sair do que foi persistido no débito, nunca de re-resolver a origem**, que
  é o mesmo raciocínio que faz `reverse_debit` ler a linha `DEBIT` em vez de recalcular.
- O estorno é **recusado em período fechado**, e nesse caso o apontamento fica **intacto**
  em vez de apagado. Decida o equivalente para bolsa encerrada.
- **`ServiceLog.debited_period`** é o snapshot de qual pool pagou. O critério 3 desta fase
  pede o mesmo para a bolsa. **Não** transforme isso em duas colunas que podem ambas estar
  preenchidas: o critério 7 proíbe débito parcial nos dois, e um estado não representável é
  melhor que um estado recusado por validação que alguém esquece de chamar (mesmo raciocínio
  da D5).
- Uma rota `DEBIT_POOL` **agora consulta contrato**. Um cliente com dois contratos e nenhum
  `is_default` recusa apontamentos; o painel de alertas expõe isso em
  `clients_without_default_contract`.

## Verificação por sabotagem: a lição da Fase 4

A Fase 4 sabotou os cinco pontos onde o dinheiro vaza, e **a sabotagem encontrou um
falso-verde no próprio teste de concorrência da fase**. O design previa que
`select_for_update` e o `F()` fossem "duas metades de uma garantia" sabotáveis
independentemente. São *independentemente suficientes*: o teste passava com **cada uma**
removida, e só ficava vermelho com as duas fora.

Faça o mesmo aqui. Os pontos onde o dinheiro vaza nesta fase:

| Sabotagem                                              | Tem de quebrar                          |
| ------------------------------------------------------ | --------------------------------------- |
| fazer `_work_item_allowance` devolver `None` sempre     | critérios 2 e 7 (debitaria o contrato)  |
| remover a precedência (debitar os dois)                 | critério 7                              |
| devolver o estorno ao contrato em vez da bolsa          | critério 6                              |
| ignorar o multiplicador no débito da bolsa              | critério 8                              |
| deixar `NON_BILLABLE` consumir bolsa                    | critério 9                              |

E **todo teste de ausência afirma o código do motivo, com controle positivo na mesma
fixture**: "o contrato não se moveu" é verdade quando a bolsa funcionou *e* quando o motor
de débito nunca rodou, e esses são bugs opostos.

---

# Uma decisão adotada, ainda revisável: a D27

A **D27** foi adotada na prática pelo merge do PR #10, mas nunca recebeu confirmação
explícita, e é **cláusula contratual, não escolha de implementação**. Ela está registrada
inteira no `DECISOES.md`; em resumo:

O critério 24 da Fase 4 manda uma resolução de contrato "ambígua **ou vazia**" falhar.
Implementado: **ambiguidade bloqueia; resolução vazia não bloqueia** — o apontamento fica
gravado com `debited_period` nulo e o motivo é reportado pelo painel do chamado. O motivo é
que a justificativa do próprio critério 24 ("debitar o pool errado é pior que bloquear") só
vale quando existe um pool errado, e a §4, a D4 e a D9 proíbem descartar trabalho já
executado por pendência comercial.

**Ponto único de mudança se a leitura for a literal:**
`NON_BLOCKING_RESOLUTION_FAILURES` em `plane/utils/service_pool.py`.

**Isso é relevante para a Fase 5** porque o critério 3 do briefing desta fase pede o mesmo
tipo de decisão: um apontamento cuja origem não se resolve. Seja coerente com a D27 ou
proponha mudar as duas juntas — o que não pode é a bolsa e o contrato responderem
diferente à mesma pergunta.

## Depois da Fase 5

Ordem do `README.md`: **6** (precificação, depende de 3 e 4 — e consome o
`overage_hour_rate` que a Fase 4 gravou sem consumidor), **9** (dashboards, depende de 4, 5
e 6), **8** (portal do cliente).

A **Fase 7** (permissões e delegação) depende só da 3 e está desbloqueada há duas fases —
pode ser feita em paralelo, e **também não teve o passo de aprovação de modelo**. A seção de
delegação dela fala de reaplicação de débito: qualquer caminho novo que mexa em
`consumed_hours` **tem** de passar por `plane.utils.service_pool`; um `update()` direto
reabre a classe de bug que `reconcile_service_periods` existe para consertar.

## A dívida de processo, ainda em aberto, para resolver antes da Fase 6

A Fase 4 respondeu cinco ambiguidades com defaults razoáveis e testes de caracterização, mas
todas são **cláusulas contratuais**, não decisões de software, e as respostas estão nos
contratos reais com os clientes:

| Ambiguidade                                | O que a Fase 4 assumiu                                                       |
| ------------------------------------------ | ---------------------------------------------------------------------------- |
| Pro-rata do primeiro mês                   | Nenhum. `contracted_hours` é editável em período aberto (D26)                 |
| Teto de déficit                            | Não existe teto; só alerta, com re-disparo a cada 5h de piora                 |
| O que "suspenso" significa                 | Nada além de um código de alerta distinto                                     |
| Ordem de descarte pelo teto de acúmulo     | Descarta as parcelas mais novas (D25)                                        |
| Bloquear apontamento sem contrato          | Não bloqueia (D27)                                                           |

Extraí-las para o `DECISOES.md` com a resposta real antes da Fase 6 evita que a fase de
precificação pergunte as mesmas coisas de novo — e evita defaults razoáveis sendo propostos
para regras que já existem em papel.
