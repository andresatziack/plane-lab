# Achados da auditoria do fork `andresatziack/plane-lab`

Auditoria feita na branch `preview`, commit `31853ab`. Todos os achados abaixo
foram verificados diretamente no código, com arquivo e linha.

Caminho do backend: `apps/api/plane/`

---

## 1. Só existem 3 papéis. Não há Commenter nem papéis customizados

```python
# apps/api/plane/db/models/project.py:21
ROLE_CHOICES = ((20, "Admin"), (15, "Member"), (5, "Guest"))

class ROLE(Enum):
    ADMIN = 20
    MEMBER = 15
    GUEST = 5
```

O mesmo enum está duplicado em `apps/api/plane/app/permissions/base.py:13` (usado
pelas views) e em `apps/api/plane/utils/permissions/base.py:13`. No frontend,
`packages/types/src/enums.ts:7` tem `EUserPermissions { ADMIN=20, MEMBER=15,
GUEST=5 }`.

Buscas em todo o repositório (`.py`, `.ts`, `.tsx`) retornam **zero** ocorrências
de:
- `COMMENTER` / `Commenter`
- `PermissionScheme`, `permission_scheme`, `CustomRole`
- strings de permissão granular como `workitem:edit`

Não existe pasta `ee/` nem `enterprise/`, nem feature flags no `apps/web`.
`plane/license` só trata de instância, sem gate de papéis.

**Consequência para o material:** a documentação pública do Plane descreve papel
Commenter e papéis customizados com permissões granulares. **Essa versão é mais
nova (ou comercial) que este fork.** As Fases 7 e 8 não podem contar com isso —
o portal do cliente precisa ser construído estendendo o papel GUEST.

## 2. `guest_view_all_features` — a descoberta que mais afeta o portal

```python
# apps/api/plane/db/models/project.py:100
guest_view_all_features = models.BooleanField(default=False)
```

Com o valor **padrão `False`**, um Guest só vê os work items **que ele próprio
criou**:

```python
# apps/api/plane/app/views/issue/base.py:310-321 (IssueViewSet.list)
if (ProjectMember.objects.filter(..., role=5, is_active=True).exists()
        and not project.guest_view_all_features):
    issue_queryset = issue_queryset.filter(created_by=request.user)
```

E no `retrieve` (`issue/base.py:600-615`) retorna 403 "You are not allowed to view
this issue" nas mesmas condições.

O mesmo padrão se repete em: `issue/base.py:96, 909, 1031, 1337`,
`issue/comment.py:63-80`, `issue/version.py:99`, `view/base.py:150, 307, 319`,
`page/base.py:208, 302, 449`, `intake/base.py:216, 539, 591`.

**Consequência:** sem ligar essa flag, Marcel veria apenas os chamados que ele
mesmo abriu — não os abertos por colegas dele nem os abertos pelos técnicos em
nome da Marubeni. Os projects de cliente **precisam** de
`guest_view_all_features = True`. Isso é requisito de configuração, não de código.

Nota: essa flag é hoje o **único** mecanismo de granularidade para papéis baixos
em todo o Plane.

## 3. Guest já pode comentar nativamente

```python
# apps/api/plane/app/views/issue/comment.py:63
@allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
def create(self, request, slug, project_id, issue_id):
```

Editar comentário: `comment.py:109` — `@allow_permission([ROLE.ADMIN],
creator=True, model=IssueComment)`, ou seja, admin ou o próprio autor.

Atende o requisito de comentários sem nenhuma alteração.

## 4. Guest NÃO pode alterar prioridade nem estado — mas há um furo

```python
# apps/api/plane/app/views/issue/base.py:627
@allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], creator=True, model=Issue)
def partial_update(self, request, slug, project_id, pk=None):
```

GUEST não está na lista. **Mas o parâmetro `creator=True` é um bypass total de
papel:**

```python
# apps/api/plane/app/permissions/base.py:24-40
if creator and model:
    if not WorkspaceMember.objects.filter(member=request.user, ..., is_active=True).exists():
        return 403
    obj = model.objects.filter(id=kwargs["pk"], created_by=request.user).exists()
    if obj:
        return view_func(instance, request, *args, **kwargs)   # libera tudo
```

E o handler **não valida campo nenhum**:

```python
# apps/api/plane/app/views/issue/base.py:679
serializer = IssueCreateSerializer(issue, data=request.data, partial=True, context={...})
if serializer.is_valid():
    serializer.save()
```

Resultado, exatamente ao contrário do que se quer:

| Situação | Comportamento atual | O que precisamos |
|---|---|---|
| Guest edita chamado que **ele criou** | pode alterar **tudo**: prioridade, estado, responsável, labels, datas, estimativa, parent, type | só prioridade e estado |
| Guest edita chamado criado por **técnico** ou por colega | não pode alterar **nada** | prioridade e estado |

