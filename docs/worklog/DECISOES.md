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

| Situação | Antes | Agora |
|---|---|---|
| Cliente **sem contrato** nenhum | não bloqueia (D27), destino do dinheiro indefinido | **fatura avulso** |
| Contrato **suspenso** | apenas um código de alerta distinto (decisão B4) | continua aceitando apontamento, **com alerta**, e o tempo **vira avulso** |
| Contrato **vencido** e não renovado | permitido e sinalizado (D9), destino indefinido | **fatura avulso** |

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

| Decisão | Muda código? | Onde |
|---|---|---|
| D31 | **Sim** | resolução de competência: não materializar período fora da vigência; data anterior debita o primeiro período |
| D32 | Não | confirma o default |
| D33 | **Sim** | fallback para avulso nos três casos, com motivo persistido e distinção entre rota escolhida e aplicada — Fase 6 |
| D34 | Não, é cadastro | `accrual_cap_mode = NONE`, `carryover_months = NULL`. Requisito: garantir que o alerta de saldo acumulado alto exista |
| D35 | **Sim, fora da Fase 6** | carência de 30 dias e tarefa periódica de baixa — dívida nomeada |
| D36 | **Sim** | quarta origem no consolidado — Fase 6 |
