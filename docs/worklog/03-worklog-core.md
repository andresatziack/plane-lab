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

| Campo                  | Tipo                        | Obrigatório            | Observação                                     |
| ---------------------- | --------------------------- | ---------------------- | ---------------------------------------------- |
| Modo de entrada        | Toggle: Duração / Intervalo | Sim                    | Regra R9                                       |
| Tempo                  | Texto com parser            | Sim, no modo Duração   | Regra R1. Preview em tempo real                |
| Hora início / Hora fim | Time pickers                | Sim, no modo Intervalo | Regra R9                                       |
| Data do atendimento    | Date picker                 | Sim                    | Não permitir data futura                       |
| Descrição              | Textarea                    | Sim                    | Detalhes do que foi feito                      |
| Garantia               | Checkbox                    | Não                    | Regra R5                                       |
| Tipo de Hora           | Dropdown (catálogo)         | Sim                    | Pré-selecionado pela detecção da Fase 2b       |
| Tipo de Atendimento    | Dropdown (catálogo)         | Sim                    | Pré-selecionado pelo padrão do Cliente         |
| Autor                  | Seletor de membro           | Não                    | Só visível com permissão de delegação (Fase 7) |

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

## Herdado da Fase 1: dois critérios que só podem ser fechados aqui

A Fase 1 foi implementada, mas **dois dos seus critérios de aceite dependem da
existência de apontamentos** e por isso ficaram sem efeito. Eles são
responsabilidade desta fase:

| Critério da Fase 1                                                                         | O que falta                                                                                  |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| 11 — "Remover o Cliente de um project com apontamentos é rejeitado"                        | rejeitar o `PATCH` de project que zera `service_client` quando o project já tem apontamentos |
| 10 — "Mover um chamado com apontamentos para um project de outro Cliente alerta o usuário" | detectar a troca de cliente no move e devolver o alerta                                      |

**Nada foi deixado no código para isso** — nem função vazia, nem `TODO`. A
decisão foi implementar os dois de uma vez, aqui, junto com o modelo que os torna
verificáveis. Os pontos de alteração são:

1. **Critério 11** — `ProjectSerializer` (`plane/app/serializers/project.py`) já
   tem um `validate_service_client` que garante que o cliente pertence ao mesmo
   workspace. Estender esse mesmo método: se `service_client` está sendo mudado
   para `None` (ou para outro cliente) e `Worklog.objects.filter(project=...)`
   existe, levantar `ValidationError`. É o único lugar por onde o vínculo é
   gravado, porque a UI usa o `PATCH` de project e não um endpoint dedicado.

2. **Critério 10** — o move de work item entre projects. Comparar
   `origem.project.service_client_id` com `destino.project.service_client_id`; se
   diferirem e o work item tiver apontamentos, devolver o alerta. As Fases 4 a 6
   acrescentam a isso o estorno no contrato de origem e o débito no de destino,
   com auditoria.

Ao fechar os dois, marcar os critérios 10 e 11 da Fase 1 como atendidos.

## Critérios herdados da Fase 2: cinco que só podem ser fechados aqui

A Fase 2 foi implementada, mas **cinco dos seus sete critérios de aceite dependem da
existência de apontamentos**. Eles estão marcados na `02-catalogos-configuraveis.md`
como parciais ou como "verificável na Fase 3", e são responsabilidade desta fase.

| Critério da Fase 2                                                       | O que falta                                                                                     |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| 1 — novo Tipo de Hora "passa a aparecer no formulário de apontamento"    | o formulário. O CRUD e a API já existem e são testados                                          |
| 2 — "a ordem se reflete no dropdown do formulário"                       | o dropdown. A reordenação por arraste e o `ordering` da API já existem                           |
| 3 — "apontamentos antigos continuam exibindo o nome corretamente"        | os FKs `DO_NOTHING` no apontamento, e o serializer resolvendo o nome mesmo com a opção inativa   |
| 4 — "excluir um Tipo de Hora **em uso** retorna erro explicativo"        | a contagem de apontamentos vinculados em `validate_catalog_delete`                                |
| 5 — "alterar o multiplicador não altera apontamento já registrado"       | o snapshot da R4 no apontamento                                                                  |

