# Fase 10 — O ambiente de demonstração, e o que ele revelou

> **Estado:** fechada. Onze PRs mergeados (#26 a #36), os três itens pendentes entregues, e o
> ambiente republicado rodando a `preview` atual — verificado com captura de tela nos dois
> papéis.

A série fechou em nove fases com 2505 testes passando. Esta fase não acrescentou
funcionalidade nova ao domínio: publicou o produto num host acessível e o usou. Em uma sessão
de uso apareceram **nove defeitos**; ao entregar os três itens pendentes e verificar a
republicação apareceram **mais três**. Nenhum dos doze seria pego pelos critérios de aceite das
fases anteriores.

Isso é o achado que importa mais do que qualquer correção individual, e está na seção final.

---

## 1. O ambiente

**https://planedev.satziack.com** — fork completo, branch `preview`, cenário semeado.

Não é o `plane.satziack.com`. São instância, VPC, IP e certificado próprios, e o deploy antigo
(`i-0a3fe8f39a8d92d01`, EIP `50.16.32.183`) nunca foi tocado.

### Credenciais

Senha de todos rotaciona a cada execução do seed. Estas valem enquanto o seed não rodar de
novo:

| Papel                                    | E-mail                           | Senha                       |
| ---------------------------------------- | -------------------------------- | --------------------------- |
| Admin do workspace e da instância        | `admin@planedev.satziack.com`    | `AdZx2mLbAwXjoEks2QX2-7555` |
| Técnico MEMBER, com `can_delegate`       | `tecnico@planedev.satziack.com`  | `YvEYLyTrNaeARxp3DiXh-9972` |
| Técnico MEMBER sem concessão             | `tecnico2@planedev.satziack.com` | `foStqjxNwMyZaUgPvsUa-3786` |
| Marcel — GUEST de Marubeni **e** Terlogs | `marcel@planedev.satziack.com`   | `p7TnBwyftaib85RKGHNm-2722` |
| Adriano — GUEST só de Terlogs            | `adriano@planedev.satziack.com`  | `Xep9MNGJeEqfiACh3Ca7-7348` |
| Rafael — 2º GUEST da Marubeni            | `rafael@planedev.satziack.com`   | `YvGpRTExpLAdKbhVBdYR-8892` |
| **Patrícia — GUEST do Cliente Avulso**   | `patricia@planedev.satziack.com` | `wbw5KqLziEFuQM5ikYsW-8833` |

### Rotas úteis

Workspace `worklog-demo`. A interface está em **português por padrão** desde o item 9 — o
`Profile.language` do modelo é `pt-BR` e o seed grava explicitamente. Ninguém precisa trocar
nada no perfil.

| O que ver                                                        | Rota                                                                               |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Consumo da Marubeni (com contrato)                               | `/worklog-demo/projects/d308dff3-2c81-469b-b3c7-aa18e73e0b48/service-consumption/` |
| Consumo da Terlogs (excedente faturado)                          | `/worklog-demo/projects/c791da04-454e-4ec8-9774-bbe85ee51db3/service-consumption/` |
| **Consumo da Bonfim (Avulso, sem contrato)**                     | `/worklog-demo/projects/9281946a-334e-4836-b6a7-c85710a0b8dd/service-consumption/` |
| Chamado com multiplicador 1.5 e Solicitante                      | `/worklog-demo/browse/MRB-3/`                                                      |
| Garantia e cortesia                                              | `/worklog-demo/browse/MRB-4/` e `/worklog-demo/browse/MRB-5/`                      |
| **Chamados Avulso em reais**                                     | `/worklog-demo/browse/BNF-1/` e `/worklog-demo/browse/BNF-2/`                      |
| Relatórios internos (Consumo, Operacional, Atenção, Faturamento) | `/worklog-demo/service-reports/`                                                   |
| Membros (célula de concessões)                                   | `/worklog-demo/settings/members/`                                                  |
| Clientes (atribuição em massa)                                   | `/worklog-demo/settings/service-clients/`                                          |
| Catálogos (**ADMIN apenas**)                                     | `/worklog-demo/settings/worklog/hour-types/`                                       |

O cenário tem **quatro** Clientes: Marubeni e Terlogs com contrato e portal, Vale com contrato
e **sem usuário de portal** (o canário de vazamento dos testes de isolamento), e Bonfim
Alimentos **sem contrato**, faturada em reais.

### Infraestrutura

Tudo com a tag `Project=planedev-worklog`, e a instância e o volume também com
`auto-delete=no`.

| Recurso           | Id                                                            |
| ----------------- | ------------------------------------------------------------- |
| Instância         | `i-049f2527927bf4304` (t3.medium, 60 GB gp3)                  |
| EIP               | `eipalloc-089f5750deadee29a` → `98.86.24.174`                 |
| VPC / subnet      | `vpc-0eded5783a4fcff14` / `subnet-056cec9d4a1801e42`          |
| IGW / route table | `igw-04c2f86eb1a938098` / `rtb-04581cc23312e151c`             |
| Security group    | `sg-07e4c5287f7135b53` — 80 e 443 em `0.0.0.0/0`, **sem SSH** |
| IAM               | `planedev-worklog-ssm-role` / `planedev-worklog-profile`      |
| DNS               | `planedev.satziack.com` na zona `Z0459592WJKL7XKUB2OK`        |

TLS é Let's Encrypt no Caddy do próprio compose, não ALB: custa zero e o `Caddyfile.ce` já
fazia. Renovação em ~60 dias precisa da porta 80 aberta ao mundo, o que já é o caso.

**Custo:** ≈ US$ 39/mês ligada (t3.medium 30,37 + 60 GB gp3 4,80 + IPv4 3,65), ≈ US$ 8,45
parada.

### Operação

Sem SSH por decisão: acesso por SSM. **Os scripts em `/projects/sandbox/` não sobrevivem entre
sessões** — `ssm.sh` foi reescrito nesta sessão do zero. O que sobrevive é o que está no repo
(`tools/agent-sandbox/`) e o que está na caixa (`/opt/planedev/`).

```bash
/opt/planedev/seed.sh          # semeia (DEBUG=1 só num container efêmero)
/opt/planedev/logs.sh 80 api   # logs
```

Um `ssm.sh` mínimo, para recriar: `aws ssm send-command` com `AWS-RunShellScript`,
`workingDirectory` em **`/opt/planedev/plane-lab`** (não `/opt/planedev` — o compose vive no
checkout, e de fora dá "no configuration file provided"), e polling em
`get-command-invocation` em vez de `aws ssm wait`, cujo waiter estoura em 20 tentativas, muito
pouco para um build.

**A caixa tinha um clone shallow com refspec preso a uma branch de feature**, então
`origin/preview` não existia e `git pull` falhava. Corrigido na caixa:

```bash
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
git fetch --depth 1 origin preview && git reset --hard FETCH_HEAD
```

**Diferenças de produção real, deliberadas:** `DEBUG=0` nos serviços e `DEBUG=1` apenas no
container efêmero do seed; sem SMTP (`ENABLE_SMTP=0`, por isso as contas nascem com senha);
uploads em MinIO no EBS da instância, sem durabilidade; **sem backup de nada**, inclusive
Postgres; tudo numa caixa só. Acesso travado na aplicação: `ENABLE_SIGNUP=0`,
`ENABLE_MAGIC_LINK_LOGIN=0`, `DISABLE_WORKSPACE_CREATION=1`.

---

## 2. Duas armadilhas de rebuild que custaram tempo

**`docker compose run` usa a IMAGEM, não a árvore de trabalho.** A api tem `COPY plane plane/`
embutido no build. Um `git pull` no host não muda o código dentro do container. Depois de
trazer código novo é obrigatório:

```bash
docker compose build api worker beat-worker migrator   # mudanças de backend
docker compose build web                                # mudanças de frontend
docker compose up -d <serviços>
```

**O build do frontend não cabe em 4 GB.** Subir a instância, buildar, descer de volta:

```bash
aws ec2 stop-instances --instance-ids i-049f2527927bf4304 --region us-east-1
aws ec2 wait instance-stopped --instance-ids i-049f2527927bf4304 --region us-east-1
aws ec2 modify-instance-attribute --instance-id i-049f2527927bf4304 --instance-type Value=t3.xlarge --region us-east-1
aws ec2 start-instances --instance-ids i-049f2527927bf4304 --region us-east-1
# ... build (5 imagens em ~3 min em t3.xlarge) ...
# e o inverso, com Value=t3.medium
```

Custa centavos por ciclo. Em t3.medium a stack em repouso usa **1,1 GB de 3,8 GB e swap
zero** — o tamanho está certo para rodar, só não para compilar.

**Se a mudança é só de frontend, `docker compose build web` basta e o seed não roda** — as
senhas da seção 1 sobrevivem. Foi assim que os PRs #35 e #36 entraram sem rotacionar nada.

---

## 3. Ferramental de teste

**Use `tools/agent-sandbox/`, que está no repo.** A sessão anterior escreveu um runner
ad-hoc em `/projects/sandbox/` sem notar que o repo já tinha um, e daí saiu um número errado
que ficou registrado aqui: "a suíte `contract/` dava 50 falhas, 32 delas ambientais". **Com o
runner do repo não há falha nenhuma.** O `tools/agent-sandbox/README.md` documenta as
armadilhas; as que mais custam:

- **`--reuse-db` + `--nomigrations`** no `pytest.ini`: o schema vem dos modelos uma vez e o
  banco é reusado para sempre. Depois de **qualquer** mudança de modelo, `RECREATE_DB=1`, ou
  dezenas de erros `column ... does not exist` parecem regressão catastrófica.
- **A suíte não valida migração nenhuma**, pelos mesmos dois flags. Migração se valida à mão
  com `make-migration.sh` contra um banco descartável — ver a seção do item 9.
- Postgres em **5433** e Redis em **6380**; 5432 e 6379 são interceptados.
- O hook de pre-commit roda `oxlint --deny-warnings` no arquivo _staged_, **inclusive avisos
  pré-existentes**. Lintar o caminho antes de dar `git add`.

Números atuais na `preview`: **2568 testes passando** em `plane/tests`, zero falhas.

**"Zero regressão" se afirma por comparação, não por inspeção.** O padrão usado nesta sessão:
um worktree limpo da `preview` (`git worktree add ../clean-preview preview`), a mesma seleção
de testes nos dois, e o _conjunto_ de falhas comparado com `comm`. Um contador de aprovados não
serve — a suíte pode ganhar testes e perder outros no mesmo número.

**E "parece ambiental" não é medida.** Se um teste falha, rodar com e sem a mudança.

### Sabotagem, e a armadilha de reverter com git

Uma asserção de ausência sem prova de que falha quando a coisa está presente não mede nada. O
padrão: quebrar de propósito, ver o teste certo ficar vermelho, restaurar.

**Nunca reverta a sabotagem com `git checkout -- <arquivo>` se aquele arquivo tem trabalho
legítimo não commitado.** Isso apaga os dois. Aconteceu nesta sessão: a correção da
`CLIENT_REACHABLE` foi silenciosamente apagada, e o único sintoma foi uma contagem de testes
coletados que deixou de subir. Restaure por **cópia**, e guarde a cópia em `/projects/sandbox/`
— `/tmp` **não sobrevive entre invocações do bash** neste sandbox, então o restore falha e o
`&&` aborta _antes_ da verificação imprimir, deixando o arquivo sabotado com a saída dizendo
sucesso.

### Navegador

O CLI `agent-browser` **perde a sessão entre invocações**. Playwright com o chromium em disco:

```python
p.chromium.launch(executable_path="/opt/playwright/chromium-1232/chrome-linux64/chrome",
                  args=["--no-sandbox"])
```

Duas coisas que enganam na verificação visual desta aplicação:

- **`full_page=True` não resolve.** O Plane rola um contêiner interno, não o `body`, então a
  captura para no viewport. Use `scroll_into_view_if_needed()` no elemento que interessa.
- **`inner_text()` do `body` mede a página, não o campo.** Foi o que deixou o defeito do
  Solicitante passar por duas verificações: `"Marcel" in body` e `"Sem solicitante" in body`
  davam **as duas** `True`, porque o nome também aparece no feed de atividade. Só a leitura do
  DOM do próprio campo separou os casos.

---

## 4. O que foi corrigido

| PR                                                        | Conteúdo                                                                               |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| [#26](https://github.com/andresatziack/plane-lab/pull/26) | `seed_service_demo` + 17 testes: o cenário de referência semeado **pelo domínio**      |
| [#27](https://github.com/andresatziack/plane-lab/pull/27) | painel de apontamentos também no peek                                                  |
| [#28](https://github.com/andresatziack/plane-lab/pull/28) | ACL dos catálogos, admin editando apontamento, `Cliente \| ...` do portal              |
| [#29](https://github.com/andresatziack/plane-lab/pull/29) | horas legíveis, `Meu consumo` na sidebar, ícone de contorno                            |
| [#30](https://github.com/andresatziack/plane-lab/pull/30) | modal com teto relativo à janela, Solicitante _(esta metade não funcionou — ver nº 9)_ |
| [#32](https://github.com/andresatziack/plane-lab/pull/32) | **item 7**: tabela de chamados no portal + varredura de isolamento (nº 10)             |
| [#33](https://github.com/andresatziack/plane-lab/pull/33) | **item 9**: pt-BR como padrão, nos três lugares                                        |
| [#34](https://github.com/andresatziack/plane-lab/pull/34) | **item 10**: Bonfim Alimentos, Cliente Avulso sem contrato                             |
| [#35](https://github.com/andresatziack/plane-lab/pull/35) | saldo de bolsa legível (nº 11)                                                         |
| [#36](https://github.com/andresatziack/plane-lab/pull/36) | o Solicitante, de verdade (nº 9 revisado)                                              |

As causas raiz, porque o padrão entre elas é o assunto da seção 6:

1. **Painel de apontamentos ausente do peek.** `ServiceLogSection` estava montado só em
   `issue-detail/main-content.tsx`; `peek-overview/*` não tinha nenhuma referência a
   `ServiceLog`. Como o peek é o que abre ao clicar na lista, o CRUD de horas era inalcançável
   pelo caminho normal do produto. A **D64** já dizia que o peek precisa do gêmeo — a Fase 8b
   aplicou isso ao Solicitante e o painel da Fase 3 nunca recebeu.

2. **Tela de catálogos recusando todo mundo, inclusive ADMIN.** `pathnameToAccessKey` resolvia
   a chave da ACL cortando dois segmentos, e `WORKSPACE_SETTINGS_ACCESS` era indexada pelo
   `href`. O worklog é o único item com href de três segmentos, então o lookup devolvia
   `undefined` — que o guarda lê como "não pode". Não era papel insuficiente: era entrada
   inexistente, e a tela **nunca funcionou para ninguém**. Verificado na republicação: o ADMIN
   abre e vê o multiplicador 1,50; o MEMBER é recusado, e isso é a tela de settings ser
   ADMIN-only, **não** regressão — os dois foram comparados para poder afirmar isso.

3. **Admin sem botão de editar apontamento.** `canModify` era só autoria. A Fase 3 escreveu
   "por ora, apenas o autor", a Fase 7 levantou a regra no backend (`may_edit` retorna `True`
   para `can_manage_others` antes de olhar autoria) e o frontend ficou com a metade antiga. O
   endpoint `/service-member-permissions/me/` existia e **não tinha um único consumidor**.

4. **Cliente vendo `Cliente | ...`.** `IssueServiceClientProperty` resolvia o nome pela lista
   de Clientes do workspace, que é `[ADMIN, MEMBER]` justamente para um cliente não enumerar os
   outros. GUEST levava 403 e o chip caía no fallback. A linha passou a ser escondida dele.

5. **Horas com quatro decimais.** `contract_balance_statement` e `allowance_summary` devolviam
   decimal cru. Agora cada grandeza viaja em par, o decimal e o gêmeo `_display`, criados **no
   domínio**. O precedente era o `amount_display` do dinheiro. **Não terminou aqui — ver nº 11.**

6. **`Meu consumo` aparecendo depois dos outros itens.** O `.values()` de
   `ProjectViewSet.list` não trazia `service_client` nem `is_time_tracking_enabled`, e a
   sidebar decide por eles. `guest_view_all_features` foi adicionado ali quando o portal
   precisou e os dois companheiros passaram batido.

7. **Ícone fora da família.** `BarIcon` era sólido; o resto da navegação é contorno com
   `strokeWidth 1.25`.

8. **Modal saindo da tela.** `Dialog.Panel` não tinha teto de altura; o formulário tem 612px e
   `EModalPosition.TOP` acrescenta 80px em cima e embaixo, exigindo 772px. Em janela de ~620px
   o botão de salvar nascia fora **antes de qualquer interação**, e o scroll de foco levava o
   topo do painel para `y=-72`. O teto agora sai das margens do próprio `EModalPosition`
   (`max-h-[calc(100dvh-5rem)] md:max-h-[calc(100dvh-10rem)]`), com `dvh` e não `vh` porque
   `vh` ignora a barra de endereço e reintroduziria o problema no celular. **Medido na
   republicação em 620px:** painel em 80–540 dentro da janela, e o Salvar vai de `y=652` para
   `y=500` ao rolar o painel — alcançável e clicável.

9. **Solicitante exibindo "Sem solicitante" ao lado do próprio avatar.** _A causa registrada
   aqui antes estava errada, e a correção da #30 não resolvia o rótulo._

   Não é `value` ausente do `memberIds`. `MemberDropdownBase` tem `showUserDetails = false` por
   padrão, e o `getDisplayName` dele devolve o _placeholder_ **incondicionalmente** nesse caso —
   nunca chega a procurar o valor, e `memberIds` não entra na conta. A #30 corrigiu a lista de
   opções, que era um problema real (e cobre o solicitante que deixou de ser GUEST do project),
   e não podia corrigir o rótulo.

   O avatar não passa pelo mesmo portão: vem direto de `value`. Daí a inicial de alguém ao lado
   das palavras "sem solicitante", que parece store inconsistente e é prop faltando. **E o nome
   já estava na store todo esse tempo**: o `ButtonAvatars` monta aquele "M" a partir de
   `getUserDetails(value).display_name`. O avatar era o controle positivo, dentro da própria
   captura de tela.

   Corrigido na #36 com uma prop, `showUserDetails`. Nenhum outro `MemberDropdown` do app a
   passa porque todos são `*-without-text`, onde o rótulo não é renderizado, ou `multiple`, onde
   aparece a contagem: dropdown de valor único com variante de texto é combinação que este fork
   introduziu.

10. **A varredura de isolamento exemptava doze rotas em vez de três.**
    `test_service_client_isolation_app.py` existe para que uma decisão **esquecida** falhe — o
    docstring dele diz "the failure mode being guarded against is not a wrong decision; it is an
    _absent_ one". A `CLIENT_REACHABLE` casava por **substring**, e `issues/<uuid:issue_id>/`
    aparecia no **meio** de toda rota por chamado: `service-logs/`, `service-logs/preview/`,
    `service-logs/totals/`, `service-logs/batches/<id>/`, `.../author/`, `service-allowance/`,
    `service-pool/` e `service-requester/` — a superfície inteira de apontamento por work item —
    estavam fora da varredura desde que o marcador foi criado. O docstring do
    `ServiceLogViewSet` afirma "GUEST is on no endpoint here, including reads"; o teste que
    devia provar isso não olhava. **As rotas recusam de fato**, então nada vazava: a isolação
    era real e apenas não-provada — e "real mas não-provada" é indistinguível de "ausente" até
    o dia em que regride.

    Descoberto por **tropeçar nele**: a rota nova do item 7,
    `service-reports/portal/issues/`, contém `service-reports/portal/` e foi exemptada sozinha,
    sem ninguém decidir nada. Casamento agora é pelo fim da rota. Provado por sabotagem
    comparada: admitindo GUEST em `ServiceLogViewSet.list`, a varredura com casamento por fim
    fica vermelha nomeando a rota, e com o casamento por substring anterior fica **verde**.

11. **Saldo de bolsa ainda chegando como `46.0000`.** O nº 5 outra vez, em quatro sítios que a
    #29 não visitou — dois no portal do cliente e dois no dashboard interno. O payload **já
    trazia** `balance_hours_display`, o tipo o declarava, e o comentário do próprio tipo dizia
    "Raw to compute with, `_display` to render". No mesmo arquivo, poucas linhas acima, as
    linhas do extrato usavam o gêmeo corretamente. Encontrado olhando a captura de tela da
    republicação.

### O que se investigou e **não** era defeito

- **Cliente alterando Estado e Prioridade funciona.** `PATCH state_id → 204`, e ele recebe só
  `Todo, In Progress, Done, Cancelled` — Backlog fica de fora pelo allowlist, enquanto o técnico
  recebe as cinco. A D59/D60 no backend e a D64 no frontend estão corretas. O diagnóstico
  inicial de que estava quebrado veio de procurar `"State"` e `"Priority"` numa interface em
  português.
- **O modal não fecha ao clicar nos campos.** Testado com clique de mouse real em cada
  controle, arrasto com `mouseup` fora do painel, botão direito, date picker nativo e escolha de
  opção nos dois comboboxes, em três viewports. Nunca fechou.
- **MEMBER recusado na tela de catálogos.** É a ACL de workspace settings ser ADMIN-only, não a
  regressão do nº 2. Distinguido comparando com o ADMIN na mesma rota.

---

## 5. Os três itens entregues

### Item 7 — a tabela de chamados no portal ([#32](https://github.com/andresatziack/plane-lab/pull/32))

Tabela paginada ao fim da página, link para cada ticket, e o clique **na barra do gráfico** ou
num chip de competência filtrando a tabela. Modal foi descartado: esconderia a resposta
principal atrás de um gesto que ninguém descobre.

O que decidiu o desenho:

- **Os chamados são agrupados a partir dos mesmos work logs que o descritor seleciona**, não de
  uma listagem de `Issue` somada à parte. É isso que faz o `total_count` da tabela ser por
  construção igual ao `totals["issues"]` do dashboard, que é `Count("issue_id", distinct=True)`
  sobre a mesma seleção. A alternativa óbvia é exatamente como os dois números começam a
  divergir.
- **`ServicePortalBaseView`** passou a existir: a projeção do cliente e a tenancy da D67 moram
  uma vez e as duas rotas do portal herdam. `_portal_scope` devolve o filterset **já
  estreitado**, para que a ordem obrigatória não seja algo que cada rota nova precise lembrar.
- **Sem dinheiro para papel nenhum nesta rota, inclusive o Admin** — a D58 licencia valor por
  apontamento, não por chamado: um ticket metade absorvido por bolsa e metade faturado não tem
  valor único que corresponda a uma linha de fatura.
- **Os chips são clicáveis além das barras**, e não redundantemente: o gráfico de barras só
  existe quando há contrato, então um cliente Avulso tem os chips e nada mais. Verificado na
  republicação: a Patrícia tem **0 barras** e 2 chips, e o chip filtra.

### Item 9 — pt-BR como padrão ([#33](https://github.com/andresatziack/plane-lab/pull/33))

Três lugares, porque nenhum cobre os outros dois: o default de `Profile.language`, a migração
0134 que move os perfis **ainda em `en`** (uma escolha deliberada por outro idioma fica
intacta), e o `seed_service_demo` gravando explicitamente — necessário porque `_user` usa
`get_or_create`, então num re-seed o perfil já existe e o default da coluna não roda.

**A migração foi validada à mão**, porque a suíte não roda migração nenhuma. Duas armadilhas
que fazem uma validação _parecer_ ter passado sem validar nada, ambas registradas no processo:

- rodar `migrate` puro para subir os **outros** apps também aplica `db/0134`, então as linhas
  semeadas depois nunca encontram o back-fill. Ordem certa: cadeia inteira → reverter `db` para
  0133 → semear → rolar para frente;
- **Django não coloca default de `CharField` no banco**: `information_schema.column_default` é
  `NULL`, então afirmar sobre ele não prova nada a respeito do `AlterField`. O default se lê do
  `project_state` do próprio grafo de migrações, que é independente de `models.py`.

Na republicação a migração moveu **2** perfis, não 6 — os outros já tinham sido trocados à mão
na sessão anterior. O back-fill seletivo funcionando em dados reais.

**Fica em aberto:** `packages/i18n` tem `FALLBACK_LANGUAGE = "en"`, então a tela de login está
em inglês na **primeira** visita de um navegador. Depois do primeiro login o `setLanguage`
grava `localStorage.userLanguage` e a partir daí até o login fica em português. Mudar isso é
decisão de pacote compartilhado — afeta `apps/admin` e `apps/space` também.

### Item 10 — o Cliente Avulso ([#34](https://github.com/andresatziack/plane-lab/pull/34))

**Bonfim Alimentos**, projeto `BNF`, preço base R$ 240,00/h, `default_billing_type` = Avulso, e
**nenhum contrato**. Três chamados, quatro apontamentos, duas competências, R$ 1.800,00.

O multiplicador vira preço, com uma taxa configurada e um multiplicador configurado:

| Tipo de Hora        | mult. | apontadas | equivalentes | valor         |
| ------------------- | ----- | --------- | ------------ | ------------- |
| Horário comercial   | 1.00  | 2,0000    | 2,0000       | **R$ 480,00** |
| Fora do expediente  | 1.50  | 1,0000    | 1,5000       | **R$ 360,00** |
| Domingos e feriados | 2.00  | 2,0000    | 4,0000       | **R$ 960,00** |

A linha do meio é 1h de trabalho faturada como R$ 360,00 — a frase do contexto mestre, em
dados, pela primeira vez na série. Mais uma Garantia de R$ 0,00: sem pool, zero só pode
significar que a rota recusou cobrar.

**A ausência de contrato é a feature.** `_bonfim_scenario` não chama `resolve_period` e não cria
contrato, e o comentário no código diz que uma mudança futura que precise de período está
errada. Isto é o que dá exemplo navegável ao `shape == "standalone"`, ao `revenue_series` e à
origem **`standalone_log`** da D36 — os três eram alcançáveis só por código.

Um erro meu que o teste pegou, e que vale registrar: eu esperava
`applied_hour_rate == 360.00` na linha de 1,5x. **Está errado.** `applied_hour_rate` guarda a
base snapshotada (R$ 240,00 nas três linhas) e o multiplicador **já entrou pelas horas**;
`ServiceRateBasis.BASE_MULTIPLIER` registra qual fórmula produziu o valor, porque com
multiplicador 1.00 as duas dão o mesmo produto e dividir de volta é ambíguo. Se eu tivesse
afirmado só os valores, os três passariam e a asserção errada nunca apareceria.

---

## 6. Dívidas abertas

- **`attention-tab.tsx` ainda mostra `12.0000`.** O nº 11 pela metade: o payload do painel de
  atenção **não tem gêmeo** — `TServicePeriodAlertEntry` e `TServiceAllowanceAlertEntry`
  declaram só o decimal cru, e `workspace_alert_panel` / `workspace_allowance_alerts` não
  computam `_display`. É mudança de backend, no padrão que a #29 usou para
  `contract_balance_statement`.
- **Nada impede o próximo `MemberDropdown` de valor único com texto de nascer com o nº 9.** O
  guarda natural seria o default de `showUserDetails` derivar de `BUTTON_VARIANTS_WITH_TEXT` em
  vez de ser `false` — mudança no componente do core, que afeta todos os chamadores.
- **`ModalCore` é usado por 84 modais** e só o de apontamento foi verificado. O
  `overflow-y-auto` faz do painel um contêiner de rolagem, que recortaria um filho posicionado
  absolutamente. Nesta feature os dropdowns vão para portal fora do painel; os outros 83
  merecem o olhar de quem os conhece.
- **Gate por campo nas list e spreadsheet views.** A D64 chegou ao detalhe e ao peek; em
  `base-list-root.tsx:95-98` e `base-spreadsheet-root.tsx:64-67` o gate ainda é
  `[ADMIN, MEMBER]`, então a edição inline de estado e prioridade não funciona para o cliente.
- **A janela do portal são as últimas 12 competências**, fixa. Contrato mais longo tem meses
  que o cliente não alcança desta tela, agora inclusive na tabela de chamados.
- **`ServiceRateBasis.ABSOLUTE_OVERRIDE` segue sem exemplo semeado.** A segunda fórmula de
  precificação é coberta por teste unitário e não por navegação; acrescentá-la exigiria uma taxa
  absoluta no catálogo compartilhado pelos quatro Clientes.
- **GUEST abrir chamado** segue fora de escopo por decisão do usuário. Hoje o caminho é o
  intake (Triage) ou o técnico abrindo com Solicitante (D62).
- **`apps/web` não tem infraestrutura de teste.** Nenhum arquivo de teste, nenhum vitest — só
  `apps/live` e `packages/codemods` têm. Os PRs #27 a #36 foram verificados por typecheck, lint
  e navegação com captura de tela. Vale dizer o que um teste pegaria e o que não: os nº 9 e 11
  um teste de renderização pegaria; um teste de API não, porque a API sempre respondeu certo —
  `GET .../service-requester/` devolvia o Marcel enquanto a tela dizia que não havia
  solicitante. **Três dos doze defeitos estavam entre o payload e o pixel.**

  Se houver interesse, dois guardas baratos e no padrão do repo
  (`packages/i18n/scripts/sync-check.ts` rodado em CI):
  1. **paridade de montagem entre `issue-detail` e `peek-overview`** — codificaria a D64 e
     pegaria a próxima seção montada só no detalhe (nº 1);
  2. **paridade de `_display`** — afirmar que nenhum campo `*_hours` é renderizado sem o gêmeo.
     O nº 5 apareceu três vezes, em consumidores diferentes.

---

## 7. O achado que vale mais que as correções

**Nenhum dos doze defeitos seria pego pelos critérios de aceite das nove fases.** Não por
descuido na escrita deles, mas por causa da forma que eles têm.

Os critérios descrevem **telas**: "o campo Solicitante existe no detalhe e no peek", "o painel
de apontamentos mostra as três grandezas", "o dashboard discrimina garantia e cortesia". Todos
verdadeiros, todos verificados, e o produto ainda assim tinha uma tela que ninguém alcançava,
um CRUD invisível pelo caminho normal de navegação, um admin sem botões, a escala do banco
aparecendo na cara do cliente, e um campo afirmando que não havia solicitante ao lado do avatar
do solicitante.

O que os defeitos têm em comum é que aparecem **entre** as coisas: no payload leve que a
sidebar usa e o detalhe não, na chave de ACL que o guarda calcula e a constante não, no
componente montado num painel e não no gêmeo, na altura do modal contra a altura da janela, no
gêmeo `_display` que um consumidor usa e o vizinho não, na prop que o componente do core espera
e o chamador não passa. Verificação por tela não cruza fronteiras; uso cruza.

E o nº 10 é a versão mais desconfortável disso: **o guarda que existia para pegar exatamente
esta classe de erro estava, ele mesmo, com o mesmo erro.** Casava por substring, exemptava
quatro vezes mais rotas do que devia, e passou nove fases verde. Um teste que afirma uma
ausência é código como qualquer outro, e precisa da mesma prova que ele exige dos outros.

Três consequências práticas:

1. **Critérios de aceite deveriam descrever jornadas**, não telas. "Abrir um chamado a partir
   da lista de work items e registrar 1h" atravessa lista → peek → modal → salvar, e teria pego
   três dos doze. "Como admin, ajustar o multiplicador de um Tipo de Hora" teria pego o nº 2 na
   hora.
2. **Toda asserção de ausência precisa de controle positivo.** Eu errei o diagnóstico cinco
   vezes nesta fase, sempre do mesmo jeito: `inner_text` no lugar de visibilidade real, um regex
   de `"delegate"` que casou com a linha de outro membro, rótulos em inglês numa interface em
   português, `"R$" in body` numa aba que eu nunca tinha aberto, e `"Marcel" in body` casando
   com o feed de atividade enquanto o campo estava errado. Nos cinco casos "não encontrei" foi
   lido como "não existe", ou "encontrei" como "está certo". O que corrigiu foi sempre comparar
   com um caso que deveria dar o resultado oposto — o técnico ao lado do admin, o ADMIN ao lado
   do MEMBER na mesma rota, o chamado sem solicitante ao lado do que tem — e olhar a captura de
   tela em vez de confiar na consulta.
3. **A correção de um defeito precisa procurar os irmãos dele.** O nº 5 foi corrigido em dois
   consumidores e voltou num terceiro e num quarto. O nº 1 é literalmente a D64 aplicada a um
   componente e não ao outro. Quando a causa é "este lugar e aquele deviam concordar", a
   pergunta seguinte é sempre: **quantos lugares são, na verdade?**
