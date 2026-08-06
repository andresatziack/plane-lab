# Fase 2b — Calendário de feriados, janelas de classificação e detecção de Tipo de Hora

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 2.
> Decisões desta fase: D8, D13, D14, D16 e D17 vinham resolvidas; **D21 e D22 foram
> decididas aqui** e estão registradas em `DECISOES.md`.
>
> **Implementada depois da Fase 3**, que foi executada fora de ordem. Por isso esta fase
> fecha também os critérios 8 e 9 da Fase 3 — ver "Critérios herdados da Fase 3".

## Objetivo

Parametrizar quando é feriado e quais faixas de dia e horário correspondem a
cada Tipo de Hora, e construir o motor que usa isso para **classificar
automaticamente** o Tipo de Hora de um apontamento — dividindo o apontamento em
segmentos quando ele atravessa faixas diferentes.

Elimina a dependência de o técnico lembrar de marcar "fora do expediente" ou
"domingos e feriados", que é a fonte de erro mais provável de todo o cálculo
financeiro.

Comportamento de referência: o plugin Tempo para Jira faz esse tipo de
classificação automática a partir de calendário configurado.

## Regras de negócio reais a modelar

Estas são as regras vigentes nos contratos com os clientes. Elas devem ser
**configuração inicial (seed)**, não código:

- **Segunda a sexta, 08:00 às 18:00** → Horário comercial (1.0)
- **Segunda a sexta, 18:00 até 08:00 do dia seguinte** → Fora do expediente (1.5)
- **Sábado, o dia inteiro** → Fora do expediente (1.5)
- **Domingo, o dia inteiro** → Domingos e feriados (2.0)
- **Feriado, o dia inteiro** → Domingos e feriados (2.0)

Observe que "Fora do expediente" é uma janela **contínua que atravessa a
meia-noite** (18:00 → 08:00). O modelo precisa suportar isso nativamente. Note
também que sábado é 1.5 e domingo é 2.0 — a classificação não pode ser derivada
de um simples "dia trabalhado / não trabalhado".

## Requisito negativo explícito

**Não criar entidade de jornada de trabalho por técnico.** As janelas de
classificação da seção 2 já são a única fonte da verdade sobre o que é horário
comercial, e são as mesmas para todos os técnicos porque refletem o que está
escrito nos contratos com os clientes.

Duas configurações de horário coexistindo (janelas comerciais + jornada
individual) inevitavelmente divergem, e o resultado é que a fatura do cliente
passa a depender de qual técnico atendeu — o que contraria o contrato.

Se algum dia surgir necessidade real de diferenciação por técnico, o modelo de
janelas é extensível com um escopo opcional. Não construir isso agora.

## Escopo

### 1. Calendário de feriados

Cadastro no painel de administração de apontamentos, restrito a Admin:

- Nome do feriado
- Data
- **Recorrência anual** — flag. Natal repete todo ano na mesma data; Carnaval
  muda e precisa ser cadastrado ano a ano
- Abrangência (nacional / estadual / municipal) — campo no modelo, sem lógica de
  filtro por localidade nesta fase (decisão D14)
- Ativo / inativo

Requisitos:

- Importação em lote (CSV) e visão de calendário anual para conferência
- Não permitir duas entradas ativas para a mesma data e abrangência
- Alterar o calendário **não** reclassifica apontamentos já registrados (R4)

### 2. Janelas de classificação

Cada Tipo de Hora recebe um conjunto de janelas, e o motor resolve por
prioridade.

Entidade Janela de Classificação:

- Tipo de Hora (FK)
- Escopo do dia — enum: `SEGUNDA` … `DOMINGO`, `FERIADO`
- Hora de início e hora de fim (suportar `00:00–24:00` para dia inteiro e
  janelas que atravessam a meia-noite, como `18:00–08:00`)

Cada Tipo de Hora recebe também um campo `prioridade` (inteiro). O motor avalia
as janelas em ordem de prioridade e o **primeiro match vence**. Tipo de Hora sem
nenhuma janela nunca é sugerido — apenas selecionável manualmente.

