# Fase 8 — Portal do cliente: papel, escopo de visibilidade e ações

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende das Fases 1 e 7.

## Objetivo

Permitir que usuários do cliente (end users) acessem o sistema, vejam apenas os
chamados das empresas às quais pertencem, e interajam de forma limitada.

## Premissa arquitetural

Conforme a seção 2b do contexto mestre, o isolamento entre clientes é entregue
pelo **mecanismo nativo do Plane**, não por filtro customizado:

- Cliente ↔ um ou mais projects
- Usuário do cliente = **workspace Guest**, adicionado aos projects dos seus
  Clientes
- Um Guest do Plane só vê os projects aos quais foi explicitamente adicionado, e
  não vê outros projects nem informação de nível de workspace
- A troca de "portal" é o **seletor de project nativo**

Isso reduz drasticamente a superfície de segurança customizada desta fase. O
trabalho aqui é sobretudo **configurar e restringir** o que já existe, e não
construir um sistema de visibilidade paralelo.

## Ponto de partida já auditado

Não investigue o que já está respondido em `docs/worklog/ACHADOS-DO-CODIGO.md`:

| Requisito                                     | Situação no fork                                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Papel Commenter                               | **não existe.** Só `ADMIN=20`, `MEMBER=15`, `GUEST=5`                                             |
| Papéis customizados / permissões granulares   | **não existem** nesta edição                                                                      |
| Guest só vê os projects em que foi adicionado | **existe**, nativo (`app/views/project/base.py:101-114`)                                          |
| Guest ver todos os chamados do project        | existe via `Project.guest_view_all_features`, **padrão `False`**                                  |
| Guest comentar                                | **já funciona** (`app/views/issue/comment.py:63`)                                                 |
| Guest alterar prioridade / fechar / reabrir   | **não pode** — `partial_update` é `[ADMIN, MEMBER]` (`issue/base.py:627`)                         |
| Allowlist de campos por papel                 | **não existe** para work item; existe como precedente manual no intake (`intake/base.py:404-408`) |
| Intake aceita criação por Guest               | **sim**, forçando o state Triage (`intake/base.py:228+`)                                          |

O trabalho desta fase é, portanto: **ligar uma flag, estender uma permissão, e
criar a allowlist de campos que não existe.** Não criar papel novo.

## Escopo

### 1. Configuração do project de cliente

`Project.guest_view_all_features = True` nos projects de Cliente.

Sem isso, o filtro `issue_queryset.filter(created_by=request.user)`
(`issue/base.py:310-321`) faz o Guest ver **apenas os chamados que ele mesmo
criou** — Marcel não veria os chamados abertos por colegas dele nem os abertos
pelos técnicos em nome da Marubeni. Deve ser o padrão ao associar um project a um
Cliente (Fase 1).

### 2. Escopo de visibilidade

Garantido pelo Plane: o usuário Guest vê os projects aos quais foi adicionado.

**Usuário multi-empresa.** Marcel é Guest nos projects `Marubeni` e `Terlogs`.
Ele alterna entre os dois pelo seletor nativo, e em cada contexto vê:

- os chamados daquele Cliente
- o dashboard de consumo do contrato daquele Cliente (Fase 9)

Não construir visão consolidada das duas empresas: os contratos são
independentes, os dashboards são dedicados, e a separação é o comportamento
desejado. Adriano, Guest apenas em `Terlogs`, nunca enxerga a Marubeni.

Note que **não é preciso pedir ao usuário para escolher a empresa** ao abrir
chamado. Ele abre dentro de um project, e o project pertence a um único Cliente.

### 3. O que o usuário do cliente NÃO pode ver

Verificar explicitamente, com teste, que não há acesso a:

- projects de Clientes aos quais não pertence
- informação de nível de workspace: lista de membros técnicos, outros projects,
  configurações, catálogos, calendário, contratos de terceiros
- **valores em R$** de apontamentos, multiplicadores e tabela de preços
- painéis administrativos e relatórios de faturamento

### 3b. Visão de apontamentos do cliente — apenas o valor convertido

