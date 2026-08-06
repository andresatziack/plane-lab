# Fase 2 — Catálogos configuráveis: Tipo de Hora e Tipo de Atendimento

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 1.

## Objetivo

Criar os catálogos que parametrizam todo o cálculo de apontamento. São eles que
transformam regras de negócio em configuração, em vez de código.

## Escopo

### 1. Tipo de Hora

Ex.: Horário comercial, Fora do expediente, Domingos e feriados.

Campos:

- Nome e descrição
- **Multiplicador** (`Decimal`, ex.: 1.0 / 1.5 / 2.0) — aplicado sobre as horas
  apontadas para gerar as horas equivalentes (ver seção 4 do contexto mestre)
- Cor / identificação visual
- Ordem de exibição
- Ativo / inativo
- Flag de padrão (exatamente um padrão por catálogo)

As **janelas de classificação** (dia da semana + faixa horária) e o campo de
**prioridade** são adicionados na Fase 2b, junto com o motor que os consome.
Deixe o modelo preparado para recebê-los, mas não implemente a classificação
aqui.

### 2. Tipo de Atendimento

Ex.: Contrato, Avulso, Cortesia, Projeto.

Campos:

- Nome e descrição
- **Rota de faturamento** — enum: `DEBIT_POOL`, `BILL_AMOUNT`,
  `NON_BILLABLE`. Este campo é o que decide, no momento do apontamento, para
  onde o tempo vai. É o coração da regra R6.
- Ordem, ativo/inativo, flag de padrão

### 3. Padrão por Cliente

O **Cliente define o Tipo de Atendimento padrão** dos apontamentos dos seus
chamados (definido na Fase 1 como modalidade padrão de faturamento). O
formulário de apontamento pré-seleciona esse padrão, mas o técnico pode escolher
outro Tipo de Atendimento — e com isso mudar a rota de faturamento daquele
apontamento específico.

Caso de uso: cliente com contrato de suporte recebe um serviço fora de escopo.
O apontamento é lançado com Tipo de Atendimento "Avulso" e gera cobrança em R$,
sem consumir o pool contratado.

O catálogo geral define o padrão global; o Cliente sobrescreve; o apontamento
sobrescreve o Cliente.

### 4. Escopo dos catálogos

Manter no nível de **workspace**, para que multiplicador e rota de faturamento
sejam únicos e consistentes em toda a operação. Permitir, opcionalmente,
habilitar/desabilitar quais opções aparecem em cada projeto — mas sem duplicar
a definição do multiplicador por projeto.

### 5. Ciclo de vida das opções

- Opção inativa não aparece em novos apontamentos
- Apontamentos históricos que usam opção inativa continuam válidos, exibíveis e
  íntegros nos relatórios
- Excluir opção com apontamentos vinculados é proibido — apenas inativar
- Alterar o multiplicador **não** recalcula apontamentos existentes (regra R4)

### 6. Painel de administração

CRUD, reordenação por arraste, ativação/desativação e definição de padrão, para
os dois catálogos. Restrito a Admin do workspace.

Este painel é o mesmo que receberá, na Fase 2b, o cadastro de feriados e de
janelas de classificação. Estruture a navegação prevendo essas seções.

## Critérios de aceite

Status registrado após a implementação. **Cinco dos sete dependiam da existência de
apontamentos** e por isso só eram verificáveis na Fase 3. **A Fase 3 fechou os cinco**,
e os sete estão atendidos. Os pontos de alteração usados estão em
`03-worklog-core.md`, seção "Critérios herdados da Fase 2". Sem esse registro eles
nunca seriam verificados: a Fase 2 não conseguia e a Fase 3 não saberia que existem.

1. Admin cria um Tipo de Hora "Sábado" com multiplicador 1.5 e ele passa a
   aparecer no formulário de apontamento
   — **atendido.** O CRUD e a API na Fase 2; o formulário na Fase 3
2. Admin reordena as opções e a ordem se reflete no dropdown do formulário
   — **atendido.** Reordenação por arraste e `ordering` na Fase 2; o dropdown na Fase 3,
   que consome `activeHourTypes` já ordenado por `sequence`
3. Ao inativar "Fora do expediente", ele desaparece de novos apontamentos mas
   os apontamentos antigos continuam exibindo o nome corretamente
   — **atendido.** `?only_active=true` na Fase 2; na Fase 3, os FKs `DO_NOTHING` no
   apontamento e o serializer resolvendo o nome por `all_objects`. Coberto por teste
   tanto para opção inativa quanto para opção soft-deletada, e a API recusa escolher uma
   opção inativa em apontamento novo
4. Tentar excluir um Tipo de Hora em uso retorna erro explicativo
   — **atendido na Fase 3**, em `validate_catalog_delete`, no ponto de extensão que esta
   fase deixou documentado. Códigos `HOUR_TYPE_IN_USE_BY_SERVICE_LOGS` e
   `BILLING_TYPE_IN_USE_BY_SERVICE_LOGS`, contados por `all_objects` — apontamento
   soft-deletado ainda conta — e bloqueando também quando a opção aparece apenas como
   `suggested_hour_type`
5. Alterar o multiplicador de 1.5 para 1.8 não altera nenhum valor ou débito de
   apontamento já registrado
   — **atendido na Fase 3** pelo snapshot da R4. A Fase 3 snapshota também a **rota de
   faturamento**, que a R4 não citava: dois Tipos de Atendimento podem compartilhar a
   mesma rota (Garantia e Cortesia), e sem isso um relatório histórico não consegue
   separá-los depois de uma edição de catálogo
6. Cada catálogo tem exatamente uma opção padrão a qualquer momento
   — **atendido.** Constraint parcial no banco mais recusa de despadronizar,
   inativar ou excluir a opção padrão
7. Chamado de um cliente de contrato pré-seleciona Tipo de Atendimento
   "Contrato"; trocar para "Avulso" no apontamento é permitido
   — **parcial.** A FK `default_billing_type` no Cliente e o resolvedor
   `resolve_default_billing_type` estão prontos e testados nos dois níveis de
   precedência; **a pré-seleção no formulário é verificável na Fase 3**

## Entregar

Modelo de dados primeiro. Incluir seed com os valores iniciais: Horário
comercial (1.0), Fora do expediente (1.5), Domingos e feriados (2.0); Contrato
(DEBIT_POOL, padrão), Avulso (BILL_AMOUNT), Garantia (NON_BILLABLE) e Cortesia
(NON_BILLABLE) — ver R5 do contexto mestre para a distinção entre os dois últimos.
