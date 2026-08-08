# Decisões

## Resolvidas — todas incorporadas aos prompts

| #   | Decisão                                                                                                                                                                                   | Onde foi aplicada                         |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| D1  | Duração abaixo de 6 min arredonda para o **piso de 15 min**                                                                                                                               | R2 do contexto mestre                     |
| D2  | Arredondamento **no meio do bloco** (7min30s), empate para cima                                                                                                                           | R2, tabela de testes                      |
| D3  | Saldo não utilizado **acumula** para os meses seguintes                                                                                                                                   | Fase 4, seção 3                           |
| D4  | Saldo **pode ficar negativo**, sem bloquear. Ao fechar o mês, Admin escolhe transportar o déficit ou **faturar o excedente em R$**                                                        | Fase 4 seção 4, Fase 6 seção 6            |
| D6  | Preço avulso = **valor/hora base × multiplicador**, com override absoluto opcional                                                                                                        | Fase 6, seção 1                           |
| D7  | **O Cliente define o padrão** de Tipo de Atendimento; o apontamento pode sobrescrever                                                                                                     | Fase 2, seção 3                           |
| D8  | Feriados e horários cadastráveis no painel; classificação automática; modo de entrada por hora início/fim                                                                                 | Fase 2b + Fase 3                          |
| D9  | Contrato vencido: **permitir, alertar e sinalizar** no relatório. Nunca descartar registro por pendência comercial                                                                        | Fase 4, seção 1                           |
| D10 | **Sem workflow de aprovação** de timesheet. Fica o travamento de período                                                                                                                  | Fase 4, seção 6                           |
| D11 | Usuário de cliente pode responder por **mais de uma empresa** (matriz e filial). Implementado via participação em múltiplos projects — **ver D19**, que substituiu a tabela de associação | Fase 1 seção 2, Fase 8 seção 2            |
| D12 | **Sem migração** agora, mas com campo de origem no apontamento desde já                                                                                                                   | Fase 3, seção 4                           |
| D13 | **Divisão em segmentos apenas no modo Intervalo.** No modo Duração o técnico seleciona o tipo                                                                                             | R10, Fase 2b seções 3 e 4, Fase 3 seção 3 |
| D14 | Calendário de feriados **único no workspace**, campo de abrangência no modelo sem lógica de filtro                                                                                        | Fase 2b, seção 1                          |
| D15 | Três caminhos de renovação, incluindo **converter saldo em bolsa de horas de um chamado**. Teto de acúmulo + alertas de alto e baixo consumo                                              | Fase 4 seções 3, 8 e 9; Fase 9 seção 3b   |
| D16 | **Não existe jornada por técnico.** As janelas de classificação do workspace são a única fonte da verdade                                                                                 | Fase 2b, requisito negativo explícito     |
| D17 | **Arredondar por segmento**, com guardrail para lançamentos abaixo de 15 min                                                                                                              | Fase 2b, seção 6                          |

| D23 | **Livro-caixa de horas append-only.** O período carrega o saldo, o livro-caixa carrega o histórico, e os dois na mesma transação | Fase 4, `service_hour_ledger_entries` |
| D24 | **Consumo FIFO**, parcela mais antiga primeiro, para que nenhuma hora expire podendo ter sido usada | Fase 4, seção 3 |
| D25 | **O teto de acúmulo descarta as parcelas mais NOVAS**, preservando as mais antigas | Fase 4, seção 3 |
| D26 | **Mês parcial é dado, não regra:** `contracted_hours` editável enquanto o período está aberto, com auditoria | Fase 4, seção 2 |
| D27 | **Resolução vazia de contrato não bloqueia o apontamento**; ambiguidade bloqueia | Fase 4, seções 1b e 5 |
| D28 | **A bolsa de horas é herdada pela árvore de work items**, ancestral mais próximo primeiro, com limite de 10 | Fase 5, seção 2 |
| D29 | **A bolsa reusa o livro-caixa do contrato** em vez de ganhar um segundo journal | Fase 5, `service_hour_ledger_entries` |
| D30 | **Bolsa encerrada bloqueia o apontamento e não cai para o contrato** | Fase 5, seção 3 |

Nenhuma decisão bloqueia a implementação. Os prompts estão prontos para uso.

---

## Registro das três decisões que mudaram a arquitetura

### As janelas de classificação substituíram o enum de detecção

O modelo original usava enums (`FORA_DA_JORNADA`, `DIA_NAO_TRABALHADO`). Ele
**não conseguia expressar** que sábado é 1.5 e domingo é 2.0 — os dois são dias
não trabalhados. Nem a janela contínua 18:00→08:00 atravessando a meia-noite.

O modelo atual é de janelas (dia da semana + faixa horária) com prioridade, e
resolve os dois casos nativamente. Consequência: apontamento que vira a noite em
dia útil **não** é dividido, mas sábado 22:00 → domingo 02:00 **é**, porque cruza
de 1.5 para 2.0.

### A jornada por técnico foi removida do escopo (D16)

Era uma entidade que eu havia proposto e que se mostrou desnecessária: as janelas
comerciais já são a fonte da verdade sobre horário, e são as mesmas para todos
porque refletem os contratos com os clientes.

Manter as duas coisas faria a fatura do cliente depender de qual técnico
atendeu — um técnico em escala noturna geraria hora comercial (1.0) às 22h
enquanto outro geraria 1.5 pelo mesmo trabalho no mesmo horário.

Está registrado na Fase 2b como **requisito negativo explícito**, para que o
agente não reinvente a entidade por conta própria, com um critério de aceite que
verifica sua ausência.

### O usuário de cliente pode responder por mais de uma empresa (D11)

A primeira solução foi uma tabela de associação N:N usuário↔Cliente, mais seleção
explícita da empresa na abertura do chamado.

**Substituída pela D19.** Com o Cliente ancorado a projects, o vínculo passa a ser
derivado da participação do usuário nos projects, e a seleção explícita deixa de
ser necessária — o chamado nasce dentro de um project, que pertence a um único
Cliente. Menos código, menos superfície de erro, e a ambiguidade desaparece por
construção em vez de ser tratada.

O que permaneceu: o teste de isolamento "usuário de A e B vê A e B, não vê C",
agora aplicado às entidades novas (apontamento, contrato, período, bolsa, preço),
que são código próprio e não herdam a proteção do Plane automaticamente.

---

## D18 e D19 — resolvidas com o cenário Marubeni / Terlogs

### D18 — Hierarquia matriz e filial: **não modelar como regra**

O cenário real (Marubeni é matriz da Terlogs, cada uma com contrato próprio e
vigência própria) mostrou que a relação societária **não afeta faturamento**:
cada Cliente tem seu contrato, seu pool e seu dashboard dedicado.

Decisão: Clientes permanecem **independentes**. Foi adicionado apenas um campo
`cliente_pai` nullable, **sem nenhuma lógica associada**, para viabilizar um
relatório consolidado de grupo econômico na Fase 9, caso você queira depois.

Coluna nullable sem código é custo próximo de zero e não compromete nada. Foi por
isso que ela entrou, enquanto a jornada por técnico (D16) foi removida: aquela
exigia entidade, tela de configuração e um caminho de resolução no motor de
classificação, com risco real de divergir da configuração comercial.

### D19 — Cliente é ancorado a projects do Plane

Esta é a decisão de maior impacto do material e substitui o desenho anterior, em
que o Cliente era um FK livre no work item.

**Cada Cliente corresponde a um ou mais projects do Plane.** Usuários do cliente
são workspace Guests adicionados aos projects dos seus Clientes.

Fundamento verificado na documentação do Plane: um Guest só vê os projects aos
quais foi explicitamente adicionado, e não vê outros projects nem informação de
nível de workspace. Workspace Admins têm acesso a todos os projects sem precisar
de participação em cada um.

O que isso resolve:

| Problema                             | Como o desenho anterior resolvia                | Como resolve agora                            |
| ------------------------------------ | ----------------------------------------------- | --------------------------------------------- |
| Isolamento entre clientes            | Filtro de queryset customizado em cada endpoint | Mecanismo nativo do Plane                     |
| Vínculo usuário↔Cliente              | Tabela de associação N:N própria                | Derivado da participação nos projects         |
| Troca de portal (Marcel)             | Seletor de empresa customizado                  | Seletor de project nativo                     |
| Qual Cliente é o chamado             | Campo editável, com risco de ficar errado       | Derivado do project, impossível divergir      |
| Ambiguidade do usuário multi-empresa | Seleção obrigatória na abertura                 | Deixa de existir: o chamado nasce num project |
| Relatório cross-cliente              | —                                               | Admin e Member enxergam todos os projects     |

Onde foi aplicada: contexto mestre seção 2b; Fase 1 reescrita; Fase 8 reescrita;
Fase 4 seções 1b e 5; Fase 9 seções 1b, 1c e 3c.

**Alternativa descartada: um workspace do Plane por Cliente.** Daria isolamento
nativo ainda mais forte e o switcher nativo de workspace. Foi descartada porque
quebraria o relatório cross-cliente (horas por técnico, painel de clientes que
precisam de atenção), exigiria os técnicos como membros de todos os workspaces, e
duplicaria a configuração de tipos de hora, janelas e feriados por workspace —
recriando exatamente o problema de fontes divergentes que motivou a D16.

### Consequência adicional: múltiplos contratos por Cliente

O exemplo levantou um caso que o material não cobria: um Cliente com **mais de um
contrato ativo** (ex.: suporte + infraestrutura, com pools separados).

A ancoragem em projects resolve elegantemente: o **project pode fixar um
contrato**, e a resolução é contrato do project → contrato padrão do Cliente. Está
na Fase 4, seção 1b. A restrição anterior de "nenhum contrato ativo sobreposto"
foi removida.

---

## Nada pendente

Não há decisão bloqueando nenhuma fase. Duas confirmações desejáveis, com padrão
já assumido:

1. **Fase 8, seção 3** — o cliente com contrato vê horas e descrição dos
   apontamentos dos seus chamados, mas não vê valores em R$ nem multiplicadores.
   O cliente avulso vê horas **e** valores, porque o valor é a fatura dele.
2. ~~Fase 8, investigação sobre papéis customizados~~ — **respondido pela
   auditoria do fork.** Não existem papéis customizados nem papel Commenter; o
   caminho é estender o GUEST. Ver a seção seguinte.

---

## Auditoria do fork: o que ela resolveu e o que ela criou

O fork `andresatziack/plane-lab` foi auditado (branch `preview`, commit
`31853ab`). Detalhes com arquivo e linha em `docs/worklog/ACHADOS-DO-CODIGO.md`.

### Confirmações pendentes que foram respondidas pelo código

**Papéis customizados e papel Commenter: não existem neste fork.** Só há
`ADMIN=20`, `MEMBER=15`, `GUEST=5`. A documentação pública do Plane descreve
Commenter e papéis customizados porque se refere a uma versão mais nova ou
comercial. A Fase 8 foi reescrita para estender o GUEST, sem criar papel novo.

Boa notícia: a premissa da **D19 se confirmou**. O isolamento nativo por project
existe e funciona (`app/views/project/base.py:101-114`), então a arquitetura de
ancorar Cliente em projects continua sendo a certa — e continua economizando a
maior parte do trabalho de segurança.

### Correção de rota no material

O contexto mestre instruía "criar app(s) Django próprio(s)". **Estava errado para
este repositório:** `plane.db` é o único app com modelos de domínio, e há uma
cadeia única e linear de migrations (já em `0122`). A seção 6 do contexto mestre
foi reescrita com a estrutura real.

### Riscos novos que a auditoria revelou

1. **O bypass `creator=True`** (`app/permissions/base.py:24-40`) libera a view
   inteira para o criador do registro, ignorando o papel — e o `partial_update` de
   work item não valida campo nenhum. Hoje um Guest altera **tudo** nos chamados
   que ele criou, e **nada** nos abertos por técnicos. Está tratado na Fase 8,
   seção 4b, com critério de aceite dedicado (nº 16).

2. **`guest_view_all_features` tem padrão `False`**, o que faria Marcel ver apenas
   os chamados que ele mesmo abriu. Virou requisito de configuração na Fase 1 e na
   Fase 8, seção 1.

3. **`--nomigrations` no `pytest.ini`** significa que o schema de teste vem dos
   models. Migration quebrada **não** é detectada pelos testes. Registrado na
   seção 6 do contexto mestre.

4. **Colisão com a feature paga de time tracking do Plane.** Existe a flag
   `Project.is_time_tracking_enabled` sem nenhum modelo que a consuma, e a choice
   de exportação `issue_worklogs` sem implementação — ganchos da feature
   comercial. Reutilizamos a flag, mas as entidades novas devem ser nomeadas para
   não colidir, caso um dia haja migração para a Commercial Edition.

5. **A Fase 8 é a única que altera código do core do Plane.** Todas as outras só
   adicionam. Isso a torna a fase de maior atrito em merges futuros com o upstream,
   e a de maior risco de segurança se implementada na ordem errada — a allowlist de
   campos precisa vir **antes** de incluir GUEST no `partial_update`.

---

## D20 — A flag booleana `garantia` foi eliminada

Decidida durante a implementação da Fase 2, quando ficou visível que "Cortesia"
(um Tipo de Atendimento de rota `NON_BILLABLE`) e a flag booleana `garantia`
produziam o mesmo efeito por dois caminhos diferentes.

**O problema.** Com os dois mecanismos, nada definia o comportamento de um
apontamento marcado como garantia **e** com Tipo de Atendimento "Cortesia". Rota
de faturamento é um eixo único e precisa de um mecanismo único.

**A decisão.** A flag deixa de existir. "Não cobrar" passa a ser expresso
exclusivamente por `ServiceBillingType.billing_route == NON_BILLABLE`, e o seed
traz **dois tipos distintos** com essa rota:

| Tipo         | Significado                                                  | O que um volume alto indica          |
| ------------ | ------------------------------------------------------------ | ------------------------------------ |
| **Garantia** | retrabalho — o serviço já foi cobrado e você está corrigindo | problema de qualidade na sua entrega |
| **Cortesia** | decisão comercial de não cobrar                              | desconto concedido                   |

**Por que não fundir os dois num só.** São o mesmo mecanismo mas informações
diferentes, com ações de gestão opostas. Um mês com 20h de garantia é um problema
de execução; com 20h de cortesia é um desconto deliberado. Os relatórios agrupam
por Tipo de Atendimento, então a distinção sobrevive sem campo extra — e o admin
pode criar outros motivos ("Erro interno", "Pré-venda") pelo painel, sem código.

**Ganho colateral:** o formulário de apontamento perde um campo, e o catálogo
ganha extensibilidade que a flag não tinha.

