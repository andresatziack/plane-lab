# Fechamento da série — não existe próxima fase

A Fase 8 era a última do núcleo. **Este documento não aponta para uma fase seguinte.** Ele
registra o que as nove fases entregaram, o que existe em API sem tela, as duas perguntas que
continuam em aberto, as melhorias que nunca tiveram prompt escrito, e as dívidas que a própria
Fase 8 deixou.

Se você abrir uma sessão nova a partir daqui, ela não tem um roteiro. Tem um inventário.

Leitura obrigatória antes de qualquer coisa continua a mesma: `.kiro/steering/worklog-contexto.md`
(contexto mestre, com as regras R1 a R11), `DECISOES.md` (D1 a D66), `ACHADOS-DO-CODIGO.md`
(inclui a seção 15, que é informação de operador e não tarefa) e `CONVENCOES-DE-TRABALHO.md`.

---

## 1. Estado final

Nove fases mescladas: 1, 2, 2b, 3, 4, 5, 6, 7, 9 e 8.

| Fase | O que entregou                                                                                                               | Migração  |
| ---- | ---------------------------------------------------------------------------------------------------------------------------- | --------- |
| 1    | Cliente (`ServiceClient`), vínculo Project → Cliente, grupo econômico sem comportamento (D18)                                | 0120-0122 |
| 2    | Catálogos configuráveis: Tipo de Hora com multiplicador, Tipo de Atendimento com rota de faturamento, trilha de configuração | 0123-0125 |
| 2b   | Calendário de feriados, janelas de classificação, classificação automática de Tipo de Hora                                   | 0126-0127 |
| 3    | O apontamento: quatro grandezas de hora, lote por submissão, segmentação por janela, trilha R8                               | 0128      |
| 4    | Contrato, período de competência, bolsa de horas, acúmulo, teto, déficit, excedente                                          | 0128      |
| 5    | Bolsa por work item, concessão avulsa, alertas                                                                               | 0129      |
| 6    | Precificação, vigência de preços, rota liquidada (D33/D44), consolidado de faturamento, exportação                           | 0130      |
| 7    | Três capacidades elevadas fora do core (D45), delegação, reatribuição de autor                                               | 0131      |
| 9    | Dashboards de consumo, agregação em SQL, um descritor de filtro (D50), projeção por papel (D51)                              | 0132      |
| 8    | Portal do cliente: allowlist de campos, correção do feed, projeções do cliente, solicitante, varredura de escopo             | 0133      |

Suíte ao fim: **2495 passando, 0 falhando** (`RECREATE_DB=1`). A linha de base medida no
início da Fase 8 era 2273.

### O que a Fase 8 acrescentou de estrutural, além dos critérios

- **A allowlist de campos do cliente** vive em `plane/utils/service_portal.py`, não na view.
  É o módulo que qualquer fase futura deve editar para mudar o que o cliente pode alterar.
- **`client_may_reach_issue` é a única definição** de "este cliente alcança este work item",
  usada em seis lugares. O core fazia essa pergunta em três, com três grafias.
- **A varredura de escopo enumera o roteador do Django** (D66), então uma rota nova é negada
  por padrão. Isso é reutilizável e é o que encontrou dois bugs.

---

## 2. O que existe em API e não tem tela

Ordem de utilidade, não de esforço.

| Superfície                                                          | Estado                                                                                               | Onde                                                   |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **Dashboard do portal do cliente**                                  | API completa e testada, **nenhuma tela**                                                             | `GET /service-reports/portal/`                         |
| **Concessões da Fase 7**                                            | API completa, nenhum serviço no frontend, nenhuma UI                                                 | `service-member-permissions/`, `/me/`, `/<member_id>/` |
| **Solicitante do chamado**                                          | API completa (GET/POST/DELETE), nenhum controle no formulário                                        | `.../issues/<id>/service-requester/`                   |
| Sugestão de `guest_view_all_features` ao vincular Project a Cliente | O endpoint aceita a flag e **deliberadamente nunca a infere** — a decisão é da UI, que não a oferece | `service-clients/<pk>/assign-projects/`                |

As três primeiras são dívidas nomeadas na seção 5. A quarta é da Fase 1 e está no docstring de
`assign_projects` desde então: _"the master context requires that turning on
guest_view_all_features be suggested to the admin rather than done silently, so the decision
stays with the UI and this endpoint never infers it."_ Hoje ninguém sugere, então um admin que
vincula um Project a um Cliente não descobre que precisa ligar a flag para o portal funcionar.

---

## 3. As duas perguntas em aberto

Estas estavam como "A definir" nas tabelas de dívida das fases anteriores. **Elas não são
tarefas.** São decisões de negócio que ninguém tomou, e vão desaparecer do registro se ninguém
as adotar — que é a razão de estarem aqui em vez de numa tabela de pendências.

### 3.1 — D35: a carência de 30 dias da bolsa

