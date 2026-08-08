# Handoff de fechamento da série

**A série está fechada.** Dez fases mescladas — 1, 2, 2b, 3, 4, 5, 6, 7, 9, 8 e 8b — e a 8b
fechou os dois critérios que a Fase 8 deixou dependendo de tela. **Este documento não aponta
para uma fase seguinte, e não existe uma.**

Ele registra o que as fases entregaram, o que existe em API sem tela (nada, agora), as duas
perguntas de negócio que continuam em aberto e vão desaparecer do registro se ninguém as
adotar, as melhorias que nunca tiveram prompt escrito, e as dívidas — incluindo a que a 8b
criou.

**Duas correções que esta versão faz**, porque texto que descreve um estado que deixou de
existir é pior que texto ausente:

- A versão anterior dizia "falta a Fase 8b para fechar a Fase 8" e chamava a 8b de próxima
  sessão. Ela foi feita.
- A seção 2 listava quatro superfícies com API e sem tela. **As quatro têm tela.** E a primeira
  delas revelou, ao ganhar tela, que a API não estava pronta: o dashboard do portal respondia as
  duas empresas de um cliente multi-empresa somadas, contra a §2 da Fase 8. Corrigido pela
  **D67**, que é a única decisão nova da 8b.

Leitura obrigatória antes de qualquer coisa continua a mesma: `.kiro/steering/worklog-contexto.md`
(contexto mestre, com as regras R1 a R11), `DECISOES.md` (D1 a **D67**), `ACHADOS-DO-CODIGO.md`
(inclui a seção 15, que é informação de operador e não tarefa) e `CONVENCOES-DE-TRABALHO.md`.

---

## 1. Estado final

Dez fases mescladas: 1, 2, 2b, 3, 4, 5, 6, 7, 9, 8 e 8b.

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
| 8b   | As quatro telas que faltavam, e a **D67**: o dashboard do portal passa a ser de um Cliente por vez                           | nenhuma   |

Suíte ao fim: **2505 passando, 0 falhando** (`RECREATE_DB=1`). A linha de base medida no
início da 8b era 2495, e a própria 8b não mudou modelo nenhum — os 10 testes novos são todos da
D67, e um deles é um controle positivo que existe porque sem ele um erro de tipo deixaria a
feature morta com a suíte verde.

### O que a Fase 8 acrescentou de estrutural, além dos critérios

- **A allowlist de campos do cliente** vive em `plane/utils/service_portal.py`, não na view.
  É o módulo que qualquer fase futura deve editar para mudar o que o cliente pode alterar.
- **`client_may_reach_issue` é a única definição** de "este cliente alcança este work item",
  usada em seis lugares. O core fazia essa pergunta em três, com três grafias.
- **A varredura de escopo enumera o roteador do Django** (D66), então uma rota nova é negada
  por padrão. Isso é reutilizável e é o que encontrou dois bugs.

---

## 2. O que existia em API e não tinha tela — as quatro têm tela

**Fechado pela Fase 8b.** Mantido como registro porque a primeira linha é a lição da fase.

| Superfície                                       | Onde                                                   | Estado                                                                                |
| ------------------------------------------------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| **Dashboard do portal do cliente**               | `GET /service-reports/portal/`                         | **Tela feita** — e a API **não estava pronta**, ao contrário do que se registrou. D67 |
| **Concessões da Fase 7**                         | `service-member-permissions/`, `/me/`, `/<member_id>/` | **Coluna na tabela de membros**, sem tela nova, como a Fase 7 quis                    |
| **Solicitante do chamado**                       | `.../issues/<id>/service-requester/`                   | **Controle no detalhe e no peek**                                                     |
| Atribuição de projects **em massa** a um Cliente | `service-clients/<pk>/assign-projects/`                | **Modal a partir da lista de clientes**; `assignProjects` finalmente tem chamador     |

**A lição, e é o motivo de esta tabela ficar no registro:** "API completa e testada, nenhuma
tela" era uma afirmação sobre três coisas, e uma delas não se sustentou. O endpoint do portal
era escopado pelo usuário e não pelo project, então para um cliente presente em duas empresas
ele somava as duas — exatamente a visão consolidada que a §2 da Fase 8 proíbe pelo nome. Estava
testado, e o teste nunca fez a pergunta: a fixture chamada `marcel` era membro de um project só,
enquanto o Marcel do cenário de referência é GUEST em dois.

**Uma API sem consumidor é uma API sem verificação de aceitação.** Os testes de contrato provam
as regras que alguém formulou; a tela é o que faz o cenário real ser executado ponta a ponta. Um
critério pendurado entre fases porque "só falta a tela" pode estar escondendo um defeito de API
justamente porque ninguém consegue exercitá-lo.

**Correção de um erro de registro:** uma versão anterior deste documento afirmava que a UI não
sugere `guest_view_all_features` ao vincular um project. **Ela sugere.**
`ProjectServiceClientSelect` está montado em `project/settings/service-client-section.tsx` e abre
um modal de confirmação com as duas flags — exatamente o "sugerir e não impor" que o contexto
mestre pede. A tela de atribuição **em massa**, que era o que de fato faltava, foi feita na 8b e
reusa o mesmo campo das duas flags — extraído em `ServiceClientFlagsFields` para que a cópia não
divirja entre os dois caminhos.

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

## 5. A Fase 8 fechou em 22 de 22 — os dois últimos, na 8b

