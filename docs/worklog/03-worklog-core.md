# Fase 3 — Work Log core: registro, parser, arredondamento e totais

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende das Fases 1, 2 e 2b.
> Esta é a fase mais importante. As regras R1 a R5 e R8 a R10 do contexto mestre
> são requisitos literais aqui.

## Objetivo

Permitir que o técnico registre tempo em um work item, com conversão correta de
texto para decimal, e exibir os totais consolidados na tela do chamado. Ainda
**sem** débito de pool (Fase 4) e **sem** valor em R$ (Fase 6) — mas com o
cálculo de horas equivalentes já pronto, que é a base das duas.

## Escopo

### 1. Ação de apontar

Adicionar ao menu de ações do work item a opção **"Adicionar work log"**, que
abre um formulário com:

| Campo | Tipo | Obrigatório | Observação |
|---|---|---|---|
| Modo de entrada | Toggle: Duração / Intervalo | Sim | Regra R9 |
| Tempo | Texto com parser | Sim, no modo Duração | Regra R1. Preview em tempo real |
| Hora início / Hora fim | Time pickers | Sim, no modo Intervalo | Regra R9 |
| Data do atendimento | Date picker | Sim | Não permitir data futura |
| Descrição | Textarea | Sim | Detalhes do que foi feito |
| Garantia | Checkbox | Não | Regra R5 |
| Tipo de Hora | Dropdown (catálogo) | Sim | Pré-selecionado pela detecção da Fase 2b |
| Tipo de Atendimento | Dropdown (catálogo) | Sim | Pré-selecionado pelo padrão do Cliente |
| Autor | Seletor de membro | Não | Só visível com permissão de delegação (Fase 7) |

O modo escolhido deve ser lembrado como preferência do usuário.

### 2. Camada de domínio

Implementar em módulo puro, sem dependência de Django views ou DRF:
- `parse_duracao(texto) -> minutos: int` — regra R1
- `duracao_de_intervalo(inicio, fim) -> minutos: int` — regra R9, segundos
  truncados, tratar virada de meia-noite
- `arredondar(minutos) -> minutos: int` — regra R2, blocos de 15, empate para
  cima, piso de 15
- `para_decimal(minutos) -> Decimal` — regra R3
- `formatar(minutos) -> str` — saída legível pt-BR (`1h 15min`)
- `horas_equivalentes(horas_apontadas, multiplicador) -> Decimal`

Suíte de testes cobrindo integralmente a tabela de casos de referência da regra
R2, mais entradas inválidas, mais idempotência (arredondar duas vezes dá o mesmo
resultado), mais equivalência entre os dois modos de entrada (14:00–15:30 deve
produzir exatamente o mesmo resultado que `1h 30min`).

### 3. Integração com a classificação automática

Ao preencher data e, no modo Intervalo, os horários, chamar o motor da Fase 2b,
exibindo o motivo da classificação (regra R10).

**Modo Intervalo** — o motor pode retornar múltiplos segmentos. Esta fase gera
**um apontamento por segmento**, todos vinculados por um identificador de
lançamento comum, cada um com sua própria data, duração e Tipo de Hora.

A UI deve mostrar um preview do que será criado **antes** de salvar. Ex.: técnico
informa segunda-feira 17:00 às 20:00 e vê "serão criados 2 apontamentos: 1h
Horário comercial + 2h Fora do expediente" com o total resultante.

Apontamentos do mesmo lançamento devem poder ser editados e excluídos em
conjunto, e a exclusão de um lançamento remove todos os seus segmentos de forma
transacional.

**Modo Duração** — nunca dividir. O motor classifica apenas pela data: feriado,
domingo e sábado são determinados automaticamente; em dia útil o técnico
seleciona o Tipo de Hora, com o padrão do catálogo pré-selecionado.

### 4. Persistência

Novo módulo em `plane/db/models/`, herdando **`ProjectBaseModel`** (o `save()`
denormaliza `workspace` a partir do project automaticamente). Habilitado por
project pela flag já existente e hoje sem uso `Project.is_time_tracking_enabled`
(`db/models/project.py:98`).

Para a trilha de auditoria da regra R8, usar os mecanismos que já existem:
`ChangeTrackerMixin` com `TRACKED_FIELDS` (`plane/db/mixins.py:92`) e o padrão de
`IssueActivity` + task `issue_activity.delay(...)`. Não inventar mecanismo
próprio.

