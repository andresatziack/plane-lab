# Oportunidades: ganchos mortos no fork e melhorias sugeridas

Duas partes: **(A)** o que existe no código como gancho não terminado, provável
resquício da edição paga, e **(B)** lacunas do escopo atual que eu recomendo
cobrir. Tudo verificado no fork, com arquivo e linha.

---

# Parte A — Ganchos mortos que valem a pena

## A1. Work Item Types — o achado mais valioso ⭐

**Estado: modelos completos, FK ligada, flag existente, frontend parcialmente
cabeado. Falta só a API de CRUD e a tela de administração.**

```python
# apps/api/plane/db/models/issue_type.py:14
class IssueType(BaseModel):
    workspace = FK("db.Workspace", related_name="issue_types")
    name, description, logo_props
    is_epic = BooleanField(default=False)
    is_default = BooleanField(default=False)
    is_active = BooleanField(default=True)
    level = FloatField(default=0)
    external_source, external_id

class ProjectIssueType(ProjectBaseModel):   # liga tipo ↔ project
    issue_type = FK("db.IssueType", related_name="project_issue_types")
    level, is_default
```

O que já existe além dos modelos:
- **`Issue.type` FK já está no modelo** de work item (`db/models/issue.py`)
- **Flag `Project.is_issue_type_enabled`** (`db/models/project.py:99`) — hoje morta
- Serializer da API pública já referencia (`api/serializers/issue.py`)
- **Frontend já tem referências espalhadas em 10+ arquivos**:
  `issues/issue-modal/base.tsx`, `issue-layouts/list/block.tsx`,
  `issue-detail/parent-select.tsx`, `core/modals/existing-issues-list-modal.tsx`,
  `home/widgets/recents/issue.tsx`, entre outros

O que falta: viewset/serializer/urls de CRUD em `plane.app`, tela de administração,
e ligar a flag.

**Por que importa para o seu negócio:** um service desk precisa de tipos de
chamado — Incidente, Requisição de Serviço, Mudança, Problema. E isso **interage
direto com o seu faturamento**: cada tipo pode ter um Tipo de Atendimento padrão
diferente e uma regra de SLA diferente. É um eixo que o seu escopo atual não tem.

Note que `is_epic` no modelo indica que a mesma entidade serve de base para Epics
(feature Pro). Você ganha os dois com o mesmo trabalho.

**Custo estimado: baixo.** É a relação melhor esforço/retorno de toda esta lista.

## A2. Exportação de apontamentos — a vaga já tem o seu nome

```python
# apps/api/plane/db/models/exporter.py:26
("issue_worklogs", "Issue Worklogs")
```

Essa choice existe e **nenhum código a usa**. Mas a infraestrutura de exportação
em volta dela está pronta e funcionando:
- `plane/bgtasks/export_task.py` — pipeline de export assíncrono
- `plane/bgtasks/analytic_plot_export.py` — export de analytics
- `ExporterHistory` com status, filtros e URL do arquivo
- `plane/utils/exporters/formatters.py`

**Consequência prática para a Fase 9:** o requisito de "exportação em CSV" **não
deve construir nada novo**. Deve criar o handler para o tipo `issue_worklogs` no
pipeline existente, e ganha de graça: fila, histórico, status, retry, download.

Atenção ao TODO já registrado em `export_task.py:41` — os exports ainda não
entram na tabela `FileAsset`.

## A3. Estimativa em horas — parece um presente, mas é uma armadilha

O frontend tem o sistema pronto:

```ts
// packages/constants/src/estimates.ts:122-141
time: {
  name: "Time",
  templates: { hours: { title: "Hours", values: [1,2,3,4,5,6] } },
  is_available: true,
  is_ee: true,          // ← marcado como enterprise edition
}
```

E está bloqueado por uma função de três casos:

```ts
// apps/web/core/components/estimates/create/helper.tsx:10-19
export const isEstimateSystemEnabled = (key) => {
  switch (key) {
    case EEstimateSystem.POINTS: return true;
    case EEstimateSystem.CATEGORIES: return true;
    default: return false;          // ← TIME cai aqui
  }
};
```

**Mas não é só destravar o gate.** O backend não suporta:

```python
# apps/api/plane/db/models/estimate.py:13
class EstimateType(models.TextChoices):
    CATEGORIES = "categories", "Categories"
    POINTS = "points", "Points"
    # TIME não existe
```