**Já decidida, e a decisão é "ação humana".** Registrada aqui porque o registro anterior dizia
"exige tarefa periódica", e isso está errado desde a revisão da Fase 9.

A bolsa por work item tem 30 dias de carência contados do fechamento do chamado. A leitura
original disso exigiria uma varredura periódica que fecha bolsas vencidas. A Fase 9 recusou
esse desenho, e o motivo está na revisão da D35: aquela varredura teria feito do módulo de
alertas **o primeiro escritor não atendido do livro-caixa** — um processo automático movendo
saldo sem ninguém pedindo. Existe teste que mantém o módulo read-only
(`test_nothing_in_this_module_writes_a_ledger_entry`).

O que existe no lugar: `ALLOWANCE_PENDING_CLOSURE` e `pending_closure_q`, que separam bolsas
"ativas" de bolsas **aguardando decisão**, e o painel de atenção mostra as duas listas
separadas. Um humano vê "esta bolsa venceu a carência" e decide.

**A pergunta que sobra, e é pequena:** ninguém definiu _quem_ olha esse painel e com que
frequência. Não é código. Se a resposta for "ninguém", a lista cresce até parar de ser lida, e
aí a decisão de fato terá sido tomada por omissão — o que é exatamente o que a revisão da D35
tentou evitar. **Adotar isso é escolher um responsável, não escrever uma tarefa.**

### 3.2 — D42: reabrir período ou lançar ajuste na competência aberta

**Genuinamente em aberto, e genuinamente não é tarefa.**

Não existe estorno de excedente faturado (D42). A pergunta é o que fazer quando um excedente
foi faturado e depois se descobre que estava errado. Duas saídas, e elas são incompatíveis:

1. **Reabrir o período fechado** e corrigir na competência de origem.
2. **Lançar um ajuste na competência aberta**, deixando o período fechado intacto.

O que trava a decisão não é técnico. É que **contabilidade não edita período fechado.** Se a
sua operação segue essa regra — e a maioria segue —, a opção 1 está descartada e a resposta é a 2. Mas isso tem consequência de produto: o extrato do cliente vai mostrar um ajuste num mês que
não é o mês do trabalho, e alguém vai ter que explicar isso ao cliente.

O código hoje **não implementa nenhuma das duas**, e a D42 documenta isso como dívida nomeada
em vez de escolher por conta própria. O comportamento atual é: `PERIOD_IS_CLOSED` recusa a
escrita, com `remediation: RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE` — ou seja, a API já
_sugere_ a opção 2 sem a implementar como fluxo.

**Adotar isso é uma conversa com quem responde pela contabilidade**, e o resultado dela é uma
decisão nova na `DECISOES.md`. Só depois existe tarefa.

---

## 4. Melhorias do roadmap que nunca tiveram prompt escrito

Estão em `OPORTUNIDADES.md` com mais detalhe. Aqui está o que importa para decidir: **o que já
existe de código**, porque isso muda a estimativa por um fator grande.

### 4.1 — Timesheet semanal (`OPORTUNIDADES.md` B1) ⭐

**Código existente: tudo o que importa, exceto a tela.**

Hoje todo apontamento acontece dentro do work item. Um técnico que atende 15 chamados por dia
abre 15 telas. O que falta é uma tela "Meus apontamentos" com grade semanal.

Já existe: o modelo `ServiceLog` completo, o endpoint de criação em lote, o de preview
(valida e classifica sem persistir), `ServiceLogFilterSet` (D50) que já filtra por autor e por
período, e `ServiceLogSerializer`. Uma grade semanal é uma tela sobre APIs que existem.

**Por que é a maior lacuna:** todo o faturamento depende da qualidade do dado de apontamento, e
a diferença entre apontar no fim do dia e chutar na sexta é a diferença entre uma fatura
defensável e uma discussão. Isso é requisito, não conforto.

### 4.2 — Work Item Types (`OPORTUNIDADES.md` A1) ⭐

**Código existente: modelos completos, FK ligada, flag existente, frontend parcialmente
cabeado. Falta CRUD e tela.**

`IssueType` e `ProjectIssueType` estão modelados. `Issue.type` FK já existe.
`Project.is_issue_type_enabled` existe e está morta. O serializer da API pública já referencia,
e há referências espalhadas em mais de dez arquivos do frontend.

**Por que interage com faturamento:** cada tipo de chamado — Incidente, Requisição, Mudança,
Problema — pode ter Tipo de Atendimento padrão diferente. É um eixo que o escopo atual não tem.
`is_epic` no modelo indica que a mesma entidade serve de base para Epics.

**Melhor relação esforço/retorno da lista**, e é pré-requisito prático do SLA abaixo.

### 4.3 — SLA de atendimento (`OPORTUNIDADES.md` B3)

**Código existente: metade, e é a metade difícil.**

O escopo atual tem zero SLA. O mínimo útil é prazo de primeira resposta e de solução por
prioridade e por tipo de chamado, com relógio que respeita o expediente.

