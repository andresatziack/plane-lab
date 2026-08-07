# Próxima sessão: Fase 8 — portal do cliente

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 8**: `docs/worklog/08-portal-do-cliente.md` — o que o usuário do
cliente vê, e o que ele não pode ver nem por payload.

**O modelo de dados ainda NÃO foi apresentado para esta fase.** A §7.1 do contexto mestre
vale integralmente: **apresente o modelo e as decisões, e aguarde OK antes de escrever
código.**

Por que a 8: a Fase 7 acabou de destravá-la, e ela é a única que ainda bloqueia outra coisa
— o dashboard do cliente da Fase 9 depende dela. A 9 também está liberada; se preferir a 9,
leia a seção "Se você for fazer a Fase 9 em vez da 8".

**Leia o aviso da própria Fase 8 antes de planejar:** ela é **a única fase que altera código
do core do Plane**. Todas as outras só adicionam. Isso a torna a de maior atrito em merges
futuros com o upstream e a de maior risco de segurança **se implementada na ordem errada** —
a allowlist de campos precisa vir **antes** de incluir GUEST no `partial_update`.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
   Para esta fase a **R11** (quem vê o quê, e a alínea **(b)**: a restrição é de serializer)
   e a **§2b** (Cliente ancorado em projects) são as partes que mais importam.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as armadilhas.
   **Os scripts já existem. Não os recrie.**
4. **`docs/worklog/08-portal-do-cliente.md`** — o briefing.
5. **`docs/worklog/ACHADOS-DO-CODIGO.md`** — desta vez é obrigatório, não opcional: a Fase 8
   mexe no core, e as seções sobre o bypass `creator=True` e sobre
   `guest_view_all_features` descrevem o código que você vai editar.
6. **`docs/worklog/DECISOES.md`** — a Fase 7 acrescentou **D45 a D47** e **reescreveu a
   D42**. A **D47** fixa a convenção de status code que o portal também deve seguir.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b, 3, 4, 5, 6 e 7 mescladas.**
- Última migração: **0131**. A sua é a **0132**.
- Baseline da suíte: **2139 passando, 0 falhando** — medido com `RECREATE_DB=1` na branch
  da Fase 7. **Meça de novo antes de tocar em nada**, para não herdar regressão alheia
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
  seu trabalho ainda não está commitado, ele é **destruído**. **Commite antes de sabotar** —
  a Fase 7 fez exatamente isso e as seis sabotagens saíram sem susto.
- **Sabotagem que falha pelo motivo errado não prova nada.** Confira **a mensagem** de cada
  falha, não só a cor.
- **`run-api-tests.sh` recebe argumentos do pytest direto**, não por variável de ambiente:
  `./run-api-tests.sh plane/tests/unit/utils/test_x.py -q`. Aceita vários caminhos e
  também `arquivo::Classe::teste`, que é como se verifica uma correção em 4 segundos em vez
  de 16 minutos.
- **A suíte inteira leva ~16 minutos e não emite saída parcial.** Não parece travada, está
  rodando. Rode-a **uma vez, no fim**; para iterar, rode só os arquivos afetados.
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
- Nomes de índice do Django têm **limite de 30 caracteres** (`models.E034`). Constraints
  não têm esse limite; só `indexes`.
- **Uniques parciais do Django viram índices únicos**, não `pg_constraint`. Se você for
  verificar DDL a mão, procure em `pg_indexes`.

---

# O que a Fase 7 mudou e você precisa saber

## Autoridade sobre um apontamento não é mais "só o autor"

`plane.utils.service_permission` é novo e é **o único lugar** que decide quem pode o quê com
apontamento. `resolve_capabilities(user, slug=...)` devolve as capacidades resolvidas; os
guardas (`validate_can_change`, `validate_can_delegate`, `validate_can_reassign`,
`validate_closed_period_access`) consomem esse objeto.

Se o portal do cliente criar qualquer caminho novo de leitura ou escrita de `ServiceLog`,
ele passa por aí. **Não reintroduza uma checagem de autoria à mão** — era o que existia
antes e a Fase 7 a substituiu justamente para não haver duas respostas para a mesma
pergunta.

## `resolve_capabilities` já barra GUEST, e isso importa diretamente para você