Regra R11 do contexto mestre. O cliente vê o apontamento **já convertido**, nunca
o apontamento original do técnico.

| Campo                                            | Cliente vê?                |
| ------------------------------------------------ | -------------------------- |
| Horas equivalentes / debitadas                   | **sim**                    |
| Tipo de Hora (rótulo, ex.: "Fora do expediente") | sim                        |
| Descrição do apontamento                         | sim                        |
| Autor e data                                     | sim                        |
| Tipo de Atendimento (rótulo, ex.: "Garantia")    | sim, com `0h` descontado   |
| Duração bruta digitada pelo técnico              | **não**                    |
| Horas apontadas (pós-arredondamento)             | **não**                    |
| Multiplicador numérico                           | **não**                    |
| Valor em R$                                      | só se o cliente for avulso |

Implementação obrigatória:

**Serializer dedicado.** Criar um serializer específico para o portal que
simplesmente **não declara** os campos `duracao_bruta_minutos`,
`horas_apontadas` e `multiplicador_aplicado`. Filtrar na UI e enviar no payload é
vazamento — a API é acessível ao usuário autenticado.

**Rótulo correto.** O total do cliente não vai bater com o do técnico: 1h
trabalhada fora do expediente aparece como 1,5h. Rotular como "Horas consumidas do
contrato" ou "Horas faturadas", **nunca** "Horas trabalhadas". O rótulo errado
transforma uma cláusula contratual legítima em suspeita de inflação de horas, e
gera exatamente a ligação de cliente que essa feature deveria evitar.

**Apontamento não faturável.** Garantia e Cortesia aparecem na lista como
esforço realizado com `0h` descontado, com o rótulo do Tipo de Atendimento
visível. É a concessão que você fez — deve estar visível para o cliente, não
escondida.

### 4. Ações permitidas ao usuário do cliente

Permitido:

- abrir chamado no project do seu Cliente
- adicionar comentários — **já funciona nativamente**, não mexer
- alterar a **prioridade** do chamado
- **fechar** e **reabrir** o chamado
- anexar arquivos aos próprios chamados

Proibido:

- criar, editar ou excluir apontamentos
- alterar qualquer campo além de prioridade e estado
- alterar responsável, project, estimativa, labels, datas, parent, tipo
- acessar qualquer configuração ou painel administrativo

### 4b. O problema real de permissão, e como resolver

O comportamento atual está errado **nas duas direções**, e isso precisa ser
corrigido com cuidado. Ver `docs/worklog/ACHADOS-DO-CODIGO.md`, seção 4.

`IssueViewSet.partial_update` (`app/views/issue/base.py:627`) é decorado com
`@allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], creator=True,
model=Issue)`. O parâmetro `creator=True` aciona um bypass em
`permissions/base.py:24-40` que libera a view inteira para o criador do registro,
exigindo apenas que ele seja `WorkspaceMember` ativo — sem olhar o papel. E o
handler não valida campo nenhum: `IssueCreateSerializer(issue, data=request.data,
partial=True)`.

Resultado:

| Caso                                                                | Hoje                                                                                      | Precisa ser            |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------- |
| Guest edita chamado que **ele criou**                               | altera **tudo**: prioridade, estado, responsável, labels, datas, estimativa, parent, tipo | só prioridade e estado |
| Guest edita chamado criado por **técnico** ou por colega da empresa | não altera **nada**                                                                       | prioridade e estado    |

O segundo caso é o mais comum na prática — o técnico abre o chamado em nome do
cliente (seção 5) e o cliente precisa poder fechá-lo.

Implementar as duas correções:

1. **Allowlist de campos por papel.** Quando o requisitante for GUEST, reduzir o
   payload a `priority` e ao campo de estado antes de serializar. Seguir o
   precedente que já existe no intake:

   ```python
   # app/views/intake/base.py:404-408
   if project_member.role <= ROLE.GUEST.value:
       issue_data = {k: v for k, v in issue_data.items()
                     if k in ["name", "description_html", "description_json"]}
   ```

   Aplicar o mesmo padrão em `IssueViewSet.partial_update`. Isso **fecha** o
   excesso de permissão do primeiro caso.