Ou seja, é permissivo demais no caso errado e restritivo demais no caso certo.
A Fase 8 precisa de **allowlist de campos por papel**, que não existe hoje.

**Precedente a copiar:** o intake já faz exatamente esse tipo de allowlist manual:

```python
# apps/api/plane/app/views/intake/base.py:404-408
if project_member.role <= ROLE.GUEST.value:
    # guest só pode editar nome e descrição
    issue_data = {k: v for k, v in issue_data.items()
                  if k in ["name", "description_html", "description_json"]}
```

Há equivalentes em `plane/api/views/intake.py:378` e
`plane/space/views/intake.py:208`.

## 5. Não existe apontamento de horas — mas o gancho existe

```python
# apps/api/plane/db/models/project.py:98
is_time_tracking_enabled = models.BooleanField(default=False)
```

**Nenhum modelo consome essa flag.** É o gancho da feature paga de Time Tracking
do Plane, deixada morta no código open source. Exposta na API pública em
`plane/api/serializers/project.py:94`.

Também existe a choice de exportação, igualmente sem implementação:

```python
# apps/api/plane/db/models/exporter.py:26
("issue_worklogs", "Issue Worklogs")
```

Nenhum modelo de worklog, timesheet ou time entry existe em `plane/db/models/`.
O único mecanismo de esforço é `Estimate` / `EstimatePoint`
(`plane/db/models/estimate.py`), cujos tipos são `CATEGORIES` e `POINTS` — não há
tipo em horas.

**Consequência:** reutilizar `is_time_tracking_enabled` como o toggle da feature
por project. E atenção ao **risco de colisão com o upstream**: o Plane comercial
tem time tracking próprio. Se um dia você migrar para a Commercial Edition, terá
duas implementações concorrentes. Vale nomear suas entidades de forma que não
colidam.

## 6. Um único app Django, uma única cadeia de migrations

`INSTALLED_APPS` (`plane/settings/common.py:97-118`) tem `plane.db`, `plane.app`,
`plane.api`, `plane.space`, `plane.license`, `plane.authentication`,
`plane.bgtasks`, `plane.analytics`, `plane.utils`, `plane.web`,
`plane.middleware`.

**`plane.db` é o único app com modelos de domínio.** Todos os modelos —
Workspace, Project, Issue, Cycle, Estimate — vivem em `plane/db/models/*.py` e
compartilham `plane/db/migrations/`, que tem **123 arquivos numerados
linearmente** (`0001` até `0122_alter_draftissue_assignees_...`). O único outro
diretório de migrations é `plane/license/migrations`.

**Consequência:** a instrução original do material — "criar app(s) Django
próprio(s)" — está **errada para este repositório**. O padrão da casa é adicionar
módulos em `plane/db/models/`, exportar em `plane/db/models/__init__.py`, e gerar
migrations na cadeia única existente.

## 7. Classes base a usar

```python
# apps/api/plane/db/models/project.py:180-189
class ProjectBaseModel(BaseModel):
    project = models.ForeignKey("db.Project", on_delete=CASCADE, related_name="project_%(class)s")
    workspace = models.ForeignKey("db.Workspace", on_delete=CASCADE, related_name="workspace_%(class)s")
    class Meta: abstract = True
    def save(self, *args, **kwargs):
        self.workspace = self.project.workspace     # sempre denormaliza
        super().save(*args, **kwargs)
```

```python
# apps/api/plane/db/models/workspace.py:185-195
class WorkspaceBaseModel(BaseModel):
    workspace = ...                 # obrigatório
    project = ...                    # nullable
```

Cadeia de abstratos (`plane/db/mixins.py`, `plane/db/models/base.py:17`):
`TimeAuditModel` + `UserAuditModel` + `SoftDeleteModel` → `AuditModel` →
`BaseModel` (id UUID) → `ProjectBaseModel` | `WorkspaceBaseModel`.

Mapeamento recomendado para as entidades novas:

| Entidade | Classe base | Por quê |
|---|---|---|
| Cliente | `WorkspaceBaseModel` | vive no workspace, sem project obrigatório |
| Contrato, Período de competência | `WorkspaceBaseModel` | pertencem ao Cliente |
| Tipo de Hora, Tipo de Atendimento, Feriado, Janela | `WorkspaceBaseModel` | catálogos de workspace |
| Apontamento (worklog) | `ProjectBaseModel` | sempre atrelado a um work item de um project |
| Bolsa de horas | `ProjectBaseModel` | atrelada a um work item |

## 8. Soft delete é o padrão, e a unicidade tem um padrão próprio