Um GUEST resolve para **nenhuma capacidade e `is_member=False`**, mesmo que exista linha de
concessão. Isso é deliberado e é a camada que sustenta a segurança (D45).

**Consequência para a Fase 8, que é a fase do GUEST:** ao estender o GUEST para o portal,
você vai afrouxar `allow_permission` em rotas que hoje o barram. A resolução de capacidades
**não** afrouxa junto — ela continua devolvendo nada. Isso é a rede de proteção que você
quer, mas significa que qualquer coisa que o cliente precise legitimamente fazer terá de ser
um caminho **explícito** e não um efeito colateral de virar membro.

## Existe um modelo de concessão, e ele NÃO é `WorkspaceMember`

`ServiceMemberPermission`, com `can_manage_others`, `can_delegate`,
`can_reassign_author`. Auditado em `ServiceConfigActivity` via `ChangeTrackerMixin`.
Endpoints: `GET`/`PATCH /api/workspaces/<slug>/service-member-permissions/[<member_id>/]`
(ADMIN de workspace) e `GET .../service-member-permissions/me/` (ADMIN e MEMBER).

**Não adicione capacidade de cliente aqui.** Este modelo é sobre técnicos com poderes
elevados; papel de cliente é outra coisa e a Fase 8 tem o seu próprio desenho. Misturar os
dois faria uma tabela responder a duas perguntas.

**Dívida que é sua:** a UI dessas concessões não existe. A API está pronta, e a Fase 8 já vai
mexer na tela de membros do workspace — se couber, feche essa dívida de passagem. O
`.../me/` existe para o formulário de apontamento decidir se mostra o campo "Autor".

## `workspace/member.py` já tem uma alteração da Fase 7 dentro

O `partial_update` revoga as capacidades quando alguém é rebaixado a GUEST, dentro do `if`
que já zerava papéis de project. São ~6 linhas mais comentário. **A Fase 8 vai mexer nesse
mesmo arquivo** — não a apague ao reorganizar, e note que ela é o precedente de como fazer o
toque mínimo no core: dentro de uma condição existente, não como branch nova.

## A convenção de status code está fixada (D47)

> **403 quando o ator não tem a capacidade. 400 quando o payload está errado — inclusive
> quando nomeia alvo inelegível.**

Motivo, e ele é sobre a UI: 403 significa "esconda ou desabilite a ação"; 400 significa
"mostre erro no campo". Devolver 403 para "esse alvo não serve" faz a interface dizer "você
não tem permissão" a quem tem, e o operador vai pedir permissão que já possui.

O portal do cliente vai gerar muitas recusas. Siga a regra.

## A trilha de atividade ganhou dois tipos, e continua sem dinheiro

`service_log.activity.reassigned` e `service_log.activity.delegated`, em
`issue_activities_task.py`. Os payloads são **construídos à mão** com nomes e ids, nunca
reusando um `ServiceLog` serializado.

Isso é a R11(b) de novo: o feed de atividade de um work item é legível por qualquer Member do
project, e a partir da Fase 8 possivelmente pelo cliente. `test_the_activity_trail_carries_no_money`
(Fase 6) e `TestTheActivityTrailCarriesNoMoney` (Fase 7) fixam a ausência.

**Se o portal expuser o feed de atividade ao cliente, isso é uma decisão de visibilidade
nova e precisa do seu próprio teste de ausência.** A R11 esconde do cliente a duração bruta,
as horas apontadas e o multiplicador — e o payload de `service_log.activity.created` carrega
os três.

## Período fechado, e a pergunta que você não deve responder por acidente

Período fechado é **ADMIN-only, sem exceção** (D46), porque o que ele protege é a
reprodutibilidade de um documento já enviado — o consolidado carrega o nome do técnico, e a
R11 diz que o cliente vê o autor.

Um ADMIN que edite **horas** em período fechado ainda recebe `PERIOD_IS_CLOSED`, agora com
`remediation: RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE`. **A D42 foi reescrita como
pergunta:** reabrir período ou lançar ajuste na competência aberta? Contabilidade não edita
período fechado. Não construa a reabertura sem responder a pergunta — é uma cascata.

---

# Coisas das fases anteriores que continuam valendo

