# Work Log e Faturamento — especificação de implementação

Especificação para transformar este fork do Plane em um service desk com
apontamento de horas e faturamento, equivalente a "Jira Service Management +
Tempo Timesheets", adaptado às regras de negócio que hoje vivem em relatórios de
Power BI externos.

**Status: pronto para implementação.** Todas as decisões bloqueantes foram
resolvidas e o código do fork foi auditado.

---

## Como usar em uma nova sessão do Kiro

**Diga só isto:**

```
Leia docs/worklog/PROXIMA-SESSAO.md e siga.
```

`PROXIMA-SESSAO.md` era reescrito ao fim de cada fase e descrevia a próxima. **O
núcleo está fechado**, então hoje ele não descreve fase nenhuma: descreve o estado
final, o que existe em API sem tela, as duas perguntas em aberto, as melhorias sem
prompt escrito e as dívidas. O caminho nunca muda, então continua sendo o único que
vale memorizar.

Ele aponta para `CONVENCOES-DE-TRABALHO.md`, que reúne o que vale para **todas**
as fases: filosofia de teste, regras de modelagem, convenções de PR e o mecanismo
de critérios herdados.

> **Correção importante.** Este arquivo afirmava que o contexto mestre em
> `.kiro/steering/worklog-contexto.md`, por ser `inclusion: always`, é carregado
> automaticamente e não precisa ser lido. **Isso só vale quando o Kiro roda com
> `plane-lab` como raiz do workspace.** No sandbox web o repositório fica numa
> subpasta, o diretório `.kiro/` lido é o da raiz do workspace, e o contexto mestre
> **não é carregado** — foi verificado. Por isso `PROXIMA-SESSAO.md` manda lê-lo
> explicitamente. Assumir o carregamento é como se perdem as regras financeiras.

Sugestões práticas:

- **Uma fase por sessão.** As fases foram dimensionadas para isso. Rodar duas na
  mesma sessão degrada a qualidade da segunda.
- **Não pule a validação do modelo de dados.** É o passo que evita retrabalho
  caro, e está pedido explicitamente na seção 7 do contexto mestre.
- Na Fase 4 (ou antes), leia a **D18** em `DECISOES.md`.
- `ACHADOS-DO-CODIGO.md` vale por inteiro antes de mexer em permissão. A **seção
  14** é a mais importante e não é tarefa: registra leitura e escrita cross-tenant
  no core do Plane, medida e não corrigida, para decisão de operador.
- Ao retomar uma fase já começada, informe o que já foi feito — o Kiro não tem
  memória entre sessões além do que está no repositório.

---

## Ordem de execução

### Núcleo — Fases 1 a 9

| Fase | Arquivo                                   | Entrega                                                                  | Depende de | Status |
| ---- | ----------------------------------------- | ------------------------------------------------------------------------ | ---------- | ------ |
| 1    | `01-fundacao-clientes.md`                 | Entidade Cliente ancorada a projects do Plane                            | —          | feita  |
| 2    | `02-catalogos-configuraveis.md`           | Tipo de Hora (multiplicador) e Tipo de Atendimento (rota de faturamento) | 1          | feita  |
| 2b   | `02b-calendario-janelas-classificacao.md` | Feriados, janelas de classificação e motor de segmentação                | 2          | feita  |
| 3    | `03-worklog-core.md`                      | Parser, arredondamento, modo duração/intervalo, CRUD, totais             | 2b         | feita  |
| 4    | `04-contratos-e-pools.md`                 | Contratos, pool mensal, acúmulo, excedente, renovação, alertas           | 3          | feita  |
| 5    | `05-bolsa-por-workitem.md`                | Bolsa de horas isolada por chamado                                       | 4          | feita  |
| 6    | `06-avulso-precificacao.md`               | Preços por cliente, valor em R$, faturamento de excedente                | 3 e 4      | feita  |
| 7    | `07-permissoes-delegacao.md`              | Permissões de apontamento, delegação, auditoria                          | 3          | feita  |
| 8    | `08-portal-do-cliente.md`                 | GUEST estendido por project, escopo, allowlist de campos                 | 1, 7       | feita  |
| 9    | `09-dashboards-consumo.md`                | Gráficos de consumo, alertas e relatórios de faturamento                 | 4, 5, 6    | feita  |

A Fase 6 depende da 4 apenas para o faturamento de excedente (seção 6); as seções
1 a 5 só precisam da Fase 3. A Fase 7 é independente das 4, 5 e 6 e pode ser feita
em paralelo.

**O núcleo está completo.** As nove fases estão mescladas e a suíte fecha em 2495
passando, 0 falhando. A ordem real de execução foi 1, 2, 2b, 3, 4, 5, 6, 7, 9, 8 —
a 9 saiu antes da 8 porque a 8 depende da 7 e não da 9, e a projeção GUEST que a 8
consome foi construída e provada na 9 (D55).

Não existe fase 10. `PROXIMA-SESSAO.md` fecha a série e lista quatro caminhos
independentes, sem ordem imposta entre eles.

### Depois do núcleo — roadmap acordado

Ainda **não** têm prompt escrito. Detalhamento e justificativa em
`OPORTUNIDADES.md`.