2. **Permitir GUEST no `partial_update`** dos projects de Cliente, para que o
   segundo caso funcione. Como a allowlist já limita os campos, incluir
   `ROLE.GUEST` na lista de papéis é seguro — mas só depois da allowlist estar
   implementada e testada. Nunca antes.

Sobre a mudança de estado: o cliente pode mover para estados de grupo
`COMPLETED`/`CANCELLED` (fechar) e de volta para `UNSTARTED`/`STARTED` (reabrir).
Não deve poder mover para `TRIAGE` nem escolher estado arbitrário. Validar o
grupo do estado de destino, não apenas o campo.

Nota de manutenção: esta é a alteração mais invasiva de todo o material, porque
mexe em código do core do Plane. Isolar em função auxiliar própria, com comentário
explicando o motivo, para reduzir dor em merges futuros com o upstream.

### 4c. Frontend

O gate de edição no front é
`allowPermissions([EUserPermissions.ADMIN, EUserPermissions.MEMBER],
EUserPermissionsLevel.PROJECT, ...)` em
`apps/web/core/components/issues/issue-detail/root.tsx:221`, propagado como
`disabled` para estado, prioridade e demais propriedades
(`main-content.tsx:92,105,113`).

Note que `allowPermissions` faz inclusão simples na lista, **sem hierarquia
`>=`** (`core/store/user/base-permissions.store.ts:193`). Para o Guest ver
prioridade e estado habilitados, e o resto desabilitado, o gate precisa deixar de
ser um único booleano `isEditable` e passar a ser por campo. Não basta adicionar
GUEST à lista — isso habilitaria tudo na UI.

### 5. Abertura de chamado pelo técnico em nome do cliente

O técnico abre o chamado no project do Cliente — com isso o Cliente e o contrato
já estão corretos por construção. Deve poder registrar o **solicitante** (o
usuário do cliente em nome de quem o chamado foi aberto), para que aquele usuário
veja o chamado no portal dele e seja notificado.

### 6. Testes de isolamento

Testes de segurança são obrigatórios nesta fase, mesmo com o isolamento nativo —
o objetivo é provar que as entidades **novas** (apontamento, contrato, período,
bolsa, preço) respeitam o mesmo escopo dos work items.

Para cada endpoint novo criado nas fases anteriores, testar com o cenário de
referência:

| Ator                                 | Deve ver                                              | Não deve ver                                                     |
| ------------------------------------ | ----------------------------------------------------- | ---------------------------------------------------------------- |
| Marcel (Guest em Marubeni e Terlogs) | chamados, apontamentos e consumo de contrato das duas | qualquer terceiro Cliente; valores em R$; contratos de terceiros |
| Adriano (Guest em Terlogs)           | apenas Terlogs                                        | Marubeni e qualquer outro                                        |

Testar por ID direto, por filtro, por busca e por endpoint de listagem. Um
endpoint novo que esqueça o escopo de project é o vazamento mais provável desta
arquitetura — os work items estão protegidos pelo Plane, mas os apontamentos e
contratos são código seu.

## Critérios de aceite

1. Marcel faz login e vê os projects `Marubeni` e `Terlogs`, e mais nenhum
2. Marcel alterna entre os dois pelo seletor nativo e cada contexto mostra os
   chamados e o dashboard de contrato do Cliente correspondente
3. Adriano faz login e vê apenas `Terlogs`
4. Adriano é negado ao acessar por ID direto um chamado da Marubeni
5. Marcel abre chamado dentro de `Terlogs` e ele nasce vinculado à Terlogs, sem
   nenhuma escolha de empresa
6. Usuário do cliente comenta, altera prioridade, fecha e reabre chamado
7. Usuário do cliente não consegue alterar responsável, project ou labels — nem
   pela UI, nem pela API