A Fase 8 entregou 20 dos seus 22 critérios. Os dois restantes dependiam de tela e **foram
fechados pela Fase 8b**, pelo mecanismo de critérios herdados que a série já usara três vezes:
marcados na origem (`08-portal-do-cliente.md`) e provados no documento que fecha
(`08b-telas-que-faltam.md`, §10).

| Critério | Enunciado                                                                                  | Como fechou                                                                                                          |
| -------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| **2**    | "cada contexto mostra os chamados **e o dashboard de contrato do Cliente correspondente**" | Tela feita **e API corrigida** (D67). O registro anterior — "a API existe e é testada" — era falso para este cenário |
| **15**   | "Técnico abre chamado no project do Cliente **registrando o solicitante**"                 | Controle no detalhe e no peek. A regra já estava provada (D62); faltava onde registrar                               |

**Uma ressalva honesta sobre o que "fechado" significa aqui.** Seis dos dezesseis critérios da
8b fecham **por construção**, não por teste, porque `apps/web` não tem runner de teste — os
scripts são `check:lint`, `check:types` e `check:format`, e não existe um `*.test.*` no app. A
§9 da 8b classifica os dezesseis em três estados em vez de dois, exatamente para não misturar
"provado" com "lido". A dívida correspondente está na tabela abaixo, e é a maior que a série
deixa em aberto.

### Dívidas propriamente ditas

Estas não são critério de ninguém.

| Dívida                                       | Por que ficou                                                                                                                                                                                                                               | Custo                                                                                                                                                      |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Runner de teste no `apps/web`** ⭐         | **A maior que a série deixa.** Criada pela 8b: quatro telas novas cuja regressão nenhuma suíte pega. Escolher e instalar um runner é decisão de repositório, não efeito colateral de uma fase de UI.                                        | Médio. O que cobrir primeiro, em ordem de risco, está na §9 da 8b — o gate `restrictedFields`, o filtro de GUESTs do solicitante, e a célula de concessões |
| **Os dois arrays de navegação duplicados**   | Pré-existente ao fork desta série, e a 8b acrescentou o sétimo item aos dois. Mexer em um só produz um item que existe num modo de navegação e não no outro, e nada além de leitura pega isso.                                              | Baixo para unificar; alto para descobrir se esquecerem. Comentário gêmeo nos dois arquivos aponta um para o outro                                          |
| **Janela fixa de 12 competências no portal** | Não é critério da 8b. Um contrato de 36 meses tem dois anos que o cliente não alcança pela tela.                                                                                                                                            | Baixo. `WINDOW_MONTHS` em `portal-consumption-dashboard.tsx`; um seletor de janela reusaria `useReportFilters`                                             |
| **Filtro de estado no drill-down do portal** | Continua não existindo drill-down de portal, e agora por decisão explícita: `service-reports/logs/` recusa um cliente, então um botão ali só poderia dar 403. Se algum dia existir, **o descritor tem de ser reescopado na entrada** (D63). | —                                                                                                                                                          |
| **Traduções das 37 chaves da 8b**            | `en` e `pt-BR` traduzidas; as outras 17 carregam inglês, seguindo o padrão da série. Paridade verificada: 4356 chaves em 19 locales.                                                                                                        | Baixo.                                                                                                                                                     |

Duas dívidas que constavam aqui **saíram porque foram pagas na 8b**: a UI das concessões da Fase
7, e a atribuição de projects em massa. A de "sugerir `guest_view_all_features`" já estava paga
antes — ver a correção de registro na seção 2.

### O que a Fase 8 não é dona, e não corrigiu de propósito

`ACHADOS-DO-CODIGO.md` seção 15 registra **leitura e escrita cross-tenant no core do Plane**
(`WorkspaceViewViewSet`): um usuário membro de nenhum workspace lê e **modifica** views
compartilhadas de qualquer workspace. Medido, não inferido.

Não foi corrigido porque correção de tenancy não deve viajar dentro de PR de feature — o mesmo
critério que fez a correção do bug análogo na Fase 2b sair sozinha. Três caminhos estão
escritos lá. **Isso não é tarefa de fase: é decisão de operador.**

---

## 6. Se você for continuar

**A Fase 8b vem primeiro, e é a única com ordem imposta:** ela fecha critérios de aceite em
aberto, e sem ela a Fase 8 não está entregue. É frontend sobre APIs que já existem e já têm
teste — sem migração, sem decisão nova, sem toque no core.

Depois dela, três caminhos independentes, sem ordem entre si:

1. **Fechar as dívidas de tela restantes da seção 5.** Comece por sugerir
   `guest_view_all_features`: sem ela o portal parece vazio, e é a de melhor retorno por linha.
   (Se a 8b a incluir, este item já sai resolvido.)
2. **Adotar uma das duas perguntas da seção 3.** Não escreva código antes da conversa. O
   resultado é uma decisão nova na `DECISOES.md`, e só então uma tarefa.
3. **Pegar uma melhoria da seção 4.** Timesheet semanal se o gargalo é qualidade do dado de
   apontamento; Work Item Types se é organização e é pré-requisito do SLA.
4. **Decidir o que fazer com o achado 15.** Independente de tudo acima, e o único item com
   consequência hoje se a instância hospedar mais de uma empresa.

Qualquer um deles começa relendo o contexto mestre e a `DECISOES.md`. Sessenta e seis decisões
existem para que a próxima não seja tomada duas vezes com respostas diferentes.