Onde foi aplicada: R5 reescrita e R11 no contexto mestre; §4 (grandeza 4) e §4b;
Fases 3, 4, 5, 6, 8 e 9.

**Consequência para a Fase 3:** `horas_debitadas = 0` e `valor = 0` passam a ser
derivados da rota, não de uma flag. E o apontamento precisa snapshotar a **rota
aplicada** além do multiplicador — a R4 citava apenas o multiplicador, mas dois
Tipos de Atendimento podem compartilhar a mesma rota, e sem o snapshot um
relatório histórico não consegue separá-los depois de uma edição de catálogo.

## Alinhamento de nomenclatura com o código entregue

Os documentos citavam os valores da rota de faturamento em português
(`DEBITA_POOL`, `FATURA_REAIS`, `NAO_FATURAVEL`). A Fase 2 implementou seguindo a
convenção da casa, com valores em inglês. Os documentos foram alinhados ao código:

| Nos documentos (antes) | No código (`ServiceBillingType.BillingRoute`) |
| ---------------------- | --------------------------------------------- |
| `DEBITA_POOL`          | `DEBIT_POOL` = `"debit_pool"`                 |
| `FATURA_REAIS`         | `BILL_AMOUNT` = `"bill_amount"`               |
| `NAO_FATURAVEL`        | `NON_BILLABLE` = `"non_billable"`             |

## D21 e D22 — decididas na Fase 2b, porque só ali se tornaram inevitáveis

### D21 — A cobertura da semana é uma **catraca**, e o motor é **total**

A Fase 2b pedia que "o conjunto total deve cobrir 100% da semana" e que um instante sem
classificação fosse "impedido no cadastro". Implementado, mas com duas restrições que o
texto original não previa:

**A cobertura é verificada como catraca: um conjunto completo nunca pode ficar
incompleto.** Exigi-la incondicionalmente tornaria impossível criar a _primeira_ janela
de um workspace montado à mão, e impossível **consertar** um workspace já quebrado — o
admin ficaria trancado fora da única tela capaz de corrigir. Um workspace semeado nasce
completo, então na prática toda instalação está protegida desde o primeiro dia.
Sobreposição no mesmo par `(escopo do dia, prioridade)`, ao contrário, é **sempre**
recusada: não existe desculpa transitória para duas janelas entre as quais não há regra
de decisão.

**Em consequência, o motor é total: nunca levanta exceção.** Num instante descoberto
devolve `suggested_hour_type=None` com o motivo. A R10 diz que a classificação é
conveniência e não trava, e a D4 e a D9 estabelecem que nada bloqueia trabalho já
executado por pendência de configuração ou de contrato. Um motor que estourasse numa
faixa descoberta faria uma configuração ruim parar **todo** o apontamento do workspace.

O preço disso é que "incompleto" passa a ser um estado real e precisa ser visível: o
indicador de saúde do painel diz **quais** escopos e **quais** faixas estão descobertos.
"Incompleto" sozinho não dá ao admin nada em que agir, e um estado incompleto invisível
só reapareceria como um Tipo de Hora em branco no formulário, onde ninguém ligaria as
duas coisas.

### D22 — A trilha de configuração ganhou `verb`, não nullable

Para os catálogos da Fase 2, o evento financeiro é **editar** — um multiplicador que
muda. Para feriado e janela a relação **se inverte**: criar e excluir _são_ os eventos
financeiros, porque cadastrar 15/03 como feriado dobra a fatura daquele dia sem que
campo nenhum de linha existente mude. `ChangeTrackerMixin` estruturalmente não emite
evento de criação nem de exclusão, então sem `verb` esses eventos não apareceriam em
lugar nenhum da trilha.

`created_by` e `deleted_at` na própria entidade **não** substituem: quem está sob pressão
porque o cliente contestou a fatura tem de ler **um** lugar, e o endpoint de auditoria
não mostraria nada.

A coluna é **não nullable, com back-fill para `updated`**. O back-fill é fato e não
palpite — toda linha existente veio do `ChangeTrackerMixin`, que só dispara em edição. Uma
coluna de auditoria nullable obrigaria todo consumidor a ramificar no nulo, e o nulo
codificaria um chute sobre o que aconteceu.

As Fases 4 e 6 devem usar os três verbos. O ponto de extensão está no docstring de
`ServiceConfigActivity`, junto com o aviso de que a constraint que exige `old_value` e
`new_value` em `updated` cobra um preço de quem rastrear um campo **nullable** — o preço
sobrescrito opcional da Fase 6 é o caso concreto.

## D23 a D27 — decididas na Fase 4, porque só ali se tornaram inevitáveis

### D23 — O livro-caixa de horas, e por que ele não é a "segunda tabela de auditoria"

Quatro exigências do briefing da Fase 4 eram a mesma exigência: rastrear a competência de
origem de cada parcela para expirar a certa (critério 6), estorno idempotente (11 e 12),
"saldo nunca desaparece sem registro de auditoria" (20) e concorrência sem corromper saldo
(14). Sem um journal, cada uma precisa de mecanismo próprio.

Com ele, o critério 20 passa a valer **por construção**: não existe caminho que reduza
saldo sem inserir linha, porque a redução _é_ a linha. O invariante que prova isso é
verificável: **um período fechado soma exatamente zero** no livro-caixa — tudo saiu, foi
faturado ou foi baixado, e cada um desses é uma linha.

A §6 do contexto mestre proíbe uma segunda tabela de auditoria de configuração, e esta não
é uma. A divisão é explícita: `ServiceConfigActivity` registra **configuração** (contrato
criado, horas mensais alteradas); o livro-caixa é **movimento contábil**. Apagar uma linha
lá perderia a _explicação_ de um número; apagar uma linha aqui **mudaria** o número.

Consequência de projeto (D2 do design aprovado): o período carrega os totais e é a linha que
se tranca; o livro-caixa carrega os movimentos; os dois são escritos na mesma transação,
sempre. `reconcile_period` afirma que os dois batem, **coluna por coluna** — um agregado
único seria satisfeito por dois erros que se cancelam, e diria "algo está errado" quando o
operador precisa saber _qual número_ confiar.

### D24 — Consumo FIFO, parcela mais antiga primeiro

Regra que o briefing não enuncia e sem a qual o critério 6 é indeterminado: "o saldo de
janeiro não utilizado expira no momento correto" depende de as horas de janeiro terem sido
ou não as gastas.

A cota do mês é uma parcela com origem no próprio mês, e o consumo come sempre da mais
antiga. Consequência, que é o motivo da escolha: o transportado é consumido antes, e **hora
nenhuma expira podendo ter sido usada**. A ordem inversa faria o cliente perder saldo com o
pool cheio, e ele reclamaria com razão.

A alocação acontece na **leitura**, não no débito. É isso que permite ao débito ser uma
única linha contra o período sob um único lock, em vez de uma linha por parcela tocada.

### D25 — O teto de acúmulo descarta as parcelas mais NOVAS

Ambiguidade que o briefing não fecha: com 75h querendo transportar e teto de 60h, _quais_
15h são descartadas?

Descartam-se as mais **novas**, preservando as mais antigas. Dois motivos, e eles
concordam: sob FIFO as mais antigas são as que o consumo alcança primeiro, então preservá-las
faz do saldo sobrevivente o saldo que de fato será usado; e é o que o cliente espera ouvir
— "você perdeu as horas que não usou este mês", não "você perdeu horas do trimestre
passado".

Fixado por teste de caracterização, para que mudar a ordem tenha de ser deliberado.

### D26 — Mês parcial é dado que o admin informa, não regra que o sistema inventa

Nem pro-rata nem "sempre integral". `contracted_hours` já era snapshot por período (R4), e
passou a ser **editável enquanto o período está `OPEN`**, com default `monthly_hours`.

Assim o pro-rata deixa de ser regra inventada e passa a ser o que o contrato disser, sem
campo de política e sem chute. Obrigatório, e implementado: a edição vai para a trilha de
auditoria e é **recusada** em período `CLOSED`.

Efeito colateral no livro-caixa, registrado porque foi o único ponto em que o conjunto
fechado de tipos de lançamento teve de ceder: corrigir 30h para 15h precisa mover o saldo, e
num journal append-only uma correção é uma linha nova. Essa linha é um `GRANT` de −15, então
`GRANT` passou a ser o único tipo aditivo que aceita valor negativo. A alternativa era um
décimo segundo tipo significando "ajuste de cota", que transformaria
`sum(GRANT) == contracted_hours` — o invariante que a reconciliação de fato verifica — numa
soma de dois termos, sem ganho.

### D27 — Resolução vazia de contrato não bloqueia o apontamento; ambiguidade bloqueia

**A única decisão da Fase 4 que se afasta da letra de um critério de aceite, e está
sinalizada para confirmação porque é cláusula contratual, não escolha de implementação.**

O critério 24 diz que uma resolução "ambígua **ou vazia** falha com mensagem explícita". A
tensão é com a §4, a D4 e a D9, que dizem três vezes, de três ângulos, que trabalho já
executado nunca é descartado por pendência comercial — e "o comercial ainda não cadastrou o
contrato" é exatamente uma pendência comercial. Cumprir o critério 24 ao pé da letra fazia o
apontamento ser **recusado**, e apontamento recusado é apontamento perdido.

A reconciliação está na justificativa do próprio critério 24: _"debitar o pool errado é pior
que bloquear o apontamento"_. Esse raciocínio só morde quando existe um pool errado a
debitar — o caso **ambíguo**. Sem cliente e sem contrato não há pool errado; não há pool.

Decidido:

- **ambiguidade, e pin para o contrato de outro cliente** → bloqueia, com código explícito
  e nomeando os candidatos. É o caso para o qual o critério 24 foi escrito;
- **resolução vazia** (`NO_SERVICE_CLIENT_FOR_PROJECT`, `NO_CONTRACT_FOR_CLIENT`) → falha
  **alto, sem bloquear**: o apontamento fica gravado com `debited_period` nulo, e o painel
  do chamado informa o código. A ausência diz o motivo, porque "não faturável" e "não existe
  contrato" produzem o mesmo saldo e são bugs opostos.

A D21 já resolveu a questão idêntica para o motor de classificação e o tornou total, com o
argumento de que uma configuração ruim não pode parar **todo** o apontamento do workspace. O
mesmo argumento vale aqui sem alteração.

Efeito prático que confirma a escolha: com o bloqueio, 39 testes das Fases 2b e 3 passavam a
falhar — todos eles apontamentos de rota `DEBIT_POOL` em workspaces sem contrato, que é
precisamente o estado de qualquer instalação antes de o comercial cadastrar o primeiro
contrato.

## D28 a D30 — decididas na Fase 5, porque só ali se tornaram inevitáveis

### D28 — A bolsa de horas é herdada pela árvore de work items

Ambiguidade que o briefing não fecha e que a R6 não menciona: um projeto de 40h vendido à
parte é aberto como **um** chamado, e depois quebrado em sub-tarefas por qualquer um que o
execute. As sub-tarefas consomem a bolsa do pai?

A proposta inicial foi **sem herança** — a bolsa vale só para o work item em que foi
creditada — pelo argumento de que subir a cadeia de pais é regra de negócio que ninguém
escreveu. **Recusada**, e o motivo é que as consequências dos dois erros não são simétricas:

- **sem herança**, o pai fica com 40h que ninguém aponta enquanto cada sub-tarefa debita o
  pool de 30h/mês do contrato. O isolamento que a bolsa existe para dar deixa de existir, e
  deixa de existir **em silêncio**: nenhum erro, nenhum alerta, apenas o pool errado
  pagando. O cliente consome suporte que não devia;
- **com herança**, o custo é uma query estreita por nível de aninhamento.

Erro silencioso que fatura errado contra um custo de query mensurável decide sozinho.

**A regra é: ancestral mais próximo com bolsa vence.** Sobe `Issue.parent` a partir do work
item do apontamento e para na primeira bolsa encontrada. Não é ambíguo — é ordem total ao
longo da cadeia — então a **D27 não é tocada**: não existe ambiguidade a resolver. A única
forma que _seria_ ambígua, várias bolsas num mesmo work item, é irrepresentável pelo índice
único parcial.

Guarda-corpos, todos com teste, porque uma regra de hierarquia sem limite é uma query sem
limite:

- **profundidade máxima de 10 ancestrais**, e estourar o limite é tratado como **ausência de
  bolsa, nunca como erro**. Recusar um apontamento por causa de como alguém aninhou os
  chamados custaria ao técnico o trabalho dele por uma forma do dado que ele não escolheu, e
  a D4 e a D9 proíbem isso;
- **pai e filho com bolsa: o filho vence**, fixado por teste de caracterização — uma
  sub-tarefa com bolsa própria foi financiada à parte, e o ancestral mais próximo é a
  resposta mais específica;
- ciclo de `parent` não trava a busca.

### D29 — A bolsa reusa o livro-caixa do contrato, e o motivo é o critério 7

Escolha de arquitetura que o briefing deixou aberta: a bolsa reusa
`ServiceHourLedgerEntry` ou ganha o seu próprio journal? Reusar exigia afrouxar `contract` e
`period` para nullable — afrouxar coluna em tabela de dinheiro, exatamente o que esta série
evita.

Reusar ganhou, e **não** pelo argumento óbvio da D23 ("um só lugar onde horas se movem").
Ganhou porque o índice único parcial `(service_log, entry_type)` já existia:

```
UniqueConstraint(fields=["service_log", "entry_type"],
                 condition=Q(entry_type__in=[DEBIT, REVERSAL], service_log__isnull=False))
```

Com um journal único, "debitar a bolsa **e** o contrato pelo mesmo apontamento" seriam duas
linhas `DEBIT` do mesmo apontamento, e o **banco recusa**. O critério 7 da Fase 5 — "estourar
a bolsa não debita do contrato em nenhuma circunstância", "sem débito parcial nos dois" —
deixa de ser propriedade da ordem de um `if` e passa a ser estrutural. Verificado por
sabotagem: debitando os dois de propósito, o erro é
`UniqueViolation: service_ledger_unique_debit_reversal_per_service_log`.

O segundo ganho está no critério 6. O estorno precisa saber para onde devolver, e a resposta
**tem** de sair do que o débito persistiu. Com journal único é uma query, uma linha, e a
resposta é uma coluna dela. Com dois journals seriam duas queries e um `if` — e um `if` é
onde a re-resolução da origem volta a entrar por descuido, mandando as horas para o contrato
no instante em que a bolsa é encerrada.

O preço da nulidade foi pago com uma constraint XOR em DDL
(`service_ledger_entry_has_exactly_one_target`): uma linha pertence a um período **ou** a uma
bolsa, nunca a nenhum, nunca aos dois. Ela deixa a tabela **mais** restrita do que era —
"linha de período cujo `contract` aponta para outro contrato" era representável antes e não é
mais.