> **O que a Fase 2 já deixou pronto para esta seção.** `ServiceHourType` existe em
> `plane/db/models/service_catalog.py` e a relação reversa `windows` está livre.
>
> Três avisos concretos ao adicionar `priority`:
>
> 1. **Não reaproveitar `sequence`.** Aquele campo é ordem de **exibição** — é o que o
>    admin arrasta no painel — e está documentado no modelo como tal. Fundir os dois
>    faria um arraste cosmético reclassificar hora e, portanto, alterar fatura. São
>    dois campos.
> 2. **Acrescentar `priority` a `TRACKED_FIELDS`**, que hoje é
>    `["multiplier", "is_active"]`. A prioridade decide qual tipo o motor escolhe, então
>    mudá-la altera o faturamento futuro com o mesmo peso do multiplicador, e a trilha
>    `ServiceConfigActivity` precisa registrar a alteração. Nada mais é necessário: a
>    gravação é automática pelo `save_with_config_activity`.
> 3. **A guarda de exclusão fica em um só lugar.** `validate_catalog_delete`
>    (`plane/utils/service_catalog.py`) já tem o ponto de extensão documentado; a
>    checagem "tem janelas" entra ali, ao lado da que a Fase 3 vai acrescentar. E a FK
>    da Janela para o Tipo de Hora tem de ser `DO_NOTHING`, pelo mesmo motivo explicado
>    em `03-worklog-core.md`: com `CASCADE` o soft delete de um tipo de hora apagaria
>    silenciosamente as janelas via `soft_delete_related_objects`.
>
> O painel de administração também já está estruturado para receber as duas seções
> novas: `WORKLOG_SETTINGS_SECTIONS` em
> `apps/web/core/components/service-catalog/worklog-settings-tabs.tsx` é um array — o
> calendário de feriados e as janelas são uma entrada cada, mais uma rota, sem mexer na
> sidebar.

Seed inicial, coerente com as regras de negócio acima:

| Tipo de Hora        | Mult. | Prio. | Janelas                                             |
| ------------------- | ----- | ----- | --------------------------------------------------- |
| Domingos e feriados | 2.0   | 10    | `FERIADO` dia inteiro; `DOMINGO` dia inteiro        |
| Fora do expediente  | 1.5   | 20    | `SABADO` dia inteiro; `SEGUNDA`–`SEXTA` 18:00–08:00 |
| Horário comercial   | 1.0   | 30    | `SEGUNDA`–`SEXTA` 08:00–18:00                       |

`FERIADO` tem a maior prioridade e portanto vence sobre a janela do dia da
semana — um feriado que cai numa quarta-feira é classificado como 2.0 no dia
inteiro. Com este modelo, criar um Tipo "Plantão de madrugada" com janela
`SEGUNDA`–`SEXTA` 00:00–06:00, multiplicador 2.5 e prioridade 15 é configuração
de painel, sem deploy.

Validação obrigatória: as janelas de um mesmo nível de prioridade não podem se
sobrepor, e o conjunto total deve cobrir 100% da semana. Um instante sem
classificação possível é bug de configuração e deve ser impedido no cadastro,
não descoberto no apontamento.

### 3. Motor de classificação

Camada de domínio pura, sem dependência de views:

```
classificar(
    data,
    hora_inicio | None,
    hora_fim | None,
    cliente,
) -> list[Segmento]
```

Onde `Segmento` = (data, hora_inicio, hora_fim, duracao_minutos, tipo_de_hora,
motivo).

Comportamento por modo de entrada (regra R9):

**Modo Intervalo** (hora início e fim informadas) — classificação completa:

- Percorrer a linha de tempo contínua do início ao fim
- Resolver a janela vigente em cada instante, por prioridade
- Emitir um segmento a cada **mudança de classificação**
- Não quebrar na meia-noite quando a classificação não muda

**Modo Duração** (apenas o total de horas) — classificação apenas por data:

- Se a data é feriado, domingo ou sábado, o Tipo de Hora é determinado pela
  janela de dia inteiro correspondente
- Em dia útil, não há como saber o horário: **o técnico seleciona o Tipo de
  Hora**, com o padrão do catálogo pré-selecionado
- **Nunca dividir** um apontamento no modo Duração

### 4. Segmentação — casos de referência

Usar exatamente como suíte de testes:

