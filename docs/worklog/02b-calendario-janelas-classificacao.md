# Fase 2b — Calendário de feriados, janelas de classificação e detecção de Tipo de Hora

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 2.
> Todas as decisões desta fase estão resolvidas (D8, D13, D14, D16, D17).

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

Seed inicial, coerente com as regras de negócio acima:

| Tipo de Hora | Mult. | Prio. | Janelas |
|---|---|---|---|
| Domingos e feriados | 2.0 | 10 | `FERIADO` dia inteiro; `DOMINGO` dia inteiro |
| Fora do expediente | 1.5 | 20 | `SABADO` dia inteiro; `SEGUNDA`–`SEXTA` 18:00–08:00 |
| Horário comercial | 1.0 | 30 | `SEGUNDA`–`SEXTA` 08:00–18:00 |

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

| Cenário | Resultado esperado |
|---|---|
| Seg 14:00–16:00 | 1 segmento: 2h Horário comercial |
| Seg 17:00–20:00 | 2 segmentos: 1h Comercial + 2h Fora do expediente |
| Seg 22:00 → Ter 01:00 | 1 segmento: 3h Fora do expediente (atravessa a meia-noite sem dividir) |
| Sex 17:00 → Sáb 02:00 | 2 segmentos: 1h Comercial + 8h Fora do expediente |
| Sáb 22:00 → Dom 02:00 | 2 segmentos: 2h Fora do expediente + 2h Domingos e feriados |
| Dom 23:00 → Seg 02:00 | 2 segmentos: 1h Domingos e feriados + 2h Fora do expediente |
| Feriado 10:00–12:00 | 1 segmento: 2h Domingos e feriados |
| Qua 10:00–12:00, sendo a Qua um feriado | 1 segmento: 2h Domingos e feriados |
| Sáb 09:00–11:00 | 1 segmento: 2h Fora do expediente (1.5, não 2.0) |
| Seg 07:00–09:00 | 2 segmentos: 1h Fora do expediente + 1h Comercial |
| Modo Duração, `3h`, em um domingo | 1 apontamento, Domingos e feriados |
| Modo Duração, `3h`, em uma terça | 1 apontamento, técnico escolhe o tipo |

Testar também os limites exatos: apontamento terminando às 18:00 em ponto
(inteiramente comercial) e começando às 08:00 em ponto (inteiramente comercial).

### 5. Data de atendimento dos segmentos

Cada segmento carrega a **sua própria data**, derivada da posição real na linha
de tempo — não a data de início do lançamento.

Isso é obrigatório por causa da regra R7: um apontamento de 31/01 23:00 a 01/02
01:00 gera segmentos em competências diferentes, e cada um deve debitar o pool
do seu mês. Usar a data de início para os dois causaria erro de faturamento na
virada do mês.

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

1. Admin cadastra 25/12 como feriado recorrente e ele aparece em todos os anos
2. Admin cadastra o Carnaval de um ano específico, sem recorrência, e ele não
   aparece no ano seguinte
3. Todos os cenários da tabela da seção 4 passam
4. Sábado é classificado como 1.5 e domingo como 2.0
5. Feriado em dia útil vence a janela do dia da semana
6. Apontamento que atravessa a meia-noite sem mudar de classificação não é
   dividido
7. Apontamento que atravessa a virada do mês gera segmentos com datas — e
   competências — corretas
8. Modo Duração nunca gera divisão
9. Modo Duração em dia útil deixa o Tipo de Hora para o técnico escolher
10. Seg 17:50–18:10 gera 2 apontamentos de 15 min cada, com o total exibido antes
    de salvar
11. Seg 17:57–18:03 gera **1** apontamento de 15 min, sem divisão (guardrail)
12. Cadastro de janelas com sobreposição no mesmo nível de prioridade é rejeitado
13. Admin cria um Tipo de Hora novo com janela e prioridade próprias pelo painel,
    e o motor passa a usá-lo sem alteração de código
14. Técnico sobrescreve a classificação e a auditoria registra sugestão e escolha
15. Alterar janelas ou calendário não reclassifica nenhum apontamento existente
16. A UI exibe o motivo de cada segmento
17. Não existe entidade de jornada de trabalho por técnico no modelo de dados

## Entregar

Modelo de dados (feriado, janela de classificação, prioridade no Tipo de Hora) e
a assinatura do motor primeiro, para validação. Depois: implementação, telas de
administração e testes. A suíte do motor deve cobrir a tabela da seção 4
integralmente, mais os dois casos de arredondamento da seção 6.
