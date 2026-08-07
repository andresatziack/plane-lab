# Fase 8b — as telas que faltam

**Esta fase conclui a Fase 8, não acrescenta escopo.** O nome segue o precedente da própria
série: a Fase 2b existe pelo mesmo motivo.

A Fase 8 entregou 20 dos seus 22 critérios. Os dois que faltam dependem de tela, e a API dos
dois está completa, testada e mesclada. **Nenhuma decisão de domínio nova, nenhuma migração,
nenhum toque no core do Plane.** É frontend sobre endpoints que existem.

Se você se pegar decidindo o que o cliente pode ver, pare: essa decisão já foi tomada e está
em `ReportViewer.guest()` (D55) e em `ServiceLogClientSerializer` (D58, D65). Esta fase
**consome** projeções, não cria.

---

## 1. O que está em aberto, e o enunciado exato

| Critério | Enunciado do briefing da Fase 8                                                                                                               | Estado                                           |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **2**    | "Marcel alterna entre os dois pelo seletor nativo e cada contexto mostra os chamados **e o dashboard de contrato do Cliente correspondente**" | API pronta, **sem tela**                         |
| **15**   | "Técnico abre chamado no project do Cliente **registrando o solicitante**, e aquele usuário passa a ver o chamado no portal"                  | API pronta, **o técnico não tem onde registrar** |

Mais duas dívidas que não são critério de ninguém, incluídas nesta fase por decisão do dono do
produto:

| Item                                         | Origem                                                                    |
| -------------------------------------------- | ------------------------------------------------------------------------- |
| UI das concessões elevadas                   | dívida da Fase 7 (D45)                                                    |
| Atribuição de projects em massa a um Cliente | `assignProjects` existe em service e store, **nenhum componente o chama** |

---

## 2. O dashboard do portal (critério 2)

### Onde fica, e por quê

**Uma página dentro do project, visível apenas para quem não pode editar campos restritos.**

O discriminante é `editPermissions.restrictedFields` do hook
`apps/web/core/hooks/use-work-item-edit-permissions.ts` (D64) — já existe e já é a resposta
para "esta pessoa faz coisas de técnico aqui". **Não escreva uma checagem de papel nova.**

Duas razões para não ser uma tela global:

1. O técnico já tem o dashboard completo da Fase 9 em
   `/[workspaceSlug]/service-reports/[tabId]/`. Uma segunda tela para ele seria redundante.
2. O critério 2 fala de "cada contexto" — o cliente alterna projects pelo seletor nativo e vê o
   dashboard **daquele** Cliente. Uma tela de workspace obrigaria a inventar um seletor de
   empresa, e a §2 da Fase 8 proíbe visão consolidada: os contratos são independentes.

### O endpoint, e o que ele devolve

```
GET /api/workspaces/<slug>/service-reports/portal/
```

ADMIN + MEMBER + GUEST. **O viewer é fixo em `ReportViewer.guest()`** (D63), então um Admin que
abrir a mesma rota vê exatamente o que o cliente vê — é assim que se audita o portal por
dentro. Escopo: os projects do próprio chamador, resolvido antes e aplicado por último.

O payload tem duas faces, decididas por `shape`:

**`shape: "contract"`** — o coração é `contracts[].statement[]`, mês a mês:

| Pergunta do cliente                     | Campo                                                        |
| --------------------------------------- | ------------------------------------------------------------ |
| "Quanto do meu contrato usei este mês?" | `contracted_hours` + `carried_hours` contra `consumed_hours` |
| "Estou perto de estourar?"              | `balance_hours`, e `overage_hours` se já estourou            |
| "De onde vieram as horas acumuladas?"   | `parcels[]`, cada uma com `origin_competence`                |
| "No que foi gasto?"                     | `distributions.hour_type`, `distributions.billing_type`      |
| "O que vocês não me cobraram?"          | `non_billable[]`                                             |
| "Tenho bolsa em algum chamado?"         | `allowances.active`, `allowances.pending_closure`            |
| "Como foi nos últimos meses?"           | `consumption_series[]`                                       |

**`shape: "standalone"`** — cliente sem contrato: sem `statement`, só série, distribuições e
totais.

`totals` traz `entries`, `issues`, `equivalent_hours(+_display)`, `debited_hours(+_display)`.

### Regras que a tela tem de respeitar

- **`non_billable` nunca vira um número só.** R5. Um total de "não faturável" agregado é o que
  transforma cortesia em discussão. Renderize item por item.
- **Nunca rotule horas como "trabalhadas".** R11(c). Uma hora fora do expediente aparece como
  1,5h; o rótulo errado transforma regra contratual em acusação de inflar horas. Use "horas
  consumidas". As chaves já existem em `work_item.service_log.client.*` — reaproveite ou siga o
  mesmo tom.