`SoftDeleteModel` (`plane/db/mixins.py:61-82`) dá `deleted_at`, manager `objects`
(que filtra deletados) e `all_objects`. O `delete()` dispara a task Celery
`soft_delete_related_objects`.

Unicidade sob soft delete aparece **sempre em par** — ex.:
`unique_together = ["workspace", "member", "deleted_at"]` **mais**
`UniqueConstraint(fields=["workspace","member"], condition=Q(deleted_at__isnull=True))`.
Ver `plane/db/models/workspace.py` (WorkspaceMember) e `project.py` (Project).

**Consequência:** o requisito "Cliente não pode ser excluído fisicamente, apenas
inativado" já vem de graça. E toda constraint de unicidade das entidades novas
precisa seguir esse padrão duplo.

## 9. Auditoria: usar o que já existe

- `ChangeTrackerMixin` (`plane/db/mixins.py:92-221`) — rastreia campos via
  `TRACKED_FIELDS`, expõe `has_changed()`, `changed_fields`, `old_values`.
  `Issue` usa isso (`plane/db/models/issue.py:104`).
- `IssueActivity` (`plane/db/models/issue.py`) + a task
  `issue_activity.delay(...)` são o padrão de trilha de atividade por work item.

A regra R8 (auditoria de apontamento) deve seguir esses dois, em vez de inventar
um mecanismo próprio.

## 10. Padrão de API

- **Duas camadas:** `plane.app` em `/api/` (autenticação por sessão/cookie,
  `BaseSessionAuthentication`) e `plane.api` em `/api/v1/` (API key,
  `APIKeyAuthentication` + rate limit). Ver `plane/urls.py:18-25`.
- `BaseViewSet` e `BaseAPIView` em `plane/app/views/base.py:48` e `:149`.
  `BaseSerializer` / `DynamicBaseSerializer` (com suporte a `?fields=` e
  `?expand=`) em `plane/app/serializers/base.py`.
- **Não há DRF router.** Todas as rotas são `path()` explícitos, um arquivo por
  recurso em `plane/app/urls/`, agregados em `urls/__init__.py`.
- Autorização por decorator `allow_permission(allowed_roles, level, creator,
  model)` — `plane/app/permissions/base.py:19`. Nas classes DRF, `ProjectEntityPermission`
  e afins em `plane/app/permissions/project.py`.
- Recurso simples para copiar ponta a ponta: **State** —
  `db/models/state.py` → `app/serializers/state.py` → `app/views/state/base.py`
  → `app/urls/state.py` (+ `api/` para a v1).

Note que os viewsets injetam escopo no save: `serializer.save(project_id=project_id)`.

## 11. Testes

- Local: `apps/api/plane/tests/` — `unit/`, `contract/api/`, `contract/app/`,
  `smoke/`, mais `factories.py` e `conftest.py`.
- Stack: **pytest + pytest-django + factory_boy**. `apps/api/pytest.ini`:
  `DJANGO_SETTINGS_MODULE = plane.settings.test`, markers `unit`/`contract`/
  `smoke`/`slow`, e `addopts = --strict-markers --reuse-db --nomigrations -vs`.
- Execução: `apps/api/run_tests.sh` ou `pytest -m unit` dentro de `apps/api`.
- Factories prontas: `UserFactory`, `WorkspaceFactory`, `WorkspaceMemberFactory`,
  `ProjectFactory`, `ProjectMemberFactory` (`plane/tests/factories.py`).
- Já existe teste de escopo de guest: `plane/tests/contract/app/test_issue_list_guest_scope_app.py`
  — bom modelo para os testes de isolamento da Fase 8.
- Atenção: `--nomigrations` significa que o schema de teste vem dos models. Uma
  migration quebrada **não** é detectada pelos testes.

## 12. Frontend

- Monorepo pnpm + turbo. `apps/web` (app principal), `apps/admin`, `apps/space`
  (boards públicos), `apps/live`, `apps/proxy`.
- **Estado: MobX** (`mobx`, `mobx-react`), não Zustand. Root store em
  `apps/web/core/store/root.store.ts`; exemplo de store a copiar:
  `core/store/state.store.ts` (interface `IStateStore`, `makeObservable`,
  `computedFn`, service injetado).
- Camada HTTP em `packages/services/src/`; tipos em `packages/types`; constantes
  em `packages/constants`; i18n em `packages/i18n`; UI em `packages/ui` e
  `packages/propel`.
- Telas de configuração:
  - workspace: `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/(workspace)/`
  - project: `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/`
  - componentes: `apps/web/core/components/settings/{workspace,project}/`