### D30 — Bolsa encerrada bloqueia o apontamento, e não cai para o contrato

A escolha óbvia é a errada, e é por isso que está registrada.

Um apontamento num chamado cuja bolsa está **encerrada** é recusado (`ALLOWANCE_IS_CLOSED`).
Ele **não** cai para o pool do contrato, e não cai para um ancestral mais distante. Cair para
o contrato seria o "excedente migra para o pool do contrato" que a §3 proíbe _nominalmente_,
"nem silenciosamente nem automaticamente" — e chegaria por omissão, que é a pior forma de uma
regra financeira aparecer. Pular para o avô gastaria horas que o encerramento do projeto
fechado já contabilizou.

Consequência de projeto: `resolve_work_item_allowance` devolve a bolsa **independentemente do
status**, e a checagem de status mora no débito, espelhando `resolve_period` + `_lock_period`.
Se ela filtrasse por bolsa aberta, o fallback proibido aconteceria sozinho.

**Coerência com a D27**, que era o ponto que o handoff cobrava: a D27 diz que ambiguidade
bloqueia e ausência não. Bolsa encerrada não é nenhum dos dois — é **trava
administrativa**, como período fechado, que também bloqueia. Ausência de bolsa não é falha
alguma: é o nível 2 da hierarquia da R6. E ambiguidade não pode existir. Portanto
`NON_BLOCKING_RESOLUTION_FAILURES` não mudou e a D27 não precisou ser revista.

O estorno em bolsa encerrada é recusado do mesmo modo, e o apontamento fica **intacto** em vez
de apagado — o equivalente exato do que a Fase 4 decidiu para período fechado.

### Nota de método: o critério 9 virou DDL, e a migração foi feita para falhar alto

O critério 9 (rota `NON_BILLABLE` não consome bolsa) já era verdadeiro por duas vias: a ordem
das guardas em `apply_debit` e a constraint da Fase 3 que zera `debited_hours`. Ganhou uma
terceira, em DDL: `service_log_non_billable_debits_no_origin`.

Ela não é redundante. As duas primeiras garantem que **nenhuma hora se move**; nenhuma
impedia um caminho de código de **gravar a origem** num apontamento não faturável, deixando a
linha _alegando_ que uma bolsa pagou por ela — que é exatamente o que um relatório da Fase 9
faturaria.

Como é constraint nova sobre coluna **existente**, ela pode falhar na aplicação em dado real.
Isso é a intenção, não o risco: linha que a viole é bug a corrigir, não constraint a relaxar.
Para a falha ser acionável, a migração 0129 roda um `RunPython` **antes** dos `AddConstraint`
que conta as linhas violadoras e levanta erro **nomeando os IDs**. Validado à mão: com uma
linha corrompida de propósito, a migração para e diz qual.

---

# As cláusulas contratuais: a dívida de processo, paga

As Fases 4 e 5 responderam oito ambiguidades com defaults razoáveis e testes de
caracterização, e registraram no handoff que isso deixaria de ser aceitável na
Fase 6 — a fase do R$, onde várias delas mudam o valor de uma fatura.

As respostas abaixo vêm dos **contratos reais com os clientes**. Elas substituem
os defaults. Onde um default assumido divergia da cláusula, está dito
explicitamente o que muda no código e onde.

## D31 — Sem pro-rata. Vigência começa no dia 1º, e apontamento anterior debita o primeiro período

A vigência de um contrato começa **no primeiro dia do mês seguinte** ao
fechamento. Não existe pro-rata: o primeiro mês vale as horas mensais cheias.

O chamado pode ser aberto antes disso, e o apontamento com **data anterior ao
início da vigência é aceito e debita o pool** — porque, no horizonte do contrato,
30h/mês por 12 meses são 360h, e antecipar algumas horas não altera esse total.

**Consequência que exige mudança de código.** Hoje `resolve_contract`
(`plane/utils/service_pool.py:192`) devolve o contrato mesmo para data fora da
vigência — nada é excluído por isso, só um aviso é emitido, conforme D9. O
apontamento então chega em `resolve_period`, que materializa o período **do mês
da data** com `contracted_hours` copiado de `monthly_hours`.

Para um contrato que começa em março, apontar em fevereiro criaria um 13º período
com 30h de cota própria: o ano passaria a valer **390h em vez de 360h**. Trinta
horas de presente, exatamente o oposto da justificativa da cláusula.

A regra, portanto, é: **apontamento com data anterior ao início da vigência
debita o primeiro período do contrato, e nenhum período é materializado fora da
vigência.** O ponto único de mudança é a resolução de competência, não o motor de
débito.

Note que isto vale só para o passado da vigência. Data posterior ao fim é outra
cláusula — ver D33.

## D32 — Não existe teto de déficit

O saldo de um período pode ficar negativo sem limite. Nada bloqueia por
excesso de déficit; o controle é o **alerta**, que já re-dispara a cada 5h de
piora.

Confirma o default assumido pela Fase 4. Nenhuma mudança.

## D33 — Pool não resolvível fatura avulso, com o motivo registrado

Três situações distintas têm a mesma resposta comercial, e por isso passam a ter
**um mecanismo único**:

| Situação                            | Antes                                              | Agora                                                                     |
| ----------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------- |
| Cliente **sem contrato** nenhum     | não bloqueia (D27), destino do dinheiro indefinido | **fatura avulso**                                                         |
| Contrato **suspenso**               | apenas um código de alerta distinto (decisão B4)   | continua aceitando apontamento, **com alerta**, e o tempo **vira avulso** |
| Contrato **vencido** e não renovado | permitido e sinalizado (D9), destino indefinido    | **fatura avulso**                                                         |

A exceção única é o contrato com **vigência futura**, que debita o primeiro
período (D31). Ou seja: contrato que ainda vai começar debita; contrato que
parou de valer, por qualquer motivo, fatura em R$.

**O motivo tem de ser registrado, não só o resultado.** Um relatório precisa
distinguir "avulso porque o cliente é avulso" de "avulso porque o contrato venceu
e ninguém renovou" — a segunda é uma pendência comercial com prazo, a primeira é
o modelo de negócio do cliente. Sem o motivo persistido, as duas viram a mesma
linha no consolidado.

**Tensão de desenho para a Fase 6 resolver.** A rota de faturamento é snapshot no
apontamento (`applied_billing_route`), derivada do Tipo de Atendimento escolhido.
Nestes três casos o tipo escolhido é "Contrato" (`DEBIT_POOL`) mas o resultado
aplicado é avulso. Os dois precisam ser recuperáveis: o que foi **escolhido** e o
que foi **aplicado**, mais o motivo do desvio. Sobrescrever o snapshot em silêncio
perderia a informação de que houve um desvio.

## D34 — O saldo acumulado é preservado. Nada é descartado

Não há descarte de saldo acumulado, nem por teto nem por idade. O saldo é
**preservado até que a situação comercial ou contratual seja resolvida**, e o
controle é alerta, não trava.

**Isto não exige mudança de código.** Os dois mecanismos da Fase 4 já são
opcionais e nascem desligados:

- `ServiceContract.accrual_cap_mode = NONE` — nada é descartado por teto, e a
  constraint `service_contract_accrual_cap_is_coherent` já garante que sem modo
  não há valor;
- `carryover_months = NULL` — nada expira por idade.

A capacidade permanece no código (`discarded_by_cap_hours`, `EXPIRED_BY_CAP`,
`EXPIRED_BY_VALIDITY`) e o critério 15 da Fase 4 continua válido, porque testa o
comportamento **quando configurado**. A decisão é de política de cadastro, não de
implementação. D25 (ordem de descarte) fica sem efeito prático, e permanece
registrada para o caso de algum contrato futuro configurar teto.

**O que essa escolha custa, e é deliberado:** sem teto e sem validade, o saldo
acumula indefinidamente. Um cliente de 30h/mês que consome 10h junta 240h em um
ano — obrigação sua de atender, concentrável em um único mês. É por isso que o
alerta de saldo acumulado alto deixa de ser conveniência e passa a ser o único
controle existente sobre esse risco. Confirmar que ele existe, e criá-lo se não
existir, é requisito.

## D35 — Bolsa encerrada tem 30 dias de carência, contados do fechamento do chamado

O saldo positivo de uma bolsa é perdido, e **nunca** vai para o pool do contrato
(§3 do briefing da Fase 5, D30). Mas a baixa não é imediata: a bolsa entra em
**carência de 30 dias a partir do fechamento do work item**, com o saldo ainda
utilizável, e só depois é baixada.

O gatilho é o fechamento do chamado porque, na prática, o técnico só fecha quando
o cliente já validou a entrega. Amarrar a carência à vigência do contrato seria
errado: a bolsa é do work item, e um projeto vendido à parte termina sem relação
com o ciclo do suporte.

**Duas consequências que precisam de decisão de implementação:**

1. **Reabrir o chamado cancela a contagem.** Um work item pode ser fechado e
   reaberto — inclusive pelo próprio cliente, a partir da Fase 8. Enquanto estiver
   aberto, não há carência corrente.
2. **A baixa exige tarefa periódica.** É a primeira coisa deste conjunto de fases
   que não é disparada por requisição: alguém tem de varrer bolsas cujo chamado
   fechou há mais de 30 dias e ainda estão abertas. `django_celery_beat` já está
   em `INSTALLED_APPS`, então há infraestrutura.

**Isto não é escopo da Fase 6.** A Fase 6 é a fase do R$; adicionar uma tarefa
periódica de expiração de bolsa ali é invasão de escopo. Fica registrado como
dívida nomeada, a ser feita em fase própria ou junto da Fase 9.

### Revisão da D35, feita na Fase 9 — a varredura automática deixa de ser desejada

A consequência 2 acima **foi recusada**, e não por escopo. A Fase 9 ia implementá-la
e o desenho foi vetado por um motivo que nem a D35 nem a proposta da Fase 9 tinham
levantado:

> **Fechar uma bolsa com saldo POSITIVO destrói horas que o cliente pagou.** Se ele
> comprou 40h e usou 30h, a varredura baixa 10h de crédito dele num timer, sem
> ninguém decidir. No dia em que ele disser "eu ainda tenho 10h lá", a resposta é
> que o sistema apagou automaticamente.

E há um argumento de propósito que fecha a questão: **a carência de 30 dias existe
para permitir negociação.** Fechar automaticamente no dia 31 remove exatamente a
decisão que a carência foi criada para possibilitar.

O que muda, então:

|                           | Desenho original                          | O que a Fase 9 fez                                                |
| ------------------------- | ----------------------------------------- | ----------------------------------------------------------------- |
| Gatilho                   | tarefa periódica varre e fecha            | alerta `ALLOWANCE_PENDING_CLOSURE` no painel da §3b               |
| Quem baixa o saldo        | o timer                                   | um humano, pelo endpoint de encerramento que a Fase 5 já entregou |
| Bolsa vencida na §1       | continuaria em "bolsas ativas"            | sai das ativas e entra em `pending_closure`                       |
| Escritores do livro-caixa | ganharia o primeiro escritor não atendido | nenhum. A Fase 9 é read-only                                      |

**A cláusula continua valendo**: o saldo _é_ perdido após 30 dias. O que muda é que
o sistema **informa e um humano confirma**, em vez de o timer executar.

Duas coisas que caíram de graça e uma limitação nomeada:

- **"Reabrir cancela a contagem" não precisou de código.**
  `Issue._sync_completed_at` zera `completed_at` em qualquer saída do grupo
  `completed`, então um chamado reaberto para de casar com o alerta sozinho. Fixado
  por `test_reopening_the_work_item_cancels_the_countdown`, que passa por uma
  troca de estado de verdade e não por `update()`.
- **O painel não apodrece.** Sem isto nenhuma bolsa jamais sai de `OPEN`, e a §1 e
  a §3b iriam acumulando bolsas de chamados fechados há meses até deixarem de ser
  lidas — que é a falha que a §3b existe para evitar. A Fase 9 é o primeiro
  consumidor que torna a ausência visível.
- **Limitação nomeada: chamado CANCELADO não fica pendente.** O Plane só marca
  `completed_at` para o grupo `completed`, então a bolsa de um chamado cancelado
  nunca vence por esta regra. Tratar cancelamento como fechamento para fins de
  faturamento é decisão comercial que a D35 não toma, e inventar uma segunda
  definição de "fechado" dentro da camada de relatórios é a divergência que a D48
  existe para evitar. Fixado por teste de caracterização, para que mudar isso seja
  deliberado.

A varredura automática **não é mais dívida pendente**. Ela deixou de ser desejada.

## D36 — Excedente de bolsa é uma origem de receita própria

O consolidado mensal da §5 da Fase 6 pedia três origens separadas. Passam a ser
**quatro**:

1. apontamentos avulsos
2. apontamentos fora de escopo de clientes com contrato
3. **excedente de contrato** faturado
4. **excedente de bolsa** faturado

O excedente de bolsa não se mistura com o de contrato porque respondem a
perguntas comerciais diferentes: excedente de contrato é suporte consumido acima
do contratado; excedente de bolsa é projeto entregue acima do orçado. Fundi-los
esconderia justamente o indicador de qualidade da sua estimativa de projeto.

Note que `ServiceIssueAllowance.overage_hours` já guarda essas horas na mesma
forma que o período, e que elas **já são equivalentes** — o multiplicador já foi
aplicado. Vale aqui a mesma advertência do critério 8: aplicá-lo de novo cobra em
dobro.

## Sobre a herança de bolsa: nada novo

As duas perguntas sobre bolsa em sub-tarefa já estavam respondidas pela **D28**,
implementada na Fase 5:

- sub-tarefa **sem** bolsa própria consome a bolsa do ancestral mais próximo, e
  não o pool do contrato — confirmado como cláusula;
- sub-tarefa **com** bolsa própria consome a dela. Não é decisão nova: o ancestral
  mais próximo é ela mesma, então o filho vence por construção da regra.

## Resumo do que muda no código

| Decisão | Muda código?            | Onde                                                                                                                  |
| ------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------- |
| D31     | **Sim**                 | resolução de competência: não materializar período fora da vigência; data anterior debita o primeiro período          |
| D32     | Não                     | confirma o default                                                                                                    |
| D33     | **Sim**                 | fallback para avulso nos três casos, com motivo persistido e distinção entre rota escolhida e aplicada — Fase 6       |
| D34     | Não, é cadastro         | `accrual_cap_mode = NONE`, `carryover_months = NULL`. Requisito: garantir que o alerta de saldo acumulado alto exista |
| D35     | **Sim, fora da Fase 6** | carência de 30 dias e tarefa periódica de baixa — dívida nomeada                                                      |
| D36     | **Sim**                 | quarta origem no consolidado — Fase 6                                                                                 |