- **Não existe dinheiro nesta camada.** `ReportViewer.guest()` não calcula valor. Se você
  sentir falta de um total em R$, é porque não deve haver. O único lugar onde o cliente vê
  valor é a linha de apontamento que gerou fatura para ele (D58), e isso já está entregue em
  `ServiceLogClientSection`.
- **Um cliente sem project nenhum recebe 200 com o payload vazio**, não 403. Trate o estado
  vazio; não é erro.

### O que reaproveitar, e o que não

- **Reaproveite** os componentes de gráfico da Fase 9 em
  `apps/web/core/components/service-reports/` (`charts.tsx`, `report-insight-card.tsx`) e os
  wrappers do `@plane/propel`. **Não instale biblioteca de gráficos nova** — ver
  `ACHADOS-DO-CODIGO.md` seção 14.
- **Não reaproveite** `consumption-tab.tsx` diretamente: ela assume o payload do Admin, com
  dinheiro e `logged_hours`. Espelhe o layout, não o componente.
- O service do frontend é `apps/web/core/services/service-reports.service.ts`. Acrescente um
  método para a rota do portal; **não** reutilize o método do consumo de Admin.

---

## 3. O controle do solicitante (critério 15)

```
GET    /api/workspaces/<slug>/projects/<project_id>/issues/<issue_id>/service-requester/
POST   .../service-requester/     {"requester_id": "<uuid>"}
DELETE .../service-requester/
```

**ADMIN + MEMBER apenas.** Um cliente não registra solicitante: nomear outro usuário do cliente
seria um cliente concedendo visibilidade a outro.

Comportamento que a tela precisa acomodar:

- `GET` num chamado sem solicitante devolve **200 com `requester: null`**, não 404. É o caso
  comum, não erro.
- `POST` é idempotente (`update_or_create`): corrigir um solicitante errado é operação
  ordinária, e obrigar um DELETE antes deixaria uma janela sem solicitante.
- O solicitante **tem de ser GUEST ativo daquele project**. Qualquer outro devolve 400
  `NOT_A_CLIENT_USER_OF_THIS_PROJECT`. **Filtre o select para GUESTs do project** — não ofereça
  uma opção que a API vai recusar. Foi o mesmo cuidado que levou o dropdown de estado a ser
  filtrado na Fase 8.
- Apagar a atribuição **remove a visibilidade** que ela concedia, mas **mantém a inscrição**
  (`IssueSubscriber`): cancelar notificação é decisão do solicitante, e o Plane já lhe dá esse
  controle.

Onde: no painel de propriedades do work item, junto ao restante — ou seja, `sidebar.tsx` e o
gêmeo `peek-overview/properties.tsx`. **Os dois**, pelo mesmo motivo da D64: o peek é o caminho
que as pessoas realmente clicam.

Visível só quando `editPermissions.restrictedFields` é verdadeiro. Um cliente não deve nem ver
o controle.

---

## 4. UI das concessões da Fase 7

```
GET/PATCH /api/workspaces/<slug>/service-member-permissions/            (ADMIN)
GET/PATCH /api/workspaces/<slug>/service-member-permissions/<member_id>/ (ADMIN)
GET       /api/workspaces/<slug>/service-member-permissions/me/          (ADMIN+MEMBER)
```

Três booleanos independentes: `can_manage_others`, `can_delegate`, `can_reassign_author`.

**A intenção da Fase 7 era explícita: nenhuma tela nova.** Os controles vão na tela de membros
do workspace que já existe.

- `apps/web/core/components/workspace/settings/useMemberColumns.tsx` — acrescente uma coluna
  depois de "Account type". `isAdmin` já está calculado no arquivo, e `rowData.member.id` é
  exatamente o `<member_id>` que a rota quer.
- O componente da coluna vai ao lado de `AccountTypeColumn` em `member-columns.tsx`, espelhando
  a forma dele.
- **Não existe service, type, store nem hook** para isso. Crie
  `apps/web/core/services/service-member-permission.service.ts` seguindo o padrão dos outros
  `service-*.service.ts` (estendem o `APIService` de `apps/web/core/services/api.service.ts`,
  **não** o de `packages/services`), e o type em `packages/types/src/`.

Duas coisas que a UI deve deixar claras, porque são regras já implementadas no backend:

- **Um ADMIN de workspace tem as três implicitamente e não precisa de linha.** A resolução é
  `is_admin OR flag`. Não mostre toggles desligados para um Admin como se ele não pudesse.
- **Um GUEST não pode ter nenhuma.** A API responde 400
  `GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS`. Rebaixar alguém a GUEST já revoga as concessões
  automaticamente (`workspace/member.py`).

---

## 5. Atribuição de projects em massa

