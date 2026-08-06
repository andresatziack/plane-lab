# Próxima sessão: Fase 7 — Permissões e delegação

> **Este arquivo é o ponto de entrada.** Para começar uma sessão nova basta dizer:
>
> > Leia `docs/worklog/PROXIMA-SESSAO.md` e siga.
>
> Ele é reescrito ao fim de cada fase e sempre descreve a **próxima**. O caminho nunca
> muda, então não precisa ser anotado em outro lugar.

## Sua tarefa

Implemente a **Fase 7**: `docs/worklog/07-permissoes-delegacao.md` — quem pode lançar por
quem, e quem pode editar o que.

**O modelo de dados ainda NÃO foi apresentado.** A §7.1 do contexto mestre vale
integralmente para esta fase: **apresente o modelo e as decisões, e aguarde OK antes de
escrever código.** As fases 1 a 4 tiveram esse passo cumprido em sessões anteriores; esta
não teve.

## Por que a 7 e não a 5

A 5 (bolsa por work item) é a sucessora natural da 4, e está desbloqueada. A 7 foi
escolhida porque **é a única que ainda não teve o passo de aprovação de modelo**, e porque
ela é independente: depende só da Fase 3, que está mesclada. Se preferir seguir a ordem do
`README.md` e fazer a **5** primeiro, ela também está pronta para começar — e tem um
critério herdado esperando por ela, descrito abaixo. Diga qual das duas.

## Leia primeiro, nesta ordem

1. **`.kiro/steering/worklog-contexto.md`** — o contexto mestre. **Leia explicitamente:**
   ele se declara `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab`
   como raiz do workspace. No sandbox web o repositório fica numa subpasta e o arquivo
   **não é carregado sozinho**. Assumir que foi é como se perdem as regras financeiras.
2. **`docs/worklog/CONVENCOES-DE-TRABALHO.md`** — fluxo, filosofia de teste, regras de
   modelagem, mecanismo de critérios herdados, convenções de PR.
3. **`tools/agent-sandbox/README.md`** — como rodar a suíte aqui, e as quatro armadilhas.
   **Os scripts já existem no repositório. Não os recrie.**
4. **`docs/worklog/07-permissoes-delegacao.md`** — o briefing da fase.
5. **`docs/worklog/DECISOES.md`** — **D8, D10, D11 e D19** valem aqui. E leia a seção
   "Auditoria do fork", risco 1: o bypass `creator=True` de
   `app/permissions/base.py` libera a view inteira para o criador do registro,
   ignorando o papel. A Fase 4 **não** o usou, de propósito, e a Fase 7 vai ter de
   decidir o que fazer com ele.

## Estado do repositório

- Repo `andresatziack/plane-lab`, base **`preview`**.
- Fases **1, 2, 2b, 3 e 4 mescladas.** PRs #1 a #10 fechados.
- Última migração: **0128**. A sua é a **0129**.
- Baseline da suíte: **1824 passando, 0 falhando** — medido com `RECREATE_DB=1`. Meça de
  novo antes de tocar em nada, para não herdar regressão alheia como se fosse sua.
- **Não existe CI.** As checagens locais são a única verificação.
- `ruff` **não vem instalado** no venv do sandbox. Instale com
  `/projects/sandbox/.plane-agent-sandbox/venv-api/bin/python -m pip install ruff` antes de
  rodar `ruff check apps/api`. Há **3 erros F401 pré-existentes** na `preview`
  (`app/views/issue/sub_issue.py` ×2, `app/views/project/invite.py`) — não são seus, e
  corrigi-los produz churn alheio à mudança.

---

# O que a Fase 4 entregou, e o que ela deixou para você

## Onde o código está

| Camada    | Arquivo                                                        |
| --------- | -------------------------------------------------------------- |
| Modelos   | `plane/db/models/service_contract.py`                          |
| Domínio   | `plane/utils/service_pool.py`                                  |
| Alertas   | `plane/utils/service_pool_alerts.py`                           |
| API       | `plane/app/{serializers,views,urls}/service_contract*`          |
| Reparo    | `plane/db/management/commands/reconcile_service_periods.py`     |

Quatro tabelas novas: `service_contracts`, `service_contract_periods`,
`service_hour_ledger_entries` (append-only) e `service_contract_alert_dismissals`. Duas
colunas novas: `project.service_contract` e `service_logs.debited_period`.

## Os dois invariantes que sustentam tudo, e que você não deve quebrar

1. **O período carrega o saldo; o livro-caixa carrega o histórico** (D23). O período tem os
   totais e é a linha que `select_for_update()` tranca; o livro-caixa tem os movimentos; os
   dois são escritos **na mesma transação, sempre**.
