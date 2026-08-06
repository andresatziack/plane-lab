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
- **Rota de faturamento** — enum: `DEBITA_POOL`, `FATURA_REAIS`,
  `NAO_FATURAVEL`. Este campo é o que decide, no momento do apontamento, para
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

1. Admin cria um Tipo de Hora "Sábado" com multiplicador 1.5 e ele passa a
   aparecer no formulário de apontamento
2. Admin reordena as opções e a ordem se reflete no dropdown do formulário
3. Ao inativar "Fora do expediente", ele desaparece de novos apontamentos mas
   os apontamentos antigos continuam exibindo o nome corretamente
4. Tentar excluir um Tipo de Hora em uso retorna erro explicativo
5. Alterar o multiplicador de 1.5 para 1.8 não altera nenhum valor ou débito de
   apontamento já registrado
6. Cada catálogo tem exatamente uma opção padrão a qualquer momento
7. Chamado de um cliente de contrato pré-seleciona Tipo de Atendimento
   "Contrato"; trocar para "Avulso" no apontamento é permitido

## Entregar

Modelo de dados primeiro. Incluir seed com os valores iniciais: Horário
comercial (1.0), Fora do expediente (1.5), Domingos e feriados (2.0); Contrato
(DEBITA_POOL), Avulso (FATURA_REAIS), Cortesia (NAO_FATURAVEL).