8. Usuário do cliente não vê nenhum valor em R$, multiplicador ou tabela de preços
9. Usuário do cliente vê as horas **convertidas** e a descrição dos apontamentos
   dos seus chamados
10. **O payload da API do portal não contém** `duracao_bruta_minutos`,
    `horas_apontadas` nem `multiplicador_aplicado` — verificado no corpo da
    resposta, não na interface
11. Apontamento de `1h` com Tipo de Hora de multiplicador 1.5 aparece para o
    técnico como `1h` e para o cliente como `1,5h`, com o rótulo de horas
    consumidas do contrato
12. Apontamento de garantia aparece para o cliente com `0h` descontado e selo de
    garantia
13. Usuário do cliente não consegue criar, editar nem excluir apontamento
14. Usuário do cliente não acessa lista de membros técnicos nem configurações do
    workspace
15. Técnico abre chamado no project do Cliente registrando o solicitante, e aquele
    usuário passa a ver o chamado no portal
16. Nenhum endpoint novo (apontamento, contrato, período, bolsa, preço, **catálogos
    de Tipo de Hora e Tipo de Atendimento**, **trilha de auditoria de configuração**,
    **calendário de feriados**, **janelas de classificação**) vaza dados de Cliente fora
    do escopo do usuário

    Sobre os cinco últimos, já implementados nas Fases 2 e 2b e já cobertos por teste
    negativo em `plane/tests/contract/app/test_service_catalog_app.py` e
    `test_service_calendar_app.py` — reverificar aqui, não reimplementar:
    - `GET /api/workspaces/<slug>/service-hour-types/` e
      `.../service-billing-types/` são ADMIN + MEMBER, **GUEST recebe 403**. O
      motivo não é organizacional: o payload do Tipo de Hora carrega `multiplier`, e
      a R11 mantém o multiplicador longe do cliente. O portal lê o **rótulo** do Tipo
      de Hora pelo serializer dedicado do apontamento, nunca por estes endpoints.
    - `GET /api/workspaces/<slug>/service-config-activities/` é **ADMIN de workspace
      apenas** — nem MEMBER. É o histórico de multiplicadores e, a partir da Fase 6,
      de preços: inteligência comercial. Não existe endpoint de escrita para ele.
    - `GET /api/workspaces/<slug>/service-holidays/` (mais `calendar/` e
      `bulk-import/`) e `.../service-classification-windows/` (mais `coverage/`) são
      ADMIN + MEMBER na leitura e **ADMIN apenas na escrita**; **GUEST recebe 403 em
      todos**, inclusive no `bulk-import/`. O payload não carrega `multiplier`, mas
      carrega a **prioridade** do Tipo de Hora, que é a estrutura de preços da operação
      lida de outro ângulo: quem enxerga as janelas sabe quais horas custam mais e
      quando. Um MEMBER pode ler porque precisa entender por que seu apontamento foi
      classificado assim; um Guest não tem essa necessidade e a R11 vale.

    Ao criar o serializer do apontamento para o portal, conferir contra a tabela da
    R11 campo por campo. A restrição é de serializer, não de UI: esconder na
    interface e mandar no payload é vazamento.

17. Marcel vê os chamados da Marubeni abertos por um **técnico** e por um **colega
    dele**, não apenas os que ele mesmo criou (`guest_view_all_features = True`)
18. Marcel fecha e reabre um chamado que foi aberto por um técnico
19. Marcel tenta alterar `assignees`, `labels`, `target_date`, `estimate_point` e
    `parent` de um chamado que **ele mesmo criou** — todos os campos são
    ignorados ou rejeitados, e nenhum é persistido
20. Marcel não consegue mover um chamado para o estado de grupo `TRIAGE`
21. Na UI, prioridade e estado ficam habilitados para o Guest e todos os demais
    campos ficam desabilitados

O critério 19 é o mais importante da fase: ele prova que a allowlist de campos
funciona. Sem ele, incluir GUEST no `partial_update` abre um buraco de permissão.

## Critérios herdados — coisas já construídas que esta fase só precisa expor

### 22. A projeção GUEST dos relatórios de consumo **já existe e já é testada**. D55