- O gate de edição de work item no front:
  `apps/web/core/components/issues/issue-detail/root.tsx:221` usa
  `allowPermissions([ADMIN, MEMBER], PROJECT, ...)`, propagado como `disabled`
  para estado, prioridade e propriedades (`main-content.tsx:92,105,113`).
  Implementação em `core/store/user/base-permissions.store.ts:193` — inclusão
  simples na lista, **sem hierarquia `>=`**.

## 13. Licença

`LICENSE.txt` na raiz é AGPL-3.0. Os arquivos têm cabeçalho
`SPDX-License-Identifier: AGPL-3.0-only`. Confirma o aviso já registrado no
README: oferecer isso como serviço a clientes externos tem implicações que valem
validação jurídica.


---

## 14. Design system e stack de gráficos

**Biblioteca de gráficos: Recharts `^2.15.1`.** Declarada no catálogo do pnpm, não
nos `package.json` individuais:

```yaml
# pnpm-workspace.yaml:159
"recharts": "^2.15.1"
```

Os consumidores referenciam via catálogo — `apps/web/package.json:68` e
`packages/propel/package.json:80` ambos têm `"recharts": "catalog:"`. Nenhuma
outra biblioteca de gráficos existe no monorepo: buscas por `@nivo`, `chart.js`,
`victory`, `visx`, `apexcharts`, `echarts` e `d3` retornam zero resultados.

**O design system é `@plane/propel`** (`packages/propel/`), que encapsula o
Recharts e exporta wrappers prontos:

```
packages/propel/src/charts/
├── area-chart/    line-chart/    radar-chart/
├── bar-chart/     pie-chart/     scatter-chart/
├── tree-map/
└── components/    (legend, tick, tooltip compartilhados)
```

Exemplo da estrutura de um wrapper (`packages/propel/src/charts/bar-chart/root.tsx`):

```tsx
import { BarChart as CoreBarChart, Bar, ResponsiveContainer, Tooltip,
         XAxis, YAxis, Legend, CartesianGrid } from "recharts";
import { AXIS_LABEL_CLASSNAME } from "@plane/constants";
import type { TBarChartProps } from "@plane/types";
import { getLegendProps } from "../components/legend";
import { CustomXAxisTick, CustomYAxisTick } from "../components/tick";
import { CustomTooltip } from "../components/tooltip";

export const BarChart = React.memo(function BarChart<K extends string, T extends string>(
  props: TBarChartProps<K, T>
) { ... })
```

Note que o wrapper já resolve `ResponsiveContainer`, tooltip customizado, legenda
com destaque de série ativa (`activeLegend`/`activeBar`), ticks customizados e
suporte a barras empilhadas.

Tipos em `packages/types/src/charts/`:
- `TChartData<K, T>`, `TBaseChartProps`, `TAxisChartProps`, `TChartLegend`,
  `TChartMargin`, `TBarChartProps`

Constantes em `packages/constants/src/chart.ts`.

Além do propel, `@plane/ui` (`packages/ui/`) tem os componentes de interface
gerais. O restante do monorepo: `packages/editor`, `packages/hooks`,
`packages/i18n`, `packages/logger`, `packages/services`, `packages/shared-state`,
`packages/tailwind-config`, `packages/types`, `packages/utils`.

**Feature de analytics já existente**, cujos padrões devem ser reaproveitados:
- `apps/web/core/components/analytics/` — `analytics-wrapper.tsx`,
  `analytics-section-wrapper.tsx`, `insight-card.tsx`, `total-insights.tsx`,
  `trend-piece.tsx`, `insight-table/{root,data-table,loader}.tsx`,
  `loaders.tsx`, `empty-state.tsx`, `export.ts`,
  `select/{duration,project,analytics-params,select-x-axis,select-y-axis}.tsx`,
  `tabs.tsx`, `use-analytics-tabs.tsx`
- `apps/web/core/components/analytics/work-items/` — `priority-chart.tsx`,
  `created-vs-resolved.tsx`, `customized-insights.tsx`,
  `workitems-insight-table.tsx`
- Rota: `apps/web/app/(all)/[workspaceSlug]/(projects)/analytics/[tabId]/`
  (`page.tsx`, `layout.tsx`, `header.tsx`)
- Backend: `plane/app/views/analytic/{base,advance,project_analytics}.py`,
  `plane/app/serializers/analytic.py`, `plane/app/urls/analytic.py`,
  `plane/db/models/` tem `AnalyticView` (migration `0031_analyticview.py`)
- Exportação assíncrona: `plane/bgtasks/analytic_plot_export.py`

**Regra derivada para a Fase 9:** não instalar biblioteca de gráficos nova e não
usar Recharts diretamente nas telas. Consumir os wrappers do `@plane/propel`. Se
faltar um tipo de gráfico, adicionar o wrapper ao propel seguindo o padrão dos
existentes.