---

# Decisões da Fase 6 — precificação em R$

Tomadas na sessão de implementação da Fase 6, todas confirmadas antes de escrever
código, conforme a §7.1 do contexto mestre. O desenho completo que as originou está
em `06-design-proposto.md`.

## D37 — A vigência de preços é resolvida por `worked_on`, não pela data de digitação

A §2 do briefing diz que o apontamento "persiste o valor/hora vigente **no momento do
registro**". A frase é ambígua e a leitura correta é "a vigência daquele registro", não
"a data em que alguém digitou".

Trabalho feito em março é cobrado ao preço de março. Cobrar o preço de maio porque o
técnico digitou em maio é indefensável diante do cliente, e a R7 mais o critério 12 já
fixaram esse princípio para a competência.

**A consequência que justifica a tabela de vigência existir:** o snapshot da R4 sozinho
não resolve isto. Um apontamento de março digitado em maio, depois de um reajuste em
abril, ainda não tem snapshot — é a vigência por `worked_on` que o faz precificar a
março. Selecionar pela data de digitação faria dois apontamentos do mesmo dia de março,
digitados com um mês de diferença, terem preços diferentes: a fatura de março deixaria
de ser reproduzível.

**Caso que o briefing não cobria, e que o desenho teve de resolver:** apontamento cujo
`worked_on` é **anterior à primeira folha de preço** do cliente. "Maior `starts_on` que
não passa da data" devolve vazio. Não bloqueia — é trabalho já executado, e valem a D27
e a D9 integralmente. Grava valor 0 com
`ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE`, visível no consolidado como pendência
de **cadastro**, distinta de `NO_PRICE_SHEET_FOR_CLIENT` (nunca cadastraram) porque são
erros opostos e pedem ações diferentes.

## D38 — O override absoluto é filho da vigência, não do Cliente

A folha de preço é uma linha por vigência (`ServiceClientPrice`), e o valor absoluto de
um Tipo de Hora é uma linha **filha dela** (`ServiceClientHourTypeRate`).

O override pendurado no Cliente sobreviveria intacto a um reajuste e, sendo valor
absoluto, **deixaria de ser reajustado em silêncio**: a base vai de R$ 200 para R$ 220 e
o valor de domingo fica no número do ano passado para sempre. Também não haveria como
_remover_ um override num reajuste, nem tê-lo em 2026 e não em 2027.

Sendo filha da folha, **cada vigência é uma folha de preço completa e autossuficiente**:
base mais suas exceções. Reajustar é copiar para frente e editar. Ler um preço é uma
busca da folha vigente e as exceções dela — sem nenhuma regra de mesclagem entre
vigências para errar, que é onde esse tipo de bug mora.

**Não existe `ends_on`.** Um par `(starts_on, ends_on)` são duas verdades que podem
discordar de duas formas silenciosas: **buraco** (data sem preço) e **sobreposição**
(data com dois). Com a regra do maior `starts_on`, buraco e sobreposição ficam
irrepresentáveis — sobreposição pelo índice único parcial, buraco porque a folha vale
até a seguinte começar. Mesmo raciocínio da D3.

**Ganho colateral: isto responde à advertência da D22 em vez de pagar o preço dela.** A
D22 avisou que rastrear uma coluna **nullable** cobra da trilha de auditoria, e nomeou "o
preço sobrescrito opcional da Fase 6" como o caso concreto. O override aqui é **uma
linha, não uma coluna nullable**: cadastrar é `CREATED`, remover é `DELETED` — os dois
verbos que a própria D22 criou — e `absolute_rate` é não nullable. O sentinel
`CONFIG_VALUE_UNSET` continua existindo e continua sendo usado por
`ServiceContract.overage_hour_rate`, que não muda de forma.

## D39 — A bolsa tem valor/hora próprio, com fallback para a base do Cliente

`ServiceIssueAllowance.overage_hour_rate`, nullable e **rastreado**. Nulo cai para o
valor base do Cliente na vigência em força.

O argumento é o mesmo que a Fase 4 usou para criar `ServiceContract.overage_hour_rate`
antes de ter consumidor: **o momento em que 40h são creditadas é o momento em que o valor
daquele projeto é conhecido**, e adicionar coluna auditada depois significa back-fill de
trilha de auditoria, que não se faz honestamente.

Uma bolsa é venda de projeto negociada à parte. Um projeto vendido a R$ 180/h não tem
excedente a R$ 200/h porque R$ 200/h é o que o suporte daquele cliente custa. Usar a base
do Cliente como **única** resposta precificaria excedente de projeto com preço de
suporte, o que é errado com a mesma frequência com que os dois preços diferem — ou seja,
quase sempre.

**Consequência no modelo:** `ServiceIssueAllowance` ganhou `ChangeTrackerMixin`, que a
Fase 5 deliberadamente não tinha. As exclusões são o ponto: `TRACKED_FIELDS` tem
**apenas** `overage_hour_rate`. O raciocínio da Fase 5 continua válido para todo o resto
— creditar horas é movimento contábil e a auditoria dele é o livro-caixa com seu próprio
`actor`; os acumuladores auditados aqui duplicariam o ledger, o que a §6 do contexto
mestre proíbe. O que mudou é que a Fase 6 acrescentou **configuração que decide
dinheiro**, e a D22 exige exatamente isso auditado.

Como é nullable e rastreado, é o único campo deste modelo que depende do sentinel
`CONFIG_VALUE_UNSET` (D4). Fixado por
`AP::test_the_rate_change_is_audited`, que afirma `old_value == "UNSET"`.

## D40 — O excedente de contrato é precificado pela vigência do **início** da competência

`period.starts_on`, não `period.ends_on` nem a data do fechamento.

Fechar em abril não pode pôr preço de abril na fatura de março. E `starts_on` em vez de
`ends_on` porque uma folha que entrasse em vigor no dia 28 passaria a valer para o mês
inteiro retroativamente, na direção desfavorável ao cliente.

**A pergunta ficou irrelevante por construção:** `starts_on` de folha de preço é
**restrito ao dia 1º em DDL** (`service_client_price_starts_on_first_of_month`). Assim
não existe folha entrando em vigor no meio de uma competência, e as duas leituras — a do
apontamento, por `worked_on`, e a do excedente, por `period.starts_on` — coincidem. É o
mesmo movimento da D31, que aboliu pro-rata em todo o sistema: em vez de acertar a regra,
tornar o caso inexistente.

Excedente de **bolsa** usa a data de encerramento, porque uma bolsa não tem competência —
ela é uma venda que termina quando alguém a encerra.

## D41 — Faturar excedente sem valor/hora resolvível **bloqueia**

`close_period(settlement=BILLED)` e `close_allowance` recusam com
`OVERAGE_RATE_NOT_CONFIGURED` quando nem o termo específico nem a base do Cliente
resolvem.

**Isto não contradiz a D27, a D9 e a D21, e a distinção é o que o torna seguro.** Aquelas
protegem **trabalho já executado por um técnico**: recusar destruiria o registro de
trabalho real por causa de configuração que o técnico não controla. Faturar excedente não
é isso. É **ato administrativo deliberado, num momento de escolha, com alternativa
legítima já disponível** — o período pode fechar como `CARRIED`.

Faturar a R$ 0,00 **zeraria um déficit real em silêncio**, destruindo a dívida sem
rastro, que é o pior dos três resultados. Recusar devolve a decisão a quem pode tomá-la.

O controle positivo está na suíte: `B::test_carrying_the_deficit_needs_no_rate` prova que
a alternativa realmente existe sem taxa nenhuma.

## D42 — Não existe estorno de excedente faturado. Dívida nomeada

O critério 6 da §6 do briefing pede que "estornar o faturamento de excedente restaure o
déficit e o transporte de forma consistente". **Isso não foi implementado, e a ausência é
anterior a esta fase**: nada no repositório escreve linha reversora de `OVERAGE_BILLED`, e
não existe `reopen_period`. A Fase 4 fechou seus critérios sem esse caminho.

Reabrir período é mecanismo que a Fase 4 deliberadamente não construiu, e o cascateamento
do saldo já transportado para a competência seguinte é problema dela, não da fase do R$.
Construí-lo aqui seria a mesma invasão de escopo que a D35 recusou.

### Antes de construir: reabrir período pode ser a solução errada

**Revisado na Fase 7, e esta parte é pergunta, não tarefa.** A redação original tratava a
reabertura de período como trabalho a fazer. Ela pode ser trabalho a **não** fazer.

Em contabilidade não se edita período fechado. Corrige-se **lançando um ajuste no período
aberto**. Isso é prática padrão exatamente por causa do problema que o item 3 abaixo
descreve: reabrir um mês já fechado obriga a recascatear o saldo transportado para as
competências seguintes — e o transporte já aconteceu, então a reabertura **não é uma
operação local, é uma cascata**, recursiva se a competência seguinte também fechou.

Ou seja: o comportamento atual pode não ser limitação, e sim o modelo certo. O que falta,
nessa leitura, não é reabrir período — é um caminho explícito de **lançamento de ajuste na
competência aberta**, que não desfaz nada, não cascateia, e é auditável por construção
porque é uma linha nova num livro-caixa append-only (D23), não a edição de uma linha antiga.

**A pergunta a responder antes de escrever código, então, é: reabrir período ou lançar
ajuste no período aberto?** Quem pegar isto economiza a construção de uma cascata que
talvez não devesse existir.

Enquanto a pergunta está aberta, a Fase 7 fez a única coisa que não a prejulga: **o erro
passou a dizer o que fazer, não só o que não pode.** Um ADMIN que edite horas de um
apontamento em período fechado recebe `PERIOD_IS_CLOSED` com
`remediation: RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE`. Antes recebia uma recusa sem
caminho, e o palpite óbvio de quem a recebesse — "pedir para alguém reabrir o mês" — é
precisamente o que esta seção agora questiona.

**O que um estorno exigiria, registrado para quem pegar isto não redescobrir** — e note que
os itens 2 e 3 são exatamente o custo que a rota do ajuste evita:

1. um `entry_type` novo e reversor, que precisa entrar em uma das três listas de sinal
   (`NEGATIVE_ONLY_`, `POSITIVE_ONLY_`, `SIGNED_LEDGER_ENTRY_TYPES`) ou a constraint
   `service_ledger_hours_sign_matches_entry_type` rejeita a linha;
2. **reabertura do período** — hoje um período fechado recusa débito, estorno, fechamento
   e edição de cota, e todos os quatro precisariam de um caminho de volta;
3. **cascateamento do transporte**: se o déficit foi faturado, a competência seguinte
   abriu com a cota inteira. Estornar recria o déficit, que agora tem de ser transportado
   — e se a competência seguinte já fechou, o cascateamento é recursivo;
4. remoção do índice único parcial `service_ledger_unique_overage_billed_per_period`, ou
   um estorno seguido de novo faturamento seria recusado pelo banco.

**Duas exigências que compensam a ausência, e ambas estão implementadas:**

- **A confirmação mostra o valor calculado e o valor/hora aplicado antes de efetivar, e
  diz que a ação não pode ser desfeita.** `overage_billing_preview` e
  `allowance_overage_preview` devolvem `is_reversible: false` como constante — a API
  afirma o fato em vez de deixar o cliente saber por conta própria. Endpoints
  `GET .../overage-preview/` e `GET .../service-issue-allowances/<pk>/close/`, mais
  `OverageBillingConfirmation` no frontend. Erro irreversível tem de ser deliberado, não
  descuidado.
- **O erro mais provável — faturar duas vezes — passou a ser recusado pelo banco**, não
  por um `if`. Ver o critério 9 no resumo abaixo.

## D43 — Project sem Cliente é trabalho interno, e sai da receita

`ServicePricingFailure.INTERNAL_PROJECT_NO_CLIENT`, e o consolidado o **exclui da
receita** em vez de listá-lo como R$ 0,00 pendente.

A §1 da Fase 1 já dizia que project sem Cliente é trabalho interno, "não apontável para
faturamento". Não é falha de precificação — é trabalho próprio, que não deve ser cobrado
de ninguém. Um código chamado `NO_SERVICE_CLIENT_FOR_PROJECT` soaria como "não consegui
descobrir quem paga", que é outra coisa.

**O motivo prático é de confiança no painel.** Uma empresa que faz trabalho interno de
verdade teria o painel financeiro cheio de "falhas" que não são falhas. Aí o operador
aprende a ignorar a lista, e no dia em que aparecer uma pendência real de cadastro ela
passa batida. Ruído em painel financeiro custa a credibilidade do painel.

Os códigos de motivo separam **três coisas** que colapsariam numa:

| Classe                    | Códigos                                                                   | Alguém precisa agir?         |
| ------------------------- | ------------------------------------------------------------------------- | ---------------------------- |
| Pendência **comercial**   | `route_deviation_reason` — contrato ausente, suspenso, encerrado, vencido | Sim, com prazo. É receita    |
| Pendência de **cadastro** | `NO_PRICE_SHEET_FOR_CLIENT`, `NO_PRICE_SHEET_IN_FORCE`                    | Sim. **Não** é receita ainda |
| Trabalho **interno**      | `INTERNAL_PROJECT_NO_CLIENT`                                              | **Não.** Fora da receita     |

Expostas em `PRICING_PENDENCY_FAILURES` e `INTERNAL_WORK_FAILURES`, no nível do módulo,
para que as constraints e o consolidado não possam divergir sobre quais são quais.

## D44 — Rota escolhida e rota aplicada são duas colunas

A D33 exige que "o que foi escolhido e o que foi aplicado" sejam recuperáveis, mais o
motivo do desvio. `applied_billing_route` **não foi redefinida**: ela continua sendo a
escolha, e `settled_billing_route` é nova.

A tentação é redefinir a coluna existente para significar "o resultado". Isso apagaria a
escolha, que é exatamente a informação que permite ao consolidado distinguir "avulso
porque o cliente é avulso" de "avulso porque o contrato venceu" — a distinção que a
própria D33 exige que apareça.

Três fatos, três colunas, e um biconditional em DDL
(`service_log_route_deviation_is_coherent`) tornando irrepresentáveis "desviou sem
motivo", "motivo sem desvio" e desvio a partir de qualquer outra rota. A alternativa
recusada era derivar a rota aplicada do motivo: funciona e economiza uma coluna, mas põe
um `CASE` no `GROUP BY` de cada relatório.

## O que a Fase 6 ganhou de estrutural, além do briefing

Dois invariantes monetários que eram `if` e viraram DDL. Ambos seguem o método da D29:
uma invariante de dinheiro garantida por uma checagem é uma invariante que uma requisição
concorrente atravessa.

