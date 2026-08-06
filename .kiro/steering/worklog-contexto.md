---
inclusion: always
---

# Contexto Mestre — Service Desk com Apontamento de Horas (fork do Plane)

> Este é um steering file com `inclusion: always` — ele é incluído
> automaticamente em toda interação do Kiro neste repositório. Os prompts de fase
> em `docs/worklog/` assumem que este contexto já está presente.

## 1. Produto

Estou construindo, sobre o Plane (https://github.com/makeplane/plane), um
service desk com apontamento de horas equivalente a "Jira Service Management +
Tempo Timesheets", adaptado ao meu modelo de negócio. Hoje essas regras de
cálculo vivem em relatórios de Power BI externos ao Jira; o objetivo é
internalizá-las por completo em um único sistema.

## 2. Glossário e mapeamento para o Plane

| Meu domínio | Equivalente no Plane |
|---|---|
| Chamado / atendimento | Work item (issue) |
| Técnico / atendente | Workspace Member |
| Cliente (empresa) | **Entidade nova** — não existe no Plane |
| Usuário do cliente (end user) | **Papel novo**, escopado por Cliente (Fase 8) |
| Apontamento / work log | **Entidade nova** vinculada ao work item |
| Contrato de suporte | **Entidade nova** vinculada ao Cliente |
| Bolsa de horas do chamado | **Entidade nova** vinculada ao work item |
| Feriados / janelas de classificação | **Entidades novas** de parametrização |

## 2b. Arquitetura de Cliente e isolamento

**Cada Cliente corresponde a um ou mais projects do Plane.**

- O **Cliente** é a entidade de negócio: contratos, preços, faturamento, dashboards
- O **project** é a unidade de acesso e organização, nativa do Plane
- O Cliente de um work item é **derivado do project**, nunca definido livremente
  no chamado
- Usuários do cliente são **workspace Guests** adicionados aos projects dos seus
  Clientes. Este fork só tem GUEST/MEMBER/ADMIN — não há papel Commenter nem
  papéis customizados, então o GUEST é estendido (ver Fase 8)
- Técnicos são workspace Members; o Admin do workspace enxerga todos os projects

Por quê: o Plane já garante que um Guest só vê os projects aos quais foi
explicitamente adicionado, e não vê outros projects nem informação de nível de
workspace (verificado em `plane/app/views/project/base.py:101-114`). Isso entrega
o isolamento entre clientes usando o mecanismo nativo e testado, em vez de um
filtro customizado replicado em cada endpoint.

**Configuração obrigatória:** os projects de cliente precisam de
`Project.guest_view_all_features = True`. Com o padrão `False`, um Guest só vê os
work items **que ele mesmo criou** — Marcel não veria os chamados abertos por
colegas dele nem os abertos pelos técnicos em nome da Marubeni. Ver
`ACHADOS-DO-CODIGO.md`, seção 2.

Consequências que valem para todas as fases:
- O vínculo usuário↔Cliente é **derivado** da participação nos projects. Não
  criar tabela de associação própria para isso
- A troca de "portal" pelo usuário do cliente é o **seletor de project nativo**
- Não há ambiguidade de qual Cliente é o chamado: o chamado nasce dentro de um
  project, e o project pertence a exatamente um Cliente
- Relatório cross-cliente funciona porque Admin e Member enxergam todos os
  projects
- Configuração (tipos de hora, janelas, feriados) fica no nível do workspace:
  fonte única da verdade

### Exemplo de referência

Usar para validar o modelo em todas as fases:

| Cliente | Project | Contrato | Usuários do portal |
|---|---|---|---|
| Marubeni (matriz) | `Marubeni` | 10h/mês, 12 meses | Marcel (gerente de TI) |
| Terlogs (adquirida pela Marubeni) | `Terlogs` | 30h/mês, 36 meses | Adriano (coordenador de TI) |

- Marcel é workspace Guest nos projects `Marubeni` e `Terlogs`.
  Alterna entre os dois pelo seletor nativo, vê o dashboard de contrato de cada
  um, e não vê nenhum outro cliente
- Adriano é workspace Guest apenas em `Terlogs`
- Chamado aberto no project `Terlogs` debita o contrato da Terlogs, sem
  ambiguidade e sem o usuário precisar escolher
- Marubeni e Terlogs são Clientes **independentes** com contratos independentes.
  A relação societária entre elas não afeta faturamento

## 3. Modelo de cobrança

Dois modos. O **Cliente define o modo padrão**; um apontamento individual pode
usar outro Tipo de Atendimento e assim seguir outra rota (ex.: cliente de
contrato recebendo um serviço fora de escopo, faturado à parte).

**Contrato** — o cliente compra um pool de horas mensais (ex.: 30h/mês) por um
período contratado (ex.: 12 meses ou mais). Os apontamentos debitam desse pool.
A tela do chamado mostra consumo de horas, não valores em R$.
- **Saldo não utilizado acumula** para os meses seguintes.
- **Saldo pode ficar negativo.** Não bloquear apontamento de trabalho já
  executado. Ao fechar o mês com déficit, há duas saídas: transportar o déficit
  para o mês seguinte, ou **faturar o excedente em R$** ao cliente, zerando o
  déficit e preservando as horas dos meses futuros.

**Avulso** — não há pool. Cada apontamento gera valor em R$, faturado no início
do mês seguinte referente aos atendimentos do mês anterior. Os valores de hora
são personalizáveis por cliente.

## 4. As três grandezas de tempo (conceito central)

Esta separação é obrigatória e deve estar explícita no modelo de dados:

1. **Duração bruta** — o tempo informado, em minutos inteiros (segundos
   truncados). Ex.: `1h 08min` = 68.
2. **Horas apontadas** — duração bruta após arredondamento em blocos de 15
   minutos, persistida em decimal com **ponto**. Ex.: 68 → 75min → `1.25`.
   É o tempo cronológico oficial do atendimento.
3. **Horas equivalentes** — horas apontadas × multiplicador do Tipo de Hora.
   Ex.: `1.25 × 1.5 = 1.875`. É a grandeza que gera valor em R$, e a **única que
   o cliente vê** (regra R11).
4. **Horas debitadas** — igual às equivalentes, exceto quando o apontamento é de
   garantia, e aí vale `0`. É o que efetivamente sai do pool.

Motivo desta modelagem: 1h trabalhada fora do expediente "custa" 1,5h para o
cliente de contrato e R$ 300,00 em vez de R$ 200,00 para o cliente avulso. O
multiplicador é o mesmo nos dois casos — um único parâmetro configurável
resolve as duas regras, em vez de duas tabelas de preço independentes que
podem divergir.

## 5. Regras invioláveis

### R1 — Parser de tempo
Entrada em texto livre. Deve aceitar `1h`, `30m`, `30min`, `1h15m`,
`1h 15min`, `1,5h`, `1.5h`, `90min`, `2 horas`. Case-insensitive, com ou sem
espaço entre unidades. Entrada inválida gera erro de validação claro no
formulário — nunca falha silenciosa nem interpretação adivinhada.
A UI mostra preview da conversão em tempo real, já com o arredondamento
aplicado (ex.: usuário digita `1h 08min`, UI mostra "= 1h 15min (1,25h)").

### R2 — Arredondamento em blocos de 15 minutos
O projeto trabalha com apontamento mínimo de 15 minutos.

Regra conceitual: arredondar para o **múltiplo de 15 minutos mais próximo**.
Empate exato (7min30s) arredonda para cima. O resultado **nunca é menor que 15
minutos** quando a duração bruta for maior que zero.

Implementação em minutos inteiros: seja `r = bruto % 15`. Se `r >= 8`,
arredonda para cima; senão, para baixo. Em seguida, aplicar o piso de 15
minutos se `bruto > 0`.

Casos de referência — usar exatamente como suíte de testes:

| Entrada | Bruto (min) | Resto | Apontado (min) | Decimal |
|---|---|---|---|---|
| `1h` | 60 | 0 | 60 | 1.0 |
| `1h 05min` | 65 | 5 | 60 | 1.0 |
| `1h 07min` | 67 | 7 | 60 | 1.0 |
| `1h 08min` | 68 | 8 | 75 | 1.25 |
| `1h 15min` | 75 | 0 | 75 | 1.25 |
| `1h 20min` | 80 | 5 | 75 | 1.25 |
| `1h 23min` | 83 | 8 | 90 | 1.5 |
| `45min` | 45 | 0 | 45 | 0.75 |
| `22min` | 22 | 7 | 15 | 0.25 |
| `23min` | 23 | 8 | 30 | 0.5 |
| `8min` | 8 | 8 | 15 | 0.25 |
| `3min` | 3 | 3 | 15 (piso) | 0.25 |
| `30h` | 1800 | 0 | 1800 | 30.0 |

### R3 — Persistência decimal, exibição legível
Persistir sempre em decimal com ponto (`1.25`). Exibir sempre em formato
legível pt-BR: `1h 15min` nas listas de apontamento, `1,25h` quando o contexto
exigir o número. Nunca persistir string formatada como fonte da verdade.

### R4 — Snapshot histórico
Todo apontamento persiste, no momento da criação: o multiplicador aplicado, o
valor/hora vigente, e a referência do pool/contrato debitado. Alterar
configuração de tipos, multiplicadores, janelas, feriados ou preços depois
NUNCA recalcula apontamentos já existentes.

### R5 — Garantia
Apontamento com `garantia = true`:
- aparece normalmente na lista e soma no **total de horas apontadas**
- NÃO debita pool, NÃO gera valor em R$, NÃO soma no **total faturável**

### R6 — Hierarquia de débito
1. Se o work item tem bolsa de horas própria → debita da bolsa do work item
2. Senão, se a rota de faturamento é Contrato → debita do pool mensal do
   contrato do cliente
3. Senão (Avulso) → gera valor em R$

Nunca debitar de dois lugares. A bolsa de um work item é isolada e não toca o
pool do contrato — o caso de uso é um cliente com contrato fixo de suporte que
fechou um projeto específico e não quer que aquele projeto consuma as horas de
suporte.

### R7 — Competência pela data do trabalho
O pool debitado é o do mês da **data do atendimento** informada no
apontamento, não a data de criação do registro. Apontamento retroativo debita
o mês retroativo.

### R8 — Auditoria
Todo apontamento registra: autor (quem executou o trabalho), criador (quem
registrou — podem diferir, ver delegação na Fase 7), data/hora de criação, e
histórico completo de alterações e exclusões.

### R9 — Dois modos de entrada de tempo
O técnico escolhe como informar o tempo, e as duas formas convergem para a
mesma duração bruta em minutos:
- **Duração** — texto livre, conforme R1
- **Intervalo** — hora de início e hora de fim (ex.: 14:00 às 15:30 = 90 min)

O modo Intervalo é o que habilita a detecção automática de R10, porque só ele
informa em que horário o trabalho ocorreu.

### R11 — Visibilidade das grandezas por papel

**O cliente vê o apontamento convertido, nunca o apontamento original do
técnico.** O técnico vê e edita o seu apontamento original e também vê o valor
final que o cliente verá.

| Informação | Técnico (autor) | Técnico (outros) | Admin | Cliente |
|---|---|---|---|---|
| Duração bruta digitada | vê e edita | vê | vê | **não vê** |
| Horas apontadas (pós-arredondamento) | vê | vê | vê | **não vê** |
| Horas equivalentes (pós-multiplicador) | vê | vê | vê | **vê** |
| Horas debitadas | vê | vê | vê | vê |
| Multiplicador numérico | vê | vê | vê | não vê |
| Tipo de Hora (rótulo) | vê | vê | vê | vê |
| Descrição do apontamento | vê | vê | vê | vê |
| Autor do apontamento | vê | vê | vê | vê |
| Flag de Garantia | vê | vê | vê | vê |
| Valor em R$ | não vê | não vê | vê | só se avulso |

Consequências obrigatórias:

**(a) `horas_equivalentes` e `horas_debitadas` são campos distintos.** As
equivalentes são sempre calculadas (horas apontadas × multiplicador). As debitadas
são `0` quando o apontamento é de garantia, e iguais às equivalentes nos outros
casos. Assim o cliente sempre vê a mesma grandeza, e o apontamento de garantia
aparece para ele como esforço realizado com `0h` descontado — o que evidencia a
cortesia concedida em vez de esconder o trabalho.

**(b) A restrição é de serializer, não de UI.** O endpoint consumido pelo portal
do cliente deve usar um serializer dedicado que **não inclui** os campos de
duração bruta, horas apontadas e multiplicador. Esconder na interface e mandar no
payload é vazamento.

**(c) Cuidado com o rótulo no portal do cliente.** O total do cliente não vai
bater com o do técnico: 1h trabalhada fora do expediente aparece como 1,5h para o
cliente. Rotular como "Horas consumidas do contrato" ou "Horas faturadas" —
**nunca** "Horas trabalhadas". O rótulo errado transforma uma regra contratual
legítima em suspeita de inflação de horas.

### R10 — Classificação automática do Tipo de Hora

Regras de negócio vigentes nos contratos com os clientes — devem ser
configuração, não código:

| Quando | Tipo de Hora | Multiplicador |
|---|---|---|
| Seg a sex, 08:00–18:00 | Horário comercial | 1.0 |
| Seg a sex, 18:00 até 08:00 do dia seguinte | Fora do expediente | 1.5 |
| Sábado, dia inteiro | Fora do expediente | 1.5 |
| Domingo, dia inteiro | Domingos e feriados | 2.0 |
| Feriado, dia inteiro | Domingos e feriados | 2.0 |

Note que "Fora do expediente" é uma janela contínua que **atravessa a
meia-noite**, e que sábado (1.5) e domingo (2.0) são diferentes — a
classificação não é derivável de "dia trabalhado / não trabalhado". O modelo é
de janelas por dia da semana e faixa horária, com prioridade (ver Fase 2b).

Comportamento por modo de entrada (R9):
- **Modo Intervalo** — classificação automática completa. Quando o intervalo
  atravessa faixas diferentes, o lançamento é **dividido em segmentos**, um
  apontamento por faixa, agrupados por um identificador de lançamento comum.
  Ex.: seg 17:00–20:00 = 1h comercial + 2h fora do expediente.
- **Modo Duração** — classificação apenas pela data. Feriado, domingo e sábado
  são determinados automaticamente (valem o dia inteiro). Em dia útil não há
  como inferir o horário, então **o técnico seleciona o Tipo de Hora**. Nunca
  dividir apontamento no modo Duração.

As janelas de classificação são **parâmetro comercial do workspace**, iguais
para todos os técnicos, porque refletem o que está escrito nos contratos com os
clientes. **Não existe jornada de trabalho por técnico** — duas configurações de
horário coexistindo divergem, e faria a fatura do cliente depender de quem
atendeu.

Ao dividir em segmentos, o arredondamento da R2 é aplicado **por segmento**,
porque cada segmento é uma unidade faturável com multiplicador próprio. Exceção:
se a duração bruta total do lançamento for menor que 15 minutos, não dividir.

Classificação é conveniência, não trava. Sobrescrita manual sempre permitida, e
registrada na auditoria quando divergir da sugestão.

## 6. Restrições técnicas

O repositório foi auditado. Os padrões abaixo são os reais — **não improvise
estrutura**. Detalhes com arquivo e linha em `ACHADOS-DO-CODIGO.md`.

### Backend — onde o código vai

- Backend Django em `apps/api/plane/`.
- **`plane.db` é o único app com modelos de domínio.** Não criar app Django novo.
  Adicionar módulos em `plane/db/models/<nome>.py` e exportar em
  `plane/db/models/__init__.py`.
- Migrations: cadeia **única e linear** em `plane/db/migrations/` (já em `0122`).
  Gerar na sequência existente.
- Classes base a herdar (`plane/db/mixins.py`, `db/models/base.py`):
  - `WorkspaceBaseModel` → Cliente, Contrato, Período, catálogos, feriados,
    janelas (workspace obrigatório, project nullable)
  - `ProjectBaseModel` → Apontamento, Bolsa de horas (project obrigatório;
    o `save()` denormaliza `workspace` automaticamente)
- **Soft delete é padrão** via `SoftDeleteModel`: managers `objects` (filtra
  deletados) e `all_objects`. Toda constraint de unicidade segue o padrão duplo
  da casa: `unique_together` incluindo `deleted_at` **mais** um
  `UniqueConstraint` condicional com `Q(deleted_at__isnull=True)`.
- Auditoria (regra R8): usar `ChangeTrackerMixin` (`TRACKED_FIELDS`) e o padrão
  de `IssueActivity` + task `issue_activity.delay(...)`. Não inventar mecanismo
  próprio.
- API em duas camadas: `plane.app` em `/api/` (sessão) e `plane.api` em
  `/api/v1/` (API key). `BaseViewSet` / `BaseAPIView` em
  `plane/app/views/base.py`; `BaseSerializer` / `DynamicBaseSerializer` em
  `plane/app/serializers/base.py`.
- **Não há DRF router.** Rotas são `path()` explícitos, um arquivo por recurso em
  `plane/app/urls/`, agregado em `urls/__init__.py`.
- Autorização: decorator `allow_permission(allowed_roles, level, creator, model)`
  em `plane/app/permissions/base.py`, e as classes DRF em
  `plane/app/permissions/project.py`.
- Recurso simples para copiar ponta a ponta: **State** (`db/models/state.py` →
  `app/serializers/state.py` → `app/views/state/base.py` → `app/urls/state.py`).
- Reutilizar a flag já existente e hoje sem uso
  `Project.is_time_tracking_enabled` (`db/models/project.py:98`) como toggle da
  feature por project.

### Papéis — o que existe de fato

Só **3 papéis**: `ADMIN = 20`, `MEMBER = 15`, `GUEST = 5`.

**Não existe papel Commenter, nem papéis customizados, nem permission schemes,
nem permissões granulares** neste fork. A documentação pública do Plane descreve
esses recursos porque se refere a uma versão mais nova ou comercial. Não conte
com eles.

O único mecanismo de granularidade para papéis baixos é o boolean por project
`Project.guest_view_all_features`.

### Testes

- `apps/api/plane/tests/` — `unit/`, `contract/app/`, `contract/api/`, `smoke/`.
- **pytest + pytest-django + factory_boy**. Rodar com `pytest -m unit` dentro de
  `apps/api`, ou `./run_tests.sh`.
- Factories prontas em `plane/tests/factories.py`: `UserFactory`,
  `WorkspaceFactory`, `WorkspaceMemberFactory`, `ProjectFactory`,
  `ProjectMemberFactory`.
- Modelo de teste de isolamento a seguir:
  `plane/tests/contract/app/test_issue_list_guest_scope_app.py`.
- Atenção: `pytest.ini` usa `--nomigrations`, então o schema de teste vem dos
  models. **Migration quebrada não é detectada pelos testes** — validar migrations
  à parte.

### Frontend

- Monorepo pnpm + turbo. App principal em `apps/web`.
- **Estado: MobX**, não Zustand. Root store em `apps/web/core/store/root.store.ts`;
  store modelo a copiar: `core/store/state.store.ts`.
- HTTP em `packages/services/src/`; tipos em `packages/types`; constantes em
  `packages/constants`; i18n em `packages/i18n`; UI em `packages/ui` e
  `packages/propel`.
- Settings de workspace e de project em
  `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/`, com componentes em
  `core/components/settings/{workspace,project}/`.

### Regras gerais

- Toda lógica de domínio (parser, arredondamento, multiplicador, classificação de
  tipo de hora, débito de pool, precificação) em camada de serviço/domínio pura e
  testável, isolada de views e serializers.
- Cálculo financeiro com `Decimal`, nunca `float`. Duas casas para moeda,
  `ROUND_HALF_UP`.
- Datas e horários: definir e documentar a estratégia de fuso. `Workspace` e
  `Project` já têm campo `timezone` — usar, não criar outro.
- Preferir extensão a modificação do core, para reduzir conflito em merges com o
  upstream. Quando precisar alterar código existente do Plane, isolar a alteração
  no menor ponto possível e registrar o motivo.
- O Plane comercial tem uma feature própria de time tracking. Nomear as entidades
  novas de modo a **não colidir** com ela, caso um dia haja migração para a
  Commercial Edition.

## 7. Como quero que você trabalhe

1. Antes de escrever código, apresente o modelo de dados proposto (campos,
   tipos, relações, constraints, índices) e as decisões de arquitetura, e
   aguarde meu OK.
2. Escreva testes unitários para toda regra da seção 5 — elas são o coração
   financeiro do sistema.
3. Entregue migrations reversíveis.
4. Não implemente fases futuras. Ao encontrar dependência de uma fase
   posterior, deixe a interface preparada, documente o ponto de extensão, e
   me avise.
5. Se alguma regra estiver ambígua para o código que você precisa escrever,
   pergunte antes de assumir. Não invente regra de negócio financeira.