| Cenário                                 | Resultado esperado                                                     |
| --------------------------------------- | ---------------------------------------------------------------------- |
| Seg 14:00–16:00                         | 1 segmento: 2h Horário comercial                                       |
| Seg 17:00–20:00                         | 2 segmentos: 1h Comercial + 2h Fora do expediente                      |
| Seg 22:00 → Ter 01:00                   | 1 segmento: 3h Fora do expediente (atravessa a meia-noite sem dividir) |
| Sex 17:00 → Sáb 02:00                   | 2 segmentos: 1h Comercial + 8h Fora do expediente                      |
| Sáb 22:00 → Dom 02:00                   | 2 segmentos: 2h Fora do expediente + 2h Domingos e feriados            |
| Dom 23:00 → Seg 02:00                   | 2 segmentos: 1h Domingos e feriados + 2h Fora do expediente            |
| Feriado 10:00–12:00                     | 1 segmento: 2h Domingos e feriados                                     |
| Qua 10:00–12:00, sendo a Qua um feriado | 1 segmento: 2h Domingos e feriados                                     |
| Sáb 09:00–11:00                         | 1 segmento: 2h Fora do expediente (1.5, não 2.0)                       |
| Seg 07:00–09:00                         | 2 segmentos: 1h Fora do expediente + 1h Comercial                      |
| Modo Duração, `3h`, em um domingo       | 1 apontamento, Domingos e feriados                                     |
| Modo Duração, `3h`, em uma terça        | 1 apontamento, técnico escolhe o tipo                                  |

Testar também os limites exatos: apontamento terminando às 18:00 em ponto
(inteiramente comercial) e começando às 08:00 em ponto (inteiramente comercial).

### 5. Data de atendimento dos segmentos

Cada segmento carrega a **sua própria data**, derivada da posição real na linha
de tempo — não a data de início do lançamento.

Isso é obrigatório por causa da regra R7: um apontamento de 31/01 23:00 a 01/02
01:00 gera segmentos em competências diferentes, e cada um deve debitar o pool
do seu mês. Usar a data de início para os dois causaria erro de faturamento na
virada do mês.

### 5b. Estratégia de fuso horário — definir aqui

O contexto mestre exige "definir e documentar a estratégia de fuso". A Fase 1 não
tocou nisso de propósito: `ServiceClient` não tem nenhum campo de data de negócio,
então não havia decisão a tomar sem inventar requisito. **Esta é a fase em que a
decisão se torna inevitável**, porque é aqui que entram faixas horárias, viradas
de meia-noite e feriados que valem "o dia inteiro".

Pontos que a decisão precisa cobrir:

- `Workspace` e `Project` **já têm campo `timezone`** (`db/models/project.py`, e o
  `save()` do Project herda o do workspace na criação). Usar um deles, não criar
  outro — instrução explícita do contexto mestre.
- Qual dos dois é a fonte da verdade para classificar um apontamento. Como as
  janelas são parâmetro comercial **do workspace** (D16), o fuso do workspace é o
  candidato coerente: usar o do project faria a mesma hora trabalhada cair em
  faixas diferentes conforme o project.
- O que significa "dia inteiro" para feriado, sábado e domingo: os limites do dia
  no fuso escolhido, não em UTC. Um apontamento de domingo 23:00 em São Paulo é
  segunda 02:00 em UTC; classificar em UTC cobraria 1.0 em vez de 2.0.
- Como a janela contínua 18:00→08:00 é avaliada na travessia de meia-noite, e o
  que acontece em dias de mudança de horário de verão, caso volte a existir.
- `TimezoneMixin` (`plane/app/views/base.py`) ativa o fuso **do usuário** por
  requisição. Isso é bom para exibição e **perigoso para cálculo**: a
  classificação não pode depender de quem abriu a tela. O motor precisa fixar o
  fuso explicitamente em vez de herdar o ativo.

Documentar a decisão junto do motor, e cobrir com teste ao menos: domingo 23:00,
sábado 22:00 → domingo 02:00, e um feriado inteiro.

### 6. Arredondamento aplicado a segmentos

**Decidido: arredondar por segmento**, aplicando a regra R2 integralmente a cada
um. Cada segmento é uma unidade faturável independente, com multiplicador
próprio, e portanto arredonda de forma independente.

Exemplo: seg 17:50–18:10 = 20 min brutos → 10 min comerciais (→ 15 min) + 10 min
fora do expediente (→ 15 min) = **30 min apontados**, sendo 0,25h a 1.0 e 0,25h
a 1.5.