**1. Um apontamento debita um pool OU é faturado em R$. Nunca os dois.**
`service_log_billed_debits_no_pool` e `service_log_pool_route_carries_no_amount`. É o
análogo monetário do que a D29 conseguiu para horas: cobrar em reais _e_ consumir as horas
contratadas pelo mesmo apontamento passa a ser recusado pelo banco. Espelha
`service_log_debits_at_most_one_origin`, que já existia.

**2. O critério 9 virou recusa do banco.** "Faturar excedente duas vezes no mesmo período
é rejeitado" era garantido por `close_period` levantando `PERIOD_ALREADY_CLOSED` — um
`if`. O índice único existente **não cobria** essas linhas: ele é condicionado a
`service_log__isnull=False`, e uma linha `OVERAGE_BILLED` não tem apontamento. Duas
linhas de excedente para o mesmo período eram **representáveis**. Corrigido por
`service_ledger_unique_overage_billed_per_period` e o gêmeo da bolsa. A sabotagem agora
falha com `UniqueViolation`.

**3. Vazamento fechado que a R11 pega e a gate de resposta sozinha não pegaria.** O
payload do Admin alimentava `IssueActivity` via `_record_activity`, e o feed de atividade
de um work item é legível por qualquer Member do project. Reusar o payload já serializado
— a coisa óbvia a fazer — poria o valor em R$ na frente exatamente da audiência de quem a
R11 o esconde, a um salto do endpoint que cuidadosamente o omitiu. Por isso existe
`_activity_payload`, que serializa **sem** contexto. Fixado por
`AP::test_the_activity_trail_carries_no_money`.

## Resumo do que muda no código

| Decisão | Muda código? | Onde                                                                                                 |
| ------- | ------------ | ---------------------------------------------------------------------------------------------------- |
| D37     | **Sim**      | `resolve_price_sheet` por `worked_on`; `NO_PRICE_SHEET_IN_FORCE` para data anterior à primeira folha |
| D38     | **Sim**      | `ServiceClientHourTypeRate.price` FK para a folha, não para o Cliente; sem `ends_on`                 |
| D39     | **Sim**      | `ServiceIssueAllowance.overage_hour_rate` + `ChangeTrackerMixin` só para ele                         |
| D40     | **Sim**      | `_bill_period_overage` resolve por `period.starts_on`; folha restrita ao dia 1º em DDL               |
| D41     | **Sim**      | `resolve_overage_rate` levanta `OVERAGE_RATE_NOT_CONFIGURED`                                         |
| D42     | Não          | dívida nomeada. Compensada por preview com `is_reversible: false` e pelo único parcial               |
| D43     | **Sim**      | `INTERNAL_PROJECT_NO_CLIENT` fora da receita; três classes de motivo                                 |
| D44     | **Sim**      | `settled_billing_route` nova; `applied_billing_route` mantém o significado                           |

---

# Decisões da Fase 7 — permissões, delegação e auditoria

Tomadas na sessão de implementação da Fase 7 e confirmadas antes de escrever código,
conforme a §7.1 do contexto mestre. A fase é a primeira desde a 1 em que a pergunta
central não é "quanto vale" e sim "quem pode" — e as duas primeiras decisões são as que
teriam custado caro se tomadas pelo caminho mais curto.

## D45 — As três capacidades elevadas são um modelo próprio, e a de reatribuição é escopada

O briefing pedia três capacidades concedíveis a usuários específicos "sem precisar
torná-los Admin", e ofereceu duas rotas: um modelo pequeno de concessão, ou flags em
`WorkspaceMember`. A proposta inicial foi **as flags** — menos código, uma query a menos, e
nenhuma tabela nova.

**Recusada, por duas razões que só aparecem depois.**

**(a) `WorkspaceMember` é modelo do core do Plane.** `plane/app/views/workspace/member.py` é
justamente o tipo de arquivo que o upstream mexe com frequência, e três colunas ali seriam
superfície de conflito permanente num caminho de gerenciamento de membros. A §6 do contexto
mestre pede preferir extensão a modificação do core exatamente por isso, e sete fases foram
entregues sem tocar em modelo do core — a única exceção prevista é a Fase 8, que vai doer e
é inevitável.

**(b) As flags perderiam auditoria de graça.** Conceder `can_manage_others` é dar a alguém o
poder de alterar registro financeiro alheio, e "quem deu essa permissão, e quando?" é
pergunta de primeira ordem no momento em que um apontamento aparece editado errado. Um
modelo `Service*` herda `ChangeTrackerMixin` e grava em `ServiceConfigActivity`, que a §6
define como a trilha de configuração que afeta dinheiro. `WorkspaceMember` não é
`ServiceConfigEntity`, e torná-lo um significa mexer no core de novo.

`ServiceMemberPermission`, então: uma linha por membro por workspace, as três flags em
`TRACKED_FIELDS`, endpoint próprio restrito a ADMIN de workspace. **A intenção de UI ficou
intacta: não existe tela nova de permissões** — os controles ficam na tela de membros que já
existe, com uma chamada separada. O operador vê um lugar; o backend não invade o core.

### As três são independentes, e `can_reassign_author` é escopada ao que o membro já pode editar

Independentes porque são eixos diferentes: gerenciar apontamento alheio move dinheiro
(horas, tipo, data), reatribuir autor não move nada, e delegar cria registro para autor
declarado. Composição livre é o correto.

**Mas `can_reassign_author` sozinha precisava de escopo, senão concederia em silêncio uma
fatia de `can_manage_others`.** Reatribuir o apontamento de outra pessoa **é** editar o
registro de outra pessoa, mesmo que o campo alterado não mexa em saldo. A regra:

| Concessões                                  | Alcance                                       |
| ------------------------------------------- | --------------------------------------------- |
| autor + `can_reassign_author`               | reatribui os próprios                         |
| `can_manage_others` + `can_reassign_author` | reatribui os de qualquer um                   |
| `can_manage_others` sozinha                 | edita os de qualquer um, **sem** trocar autor |
| `can_reassign_author` sozinha               | reatribui **só os próprios**                  |

A última linha é o caso de uso mais comum e o motivo de a capacidade existir isolada:
"lancei isto, mas o trabalho foi do João". Nenhuma flag empresta escopo da outra sem que
alguém tenha concedido.

ADMIN mantém as três implicitamente, por resolução `is_admin OR flag` — nunca por linha
concedida. Um Admin cujas capacidades dependessem de uma linha as perderia num back-fill
ruim, que é o modo de falha de codificar uma implicação como dado.

### GUEST não pode sustentar nenhuma delas, em três camadas

Um GUEST com `can_delegate` é escalação de privilégio — usuário de cliente criando
apontamento em nome de um técnico — e com `can_manage_others` edita registro financeiro.

Não é expressável em check constraint: as capacidades estão em `service_member_permissions`
e o papel está em `workspace_members`, e uma check constraint do PostgreSQL não lê outra
tabela. **Por isso a camada externa é um portão de leitura, e não DDL** — o que difere de
todo o resto desta série, onde invariante de dinheiro virou constraint:

1. `resolve_capabilities` lê o **papel vivo** a cada chamada e devolve nada para GUEST ou
   não-membro. **É esta que sustenta a segurança**, porque vale contra linha obsoleta,
   editada à mão, ou escrita por um caminho que ninguém previu;
2. a concessão recusa GUEST (`GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS`);
3. o rebaixamento a GUEST **revoga** a linha, para o dado armazenado não afirmar algo falso.

Verificado por sabotagem escrevendo a linha direto pelo ORM: com a camada 1 desligada, o
GUEST resolve as três capacidades. As camadas 2 e 3 não pegaram essa sabotagem, e é assim
mesmo — elas mantêm o dado honesto, não fecham a escalação.

Nota de cobertura, registrada porque é contraintuitiva: no nível HTTP o GUEST está barrado
**independentemente** por `allow_permission` em toda rota de apontamento, então a camada 1 é
provada por teste unitário e não por teste de contrato. As duas proteções são redundantes de
propósito; a redundância é o ponto, não desperdício.

## D46 — Período fechado continua ADMIN-only, e reatribuição não é exceção

A proposta inicial foi abrir a reatribuição de autor em período fechado para quem tem a
concessão, com o argumento de que trocar autor não move saldo. **Recusada, e o motivo não é
o débito.**

O que o fechamento de período protege não é "saldo parado" — é a **reprodutibilidade de um
documento já enviado**. O consolidado da §5 da Fase 6 lista, por linha: data, chamado,
**técnico**, tempo, tipo de hora, horas equivalentes, valor. E a R11 diz que o cliente
**vê** o autor do apontamento. Ou seja: o nome do técnico está no anexo que o cliente
recebeu. Reatribuir depois do faturamento mantém o total e **quebra o detalhe** — um cliente
que reconcilie linha por linha encontra divergência com o documento que está na mão dele.

Segundo motivo, de manutenção: a trava só funciona enquanto for uma regra só. "Fechado
significa apenas Admin, sempre" é auditável e ensinável. "Fechado significa apenas Admin,
exceto troca de autor" convida a próxima exceção, e depois a seguinte. **Travas financeiras
erodem por exceção, nunca de uma vez.**

O custo de manter é baixo, e é isso que a torna sustentável: as concessões existem para o
trabalho normal não precisar de Admin, e período fechado é anormal por definição.

**O limite honesto, que é dívida da D42 e não buraco novo.** Passar nesta checagem deixa o
ADMIN _tentar_ a escrita; não faz a camada de pool aceitá-la. `apply_debit` e `reverse_debit`
levantam `PERIOD_IS_CLOSED` para todo mundo, ADMIN incluído. Na prática a edição de um Admin
em período fechado funciona exatamente quando não move horas: reatribuição de autor, linha
não faturável, ou apontamento que nunca debitou período. Edição que moveria horas falha uma
camada abaixo, com código explícito e agora com remediação. Fixado por teste de
caracterização, para que a fase que responder a pergunta da D42 mude isto de propósito em
vez de descobrir a limitação por acidente.

## D47 — 403 é sobre o ator; 400 é sobre o payload, inclusive alvo inelegível

A primeira implementação devolvia 403 para `SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN`, com o
argumento de que toda recusa numa rota de apontamento deveria ser 403 — uma regra só, fácil
de enunciar. **Recusada por juntar duas coisas que o consumidor da API precisa distinguir:**

| Código | Significa                                                | O que a UI deve fazer          |
| ------ | -------------------------------------------------------- | ------------------------------ |
| 403    | o **ator** não tem a capacidade                          | esconder ou desabilitar a ação |
| 400    | o **payload** nomeia alvo inelegível, ou está malformado | mostrar erro **no campo**      |

No caso concreto: o chamador **tem** `can_delegate` e escolheu um autor que não é técnico.
Ele está autorizado a delegar — o que está errado é o valor do campo `author_id`. Devolver
403 faz a interface dizer "você não tem permissão" a quem tem, e o operador vai pedir
permissão que já possui.

Não há vazamento em devolver 400: quem tem `can_delegate` é MEMBER do workspace e já
enxerga a lista de membros e seus papéis.

A regra final continua sendo uma linha: **403 quando o ator não tem a capacidade, 400 quando
o payload está errado.** Trocado durante a implementação e não depois, porque status code é
contrato de API — mudar com cliente em produção quebra consumidor.

## Resumo do que muda no código

| Decisão | Muda código?  | Onde                                                                                                                                          |
| ------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| D45     | **Sim**       | `ServiceMemberPermission` novo; `resolve_capabilities` lê papel vivo; `may_reassign` compõe com `may_edit`; revogação no rebaixamento a GUEST |
| D46     | **Sim**       | `validate_closed_period_access` sem exceção para reatribuição; remediação no `PERIOD_IS_CLOSED`                                               |
| D47     | **Sim**       | `PERMISSION_DENIED_CODES` não inclui `SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN`                                                                |
| D42     | **Reescrita** | de tarefa ("construir reabertura") para pergunta ("reabrir ou lançar ajuste?")                                                                |

---

# Decisões da Fase 9 — dashboards de consumo e relatórios de faturamento

Dez decisões, **D48 a D57**, e duas delas corrigem fases anteriores em vez de
acrescentar coisa nova: a **D54** fecha a dívida que a Fase 5 nomeou, e a **D57**
conserta uma classificação da Fase 6 que já estava em contradição com o que estava no ar.

> **Nota de numeração.** A proposta desta fase começava em D47, porque a coordenação
> dizia que a Fase 7 estava usando D45 e D46. A Fase 7 acabou precisando de uma terceira
> e tomou a D47, então este bloco foi deslocado no rebase. Os commits desta branch
> anteriores ao rebase citam a numeração antiga — o código e os documentos estão na
> nova, que é a que vale.

## D48 — Consumo de contrato agrega por `debited_period`, nunca por `TruncMonth(worked_on)`

Existem **duas** noções de "que mês é este" neste domínio, e escolher a errada quebra
o critério 7 em silêncio:

| Grandeza              | Competência vem de             | Por quê                                              |
| --------------------- | ------------------------------ | ---------------------------------------------------- |
| Consumo de contrato   | `debited_period`               | o período **é** a competência, já com o clamp da D31 |
| Receita avulsa        | `worked_on`                    | R7; não existe período                               |
| Excedente de contrato | competência do **período**     | Fase 6                                               |
| Excedente de bolsa    | mês do **fechamento** da bolsa | Fase 6                                               |

A D31 faz um apontamento anterior ao início da vigência debitar o **primeiro**
período. Logo o mês de `worked_on` dele **não tem período nenhum**. Agrupar consumo
de contrato pela data de atendimento jogaria essas horas num mês que o contrato nunca
teve — exatamente o caso que a D31 criou.

Por isso `competence_basis` é **campo do descritor** e não argumento solto de cada
query: um bucket não pode ser construído sem que alguém tenha escolhido, e a escolha
viaja com o bucket até o drill-down, onde tem de bater ou os números divergem. O
endpoint de consumo troca o basis **sozinho** quando devolve o shape de contrato, para
que um frontend não possa errar mandando o parâmetro errado.

Fixado por `test_the_period_basis_finds_nothing_in_february` e
`test_contract_consumption_puts_the_retroactive_hours_in_march`. Sabotagem verificada:
agrupar sempre por `worked_on` quebra **5** testes.

## D49 — A regra de origem tem um enunciado e um caminho rápido, reconciliados por diferencial exaustivo

A Fase 9 precisa da classificação de origem em SQL, para agregar séries no banco em
vez de iterar apontamentos. Isso significa **duas implementações de uma regra que
decide dinheiro** — normalmente indefensável, e é o "quatro relatórios, quatro regras"
que o `revenue_origin_of` foi escrito para evitar.

É defensável aqui por um motivo que precisou ser **verificado, não presumido**: o
espaço de entrada é finito e pequeno.