Pontos de alteração exatos:

1. **Critérios 1, 2 e 3 (metade de UI)** — o formulário de apontamento consome
   `GET /api/workspaces/<slug>/service-hour-types/?only_active=true` e
   `.../service-billing-types/?only_active=true`, já ordenados por `sequence`. No
   front, a store `serviceCatalog` (`core/store/workspace/service-catalog.store.ts`)
   expõe `activeHourTypes`, `activeBillingTypes` e `resolveDefaultBillingTypeId`.

2. **Critério 3 (metade de modelo) — restrição obrigatória, não preferência.** Os FKs
   do apontamento para `ServiceHourType` e `ServiceBillingType` têm de ser
   `on_delete=DO_NOTHING`, **nunca `SET_NULL` nem `CASCADE`**. Dois motivos:
   - com `SET_NULL` o apontamento histórico perde o rótulo e o critério 3 fica
     impossível;
   - com `CASCADE` ou `PROTECT`, o soft delete de uma opção de catálogo cai no
     catch-all de `soft_delete_related_objects`
     (`plane/bgtasks/deletion_task.py:47`) e **soft-deleta os apontamentos**. É
     caminho de perda de dados disparado por um admin arrumando o catálogo. Mesmo
     motivo pelo qual `Project.service_client` e `ServiceClient.default_billing_type`
     são `DO_NOTHING`.

3. **Critério 4** — acrescentar a checagem em `validate_catalog_delete`
   (`plane/utils/service_catalog.py`), que já existe e já tem o ponto de extensão
   documentado com esta finalidade. Recusar com um código explicativo, no padrão
   `UPPER_SNAKE` dos demais, e traduzir no front em
   `delete-catalog-option-modal.tsx`. Não espalhar a regra pela view.

4. **Critério 5** — o apontamento persiste `multiplicador_aplicado` no momento da
   criação (R4). Snapshotar também a **rota de faturamento**: a R4 cita apenas o
   multiplicador, mas a rota decide para onde o tempo foi, e dois Tipos de Atendimento
   podem compartilhar a mesma rota — sem o snapshot, um relatório histórico não
   consegue separá-los depois de uma edição de catálogo. As escalas dos campos estão
   fechadas na **seção 4b do contexto mestre** —
   `multiplicador_aplicado` com `DecimalField(max_digits=4, decimal_places=2)` e as
   grandezas de hora com 4 casas.

Ao fechar os cinco, marcar os critérios correspondentes da Fase 2 como atendidos.

### Auditoria de configuração: o que a Fase 2 deixou pronto

Existe `ServiceConfigActivity` (`plane/db/models/service_config_activity.py`), trilha
append-only de alterações em configuração que afeta dinheiro, com
`GET /api/workspaces/<slug>/service-config-activities/` restrito a ADMIN de workspace.

Duas coisas a considerar aqui:

- A trilha registra **apenas alterações de campos rastreados** (`TRACKED_FIELDS`), não
  criação nem exclusão — `ChangeTrackerMixin` estruturalmente não emite evento de
  criação, e criação/exclusão já estão respondidas por `created_by`, `created_at` e
  `deleted_at` da própria entidade. Se a Fase 3 precisar de eventos de criação ou
  exclusão, isso exige uma coluna `verb` nova (nullable, sem backfill).
- Auditoria **do apontamento** (R8) é outra coisa e não deve usar este modelo: a R8
  pede trilha por work item, e o padrão para isso é `IssueActivity` mais
  `issue_activity.delay(...)`, como manda a seção 6 do contexto mestre.

## Entregar

Modelo de dados e assinaturas da camada de domínio primeiro. Depois:
implementação, API, UI e testes. Os testes da camada de domínio não são
opcionais.