Consequência aceita e que precisa estar documentada na UI: os mesmos 20 minutos
resultam em 0,25h se informados como duração, e em 0,5h se informados como
intervalo cruzando as 18:00. A diferença existe porque no segundo caso o sistema
sabe que o trabalho atravessou duas faixas de preço distintas.

**Guardrail obrigatório:** se a duração bruta **total** do lançamento for menor
que 15 minutos, **não dividir**. Gerar um único apontamento de 15 minutos com o
Tipo de Hora da faixa predominante.

Sem esse guardrail, um apontamento de 17:57 às 18:03 (6 minutos de trabalho)
geraria 3 min + 3 min, cada um subindo ao piso de 15, totalizando 30 minutos —
cinco vezes o tempo real. Indefensável diante do cliente.

A UI deve exibir o total resultante **antes** de salvar, sempre que houver
divisão.

### 7. Transparência da classificação

A UI deve mostrar **por que** cada segmento recebeu seu tipo (ex.: "Feriado:
Natal" ou "Fora do expediente: 18:00–20:00"). Classificação silenciosa em
cálculo financeiro gera desconfiança e disputa com o cliente.

No modo Intervalo com múltiplos segmentos, mostrar um preview do que será criado
antes de salvar. Quando o técnico sobrescrever a classificação, registrar
sugestão original e escolha final na auditoria.

## Critérios de aceite

Status registrado após a implementação. **Os dezessete estão atendidos.**

1. Admin cadastra 25/12 como feriado recorrente e ele aparece em todos os anos
   — **atendido.** `ServiceHoliday.matches()` é o único lugar que interpreta
   recorrência, e o endpoint `calendar/?year=` expande. Testado no motor e por HTTP
2. Admin cadastra o Carnaval de um ano específico, sem recorrência, e ele não
   aparece no ano seguinte — **atendido**, nos dois níveis
3. Todos os cenários da tabela da seção 4 passam — **atendido.** Os doze são testes
   nomeados em `test_service_calendar_engine.py`, mais os quatro limites exatos
4. Sábado é classificado como 1.5 e domingo como 2.0 — **atendido**
5. Feriado em dia útil vence a janela do dia da semana — **atendido.** O escopo
   `FERIADO` é **aditivo** ao dia da semana e vence por prioridade, não substituindo o
   dia — é isso que permite criar "Plantão de madrugada" na prioridade 15 sem deploy
6. Apontamento que atravessa a meia-noite sem mudar de classificação não é
   dividido — **atendido.** A união de segmentos compara só o Tipo de Hora, então
   entrar na janela idêntica do dia seguinte continua sendo um segmento
7. Apontamento que atravessa a virada do mês gera segmentos com datas — e
   competências — corretas — **atendido**
8. Modo Duração nunca gera divisão — **atendido**, e a regra é do adaptador
   `build_segments`, não do motor: ele descarta os horários antes de classificar, para
   que não exista caminho pelo qual o motor possa dividir o que a D13 proíbe
9. Modo Duração em dia útil deixa o Tipo de Hora para o técnico escolher
   — **atendido**, e agora por regra e não por acidente. Existe teste de controle
   positivo (`test_the_tuesday_case_is_a_rule_and_not_a_missing_configuration`) na
   mesma fixture, porque "sem sugestão" também é o que um motor quebrado produz
10. Seg 17:50–18:10 gera 2 apontamentos de 15 min cada, com o total exibido antes
    de salvar — **atendido.** A divisão é desta fase, o arredondamento por segmento é
    da Fase 3, e o teste cobre a composição das duas
11. Seg 17:57–18:03 gera **1** apontamento de 15 min, sem divisão (guardrail)
    — **atendido.** O motor divide e o `apply_minimum_block_guardrail` da Fase 3
    recolhe; o teste tem esse nome porque a ordem importa
12. Cadastro de janelas com sobreposição no mesmo nível de prioridade é rejeitado
    — **atendido**, e escopado por `(escopo do dia, prioridade)`: por prioridade
    apenas, segunda 08:00–18:00 e terça 08:00–18:00 seriam falsamente acusadas.
    Verificado também que a janela recusada faz rollback junto com sua trilha
13. Admin cria um Tipo de Hora novo com janela e prioridade próprias pelo painel,
    e o motor passa a usá-lo sem alteração de código — **atendido**, e testado de
    ponta a ponta: cria por HTTP, depois chama o motor. Foi esse teste que revelou que
    `priority` não estava no serializer — os testes de motor criavam pela ORM e não
    conseguiam ver a falha
14. Técnico sobrescreve a classificação e a auditoria registra sugestão e escolha
    — **atendido.** O gerador `service_log.activity.overridden` existia desde a Fase 3
    sem poder disparar, porque sem sugestão não havia divergência
15. Alterar janelas ou calendário não reclassifica nenhum apontamento existente
    — **atendido** (R4). A classificação é resolvida na escrita e congelada em
    `applied_multiplier`, `suggested_hour_type` e `classification_reason`
16. A UI exibe o motivo de cada segmento — **atendido.** O motivo vem do servidor,
    por segmento, na lista e no preview
17. Não existe entidade de jornada de trabalho por técnico no modelo de dados
    — **atendido.** As janelas são do workspace e valem para todos os técnicos

### Uma consequência que ficou fora dos critérios

A cobertura é uma **catraca** — "um conjunto completo nunca pode ficar incompleto" —
e não uma exigência absoluta. Exigir cobertura total sempre tornaria impossível criar
a primeira janela de um workspace montado à mão, e impossível **consertar** um
workspace já quebrado: o admin ficaria trancado fora da única tela capaz de corrigir.
Sobreposição, ao contrário, é sempre recusada: não há desculpa transitória para duas
janelas entre as quais não existe regra de decisão.

Isso significa que um workspace pode legitimamente ficar incompleto, e é por isso que
o motor é **total** — nunca levanta exceção num instante descoberto, devolve
`suggested_hour_type=None` com o motivo. A R10 diz que classificação é conveniência e
não trava, e a D4/D9 proíbem bloquear trabalho já executado: uma configuração ruim não
pode impedir todo o apontamento do workspace. O indicador de saúde do painel existe
como o outro lado disso — mostra **quais** escopos e **quais** faixas estão
descobertos, porque um estado incompleto invisível só reapareceria como um Tipo de Hora
em branco no formulário, onde ninguém ligaria as duas coisas.

### Limitação conhecida: horário de verão

Documentada como limitação de **configuração**, não de impossibilidade geográfica:
`Workspace.timezone` aceita qualquer fuso, inclusive um com DST. Num dia de avanço do
relógio, a janela 18:00→08:00 tem 13 horas de relógio de parede em vez de 14, e um
apontamento que atravessa a transição recebe a duração de um segmento errada em uma
hora — um erro **financeiro**, pequeno e raro. Não foi corrigido; foi **fixado por
teste de caracterização** (`TestDaylightSaving`, com fixture em Lisboa), do tipo
`assert total == 240, "se isto agora é 180, o DST foi tratado"`. O teste falha no dia
em que alguém implementar o tratamento, que é exatamente quando se quer ser avisado.

## Critérios herdados da Fase 3: dois que só podiam ser fechados aqui

A Fase 3 foi implementada **fora de ordem** — o `README.md` declara que ela depende
desta fase — e por isso dois dos seus dezesseis critérios ficaram registrados como
**BLOQUEADOS na Fase 2b** em `03-worklog-core.md`. Eles dependem do motor de
classificação, que é entrega desta fase, e são responsabilidade dela.

**Os dois foram fechados aqui.**

| Critério da Fase 3                                                                                                                                       | O que faltava                                                                                                                                                            | Status                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| 8 — "apontamento em data de feriado já abre com 'Domingos e feriados' pré-selecionado e o motivo visível, nos dois modos de entrada"                     | o calendário, a janela, `priority` e o motor. O modelo já tinha `suggested_hour_type`, `is_hour_type_overridden` e `classification_reason` esperando quem os preenchesse | **atendido** — e nos dois modos, com o motivo nomeando o feriado                                        |
| 9 — "intervalo de segunda-feira 17:00 às 20:00 cria 2 apontamentos agrupados: 1h Horário comercial e 2h Fora do expediente, com preview antes de salvar" | quem decidisse **onde** cortar. O lote, o `batch_id`, o arredondamento por segmento, o guardrail e o preview de N segmentos já estavam prontos e testados                | **atendido** — 2 segmentos, um `batch_id`, e o preview conferido campo por campo contra o que foi salvo |

Onde estão fechados: `plane/tests/contract/app/test_service_log_classification_app.py`,
12 testes, por HTTP e de ponta a ponta.

Três observações sobre **como** foram fechados, porque cada uma responde a um jeito de
fechar mal:

1. **Arquivo separado, fixture oposta.** As fixtures de `test_service_log_app.py`
   constroem Tipos de Hora à mão, sem janela e sem prioridade — que é a cara de um
   workspace antes desta fase — e continuam assim de propósito: parser, arredondamento
   e totais **não devem** depender de calendário, e aqueles testes são o que afirma
   isso. Misturar os dois deixaria ambíguo qual teste depende de qual estado.

2. **A fixture semeia as janelas explicitamente e depois afirma o que semeou** — e
   afirma as **janelas específicas** de que os testes dependem, não uma contagem. Treze
   janelas podem ser treze pelos motivos errados, enquanto "segunda é comercial das
   08:00 às 18:00 e fora do expediente a partir das 18:00" é precisamente a
   configuração que faz o corte das 17:00–20:00 cair onde o critério 9 manda. Ler o que
   o workspace por acaso tivesse é como um teste de classificação passa sem
   classificador: sem janela não há sugestão, e "sem sugestão" é também o que um motor
   quebrado produz. Verificado por sabotagem — removida a janela de segunda do seed, os
   12 testes falham nomeando a janela ausente, em vez de ficarem verdes.

3. **Controle positivo para a divisão.** `test_an_evening_that_does_not_cross_a_border_stays_one_log`
   existe porque uma suíte em que _todo_ intervalo produzisse dois segmentos — por
   cortar em algo que não é a fronteira das 18:00 — seria indistinguível desta passando.

Ao fechar os dois, os critérios 8 e 9 da Fase 3 estão marcados como atendidos em
`03-worklog-core.md`, apontando para cá.

## Fora do escopo original: a coluna `verb` na trilha de auditoria

Esta fase acrescentou `verb` a `ServiceConfigActivity` (migração 0127), o que **não
estava no briefing**. O motivo:

`ChangeTrackerMixin` estruturalmente não emite evento de criação nem de exclusão, só de
alteração de campo rastreado. Para os catálogos da Fase 2 isso bastava — o evento
financeiro ali é **editar** o multiplicador. Para feriado e janela a relação **se
inverte**: criar e excluir _são_ os eventos financeiros. Cadastrar 15/03 como feriado
dobra a fatura daquele dia sem que campo nenhum de linha existente mude, e sem `verb`
esse cadastro não apareceria em lugar nenhum da trilha.

`created_by` / `deleted_at` na própria entidade não substituem: quem está sob pressão
porque o cliente contestou a fatura tem de ler **um** lugar, e o endpoint de auditoria
não mostraria nada.

Três decisões dentro dela:

- **Não nullable, com back-fill para `updated`.** Uma coluna de auditoria nullable
  obriga todo consumidor a tratar o nulo, e o nulo codificaria um palpite. O back-fill
  é **fato, não chute**: toda linha existente veio do `ChangeTrackerMixin`, que só
  dispara em edição.
- **`field_name` passou a nullable, com `CheckConstraint`** admitindo exatamente as três
  formas coerentes (`updated` exige `field_name` + os dois valores; `created` põe o
  resumo em `new_value`; `deleted` põe em `old_value`).
- **Uma linha por criação ou exclusão**, não uma por campo. O resumo é legível e contém
  o que muda o cálculo — para feriado ele **precisa** incluir a recorrência, para a
  trilha distinguir "cadastrou o Natal de 2027" de "cadastrou o Natal para sempre".

> **Consequência a registrar, porque ela cobra o preço em outra fase.** Exigir
> `old_value` e `new_value` não nulos em `updated` é seguro hoje porque todo
> `TRACKED_FIELDS` é campo de modelo não nulo. Uma fase futura que rastreie um campo
> **nullable** — o preço sobrescrito opcional da Fase 6 é o caso concreto — violaria a
> constraint no momento da escrita. As três saídas estão listadas no comentário da
> constraint, em `plane/db/models/service_config_activity.py`.

## Entregar

Modelo de dados (feriado, janela de classificação, prioridade no Tipo de Hora) e
a assinatura do motor primeiro, para validação. Depois: implementação, telas de
administração e testes. A suíte do motor deve cobrir a tabela da seção 4
integralmente, mais os dois casos de arredondamento da seção 6.