Campos do apontamento, no mínimo:
- work item, autor, criador, data do atendimento, descrição
- campo de **origem** do registro (manual, importado, API) — barato agora e caro
  depois, previsto na decisão D12
- modo de entrada, hora de início e hora de fim (nulos no modo Duração)
- `duracao_bruta_minutos` (int) — o que foi informado
- `horas_apontadas` (Decimal) — após arredondamento
- `horas_equivalentes` (Decimal) — após multiplicador
- `horas_debitadas` (Decimal) — igual às equivalentes, ou `0` se garantia (R11)
- `multiplicador_aplicado` (Decimal) — snapshot, regra R4
- tipo de hora, tipo de atendimento, flag de garantia
- tipo de hora sugerido pelo motor e flag de sobrescrita manual
- identificador de lançamento (para apontamentos gerados por divisão)
- timestamps e trilha de alterações (regra R8)

### 5. Alerta acima de 24 horas

**Não bloquear** apontamentos acima de 24h — é caso de uso legítimo (projeto
trabalhado). Exibir aviso informativo no formulário pedindo confirmação
explícita antes de salvar, para prevenir erro de digitação.

### 6. Visão no work item

Seção de apontamentos exibindo, **na visão do técnico e do Admin**:
- Lista: data, horário (quando houver), autor, tempo formatado, tipo de hora,
  tipo de atendimento, descrição, indicador visual de garantia
- Ordenação por data do atendimento, mais recente primeiro
- **Total de horas apontadas** — soma cronológica de tudo, inclusive garantia
- **Total de horas equivalentes** — com multiplicador aplicado
- **Total de horas debitadas** — excluindo garantia
- Ações de editar e excluir (permissões vêm na Fase 7; por ora, apenas o autor)

Os totais devem ser claramente rotulados e distintos. Confundi-los é o erro mais
provável desta feature.

**O técnico precisa ver as duas leituras do seu próprio apontamento** (regra
R11): o que ele registrou e o que o cliente verá. Em cada linha, exibir o tempo
original e, quando o multiplicador for diferente de 1.0, o valor convertido com o
motivo — ex.: `1h 15min · Fora do expediente · o cliente vê 1,875h`. Sem isso o
técnico não tem como perceber que registrou o tipo de hora errado, e o erro só
aparece na fatura.

O portal do cliente tem visão própria, com serializer próprio, definida na Fase 8.
Nesta fase, não expor apontamento a papel GUEST em nenhum endpoint.

### 7. Agregação em listas

Expor as horas totais do work item de forma agregável, para que as visões de
lista e os relatórios futuros possam somar por projeto, cliente e período sem
recalcular apontamento por apontamento.

## Critérios de aceite

1. Digitar `1h 15min` persiste `horas_apontadas = 1.25`
2. Digitar `1h 08min` persiste `1.25` e a UI avisa que houve arredondamento
3. Digitar `1h 07min` persiste `1.0`
4. Digitar `3min` persiste `0.25` (piso de 15 minutos)
5. Digitar `banana` bloqueia o envio com mensagem clara
6. Informar intervalo de 14:00 às 15:30 produz o mesmo resultado que digitar
   `1h 30min`
7. Um apontamento de `1h` com Tipo de Hora de multiplicador 1.5 persiste
   `horas_apontadas = 1.0` e `horas_equivalentes = 1.5`
8. Apontamento em data de feriado já abre com "Domingos e feriados"
   pré-selecionado e o motivo visível, nos dois modos de entrada
9. Intervalo de segunda-feira 17:00 às 20:00 cria 2 apontamentos agrupados: 1h
   Horário comercial e 2h Fora do expediente, com preview antes de salvar
10. Modo Duração com `3h` numa terça-feira cria 1 apontamento e deixa o Tipo de
    Hora para o técnico escolher
11. Excluir um lançamento dividido remove todos os seus segmentos
12. Apontamento com Garantia marcada soma no total apontado e não soma no total
    faturável
13. Apontamento de `30h` salva, mostrando aviso de confirmação antes
14. Data futura é rejeitada
15. Alterar o multiplicador do catálogo depois não muda `horas_equivalentes` de
    apontamentos já salvos
16. Editar um apontamento recalcula os totais do work item corretamente e
    registra a alteração na trilha de auditoria

## Entregar

Modelo de dados e assinaturas da camada de domínio primeiro. Depois:
implementação, API, UI e testes. Os testes da camada de domínio não são
opcionais.