E é aí que está a sinergia: **as janelas de classificação da Fase 2b são exatamente o
calendário que um SLA precisa** para não contar horas fora do expediente. Feriados,
recorrência, janelas por dia da semana e a resolução por prioridade já estão construídos e
testados. Um SLA que ignore o expediente é inútil, e essa é a parte que normalmente custa caro.

Depende do 4.2 para "por tipo de chamado".

### 4.4 — Abertura de chamado por e-mail (`OPORTUNIDADES.md` A4)

**Código existente: modelos de integração completos e nenhuma view.**

`Integration`, `WorkspaceIntegration` e a família Github/Slack estão modelados, com os tipos
TypeScript correspondentes completos. Nenhuma view existe. No comparativo de planos o grupo
`integrations` inteiro é `comingSoon`, e "Emails For Intake" é _business+_.

**Por que importa mais do que parece:** para um service desk, abrir chamado por e-mail é
básico. Hoje o cliente precisa fazer login no portal — e o portal agora existe, o que torna
esta lacuna mais visível, não menos. Na prática metade dos usuários vai mandar e-mail de
qualquer forma.

Diferente das três acima, esta é feature própria e não um gancho a destravar. O Intake nativo
do Plane (`IntakeIssue`, com `source` e `source_email` já no modelo) é onde ela desemboca.

---

## 5. Dívidas que a Fase 8 deixou

Nomeadas, com o motivo de terem ficado de fora.

| Dívida                                                    | Por que ficou                                                                                                                                         | Custo                                                                                                                                                                          |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Tela do dashboard do portal**                           | A API é o critério 13 e está completa e testada. A tela foi cortada quando o PR do backend cresceu, e não voltou.                                     | Médio. Consome `GET /service-reports/portal/`, que já devolve o payload inteiro com a projeção do cliente.                                                                     |
| **UI das concessões da Fase 7**                           | Dívida herdada, e a primeira coisa que combinamos cortar se o PR crescesse. Cresceu.                                                                  | Baixo. Uma coluna em `useMemberColumns.tsx` ao lado do dropdown de papel, mais um `service-member-permission.service.ts`. `isAdmin` e `rowData.member.id` já estão no arquivo. |
| **Controle do solicitante no formulário**                 | A API saiu no PR do solicitante; o controle não.                                                                                                      | Baixo. Um select de usuários GUEST do project, no formulário do work item.                                                                                                     |
| **Sugerir `guest_view_all_features` ao vincular Project** | Dívida da Fase 1 (ver seção 2). O portal a torna visível: sem a flag, o cliente só vê o que ele mesmo abriu.                                          | Baixo, e é a de maior impacto por linha: sem ela o portal parece vazio.                                                                                                        |
| **Filtro de estado no drill-down do portal**              | Não existe drill-down de portal. Se algum dia existir, **o descritor tem de ser reescopado na entrada** — ele é um registro, não uma permissão (D63). | —                                                                                                                                                                              |
| **Traduções das 9 chaves novas**                          | `en` e `pt-BR` traduzidas; as outras 17 carregam inglês, seguindo o padrão que as chaves de apontamento já usavam.                                    | Baixo.                                                                                                                                                                         |

### O que a Fase 8 não é dona, e não corrigiu de propósito

`ACHADOS-DO-CODIGO.md` seção 15 registra **leitura e escrita cross-tenant no core do Plane**
(`WorkspaceViewViewSet`): um usuário membro de nenhum workspace lê e **modifica** views
compartilhadas de qualquer workspace. Medido, não inferido.

Não foi corrigido porque correção de tenancy não deve viajar dentro de PR de feature — o mesmo
critério que fez a correção do bug análogo na Fase 2b sair sozinha. Três caminhos estão
escritos lá. **Isso não é tarefa de fase: é decisão de operador.**

---

## 6. Se você for continuar

Não existe "a próxima fase". Existem quatro caminhos independentes, e eles não têm ordem
imposta entre si:

1. **Fechar as dívidas de tela da seção 5.** É o menor esforço e o que faz o que já existe
   parecer entregue. Comece por sugerir `guest_view_all_features`: sem ela o portal parece
   vazio, e é a de melhor retorno por linha.
2. **Adotar uma das duas perguntas da seção 3.** Não escreva código antes da conversa. O
   resultado é uma decisão nova na `DECISOES.md`, e só então uma tarefa.
3. **Pegar uma melhoria da seção 4.** Timesheet semanal se o gargalo é qualidade do dado de
   apontamento; Work Item Types se é organização e é pré-requisito do SLA.
4. **Decidir o que fazer com o achado 14.** Independente de tudo acima, e o único item com
   consequência hoje se a instância hospedar mais de uma empresa.

Qualquer um deles começa relendo o contexto mestre e a `DECISOES.md`. Sessenta e seis decisões
existem para que a próxima não seja tomada duas vezes com respostas diferentes.