- `settled_billing_route` — 3 valores
- `route_deviation_reason` — `None` + 4
- `pricing_failure_reason` — `None` + 3
- projeto tem cliente — 2
- esse cliente tem contrato cobrindo a competência — 2

As check constraints do `ServiceLog` então cortam o produto: desvio e falha de preço
só existem com `settled=bill_amount`, e `(sem cliente, com contrato)` é impossível.
Sobram **66 combinações representáveis** — não 80, que era a estimativa da proposta.
O teste enumera **todas as 66**, constrói cada linha, e afirma que as duas
implementações concordam. **E afirma a contagem**, para que um valor novo em
`ServiceRouteDeviation` fique vermelho por aritmética, não por sorte.

O teste também afirma que a sua própria leitura das constraints (`is_representable`)
casa com o que o banco aceita de fato — então uma constraint que alguém afrouxar
aparece como teste vermelho em vez de alargar em silêncio o espaço que o diferencial
cobre.

Sabotagem verificada: inverter a precedência da D33 no `Case/When` — checar o contrato
antes do desvio — quebra 3 testes, incluindo o diferencial. É uma inversão que um
conjunto de exemplos escritos à mão dificilmente cobriria.

Consequência de refatoração: `report_bucket_from` passou a aceitar **escalares** em vez
de um `ServiceLog`, porque a série classifica linhas **agrupadas** e não instâncias. E
`_origin_from` é agora a única declaração da metade standalone-vs-out-of-scope, usada
por `revenue_origin_of` **e** por `report_bucket_from`.

## D50 — Um descritor de filtro alimenta o agregado, o drill-down e a exportação

A §4 pede filtros globais, a §8 pede que todo total seja clicável até a lista, e o
critério 1 pede que o gráfico bata com a soma. Escritos como três features, são três
chances de divergir. Escritos como **um objeto**, são zero — porque o número e a lista
são literalmente a mesma query.

`ServiceLogFilterSet` é `frozen`, e isso não é estilo: um descritor é o **registro do
que já foi somado**, e um que pudesse ser editado entre produzir o número e produzir a
lista é a divergência que ele existe para impedir.

Três consequências que valem registro:

- **`workspace_id` é argumento, nunca campo.** Um descritor viaja até o browser e
  volta; um workspace que chegasse por query param seria fronteira de tenancy decidida
  pelo chamador.
- **`narrow(debited_period_ids=...)` limpa a janela de competência** e troca o basis.
  Sem isso, o drill-down de um bucket de período excluiria os apontamentos retroativos
  que o agregado contou, e a lista viria com menos horas que o número acima dela.
  Fixado pelo par `test_narrowing_to_a_period...` +
  `test_the_unnarrowed_window_would_have_missed_it`.
- **O tri-state de "não faturável" da §4 é expresso via `settled_routes`**, não como
  flag própria. Uma flag separada poderia contradizer o campo de rotas sem forma de
  resolver quem ganha.

O teste que essa estrutura habilita é uma **property test**, não um punhado de
asserções: percorrer todo bucket de todo payload, replicar o descritor e afirmar que a
soma direta é igual ao bucket. Um bucket novo entra coberto por construção.

**Ela achou dois bugs que a revisão não achou** — ver a seção no fim.

## D51 — A barreira da R11 fica na fronteira de agregação, e não computa em vez de remover

A R11(b) diz que a restrição é trabalho do serializer. Num agregado não há serializer,
há um `dict`. Então a barreira desce um nível: um `ReportViewer` que não vê dinheiro
significa que o dinheiro **nunca é computado**, não computado e depois removido.

A Fase 6 já pagou por essa diferença: o payload serializado do Admin alimentava
`IssueActivity` e vazava o valor para qualquer Member do project. Um dict que passou
por uma etapa de remoção é indistinguível de um que nunca teve a chave — até alguém
logar.

**Três projeções, não um booleano**, porque duas das três audiências diferem nas
colunas de **hora** e não no dinheiro:

| Papel  | Vê                                                                        |
| ------ | ------------------------------------------------------------------------- |
| ADMIN  | tudo, dinheiro incluído                                                   |
| MEMBER | as três quantidades de hora, sem dinheiro; **com** estado comercial (D57) |
| GUEST  | `equivalent_hours` e `debited_hours` só. Nunca `logged_hours`             |

O GUEST nunca recebe `logged_hours` porque **a diferença entre apontadas e
equivalentes é o multiplicador**, e a R11(c) avisa que mostrar isso com o rótulo errado
transforma uma regra contratual em acusação de hora inflada.

A defesa acabou tendo **duas camadas** — a agregação não computa, e `bucket()` também
recusa — e a verificação por sabotagem mostrou que cada camada tem teste próprio:
remover só a segunda quebra 1 teste, remover as duas quebra 3.

## D52 — Sem visão materializada e sem tabela de agregados, com gatilho nomeado

Na ordem de grandeza deste produto — ~5.000 linhas de apontamento por mês, ~300.000 em
cinco anos — uma janela de 36 competências é um range scan de ~10⁵ linhas em índices
que **já existem** (`svc_log_ws_settled_worked_idx`, `service_log_ws_worked_idx`,
`svc_ledger_period_type_idx`). Dezenas de milissegundos.

Um agregado materializado compraria isso e cobraria três coisas:

1. **Um segundo lugar onde o dinheiro existe.** A D23 diz que o livro-caixa é o único
   lugar onde saldo se move; um agregado de receita persistido é a mesma classe de erro
   no eixo do R$.
2. **Um caminho de invalidação com quatro portas.** `ServiceLog.delete()` existe porque
   um apontamento é excluído por quatro caminhos. A porta esquecida é uma fatura errada
   silenciosa.
3. **Defasagem no único relatório que é lido _antes_ de emitir a nota.**

**Gatilho para revisitar, nomeado para não virar "otimizamos quando doer":** 500.000
linhas numa única competência, ou p95 acima de 500 ms com `EXPLAIN ANALYZE` mostrando
scan sequencial.

**Zero índices novos.** E a afirmação de performance é medida, não declarada:
`TestThePerformanceClaimIsMeasuredNotAsserted` usa `django_assert_num_queries` e fixa
que uma série custa **1 query** e o consolidado de receita **4**, independente de
quantos meses forem pedidos.

## D53 — Médias são figura de apresentação, quantizadas uma vez, e nunca realimentam total

Um total de dinheiro dividido por uma contagem não é representável em duas decimais.
`average_amount_per_issue` é quantizado **uma vez**, com `ROUND_HALF_UP`, e nenhum
total é derivado dele. O numerador é `Sum` da coluna `amount` persistida — nunca horas
× taxa.

Uma seleção vazia devolve **ausência de média**, não `0,00`: dividir por nenhum chamado
não tem resposta, e zero seria uma.

A média em **horas** fica disponível a quem vê horas, porque é carga de trabalho e não
preço — um técnico planejando capacidade precisa dela.

## D54 — Dispensa de alerta por par nullable + XOR, seguindo a D29 literalmente

A Fase 5 entregou alertas de bolsa que não podiam ser dispensados e **disse isso no
docstring** em vez de esconder num comentário: `ServiceContractAlertDismissal.period`
era FK obrigatória.

A correção segue a **D29 literalmente** — par nullable com a exclusividade em DDL —
porque `ServiceHourLedgerEntry` já resolveu o problema idêntico "período ou bolsa,
nunca os dois" desse jeito, no mesmo módulo. Uma tabela irmã agora seriam duas soluções
para um problema.

O que tornou o par barato: `balance_at_dismissal` **já era genérico**.
`ServiceContractPeriod` e `ServiceIssueAllowance` **ambos** expõem `balance_hours`,
então o re-arme da decisão B2 funciona para qualquer alvo por **um** caminho de código,
sem `if` de tipo. `_dismissal_target_field` é o único lugar que olha o tipo, e levanta
`TypeError` para um alvo não suportado em vez de virar no-op silencioso.

Renomeado para `ServiceAlertDismissal`, **preservando `db_table`**: a tabela passa a
guardar linhas sem contrato nenhum, e um nome dizendo "Contract" mandaria o próximo
leitor procurar uma FK que não está lá.

**Dois uniques parciais, não um**, e as metades `__isnull=False` importam: no Postgres
NULLs são distintos dentro de um índice único, então um único unique em
`(period, alert_code)` deixaria de proteger qualquer coisa no instante em que `period`
ficasse nullable — toda dispensa de bolsa tem period NULL, logo todas seriam
mutuamente distintas.

A migração **0132 foi escrita à mão**, e isso é o ponto: o autodetector não infere
rename não-interativamente e propôs `CreateModel` + `DeleteModel`, que **dropa a tabela
e toda dispensa nela**. `RenameModel` aqui é operação só de estado, provado
estruturalmente — o `sqlmigrate` não contém `CREATE TABLE` nem `DROP TABLE`.

## D55 — A projeção GUEST é construída e provada no domínio; a rota é critério herdado pela Fase 8

O §3c pede o dashboard do portal, que depende da Fase 8. A Fase 9 **não monta rota
nenhuma** para GUEST.

Mas a projeção existe e é testada: `ReportViewer.guest()`, com os testes de ausência de
chave (`logged_hours`, dinheiro) e o controle positivo. O motivo é a R11(b) — a
allowlist de campos é decisão de domínio, e deixar a Fase 8 redescobrir quais colunas
vazam é como a regra vira decisão tomada num template.

Registrado como **critério herdado** em `08-portal-do-cliente.md`. A Fase 8 só precisa
expor.

## D56 — O drill-down de excedente leva ao extrato, e o bucket não carrega descritor nenhum

Duas das quatro origens da D36 **não são compostas de apontamentos**. Um excedente
faturado é a **liquidação de um déficit**: os apontamentos que geraram o déficit
debitaram o pool e carregam `amount = 0` por check constraint. **Não existe lista de
apontamentos que some o excedente.**

A primeira versão disto devolvia um descritor **mais** uma flag
`is_drillable_to_work_logs: false`. A property test pegou um bucket reportando zero
horas enquanto o descritor dele selecionava todas as linhas do mês. Uma flag é algo que
um frontend pode esquecer de ler; uma chave ausente não é.

Então: um bucket carrega **exatamente um** de `filters` ou `drill_down`, e `bucket()`
levanta `ValueError` para os dois ou nenhum. A D56 passou de convenção a invariante
estrutural.

## D57 — Dinheiro e estado comercial são dois conjuntos, e só o primeiro é restrito

A Fase 6 pôs sete chaves num único `AMOUNT_FIELDS` e restringiu todas. A classificação
ficou **larga**: `settled_billing_route`, `route_deviation_reason` e
`pricing_failure_reason` **não revelam valor, taxa nem multiplicador** — dizem em que
_situação comercial_ a linha caiu.

Três evidências de que a classificação estava errada:

1. **A Fase 4 §9 define o painel de atenção como visível aos técnicos**, e ele já
   mostra saldo negativo e status de contrato. Se rota fosse classe-dinheiro, aquele
   painel estaria violando a R11 desde a Fase 4.
2. **A R11 diz que até o CLIENTE vê o Tipo de Atendimento**, e a rota é propriedade do
   Tipo de Atendimento. Logo a rota nunca foi secreta. O que a D33 acrescentou foi o
   **desvio**, que é estado comercial, não preço.
3. **A R11 lista nominalmente o que o técnico não vê: "Valor em R$".** Não lista rota.

E operacionalmente esconder é pior: um técnico que não sabe que o contrato do cliente
venceu não pode avisar antes de gastar mais dez horas que vão para avulso.

| Conjunto                  | Campos                                                                      | Quem vê            |
| ------------------------- | --------------------------------------------------------------------------- | ------------------ |
| `MONEY_FIELDS`            | `amount`, `amount_display`, `applied_hour_rate`, `applied_rate_basis`       | ADMIN de workspace |
| `COMMERCIAL_STATE_FIELDS` | `settled_billing_route`, `route_deviation_reason`, `pricing_failure_reason` | ADMIN **e** MEMBER |

Consequência: o §1b deixou de ser ADMIN-só. O técnico vê a metade em horas e a quebra
por origem de receita; o Admin vê adicionalmente o R$.

Os testes de contrato da Fase 6 foram ajustados e ganharam
`test_a_member_does_receive_the_commercial_state`, que afirma a **presença** contra um
conjunto nomeado e que os dois conjuntos são disjuntos. Afirmar só a ausência deixaria
uma fase futura re-alargar a restrição sem teste nenhum notar.

`ServiceIssueAllowanceSerializer.AMOUNT_FIELDS` virou `MONEY_FIELDS` por consistência
de nome — ali o conjunto inteiro é dinheiro de verdade (uma taxa/hora), então o split
muda o nome e nada mais.

---

## O que a Fase 9 ganhou de estrutural, além do briefing

**1. A property test achou dois bugs que a revisão de código não achou.**

- O slice "sem cliente" de uma distribuição por cliente **não tinha como se
  descrever** e caía no descritor não-estreitado: o bucket reportava as próprias horas
  apontando para todas as linhas da janela. Corrigido com `without_service_client` no
  descritor, e `_slice_descriptor` levanta erro nomeado em vez de alargar em silêncio.
- Os buckets de excedente carregavam descritor + flag (ver D56).

Nenhum dos dois teria sido pego por asserções escritas à mão sobre valores esperados,
porque em ambos o **número** estava certo — era o descritor que mentia.

**2. Um N+1 herdado, corrigido.** `contract_balance_statement` chamava
`period_parcels` por período: um contrato de 36 meses custava **37 queries**. Era
invisível enquanto o único consumidor era a tela de detalhe de um contrato, e virou a
espinha do dashboard desta fase. `period_parcels_bulk` lê tudo numa query e a alocação
FIFO continua em **um** lugar — passar a _lógica_ adiante em vez das parcelas é como um
dashboard e um fechamento passam a discordar sobre quais horas foram gastas. Fixado com
`django_assert_num_queries(2)`.

**3. O critério 9 deixou de ser conferência manual.** "O CSV contém os mesmos números
da tela" era comparação que alguém fazia à mão, porque a tela podia filtrar por
técnico, projeto e tipo de hora e o export só entendia competência e cliente — os dois
**legitimamente** discordavam. Agora os dois consomem o mesmo `ServiceLogFilterSet`.
A forma legada da Fase 6 é **traduzida** por `from_export_filters` em vez de tratada
por um segundo caminho, e um teste afirma que as duas formas produzem CSV idêntico.

**4. A Fase 9 é read-only, e existe teste que a mantém assim.**
`test_nothing_in_this_module_writes_a_ledger_entry` afirma que avaliar alertas não move
nada. É o guardrail da revisão da D35: o desenho recusado teria feito deste módulo o
primeiro escritor não atendido do livro-caixa.

