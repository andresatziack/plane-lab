# Convenções de trabalho do worklog

Vale para **todas** as fases. O prompt de cada fase assume que isto já foi lido, para não
repetir o mesmo texto de sessão em sessão — que é como duas cópias divergem.

> O contexto de produto e as regras invioláveis estão em
> `.kiro/steering/worklog-contexto.md`. **Leia-o explicitamente.** Ele se declara
> `inclusion: always`, mas isso só vale quando o Kiro roda com `plane-lab` como raiz do
> workspace. Em ambientes onde o repositório está numa subpasta — o sandbox web é um
> deles — o arquivo **não é carregado sozinho**, e assumir que foi é como se perdem as
> regras financeiras.

## Fluxo de cada fase

1. **Antes de escrever código, apresente o modelo de dados e as decisões, e aguarde OK**
   (§7 do contexto mestre). Não é formalidade: as decisões de modelagem desta série são
   quase todas irreversíveis depois da migração.
2. Ramifique da `preview` **atualizada**. Uma fase, um PR.
3. **Mescle o PR da fase anterior antes de começar a próxima.** Não é preferência: duas
   fases abertas em paralelo criam a mesma numeração de migração e colidem no merge.
4. Ao terminar: marque os critérios de aceite um por um no documento da fase, atualize
   `PROXIMA-SESSAO.md` e abra o PR.

## Não existe CI neste fork

O GitHub Actions **nunca rodou** aqui (`actions/runs` devolve 0 no histórico inteiro).
Os workflows em `.github/workflows/` estão configurados para PRs contra `preview`, mas não
disparam.

Consequência direta: **as checagens locais são a única verificação que existe**, e a página
do PR nunca fica verde. Não interprete isso como falha, e não confie em "o CI pega".

Antes de qualquer PR:

```bash
RECREATE_DB=1 ./tools/agent-sandbox/run-api-tests.sh   # depois de mudar modelo
pnpm turbo run check:types
ruff check apps/api
./tools/agent-sandbox/run-web-checks.sh i18n
./tools/agent-sandbox/run-web-checks.sh lint <caminhos que você tocou>
```

`ruff check` é o que o CI checaria. **`ruff format` não é** — dezenas de arquivos
pré-existentes falham nele, então reformatar produz churn alheio à mudança.

Ambiente, armadilhas e validação de migração: `tools/agent-sandbox/README.md`.

## Como quero os testes

Duas lições, e as duas foram aprendidas errando.

### Um teste que afirma ausência sem afirmar o MOTIVO da ausência não protege nada

Ele passa com a feature **e sem ela**. É falso-verde.

Aconteceu na Fase 2b: testes afirmavam "nenhum Tipo de Hora é sugerido" e passavam porque
o workspace da fixture não tinha janelas semeadas — o motor acertava **por acidente**.
Quando o motor real entrou, continuaram verdes, e não porque estavam certos.

Toda afirmação de ausência tem de afirmar **o código do motivo**, e ter um **controle
positivo ao lado, na mesma fixture**. Sem o controle positivo, uma suíte em que _tudo_
está ausente é indistinguível de uma que funciona.

Isso também expôs um bug real: o motor não distinguia "terça em modo Duração não tem
sugestão, **por regra**" de "não existe configuração alguma". Mesma resposta, motivos
opostos.

### Fixtures semeiam explicitamente e depois afirmam o que semearam

E afirmam a **configuração específica** de que o teste depende, não um total. "Treze
janelas" podem ser treze pelos motivos errados; "segunda é comercial das 08:00 às 18:00" é
precisamente o que faz o corte cair onde o critério manda.

Se alguém mudar o seed amanhã, os testes têm de falhar **por isso** — nomeando o que
faltou — e não ficar verdes por outro caminho.

### Verifique por sabotagem, não por inspeção

Quebre de propósito o que o teste deveria proteger e confirme que ele fica **vermelho**.
Se ficar verde, o teste não vale nada, e você acabou de descobrir isso de graça.

Foi assim que a 2b se validou: adulterado o seed de janelas, 67 testes verdes viraram 58
erros e 4 falhas. Reverter: `git checkout -- <arquivo>`. Nunca backup em `/tmp`, e nunca
encadeie a verificação depois do restore com `&&` — leia o porquê no README dos scripts.

## Regras de modelagem que já custaram caro

- **FK de configuração para entidade usada por apontamento é `DO_NOTHING`.** Nunca
  `CASCADE` nem `SET_NULL`. Com `CASCADE`, o soft delete cai no catch-all de
  `soft_delete_related_objects` (`plane/bgtasks/deletion_task.py`) e **soft-deleta os
  apontamentos** — perda de dados disparada por um admin arrumando o catálogo. Com
  `SET_NULL`, o histórico perde o rótulo. O raciocínio completo está em
  `03-worklog-core.md`.
- **Nome resolvido por `all_objects`** quando o alvo pode estar inativo ou soft-deletado.
- **Snapshot da R4 em tudo que decide dinheiro**, não só no que a regra citou
  nominalmente. A Fase 3 snapshotou também a rota de faturamento, que a R4 não mencionava:
  dois Tipos de Atendimento podem compartilhar rota, e sem o snapshot um relatório
  histórico não os separa depois de uma edição de catálogo.
- **Precisão numérica é a §4b do contexto mestre**, não uma escolha por campo.
- **Migração reversível, e validada à mão** — a suíte não roda migração nenhuma.
  Back-fill deve ser tolerante ao que o mundo real tem (um Tipo de Hora renomeado não pode
  derrubar a migração) e deve **recusar migrar em vez de inventar** valor de auditoria.

## Critérios herdados: o mecanismo

Uma fase frequentemente contém critérios que **só a fase seguinte pode fechar**. Se
ficarem só marcados como parciais no documento de origem, ninguém volta: a fase de origem
não conseguia verificar e a seguinte não sabe que existem.

O padrão já usado três vezes (1→2, 2→3, 3→2b):

1. No documento da fase de **origem**, o critério fica marcado como "atendido na Fase N",
   apontando para onde foi fechado.
2. No documento da fase que **fecha**, existe uma seção `## Critérios herdados da Fase X`
   com uma tabela de três colunas — _critério_, _o que faltava_, _status_ — e a indicação
   do arquivo de teste que o fecha.
3. **Fechar significa provar.** Marcar como atendido sem teste que atravesse o mesmo
   caminho do usuário é a mesma doença do falso-verde, num documento em vez de num teste.

E quando uma fase torna **falsa** uma afirmação escrita antes, corrija o texto antigo.
Documentação que descreve um estado que deixou de existir é pior que documentação ausente.

## PR

Corpo em português, e reportando:

- os **bugs reais** que os testes pegaram, com o motivo de os testes anteriores não os
  verem;
- as decisões em que uma alternativa óbvia foi **recusada**, e por quê;
- as limitações **aceitas**, com o teste de caracterização que as fixa;
- a **dívida** que a fase cria para uma fase futura, nomeando a fase.

Dívida não vai escondida em comentário de código.

```bash
gh api repos/andresatziack/plane-lab/pulls \
  -f title="..." -F body=@corpo.md -f head="<branch>" -f base="preview"
```

**Nunca `gh pr create`**, nem nenhum subcomando `gh pr` / `gh issue`: são backed por
GraphQL e falham sempre neste ambiente. Leituras via `gh api` REST funcionam.