A Fase 9 construiu `ReportViewer.guest()` em `plane/utils/service_reports.py` e a
provou com testes de ausência de chave mais controle positivo. **Não a reescreva.**

O motivo de ela existir antes da rota é a R11(b): a allowlist de campos é decisão de
domínio, e deixar esta fase redescobrir quais colunas vazam é exatamente como a regra
vira decisão tomada num template. O que a projeção garante:

| Campo                                        | GUEST                                                                             |
| -------------------------------------------- | --------------------------------------------------------------------------------- |
| `equivalent_hours`, `debited_hours`          | vê                                                                                |
| `logged_hours`                               | **nunca** — a diferença entre apontadas e equivalentes _é_ o multiplicador (R11c) |
| `raw_duration_minutes`, `applied_multiplier` | **nunca**                                                                         |
| dinheiro                                     | **nunca** por esta camada                                                         |

O trabalho desta fase é **montar a rota** sobre ela: um endpoint de portal que resolve
o Cliente pelo project do Guest e chama a camada de agregação com
`ReportViewer.guest()`. As funções (`hours_series`, `distribution`,
`headline_totals`, `non_billable_breakdown`) já aceitam o viewer.

Critério de aceite desta fase, então: **o dashboard do portal passa por
`ReportViewer.guest()` e não por uma projeção nova.** Uma segunda projeção seria um
segundo lugar onde a R11 pode estar errada, e a primeira já tem os testes.

Se você achar que a projeção está errada, **corrija-a** — mas corrija no domínio, onde
o teste está, não numa view.

### 23. O painel de atenção e a exportação por descritor já existem

- `service-reports/attention/` (Fase 9) já unifica alertas de período, de bolsa e
  faltas de configuração. Um portal que precise mostrar algo disso consome o endpoint,
  com a projeção do papel.
- A exportação consome um `ServiceLogFilterSet` (D50). Se o portal oferecer export ao
  cliente, é o mesmo descritor com o viewer de GUEST — não um pipeline novo.

## Aviso de escopo: esta é a ÚNICA e a ÚLTIMA fase que altera código do core do Plane

Vale saber antes de começar, e não descobrir no meio.

Todas as fases de 1 a 7, e a 9, seguiram a §6 do contexto mestre — **extensão, nunca
modificação**: modelos novos, endpoints novos, componentes novos, e o core do Plane
intocado. É isso que mantém o rebase com o upstream viável.

Esta fase **quebra isso uma vez, deliberadamente**, e num lugar só:

```
apps/api/plane/app/views/issue/base.py  →  partial_update
```

Porque o critério 6 exige que o Guest feche e reabra chamado, e `partial_update` é
`[ADMIN, MEMBER]` hoje (`issue/base.py:627`). Incluir GUEST ali **exige** a allowlist
de campos do critério 19 na mesma mudança — sem ela, é um buraco de permissão, não uma
feature.

Três consequências práticas:

1. **A mudança tem de ser mínima e cirúrgica.** Um `if` de papel mais a allowlist, não
   uma refatoração da view.
2. **Ela tem de estar documentada no lugar onde quem faz o rebase vai olhar** — um
   comentário no ponto da alteração dizendo que é a única divergência do core nesta
   série de fases, e por quê.
3. **Se houver qualquer forma de conseguir o critério 6 sem tocar em `partial_update`**
   — um endpoint próprio de transição de estado, por exemplo, que é extensão e não
   modificação — ela é preferível, e vale gastar uma rodada avaliando isso antes de
   editar a view do core.

## Entregar

Primeiro a proposta de implementação da allowlist de campos e do gate por campo
no frontend, com o levantamento de quais endpoints novos das Fases 3 a 6 precisam
de escopo. Depois implementação e a suíte de testes de isolamento com o cenário
Marubeni/Terlogs.

Testes com `pytest`, seguindo `plane/tests/contract/app/test_issue_list_guest_scope_app.py`,
que já cobre exatamente o escopo de guest e é o modelo mais próximo do que se
precisa aqui.
