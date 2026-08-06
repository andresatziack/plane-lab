# Decisões

## Resolvidas — todas incorporadas aos prompts

| # | Decisão | Onde foi aplicada |
|---|---|---|
| D1 | Duração abaixo de 6 min arredonda para o **piso de 15 min** | R2 do contexto mestre |
| D2 | Arredondamento **no meio do bloco** (7min30s), empate para cima | R2, tabela de testes |
| D3 | Saldo não utilizado **acumula** para os meses seguintes | Fase 4, seção 3 |
| D4 | Saldo **pode ficar negativo**, sem bloquear. Ao fechar o mês, Admin escolhe transportar o déficit ou **faturar o excedente em R$** | Fase 4 seção 4, Fase 6 seção 6 |
| D6 | Preço avulso = **valor/hora base × multiplicador**, com override absoluto opcional | Fase 6, seção 1 |
| D7 | **O Cliente define o padrão** de Tipo de Atendimento; o apontamento pode sobrescrever | Fase 2, seção 3 |
| D8 | Feriados e horários cadastráveis no painel; classificação automática; modo de entrada por hora início/fim | Fase 2b + Fase 3 |
| D9 | Contrato vencido: **permitir, alertar e sinalizar** no relatório. Nunca descartar registro por pendência comercial | Fase 4, seção 1 |
| D10 | **Sem workflow de aprovação** de timesheet. Fica o travamento de período | Fase 4, seção 6 |
| D11 | Usuário de cliente pode responder por **mais de uma empresa** (matriz e filial). Implementado via participação em múltiplos projects — **ver D19**, que substituiu a tabela de associação | Fase 1 seção 2, Fase 8 seção 2 |
| D12 | **Sem migração** agora, mas com campo de origem no apontamento desde já | Fase 3, seção 4 |
| D13 | **Divisão em segmentos apenas no modo Intervalo.** No modo Duração o técnico seleciona o tipo | R10, Fase 2b seções 3 e 4, Fase 3 seção 3 |
| D14 | Calendário de feriados **único no workspace**, campo de abrangência no modelo sem lógica de filtro | Fase 2b, seção 1 |
| D15 | Três caminhos de renovação, incluindo **converter saldo em bolsa de horas de um chamado**. Teto de acúmulo + alertas de alto e baixo consumo | Fase 4 seções 3, 8 e 9; Fase 9 seção 3b |
| D16 | **Não existe jornada por técnico.** As janelas de classificação do workspace são a única fonte da verdade | Fase 2b, requisito negativo explícito |
| D17 | **Arredondar por segmento**, com guardrail para lançamentos abaixo de 15 min | Fase 2b, seção 6 |

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

| Problema | Como o desenho anterior resolvia | Como resolve agora |
|---|---|---|
| Isolamento entre clientes | Filtro de queryset customizado em cada endpoint | Mecanismo nativo do Plane |
| Vínculo usuário↔Cliente | Tabela de associação N:N própria | Derivado da participação nos projects |
| Troca de portal (Marcel) | Seletor de empresa customizado | Seletor de project nativo |
| Qual Cliente é o chamado | Campo editável, com risco de ficar errado | Derivado do project, impossível divergir |
| Ambiguidade do usuário multi-empresa | Seleção obrigatória na abertura | Deixa de existir: o chamado nasce num project |
| Relatório cross-cliente | — | Admin e Member enxergam todos os projects |

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