- **Um apontamento tem duas rotas.** `applied_billing_route` é a **escolhida**,
  `settled_billing_route` é a **aplicada**, `route_deviation_reason` diz por que diferem
  (D44). Se o portal mostrar ou filtrar rota, ler só a primeira mente.
- **R$ 0,00 é verdade por cinco motivos**, e `PRICING_PENDENCY_FAILURES` /
  `INTERNAL_WORK_FAILURES` (nível de módulo em `service_log.py`) dizem quais são quais (D43).
- **Dinheiro é ADMIN de workspace, e a barreira é de serializer.**
  `ServiceLogSerializer.AMOUNT_FIELDS` são removidas quando `context["can_see_amounts"]` é
  falso, e **o default é falso**. `ServiceLogClientSerializer` existe e **não** tem campos
  monetários — a dívida nomeada de "cliente avulso ver os próprios valores" é sua.
- **`ServiceLog.delete()` foi sobrescrito** e estorna o débito antes de apagar. A Fase 7 não
  criou porta nova: a reatribuição não apaga nada. Se o portal criar uma, ela passa por
  `delete()`.
- **O livro-caixa é o único lugar onde saldo se move** (D23, D29). A reatribuição da Fase 7
  não escreve linha nenhuma, e há teste afirmando isso contra um pool com débito real.
- **Qualquer criação de `ServiceLog` fora de `build_batch_rows`** precisa setar
  `settled_billing_route` **e** `route_deviation_reason` juntos, ou
  `service_log_route_deviation_is_coherent` recusa a linha. As três colunas são um
  biconditional.

## Verificação por sabotagem: continue fazendo

Cinco fases seguidas encontraram algo com isso. A Fase 7 usou seis sabotagens e uma delas
**confirmou empiricamente uma decisão de desenho**: ao fazer a reatribuição reconstruir o
batch, ela passou a falhar em período fechado — que era exatamente o argumento para ela
alterar `author` in place.

Para a Fase 8, os pontos onde a permissão vaza:

| Sabotagem | Tem de quebrar |
| --- | --- |
| serializar duração bruta, horas apontadas ou multiplicador para o cliente | R11(b) |
| incluir GUEST no `partial_update` **antes** da allowlist de campos | o critério 16 da Fase 8 |
| deixar um GUEST de um Cliente ver work item de outro Cliente | D19 |
| expor valor em R$ a cliente de contrato | R11 |
| deixar o feed de atividade vazar o que o endpoint esconde | R11(b), e é o vazamento que a Fase 6 já pegou uma vez |

E **todo teste de ausência afirma o código do motivo, com controle positivo na mesma
fixture.**

## Dívidas nomeadas em aberto, por fase que as herda

| Fase | Dívida | Onde está registrada |
| --- | --- | --- |
| **8** | Cliente avulso ver os próprios valores. `ServiceLogClientSerializer` não tem campos monetários | `06-avulso-precificacao.md` |
| **8** | UI das concessões da Fase 7 e do campo "Autor" no formulário. API pronta | `07-permissoes-delegacao.md` |
| **9** | Tela do consolidado mensal (API e CSV prontos) | `06-avulso-precificacao.md` |
| **9** | Alerta de bolsa não é dispensável, ao contrário do de período | `05-bolsa-por-workitem.md` |
| **9** | Histórico de créditos da bolsa não tem tela | `05-bolsa-por-workitem.md` |
| **A definir** | Bolsa encerrada com 30 dias de carência — exige tarefa periódica | **D35** |
| **A definir** | **Pergunta** da D42: reabrir período ou lançar ajuste na competência aberta | **D42**, reescrita |

## Se você for fazer a Fase 9 em vez da 8

Ela está liberada. O que já existe e ela **não deve reconstruir**:

- `consolidated_billing` entrega o mês por Cliente e por origem, com as quatro origens da
  D36, as pendências separadas em duas listas e o trabalho interno fora da receita;
- `workspace_alert_panel` e `workspace_allowance_alerts` entregam os alertas;
- `contract_balance_statement` entrega o extrato por período;
- a exportação CSV de apontamentos já usa o pipeline existente
  (`type="issue_worklogs"` em `ExporterHistory`, com `service_log_export_task`).

A Fase 9 é, em boa medida, dar tela ao que já existe em API. Note que ela **depende** do
portal para o dashboard do cliente, então fazer a 9 antes da 8 deixa essa parte pendente.