`assignProjects` existe em `service-client.service.ts` e em `service-client.store.ts`, e nenhum
componente o chama. O endpoint aceita `guest_view_all_features` e `is_time_tracking_enabled`
opcionalmente, e **nunca os infere** — a decisão fica com a UI.

Onde: na tela de Clientes,
`apps/web/app/(all)/[workspaceSlug]/(settings)/settings/(workspace)/service-clients/page.tsx`,
junto de `service-clients-list.tsx`.

**Espelhe a confirmação que já existe** em
`apps/web/core/components/service-clients/project-service-client-select.tsx`: ela sugere as duas
flags num modal, sem impor. Não invente outro fluxo — e não repita a lógica: se der, extraia o
modal.

---

## 6. Critérios de aceite

1. Marcel (GUEST em Marubeni e Terlogs) abre o dashboard do portal dentro de `Marubeni` e vê o
   consumo do contrato **da Marubeni**
2. Ele alterna para `Terlogs` pelo seletor nativo e o dashboard passa a mostrar o da Terlogs,
   sem escolher empresa em lugar nenhum
3. Adriano (GUEST só em Terlogs) não alcança o dashboard da Marubeni, nem por URL direta
4. O dashboard **não exibe nenhum valor em R$**, nenhuma `logged_hours` e nenhum multiplicador
   — verificado no corpo da resposta e na tela
5. "Não faturável" aparece **discriminado**, nunca como um total único
6. Nenhuma hora é rotulada "horas trabalhadas"
7. Um cliente cujo project não tem contrato vê a face `standalone`, sem extrato, e a tela não
   quebra
8. Um cliente sem project nenhum vê estado vazio, não erro
9. Um técnico **não** vê a página do portal (ele já tem a da Fase 9)
10. Um Admin que abrir a rota do portal vê exatamente o que o cliente vê
11. O técnico registra o solicitante pelo formulário do work item, e o select oferece **apenas
    GUESTs daquele project**
12. Registrado o solicitante, aquele usuário passa a ver o chamado no portal **com
    `guest_view_all_features` desligada** — é o único caso em que a atribuição é o que concede
    visibilidade, e é por isso que o teste tem de desligar a flag
13. O controle do solicitante aparece no detalhe **e** no peek, e não aparece para um cliente
14. Um Admin concede `can_delegate` a um técnico pela tela de membros, e o técnico passa a poder
    apontar em nome de outro
15. Rebaixar esse técnico a GUEST remove as concessões na tela sem pedir confirmação extra
16. Um Admin atribui três projects a um Cliente de uma vez, e o modal sugere as duas flags

---

## 7. Estado do repositório

- Branch de integração: `preview`. Última migração: **0133**. **Esta fase não cria migração.**
- Baseline da suíte: **2495 passando, 0 falhando** com `RECREATE_DB=1`. Meça de novo antes de
  começar e use o seu número.
- Decisões vigentes: **D1 a D66**. As que mais importam aqui: **D64** (gate por campo), **D63**
  (viewer fixo do portal), **D58** e **D65** (dinheiro e estado comercial do cliente), **D55**
  (a projeção GUEST é do domínio), **D45** (as três capacidades).

### Armadilhas do sandbox, aprendidas na Fase 8

- O hook do husky precisa de node no PATH:
  `NODE_DIR=$(ls -d /root/.nvm/versions/node/* | tail -1); export PATH="$NODE_DIR/bin:$PATH"`
- `oxfmt` é morto por OOM quando recebe muitos arquivos de uma vez. Rode arquivo por arquivo.
- Checks do frontend: `./tools/agent-sandbox/run-web-checks.sh` (`i18n`, `types`, `lint`). O
  lint precisa de caminhos explícitos, senão traz avisos pré-existentes que não são seus.
- **19 locales.** O padrão da série: `en` e `pt-BR` traduzidos, os outros 17 carregam inglês. O
  check de paridade exige a chave em todos.
- Processos em background são colhidos entre invocações de shell. Rode a suíte em uma só.
- CI **nunca rodou** neste repositório. A verificação é a que você fizer.

---

## 8. O que NÃO fazer

- Não crie uma segunda projeção do que o cliente vê. Se algo estiver errado, corrija no domínio
  (`ReportViewer.guest()`, `ServiceLogClientSerializer`), onde os testes estão.
- Não adicione `GUEST` a nenhuma lista de `allowPermissions`. `allowPermissions` faz inclusão
  simples sem hierarquia; use o hook por campo.
- Não toque no core do Plane. A Fase 8 foi a única que precisou, por um motivo nomeado (D59).
- Não corrija o achado da **seção 15** do `ACHADOS-DO-CODIGO.md` (leitura e escrita cross-tenant
  no `WorkspaceViewViewSet`). É decisão de operador e sai em PR próprio, se sair.
- Não instale biblioteca de gráficos nova.