2. **Nenhum saldo se move sem uma linha.** Um período **fechado soma exatamente zero** no
   livro-caixa. É esse invariante que faz o critério 20 valer por construção, e
   `reconcile_period` o verifica coluna por coluna.

Se a Fase 7 acrescentar um caminho de escrita que mexa em `consumed_hours` — e a
reaplicação de débito da seção "delegação" do briefing é exatamente isso —, ele **tem** de
passar por `plane.utils.service_pool`. Um `update()` direto reabre a classe de bug que a
`reconcile_service_periods` existe para consertar.

## Duas coisas que a Fase 4 mudou no comportamento existente

**1. Uma rota `DEBIT_POOL` agora consulta contrato.** Antes, qualquer apontamento salvava.
Agora ele resolve contrato e período. A resolução **vazia não bloqueia** (D27), mas a
**ambígua bloqueia** — então um cliente com dois contratos e nenhum `is_default` recusa
apontamentos até alguém marcar o padrão. O painel de alertas expõe esse defeito de
configuração em `clients_without_default_contract`, de propósito, para que ele seja
corrigido antes de aparecer na frente de um técnico.

**2. `ServiceLog.delete()` foi sobrescrito** e agora estorna o débito antes de apagar. Se
você tocar em qualquer caminho de exclusão de apontamento, leia o docstring: o estorno é
**recusado em período fechado**, e nesse caso o apontamento é deixado intacto em vez de
apagado — um mês faturado nunca é reescrito por uma exclusão.

## Critério herdado, esperando a Fase 5

O **critério 17 da Fase 4** está parcialmente atendido. Transferir e expirar o saldo
remanescente de um contrato encerrado funcionam e são auditados; **converter em bolsa de
horas de um chamado depende da entidade da Fase 5**. A interface está pronta: o tipo de
lançamento `CONVERTED_TO_ISSUE_ALLOWANCE`, o argumento `target_issue`, e hoje um
`ISSUE_ALLOWANCE_NOT_AVAILABLE` com HTTP 501. Detalhes e o que "fechar" exige estão em
`05-bolsa-por-workitem.md`, seção "Critérios herdados da Fase 4".

O ponto de extensão da **R6** também está pronto e vazio:
`plane.utils.service_pool._work_item_allowance(issue)` devolve `None` e é a primeira coisa
que `apply_debit` consulta. É função nomeada em vez de comentário porque a R6 é uma
hierarquia, e a ordem é a parte fácil de errar depois.

## Uma decisão da Fase 4 que precisa da sua confirmação

**D27**, e é a única em que a Fase 4 se afastou da letra de um critério de aceite. O
critério 24 manda uma resolução de contrato "ambígua **ou vazia**" falhar. Implementado:
ambiguidade bloqueia, resolução vazia **não** bloqueia — o apontamento fica gravado com
`debited_period` nulo e o motivo é reportado. O raciocínio inteiro está na D27 do
`DECISOES.md`. É regra de negócio, não escolha de implementação; se a sua leitura for
outra, o ponto único de mudança é `NON_BLOCKING_RESOLUTION_FAILURES` em
`plane/utils/service_pool.py`.

## Depois da Fase 7

Ordem do `README.md`: **5** (bolsa por work item, depende da 4 — e tem o critério herdado
acima), **6** (precificação, depende de 3 e 4), **9** (dashboards, depende de 4, 5 e 6),
**8** (portal do cliente).

## A dívida de processo, ainda em aberto, para resolver antes da Fase 6

A Fase 4 respondeu três ambiguidades com defaults razoáveis e testes de caracterização, mas
as três são **cláusulas contratuais**, não decisões de software, e as respostas estão nos
contratos reais com os clientes:

| Ambiguidade                                | O que a Fase 4 assumiu                                                       |
| ------------------------------------------ | ---------------------------------------------------------------------------- |
| Pro-rata do primeiro mês                   | Nenhum. `contracted_hours` é editável em período aberto (D26)                 |
| Teto de déficit                            | Não existe teto; só alerta, com re-disparo a cada 5h de piora (B2)            |
| O que "suspenso" significa                 | Nada além de um código de alerta distinto (B4)                                |
| Ordem de descarte pelo teto de acúmulo     | Descarta as parcelas mais novas (D25)                                        |
| Bloquear apontamento sem contrato          | Não bloqueia (D27)                                                           |

Extraí-las para o `DECISOES.md` com a resposta real antes da Fase 6 evita que a fase de
precificação pergunte as mesmas coisas de novo — e evita defaults razoáveis sendo propostos
para regras que já existem em papel.