Precisaria de nova choice + migration + validação. E, mais importante:
**estimativa não é apontamento.** Estimativa é esforço planejado; o seu worklog é
esforço realizado. Não substitui nada do seu escopo.

**Valor real:** permite o indicador "estimado vs. realizado" por chamado, que é
excelente para um negócio de serviço — mas é complemento, não prioridade. Não
comece por aqui.

## A4. Integrações e abertura de chamado por e-mail

Toda a família de integração está modelada e **sem nenhuma view**:
`Integration`, `WorkspaceIntegration`, `GithubRepository`, `GithubRepositorySync`,
`GithubIssueSync`, `GithubCommentSync`, `SlackProjectSync`
(`db/models/integration/{base,github,slack}.py`). Os tipos TypeScript
correspondentes existem completos em `packages/types/src/integration.ts:8-40`.

No comparativo de planos, o grupo `integrations` inteiro está marcado
`comingSoon: true`, e **"Emails For Intake"** aparece como *coming soon,
business+*.

**Por que isso importa mais do que parece:** para um service desk, abrir chamado
por **e-mail** é básico. Hoje o seu cliente precisa fazer login no portal. Na
prática, metade dos usuários vai mandar e-mail para você de qualquer forma.

Isso é uma feature própria, não um gancho a destravar — mas é a lacuna mais
sentida no dia a dia depois do apontamento.

## A5. Workflows e aprovações — gancho invertido no frontend

O frontend renderiza log de atividade para **três campos que não existem no
schema** desta edição:

```tsx
// apps/web/core/components/common/activity/helper.tsx:69-71
is_project_updates_enabled
is_epic_enabled
is_workflow_enabled
```

Aparecem só nesse arquivo. São handlers da edição paga: Epics (Pro), Workflows +
Approvals (Business), Project Updates.

**Workflows** significaria regras de transição de estado — ex.: "só o cliente pode
fechar", "não pode ir para Faturado sem aprovação". Valor médio para você, e
depende do que você decidir sobre travamento de período.

## A6. Achados menores, para registro

| Achado | Onde | Veredito |
|---|---|---|
| `Team` — só `name`, `description`, `workspace`, `logo_props`, zero views | `db/models/workspace.py:261` | casca quase vazia, precursor de Teamspaces. Pouco valor |
| `Description` / `DescriptionVersion` órfãos (os endpoints usam `IssueDescriptionVersion`, outro modelo) | `db/models/description.py` | versionamento genérico não terminado |
| `WorkspaceMember.issue_props`, `getting_started_checklist`, `tips`, `explored_features` — JSONFields sem nenhum leitor | `db/models/workspace.py:209-213` | legado / onboarding não terminado |
| `Importer` (github/jira) modelado, zero views | `db/models/importer.py:13` | gancho dos importadores pagos |
| `ProjectDeployBoard` marcado `# DEPRECATED TODO` | `db/models/project.py:298` | legado, substituído por `deploy_board.py` |
| `IssueAttachment`, `IssueBlocker`, `ProjectWebhook`, `SocialLoginConnection`, `SessionStore` — zero referências | vários | substituídos, código morto |
| `WorkspaceTheme` tem endpoint mas nenhum consumidor no front | `app/urls/workspace.py:124` | endpoint órfão |
| `HomeWidgetKeys.QUICK_TUTORIAL` e `NEW_AT_PLANE` filtrados no backend, mas com componentes prontos no front | `app/views/workspace/home.py:34` | widgets desligados de propósito |
| `features-list.tsx` tem branch `isPro` inalcançável (`isPro: false` hardcoded nas 5 entradas) | `project/settings/features-list.tsx:30-126` | resíduo do gating removido |
| Nenhum backend de billing existe. Preços e produtos são estáticos em `packages/constants/src/payment.ts`, e "upgrade" é link externo | — | o paywall é só UI |

---

# Parte B — Lacunas do escopo atual que eu recomendo cobrir

Ordenadas por valor. Nada aqui está nos prompts hoje.

## B1. Visão de timesheet semanal ⭐ — a maior lacuna de UX do escopo

Hoje todo o apontamento acontece **dentro do work item**. Um técnico que atende 15
chamados por dia vai precisar abrir 15 telas para apontar.

O que falta: uma tela **"Meus apontamentos"** com grade semanal — dias nas colunas,
chamados nas linhas, total por dia e por semana, e capacidade de adicionar ou
editar linha ali mesmo.

