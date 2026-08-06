# Fase 1 — Fundação: entidade Cliente e vínculo com projects

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Leia com atenção a seção 2b dele — a
> arquitetura de Cliente e isolamento é o que define esta fase.

## Objetivo

Introduzir o conceito de **Cliente (empresa)**, que não existe no Plane, e
ancorá-lo aos projects. Todas as fases seguintes dependem disso: sem saber de
qual cliente é o chamado, não há como debitar pool nem faturar.

## Escopo

### 1. Entidade Cliente

Novo módulo em `plane/db/models/`, exportado em `plane/db/models/__init__.py`.
Herda **`WorkspaceBaseModel`** (workspace obrigatório, project nullable). Soft
delete e auditoria vêm de graça pela cadeia de abstratos. Campos mínimos:
- Nome / razão social, nome fantasia
- Identificador fiscal (CNPJ), com validação de formato
- Status (ativo / inativo)
- Modalidade padrão de faturamento (Contrato / Avulso) — o Cliente define o Tipo
  de Atendimento padrão dos apontamentos dos seus chamados; o técnico pode
  sobrescrever no apontamento individual (ver Fase 2, seção 3)
- **Cliente pai** (FK opcional, nullable, **sem lógica associada nesta fase**) —
  registra relação societária, como Terlogs pertencer à Marubeni. Serve para um
  eventual relatório consolidado de grupo econômico na Fase 9. Não afeta
  faturamento, não afeta débito de pool, não afeta visibilidade
- Dados de contato e observações
- Timestamps e autoria, seguindo o padrão de auditoria dos modelos do Plane

CRUD completo no painel de administração do workspace, restrito a Admin.

### 2. Vínculo Cliente ↔ Project

Cada project pode pertencer a **um** Cliente. Um Cliente pode ter **vários**
projects (ex.: `Marubeni – Suporte` e `Marubeni – Projeto ERP`).

- FK nullable de Cliente em `Project`, editável por Admin na configuração do
  project
- Project sem Cliente é trabalho interno: não apontável para faturamento
- Não permitir remover o Cliente de um project que já tenha apontamentos
- Ao associar um project a um Cliente, definir **`guest_view_all_features =
  True`** como padrão. Sem isso, os usuários do cliente veriam apenas os chamados
  que eles mesmos criaram, e não os abertos por colegas ou pelos técnicos (ver
  `docs/worklog/ACHADOS-DO-CODIGO.md`, seção 2). Deve ser sugerido pela UI, não imposto em
  silêncio
- Considerar ligar também `is_time_tracking_enabled` (flag já existente e hoje sem
  uso, `db/models/project.py:98`), que a Fase 3 vai adotar como toggle da feature

**Não criar tabela de associação usuário↔Cliente.** O vínculo de um usuário aos
seus Clientes é **derivado** da participação dele nos projects daqueles
Clientes, usando o mecanismo nativo do Plane. Expor isso como consulta
(`clientes_do_usuario`), não como dado duplicado.

Motivo: duplicar o vínculo cria duas fontes da verdade sobre acesso, e a que não
é usada pelo Plane para autorizar é exatamente a que vai divergir — resultando
em vazamento ou em bloqueio indevido.

### 3. Resolução do Cliente de um work item

O Cliente de um work item é **derivado do project** ao qual ele pertence.

- Não criar campo de Cliente editável no work item
- Expor o Cliente como atributo derivado, disponível para filtro, agrupamento e
  exibição nas visões existentes do Plane
- Trocar o Cliente de um chamado = **mover o chamado para o project correto**,
  usando o mecanismo nativo do Plane
- Mover um chamado que já tem apontamentos entre projects de Clientes diferentes
  exige tratamento explícito: alertar, e — quando as Fases 4 a 6 existirem —
  estornar do contrato de origem e debitar no de destino, com auditoria

Esta decisão elimina por construção uma classe inteira de erro: work item cujo
campo de Cliente aponta para uma empresa diferente da do seu contexto. E
elimina a ambiguidade do usuário que responde por duas empresas — ele abre o
chamado dentro de um project, e o project pertence a um único Cliente.

### 4. Migração de dados existentes

Projects existentes ficam sem Cliente. Não bloquear. Prever atribuição em lote
no painel de administração.

## Critérios de aceite

1. Admin cria, edita, inativa e lista Clientes no painel do workspace
2. Admin associa um project a um Cliente na configuração do project
3. Um Cliente com dois projects funciona, e os chamados dos dois resolvem para o
   mesmo Cliente
4. O Cliente de um work item é resolvido a partir do seu project, sem campo
   editável no chamado
5. É possível filtrar e agrupar work items por Cliente nas visões existentes
6. Marcel, adicionado como Guest a `Marubeni` e `Terlogs`, é resolvido como
   pertencente aos dois Clientes — **sem** nenhuma tabela de associação própria
7. Adriano, adicionado apenas a `Terlogs`, é resolvido como pertencente somente à
   Terlogs
8. Não existe no modelo de dados tabela de associação usuário↔Cliente
9. Não existe no modelo de dados campo de Cliente editável no work item
10. Mover um chamado com apontamentos para um project de outro Cliente alerta o
    usuário
11. Remover o Cliente de um project com apontamentos é rejeitado
12. Um Cliente com projects vinculados não pode ser excluído fisicamente — apenas
    inativado
13. O campo Cliente pai aceita valor e não produz nenhum efeito em nenhuma regra
14. Cliente de um workspace nunca é visível ou selecionável em outro workspace

## Entregar

Modelo de dados e decisões de arquitetura **primeiro**, para validação —
especialmente como o Cliente derivado é exposto para filtros e agregações sem
degradar as queries de listagem existentes do Plane. Depois: migrations, models,
serializers, viewsets, telas de administração e testes.