## Resumo do que muda no código

| Decisão     | Muda código? | Onde                                                                                     |
| ----------- | ------------ | ---------------------------------------------------------------------------------------- |
| D48         | **Sim**      | `CompetenceBasis` no descritor; `_competence_group` alterna as chaves de `values()`      |
| D49         | **Sim**      | `report_bucket_from` com escalares, `report_bucket_expression`, `_origin_from`           |
| D50         | **Sim**      | módulo `service_reports_filters.py`; `consolidated_billing` passa por `report_bucket_of` |
| D51         | **Sim**      | `ReportViewer` com três projeções; agregação recebe o viewer                             |
| D52         | Não          | nada materializado. Gatilho no docstring do módulo + testes de contagem de query         |
| D53         | **Sim**      | `headline_totals` quantiza a média uma vez; ausência em vez de zero                      |
| D54         | **Sim**      | migração 0132, `ServiceAlertDismissal`, `dismiss_alert` polimórfico, endpoint de bolsa   |
| D55         | **Sim**      | `ReportViewer.guest()` provado; rota herdada pela Fase 8                                 |
| D56         | **Sim**      | `bucket()` exige exatamente um de `filters`/`drill_down`                                 |
| D57         | **Sim**      | `MONEY_FIELDS` + `COMMERCIAL_STATE_FIELDS`; testes da Fase 6 ajustados                   |
| Revisão D35 | **Sim**      | `ALLOWANCE_PENDING_CLOSURE`, `pending_closure_q`, sem tarefa periódica                   |

## D58 a D66 — decididas na Fase 8, porque só ali se tornaram inevitáveis

A Fase 8 é a última do núcleo e a única que altera código do core do Plane. Sete das
nove decisões abaixo existem por causa disso: expor o papel GUEST como ator real
obriga a responder perguntas que oito fases puderam deixar em aberto porque nenhum
cliente tinha login.

Duas fogem desse recorte e valem além da fase. A **D62** (solicitante) é entregue em PR
próprio, porque conta outra história — registrar quem pediu, não proteger o que o cliente
vê. A **D66** (varredura enumera o roteador) é método de teste, não regra de negócio, e é
reutilizável por qualquer fase futura; ela também é o que encontrou a leitura cross-tenant
corrigida em PR separado, e uma segunda no core do Plane que ficou registrada sem correção
em `ACHADOS-DO-CODIGO.md`.

## D58 — Dinheiro para o cliente segue a rota liquidada do apontamento, não uma propriedade do Cliente

**Não estende regra nenhuma — resolve uma imprecisão da tabela da R11 contra o texto
mais específico da §4 da Fase 6.** O enquadramento importa porque muda quem tem
autoridade sobre isso depois: não é uma decisão de visibilidade financeira nova, é a
leitura de duas frases que discordavam.

A tabela da R11 abreviava como "só se avulso". A §4 da Fase 6 diz, literalmente: "em
cliente de contrato: não exibir valores em R$, apenas horas — **exceto** para
apontamentos com rota `FATURA_REAIS` lançados fora do escopo do contrato, e para
excedente faturado". A exceção por apontamento sempre foi a regra. A abreviação foi
escrita antes de a D33 e a D44 criarem a distinção entre rota escolhida e rota
liquidada, e por isso não tinha vocabulário para expressá-la.

Três fatos que fecham a leitura:

1. **Não existe campo "avulso".** O enum `default_billing_mode` do `ServiceClient`
   virou `default_billing_type` na Fase 2. A pergunta "este Cliente é avulso?" não tem
   onde ser respondida, e nunca teve depois daquela fase.
2. **A §3 do contexto mestre prevê o caso misto explicitamente** — cliente de contrato
   recebendo serviço fora de escopo, faturado à parte. Uma flag no Cliente responderia
   a pergunta errada para exatamente esse caso, que não é raro.
3. **O critério que torna essa a única leitura defensável:** o cliente vê dinheiro
   exatamente onde vai receber linha de fatura. Mostrar valor de algo que ele não será
   cobrado e esconder valor de algo que ele será são os dois errados, e uma flag no
   Cliente comete um dos dois sempre.

Consequência: `settled_billing_route == BILL_AMOUNT`, por linha (D44 — liquidada, não
escolhida). A taxa e a base da taxa continuam invisíveis mesmo na linha faturada: o
valor é a linha de fatura, a taxa é o termo comercial por trás dela.

**Por linha implica `to_representation` e não o `__init__` que o
`ServiceLogSerializer` usa.** Uma resposta mistura legitimamente uma linha debitada da
bolsa sem valor com uma linha faturada com valor; um campo removido na construção
aplicaria a resposta da primeira linha a todas.

O total do work item filtra a rota **explicitamente**, em vez de confiar em que linhas
de pool carregam `0.00` por construção. Esse raciocínio é correto para o total do
Admin, mas faria a fronteira monetária do cliente ser propriedade emergente de um
check constraint em outro lugar — e no dia em que um débito de pool legitimamente
carregar valor (excedente faturado já é precificado, D40), um cliente de contrato
passaria a ver dinheiro de horas que a bolsa dele absorveu.

**A tabela da R11 no contexto mestre foi emendada.** Sem isso a próxima fase que ler a
tabela rediscute isso do zero.

## D59 — A allowlist de campos do cliente é módulo de domínio, não código de view

`plane/utils/service_portal.py`, com constante de módulo, não literal inline.

Duas razões, e a segunda é a que decide:

1. `IssueViewSet.partial_update` é código core rebaseado do upstream. Uma allowlist
   escrita ali é uma allowlist que um rebase pode alargar sem que ninguém veja.
2. Um teste que reafirma a lista continua passando depois que alguém a alarga. A
   constante é o mesmo objeto que o código usa — o padrão da D57, com o argumento mais
   afiado aqui do que lá.

**A ordem é a decisão, não a organização do arquivo.** A allowlist tem de existir
_antes_ de o GUEST entrar em `allowed_roles`, porque
`allow_permission(..., creator=True, model=Issue)` libera a view inteira para quem
criou a linha **antes de a lista de papéis ser consultada**. Um cliente que abriu um
chamado já alcança a view hoje, com qualquer papel, e sem allowlist estaria alterando
responsável, labels, datas, estimativa e parent nos chamados dele.

Isso também é o que justifica tocar o core em vez de acrescentar endpoint:
**extensão pode acrescentar uma porta segura; não pode fechar uma aberta.** O buraco
está dentro de `partial_update` e é alcançável hoje. Um endpoint novo ao lado
acrescenta um caminho; não remove nenhum. O critério 19 é o que prova isso, e ele
passa no commit em que o GUEST ainda está fora da lista de papéis — é essa a prova de
que a allowlist, e não a lista, é o que fecha.

## D60 — Campo desconhecido cai em silêncio; grupo de estado inelegível recebe 400 nomeado

Tratamentos diferentes porque as duas situações são diferentes.

**Campo fora da allowlist: descartado, sem erro.** O critério 19 aceita "ignorados ou
rejeitados", e o precedente da casa é ignorar — `IntakeIssueViewSet.partial_update`
reconstrói o dict do guest exatamente assim, cada chave com default vindo da
instância. A interface do cliente nunca ofereceu `assignees`, então não há campo onde
renderizar um erro, e um 400 transformaria um payload que um cliente bem-comportado
nunca manda em modo de falha que ele tem de tratar.

**Grupo de estado inelegível: 400 com `STATE_GROUP_NOT_PERMITTED_FOR_CLIENT`.** Por
D47: o ator genuinamente tem a capacidade de mudar estado — é o critério 6 inteiro — e
é o _alvo_ nomeado pelo payload que é inelegível. Um 403 diria "você não pode mudar
estados", que é falso, e mandaria quem lê procurar um problema de permissão que não
existe.

Quatro grupos permitidos: `completed` e `cancelled` (fechar), `unstarted` e `started`
(reabrir). Dois negados:

- **`triage`.** Já é recusado hoje, mas só como efeito colateral de `partial_update`
  não passar `allow_triage_state` no contexto do serializer. Garantia que repousa numa
  chave ausente é garantia que a próxima fase a acrescentar essa chave remove sem
  perceber. Agora é recusado de propósito, e existe teste de caracterização sobre o
  comportamento herdado.
- **`backlog`.** Não é omissão. O cliente já tem os dois controles que backlog
  embaralharia: fechar diz "não quero mais", prioridade diz "o quanto é urgente".
  Backlog é planejamento interno. Um chamado que o cliente estacionou lá não está
  fechado nem enfileirado, sai das visões ativas, e volta seis semanas depois como
  "vocês ignoraram meu pedido". Negar remove uma ambiguidade cujo único produto é
  disputa.

Valida o **grupo**, nunca a linha de estado: workspaces renomeiam e acrescentam
estados livremente, e uma lista de ids ou nomes permitidos fica obsoleta na primeira
vez que um admin cria "Aguardando cliente".

## D61 — O feed de atividade exclui as linhas de apontamento para o cliente, em vez de reprojetá-las

**A Fase 8 cria a exposição, então a Fase 8 é dona da correção.** Antes do portal,
nenhum GUEST era membro de project de Cliente, então os leitores de atividade — que
sempre admitiram GUEST — não tinham cliente lendo. Entregar o portal sem isso seria
entregar um vazamento conhecido no mesmo PR que o habilita.

**O vazamento é aritmético.** `_service_log_batch_summary` persiste
`f"{sum(logged_hours)} h (...)"` em `IssueActivity.new_value`. O cliente vê
`equivalent_hours` legitimamente; com `logged_hours` do feed, uma divisão devolve o
multiplicador — exatamente a derivação que a R11(c) existe para impedir e que a D55
recusa calcular para `ReportViewer.guest()`. Duração bruta e multiplicador ficam no
`requested_data` transitório e não são persistidos, o que não ameniza nada.

**Por que nenhum teste pegou:** `test_the_activity_trail_carries_no_money` usa
`logged_hours == "1.0000"` como **controle positivo**. Correto para um feed lido por
Member; é o vazamento para um feed lido por Guest. O teste nunca esteve errado — nunca
lhe foi feita a pergunta do Guest.

**Excluir, não reprojetar.** O conteúdo informativo inteiro da linha `service_log` _é_
as horas apontadas, então não existe resto seguro para mostrar depois de tirar o
número. E uma segunda projeção do mesmo apontamento seria um segundo lugar onde a R11
pode errar, sendo que a primeira já tem testes — o argumento da D51 sobre fronteira de
agregação, aplicado aqui.

**Os quatro valores de `field`, não só um.** A varredura do domínio de serviço no
`IssueActivity` encontrou quatro, e todos são negados ao cliente pela mesma razão: cada
um responde a uma pergunta sobre **como o trabalho foi registrado e precificado
internamente** — a trilha da R8, o registro de sobrescrita da R10 — e nenhum responde a
uma pergunta que o cliente fez.

| `field`                          | conteúdo persistido               |
| -------------------------------- | --------------------------------- |
| `service_log`                    | `{soma logged_hours} h (rótulos)` |
| `service_log_author`             | correção de quem é creditado      |
| `service_log_delegation`         | "João, registrado por Maria"      |
| `service_log_hour_type_override` | sugerido contra aplicado          |

`route_deviation_reason`, `pricing_failure_reason` e `settled_billing_route` **não
chegam ao `IssueActivity` em nenhum caminho** — a pergunta da D57 sobre eles não se
coloca no feed. Ela se coloca no endpoint de apontamento do cliente, e é a D65.

**Três portas, não uma.** Patchear a óbvia teria deixado duas abertas, porque duas
classes de permissão não aplicam filtro de papel algum em método seguro:

| leitor                          | como o cliente chega                                 |
| ------------------------------- | ---------------------------------------------------- |
| `IssueActivityEndpoint`         | GUEST está na própria `allow_permission`             |
| `WorkspaceUserActivityEndpoint` | `WorkspaceEntityPermission`, actor id vem da **URL** |
| `IssueActivityListAPIEndpoint`  | `ProjectEntityPermission`, API externa por token     |

A segunda é a mais afiada: o id do ator é segmento de URL, então o cliente pede a
atividade de um técnico nomeado diretamente.

## D62 — O solicitante é modelo próprio, e a atribuição concede visibilidade

Modelo próprio em vez de coluna em `Issue`. `Issue` é o modelo mais editado do core do
Plane, e uma FK nullable nele seria superfície de merge permanente na tabela mais
quente do schema em troca de uma atribuição opcional. O mesmo raciocínio que a D45
aplicou a `WorkspaceMember`; oito fases foram entregues sem acrescentar coluna a
modelo core.

**Sem `ChangeTrackerMixin` e não é `ServiceConfigEntity`**, ao contrário de todo outro
modelo `Service*` que carrega decisão. Registrar quem pediu não decide dinheiro nenhum
— não muda taxa, rota, multiplicador nem saldo — então `ServiceConfigActivity` é a
trilha errada (§6 do contexto mestre: aquela trilha é para configuração que afeta
dinheiro). As colunas de auditoria herdadas respondem "quem registrou e quando", que é
a pergunta forense aqui.

**A metade que teria silenciosamente não funcionado.** O critério 15 pede que o
solicitante registrado **veja** o chamado no portal. Na configuração recomendada ele
veria de todo jeito, porque o project concede visibilidade total aos clientes — então
todo teste de visibilidade desta decisão **desliga essa flag primeiro**. Com ela
ligada, uma atribuição quebrada passa. Um administrador que registra um solicitante que
depois não vê nada é uma funcionalidade que relata sucesso e não faz nada, o que é pior
que não existir.

Por isso `client_may_reach_issue` passa a ser **a única definição** de "este cliente
alcança este work item". O core fazia essa pergunta em três lugares com grafias
diferentes — `role=ROLE.GUEST.value` num, o literal `role=5` em dois, a comparação de
`created_by` uma vez como filtro de queryset e uma vez como teste de identidade — e a
Fase 8 precisa dela em três outros. Seis cópias de uma regra de visibilidade são seis
chances de uma divergir. Os três sítios do core agora chamam a função ou o gêmeo de
queryset dela, cada um **dentro da condição que já existia**.

Notificação é o `IssueSubscriber` nativo. Reaproveitar a inscrição do próprio Plane
significa que notificação, digest e cancelamento já funcionam; um caminho paralelo para
uma atribuição seria uma segunda coisa a manter e a errar. Apagar a atribuição
**deixa** a inscrição: cancelar é decisão do solicitante e o Plane já lhe dá esse
controle.

A atribuição não pode virar meio de conceder visibilidade arbitrária: o solicitante tem
de ser GUEST ativo **daquele project**. Sem isso, uma rota cujo propósito inteiro é
conceder visibilidade entregaria a qualquer membro do workspace a visão de um work item
num project onde ele não está. 400 por D47.