| Ordem | Melhoria                                                                             | Por que                                                                                                                                          | Já existe código?                                                                                                                                           |
| ----- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 10    | **Timesheet semanal**                                                                | Hoje todo apontamento é dentro do work item; um técnico com 15 chamados/dia abriria 15 telas. Todo o faturamento depende da qualidade desse dado | Não. Construir                                                                                                                                              |
| 11    | **Work Item Types**                                                                  | Tipos de chamado (Incidente, Requisição, Mudança). Pré-requisito natural de SLA                                                                  | **Sim, quase pronto** — modelos `IssueType`/`ProjectIssueType`, `Issue.type` FK, flag `is_issue_type_enabled` e ~10 arquivos de frontend. Falta CRUD e tela |
| 12    | **SLA de atendimento**                                                               | Contrato com pool de horas quase sempre tem SLA de resposta e solução. Escopo atual tem zero                                                     | Não. Mas as janelas de classificação da Fase 2b são exatamente o calendário que o SLA precisa — metade já vem construída                                    |
| 13    | **Abertura de chamado por e-mail**                                                   | Básico para service desk. Hoje o cliente precisa logar no portal                                                                                 | Parcial: família `Integration`/`Slack`/`Github` toda modelada, **sem nenhuma view**                                                                         |
| —     | Timer play/stop, entidade de Fatura, notificações ao cliente, capacidade por técnico | Ver `OPORTUNIDADES.md`, Parte B                                                                                                                  | Variado                                                                                                                                                     |

---

## Documentos de apoio

| Arquivo                              | O que é                                                                                                                                                                                                           |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.kiro/steering/worklog-contexto.md` | **Contexto mestre.** 11 regras invioláveis, as 4 grandezas de tempo, a especificação de precisão numérica (seção 4b), arquitetura de Cliente, restrições técnicas reais do repositório. Carregado automaticamente |
| `ACHADOS-DO-CODIGO.md`               | Auditoria do fork em 14 seções, com arquivo e linha. Papéis reais, flags mortas, padrões de model/API/teste, stack de gráficos                                                                                    |
| `DECISOES.md`                        | As 20 decisões de negócio tomadas, com o registro de por que quatro delas mudaram a arquitetura                                                                                                                   |
| `OPORTUNIDADES.md`                   | Ganchos mortos no fork (resquícios da edição paga) e lacunas do escopo, com prioridade                                                                                                                            |

---

## Conceito central

Se você ler só uma coisa antes de começar, leia a **seção 4 do contexto mestre**:
as grandezas de tempo (duração bruta → horas apontadas → horas equivalentes →
horas debitadas). É a modelagem que faz um único multiplicador configurável
resolver, ao mesmo tempo, o débito de pool do cliente de contrato e o valor em R$
do cliente avulso.

Praticamente todo bug financeiro possível neste sistema vem de confundir essas
grandezas.

## Regras de expediente modeladas

Configuração inicial (seed), não código:

| Quando                                | Tipo de Hora        | Mult. |
| ------------------------------------- | ------------------- | ----- |
| Seg–sex 08:00–18:00                   | Horário comercial   | 1.0   |
| Seg–sex 18:00 → 08:00 do dia seguinte | Fora do expediente  | 1.5   |
| Sábado, dia inteiro                   | Fora do expediente  | 1.5   |
| Domingo, dia inteiro                  | Domingos e feriados | 2.0   |
| Feriado, dia inteiro                  | Domingos e feriados | 2.0   |

## Cenário de referência

Usado como caso de teste em várias fases:

| Cliente                           | Project    | Contrato          | Usuário do portal |
| --------------------------------- | ---------- | ----------------- | ----------------- |
| Marubeni (matriz)                 | `Marubeni` | 10h/mês, 12 meses | Marcel            |
| Terlogs (adquirida pela Marubeni) | `Terlogs`  | 30h/mês, 36 meses | Adriano           |

Marcel é Guest nos dois projects e alterna entre eles pelo seletor nativo do
Plane, vendo o dashboard de contrato de cada um. Adriano só vê a Terlogs. Os dois
contratos são independentes e a relação societária não os mistura.

## Referências de produto

- Comportamento de apontamento desejado: [Tempo Work Logs para Jira](https://www.tempo.io/products/jira-time-tracking/work-logs)
- Base de código: [makeplane/plane](https://github.com/makeplane/plane)
- Papéis e permissões do Plane: [docs de roles and permissions](https://docs.plane.so/roles-and-permissions/overview)
  — atenção: a documentação descreve uma versão mais nova que este fork. Ver
  `ACHADOS-DO-CODIGO.md`, seção 1

## Aviso de licença

Este fork é AGPL-3.0 (`LICENSE.txt`, e cabeçalhos `SPDX-License-Identifier:
AGPL-3.0-only`). Como o sistema será oferecido como serviço a clientes externos,
vale validar as obrigações dessa licença com apoio jurídico. Existem também
edições Commercial e Airgapped com termos distintos —
[ver documentação de edições](https://developers.plane.so/self-hosting/editions-and-versions).

_Conteúdo das referências externas foi parafraseado e resumido para conformidade
com licenciamento._