É exatamente o que faz o Tempo ser adotado, e é a diferença entre o técnico
apontar no fim do dia com precisão ou chutar na sexta-feira. **Como todo o seu
faturamento depende da qualidade desse dado, isso é requisito, não conforto.**

Ganho adicional: o total diário deixa visível o dia sem apontamento, que hoje só
apareceria no fechamento do mês.

## B2. Timer (play/stop) — terceiro modo de entrada

Os prompts têm dois modos: Duração e Intervalo (regra R9). Falta o terceiro que os
técnicos realmente usam: **cronômetro rodando**.

Ao parar, cai no modo Intervalo — com hora de início e fim reais, o que ativa a
classificação automática de Tipo de Hora da Fase 2b **sem o técnico digitar nada**.
É o modo com melhor qualidade de dado.

Requer: estado de timer ativo por usuário (só um por vez), persistência para
sobreviver a refresh e a fechar o navegador, e tratamento de timer esquecido
ligado — que precisa de política própria, senão gera apontamento de 14h.

## B3. SLA de atendimento

Contrato de suporte com pool de horas quase sempre tem SLA de resposta e de
solução. Seu escopo tem **zero** SLA.

No comparativo de planos, "Custom SLAs" é business+ — não existe aqui.

O mínimo útil: prazo de primeira resposta e de solução por prioridade e por tipo de
chamado (usando A1), relógio que respeita as janelas de expediente que você já vai
ter modelado na Fase 2b, e indicador de cumprimento no dashboard do cliente.

Note a sinergia: as **janelas de classificação da Fase 2b são exatamente o
calendário que um SLA precisa** para não contar horas fora do expediente. Você já
vai ter construído metade disso.

## B4. Entidade de Fatura

A Fase 6 entrega "consolidado mensal + CSV", e a Fase 4 trava o período. Falta o
artefato que **congela** os números: uma Fatura com status (rascunho / emitida /
paga / cancelada), competência, itens e total.

Sem ela, "faturei" é um estado que só existe na sua cabeça e na planilha. Com ela,
o travamento de período ganha um gatilho natural e o faturamento de excedente da
Fase 6 tem onde ser registrado.

## B5. Notificações para o usuário do cliente

O escopo não menciona notificação. O Plane já tem `bgtasks/notification_task.py`.

Precisa confirmar e garantir que o papel de cliente recebe notificação de
comentário e de mudança de estado nos chamados da empresa dele — e **que não recebe
nada de outros clientes**. Isso é tanto funcionalidade quanto vazamento potencial:
notificação é um canal que costuma escapar do controle de escopo.

## B6. Meta de horas e capacidade por técnico

Complemento natural do B1: saber que um técnico tem 8h/dia de capacidade permite
mostrar ocupação, identificar sobrecarga e planejar. Sem isso, você tem o
consumo do cliente mas não o custo do seu time.

Valor médio. Só faz sentido depois do B1.

---

# Recomendação de prioridade

Se fosse eu decidindo, na ordem:

1. **Manter o plano atual das Fases 1 a 9** — o apontamento e o faturamento são o
   coração e nada disso substitui
2. **B1 — timesheet semanal**, incorporado à Fase 3 ou como fase própria logo
   depois. Sem isso o dado de entrada é ruim, e todo o resto depende dele
3. **A1 — Work Item Types**, porque é baratíssimo e abre SLA e classificação
4. **A2 — usar o pipeline de export existente** na Fase 9, em vez de construir CSV
   novo. É correção de rota, não trabalho extra
5. **B4 — Fatura**, junto ou logo após a Fase 6
6. **B2 — timer**, depois que o apontamento estiver estável
7. **B3 — SLA**, depois de A1
8. **A4 — e-mail para abertura de chamado**, quando o portal já estiver em uso
9. O resto: registrar e não fazer agora

## Aviso sobre reimplementar features da edição paga

Nada impede tecnicamente ou legalmente que você implemente essas features no seu
fork AGPL. Mas cada uma aumenta a divergência com o upstream e o custo dos merges
futuros. E se algum dia você migrar para a Commercial Edition, terá duas
implementações concorrentes da mesma coisa.

Por isso a lista acima privilegia o que **serve o seu negócio** (apontamento,
faturamento, SLA, timesheet) e não o que simplesmente **está destravável**.