## D63 — A rota do portal sobrescreve o viewer, e o escopo é resolvido antes e aplicado por último

**`_viewer` sobrescrito, nunca herdado.** A implementação herdada resolve pertencimento
de Admin de workspace e cai para `ReportViewer.member()` — que carrega `logged_hours`.
Uma projeção de Member entregue a um cliente é pior que um 403, porque o cliente vê
`equivalent_hours` legitimamente e os dois juntos entregam o multiplicador. O docstring
da Fase 9 já dizia isso; esta decisão é o que o cumpre.

Incondicional: um Member ou Admin chamando a rota vê exatamente o que o cliente vê.
Isso torna o portal auditável por dentro e não deixa ramo condicional por papel onde a
R11 possa errar — o mesmo desenho de `ServiceLogClientEndpoint`.

**Escopo resolvido primeiro, aplicado por último, com curto-circuito em zero projects.
Os três, porque as duas metades falham em silêncio com 200:**

1. `narrow()` é `dataclasses.replace`, então **substitui em vez de interseccionar**. Um
   escopo aplicado antes da query string é um escopo que um `?project_ids=` forjado
   alarga, sem erro em lugar nenhum.
2. `narrow(project_ids=())` **não aplica filtro de project algum**, porque
   `queryset()` só aplica cada lookup `if values:`. Escopo vazio virando acesso total é
   a falha que parece impossível em revisão, precisamente porque o código _lê_ como se
   estivesse escopando. `scope_filterset_to_client` levanta exceção em vez de aceitar
   escopo vazio, e a view curto-circuita para que ela nunca seja chamada com um.

Cada uma é coberta por exatamente um teste, verificado por sabotagem.

> **Corrigido pela D67 (Fase 8b).** O item 1 continua verdadeiro sobre `narrow()`, mas a
> conclusão que se tirou dele — deixar a requisição ser **substituída** pelo escopo do
> chamador — estava errada, e o que ela produzia era a visão consolidada de duas empresas
> que a §2 da Fase 8 proíbe nominalmente. O escopo agora é a **interseção** do project
> pedido com os projects do chamador, `project_ids` passou a ser **obrigatório e único**, e
> o teste que afirmava `entries == 1` para quem pedia o project de outro cliente afirma
> `entries == 0`. A propriedade de segurança que aquele teste nomeava não mudou; a asserção
> era artefato da substituição. Leia a D67 antes desta seção.

**O descritor é um registro, não uma permissão.** Todo bucket carrega
`filters: to_params()`, e o drill-down reconstrói com `from_params(request.GET)` sem
reescopar. Um drill-down de portal tem de reaplicar o escopo na entrada.

`revenue_series` **não é chamada**, ainda que não levantasse erro: ela devolve buckets
só-horas para viewer sem dinheiro, então chamá-la produziria uma seção silenciosamente
vazia em vez de um erro.

`contract_balance_statement` é entregue sem projeção porque cada campo dela é hora —
contratadas, acumuladas, consumidas, concedidas, saldo, descartadas, excedente, mais a
competência de origem de cada parcela. Responde à primeira pergunta do portal e não
contém nada que a R11 retenha.

## D64 — O gate do frontend passa a ser por campo, nos dois caminhos

`allowPermissions` faz inclusão simples em lista, sem hierarquia. Acrescentar GUEST ao
booleano `isEditable` habilitaria título, descrição, responsável, labels, datas,
estimativa, parent, widgets e a seção de apontamento. O gate deixa de ser um booleano e
passa a ser um registro por campo, montado de chamadas independentes: estado e
prioridade incluem GUEST, todo o resto continua `[ADMIN, MEMBER]`.

**Existem dois gates, e o segundo é o caminho que as pessoas usam.**
`peek-overview/root.tsx` duplica o mesmo `allowPermissions`, e o peek é onde se cai
clicando um item em qualquer visão de lista. Alterar só `issue-detail/root.tsx` deixaria
o critério 21 passando numa conferência manual na página de detalhe e falso no caminho
real.

Nenhum componente-folha muda: todos já aceitam `disabled`. O que muda é a origem do
booleano.

## D65 — Dos três campos de estado comercial, o cliente vê dois

A D57 separou dinheiro de estado comercial e tornou o segundo visível ao MEMBER. GUEST
era outra pergunta, e é esta. Decidida, não herdada por omissão.

| campo                    | cliente | por quê                                                                                        |
| ------------------------ | ------- | ---------------------------------------------------------------------------------------------- |
| `settled_billing_route`  | vê      | explica qual arranjo absorveu as horas; ele já vê `debited_hours`                              |
| `route_deviation_reason` | vê      | quando o valor está visível, ele vai perguntar por que está sendo cobrado tendo contrato       |
| `pricing_failure_reason` | não vê  | lacuna de configuração nossa, numa linha que ainda não é faturável; nada em que ele possa agir |

O caso do desvio é o que fecha a decisão: pela D58 o cliente vê o valor quando a rota
liquidada é `BILL_AMOUNT`, e "contrato vencido" é a **resposta** para a pergunta que
esse valor provoca. Mostrar a cobrança e esconder o motivo dela é a mesma forma de erro
que a D58 rejeita — as duas metades têm de andar juntas.

E pela D33, em DDL, a única forma de um desvio existir é "escolheu pool, foi faturado
em reais". O teste desta decisão constrói a linha assim, que é o cenário de produção de
que ela trata, e não uma combinação sintética.

## D66 — Teste de escopo enumera o roteador, nunca uma lista de endpoints mantida à mão

Vale para qualquer fase futura, e é por isso que está registrada como decisão em vez de
ficar como comentário no arquivo de teste.

**O enunciado.** Um teste que prova "nenhum endpoint vaza X" tem de descobrir os endpoints
percorrendo o resolvedor de URL do Django, e tratar cada rota encontrada como **negada por
padrão**. Admitir um papel a uma rota exige editar uma allowlist nomeada, no próprio teste,
com o motivo escrito.

**O que motivou.** A varredura do critério 16 da Fase 8 foi escrita assim e encontrou, na
primeira execução, uma leitura cross-tenant no
`ServiceClassificationWindowViewSet` da Fase 2b: `get: retrieve` estava roteado, nenhum
`retrieve` estava definido, e o herdado do `ModelViewSet` rodava sob
`permission_classes = [IsAuthenticated]` — sem checagem de papel e sem checagem de
pertencimento. `get_queryset` filtra pelo slug da URL e mais nada, então qualquer usuário
autenticado da instância lia a janela de qualquer workspace, dado o id. Devolvia **200 com o
payload inteiro** para quem não é membro de workspace algum.

**Por que uma lista à mão não pegaria.** O docstring da classe já dizia "reads are open to
admins and members, GUEST to neither". Era verdadeiro sobre toda ação **escrita** e falso
sobre a única que estava apenas **roteada**. Uma lista de endpoints escrita à mão é escrita
lendo a classe — ou seja, lendo o docstring — então ela teria afirmado o docstring e
concordado com ele. O resolvedor não lê docstring: ele sabe o que está roteado.

**A generalização, que é o valor da decisão.** As duas formas de teste falham de maneiras
diferentes:

| forma               | falha quando                                                             |
| ------------------- | ------------------------------------------------------------------------ |
| lista à mão         | alguém **acrescenta** uma rota (a lista fica silenciosamente incompleta) |
| enumerar o roteador | alguém acrescenta uma rota **e o teste fica vermelho**                   |

A segunda transforma "esqueci de decidir" em erro. A primeira transforma em nada. E o modo
de falha que importa nesta arquitetura não é uma decisão errada — é uma **ausente**: uma ação
que ninguém escreveu, mas que o roteador expõe.

**Consequência prática.** Toda condição de refusa aceitável tem de ser enumerada também.
`401`, `403` e `405` significam "nenhum dado atravessou" — o `405` inclusive, porque o DRF
recusa o verbo antes de qualquer handler. Qualquer outra coisa, inclusive `404` e `400`,
significa que a camada de permissão deixou passar até o corpo da view, e é o sinal a
investigar. Foi exatamente um `404` onde se esperava `403` que expôs o buraco: o id sorteado
não existia, e a resposta contou que a permissão havia passado.

**Corolário sobre alcance.** A varredura da Fase 8 cobre as rotas `service-*`, que são código
desta série. O mesmo padrão pode existir em rotas do core do Plane, que a operação hospeda sem
ter escrito. A Fase 8 rodou a varredura ampliada uma vez sobre todas as rotas com escopo de
workspace e registrou o resultado em `ACHADOS-DO-CODIGO.md`, **sem corrigir** — saber é
informação de operador, e decidir o que fazer é outra conversa.

## Resumo do que muda no código

| Decisão | Muda código? | Onde                                                                                      |
| ------- | ------------ | ----------------------------------------------------------------------------------------- |
| D58     | **Sim**      | `CLIENT_MONEY_FIELDS`, `to_representation` por linha, `issue_client_totals` filtra a rota |
| D59     | **Sim**      | módulo `service_portal.py`; `partial_update` passa a chamar em vez de decidir             |
| D60     | **Sim**      | `filter_issue_payload_for_client`, `validate_client_state_transition`, 400 nomeado        |
| D61     | **Sim**      | `activity_fields_hidden_from` nos **três** leitores de atividade                          |
| D62     | **Sim**      | migração 0133, `ServiceIssueRequester`, `client_visible_issues_q`, três sítios do core    |
| D63     | **Sim**      | `ServiceClientPortalReportEndpoint`, `_viewer` sobrescrito, `scope_filterset_to_client`   |
| D64     | **Sim**      | registro por campo em `issue-detail` **e** `peek-overview`                                |
| D65     | **Sim**      | `CLIENT_COMMERCIAL_STATE_FIELDS`; `pricing_failure_reason` fora do `Meta.fields`          |
| D66     | **Sim**      | a varredura percorre `get_resolver()`; allowlist `CLIENT_REACHABLE` nomeada no teste      |

---

# D67 — decidida na Fase 8b, porque a tela é que revelou o que a API respondia

## D67 — O dashboard do portal é de um Cliente por vez, e o `project_ids` é obrigatório e único

**O que estava errado.** `ServiceClientPortalReportEndpoint` era escopado pelo **usuário**,
não pelo project: `client_project_ids` devolvia todos os projects do chamador e
`scope_filterset_to_client` os aplicava com `narrow()`, que substitui. Para o Marcel real —
GUEST na Marubeni **e** na Terlogs — uma chamada devolvia `totals`, séries, distribuições e
bolsas somando as duas empresas, e `contracts[]` com **os dois contratos**.

Isso é exatamente a visão consolidada que a §2 da Fase 8 proíbe pelo nome: "não construir
visão consolidada das duas empresas: os contratos são independentes, os dashboards são
dedicados". A API não estava pronta para os critérios 1 e 2 da 8b, ao contrário do que o
briefing daquela fase registrou.

**A correção tem duas metades, e a segunda é a que importa.**

A primeira é a **interseção**: o project pedido cruzado com os projects do chamador, ainda
aplicada por último. Interseção é sempre igual ou mais estreita que o escopo do cliente,
então um id forjado continua não podendo alargar a fronteira de tenancy — os dois perigos que
a D63 nomeia permanecem fechados, e o curto-circuito para payload vazio continua sendo o que
impede `scope_filterset_to_client` de ser chamado com escopo vazio.

A segunda é **tornar o parâmetro obrigatório e limitado a um project**:

```
sem parâmetro          -> 400 PORTAL_REPORT_REQUIRES_A_PROJECT
um project             -> aquele project, se estiver no escopo do cliente
um project de outro    -> interseção vazia -> curto-circuito -> payload vazio, 200
mais de um             -> 400 PORTAL_REPORT_ACCEPTS_ONE_PROJECT
```

Sem ela a interseção resolveria o frontend e deixaria o consolidado **alcançável**: a
resposta padrão do endpoint, sem parâmetro, continuaria sendo as duas empresas somadas, e a
§2 passaria a depender de disciplina de quem chama. Com ela o endpoint fica estruturalmente
incapaz de produzir o consolidado. É o mesmo movimento que esta série já fez três vezes — a
XOR do livro-caixa, o índice único do critério 7 da Fase 5, a XOR das origens do apontamento:
**tornar o estado errado irrepresentável em vez de apenas não usado.**

Custo aceito: os testes do portal que chamavam sem parâmetro passaram a nomear o project.
Churn mecânico, e o preço certo.

### Por que nenhum teste pegou, e por que vale registrar

A classe `TestD63TheScopeIsResolvedFirstAndNarrowedLast` prova bem o isolamento **entre**
clientes, com Marubeni e Terlogs, ida e volta. O que ela nunca fez foi a pergunta do **guest
multi-project**: a fixture `marcel` era membro de **um** project só.

O cenário de referência — Marcel na Marubeni **e** na Terlogs — está escrito no contexto
mestre desde o começo da série, e é citado nominalmente na §2 da Fase 8 e na §3c da Fase 9. A
fixture que leva o nome dele **nunca o implementou fielmente**. A proteção estava correta; a
pergunta nunca foi feita.

É a mesma classe de falha que a D61 registrou no feed de atividade: um mecanismo bem
construído, testado no eixo em que alguém pensou, e cego no eixo que ninguém formulou. A
lição operacional é que **uma fixture com o nome de um ator do cenário de referência deveria
implementar aquele ator**, e não uma simplificação dele — porque o nome é o que faz o leitor
acreditar que o cenário está coberto. A 8b adicionou `marcel_multi` e o exercita nos dois
projects.

### A armadilha de implementação, que passaria por toda a suíte de segurança

A interseção compara ids vindos da query string com ids vindos do banco. Se um lado fosse
`UUID` e o outro `str`, **toda** interseção seria vazia: todo request curto-circuitaria para
payload vazio e o dashboard nasceria em branco para todo mundo.

Essa falha é invisível para uma suíte de segurança, porque falha na direção **segura**. Todo
teste que afirma "as horas do outro cliente estão ausentes" continua verde com a feature
morta. Verificado por sabotagem: substituir a coerção por uma interseção de conjuntos crua
deixa **21 dos 31 testes do portal verdes**.

Daí duas coisas, e nenhuma é opcional: a coerção explícita em `_as_uuids`, e um **controle
positivo** afirmando resultado **não vazio** para um pedido legítimo de project único. Sem o
controle positivo, um erro de tipo fica indistinguível de um escopo funcionando.

## Resumo do que muda no código

| Decisão | Muda código? | Onde                                                                                     |
| ------- | ------------ | ---------------------------------------------------------------------------------------- |
| D67     | **Sim**      | `client_project_scope` e `_as_uuids` em `service_portal.py`; `get` do endpoint do portal |
