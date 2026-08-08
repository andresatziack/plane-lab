# Fase 10 — O ambiente de demonstração, e o que ele revelou

> **Estado:** em andamento. Três PRs mergeados, três itens pendentes, ambiente publicado
> mas rodando imagem anterior aos três PRs.

A série fechou em nove fases com 2505 testes passando. Esta fase não acrescentou
funcionalidade: publicou o produto num host acessível e o usou. Em uma sessão de uso
apareceram **nove defeitos**, e nenhum deles seria pego pelos critérios de aceite das fases
anteriores.

Isso é o achado que importa mais do que qualquer correção individual, e está na seção
final.

---

## 1. O ambiente

**https://planedev.satziack.com** — fork completo, branch `preview`, cenário semeado.

Não é o `plane.satziack.com`. São instância, VPC, IP e certificado próprios, e o deploy
antigo (`i-0a3fe8f39a8d92d01`, EIP `50.16.32.183`) nunca foi tocado.

### Credenciais

Senha de todos rotaciona a cada execução do seed. Estas valem enquanto o seed não rodar de
novo:

| Papel | E-mail | Senha |
|---|---|---|
| Admin do workspace e da instância | `admin@planedev.satziack.com` | `betE8dxyfNJAubUwrTVj-4572` |
| Técnico MEMBER, com `can_delegate` | `tecnico@planedev.satziack.com` | `mu6zwQ8yt9ZuYYzTW7HW-3489` |
| Técnico MEMBER sem concessão | `tecnico2@planedev.satziack.com` | `MVpXyKrEN45EdSp7t45R-9933` |
| Marcel — GUEST de Marubeni **e** Terlogs | `marcel@planedev.satziack.com` | `uTzHFnM37bNKHyvzaSGj-3553` |
| Adriano — GUEST só de Terlogs | `adriano@planedev.satziack.com` | `NQAru926mZfmXH6qd69H-2246` |
| Rafael — 2º GUEST da Marubeni | `rafael@planedev.satziack.com` | `bS7k7Aey68XauENCBbQB-3685` |

### Rotas úteis

Workspace `worklog-demo`. A interface está em **português** (o idioma foi trocado no perfil;
o padrão do modelo ainda é `en` — ver item 9 pendente).

- Consumo da Marubeni: `/worklog-demo/projects/d308dff3-2c81-469b-b3c7-aa18e73e0b48/service-consumption/`
- Consumo da Terlogs: `/worklog-demo/projects/c791da04-454e-4ec8-9774-bbe85ee51db3/service-consumption/`
- Chamado com multiplicador 1.5: `/worklog-demo/browse/MRB-3/`
- Garantia e cortesia: `/worklog-demo/browse/MRB-4/` e `/worklog-demo/browse/MRB-5/`
- Membros (célula de concessões): `/worklog-demo/settings/members/`
- Clientes (atribuição em massa): `/worklog-demo/settings/service-clients/`
- Catálogos: `/worklog-demo/settings/worklog/hour-types/`

### Infraestrutura

Tudo com a tag `Project=planedev-worklog`, e a instância e o volume também com
`auto-delete=no`.

| Recurso | Id |
|---|---|
| Instância | `i-049f2527927bf4304` (t3.medium, 60 GB gp3) |
| EIP | `eipalloc-089f5750deadee29a` → `98.86.24.174` |
| VPC / subnet | `vpc-0eded5783a4fcff14` / `subnet-056cec9d4a1801e42` |
| IGW / route table | `igw-04c2f86eb1a938098` / `rtb-04581cc23312e151c` |
| Security group | `sg-07e4c5287f7135b53` — 80 e 443 em `0.0.0.0/0`, **sem SSH** |
| IAM | `planedev-worklog-ssm-role` / `planedev-worklog-profile` |
| DNS | `planedev.satziack.com` na zona `Z0459592WJKL7XKUB2OK` |

TLS é Let's Encrypt no Caddy do próprio compose, não ALB: custa zero e o `Caddyfile.ce` já
fazia. Renovação em ~60 dias precisa da porta 80 aberta ao mundo, o que já é o caso.

**Custo:** ≈ US$ 39/mês ligada (t3.medium 30,37 + 60 GB gp3 4,80 + IPv4 3,65), ≈ US$ 8,45
parada.

```bash
aws ec2 stop-instances  --instance-ids i-049f2527927bf4304 --region us-east-1
aws ec2 start-instances --instance-ids i-049f2527927bf4304 --region us-east-1
/projects/sandbox/planedev-destroy.sh   # derruba tudo e prova que o deploy antigo ficou intacto
```

### Operação

Sem SSH por decisão: acesso por SSM.

```bash
/projects/sandbox/ssm.sh 'comando'          # shell na caixa
/opt/planedev/seed.sh                       # semeia (DEBUG=1 em container efêmero)
/opt/planedev/logs.sh 80 api                # logs
```

**Diferenças de produção real, deliberadas:** `DEBUG=0` nos serviços e `DEBUG=1` apenas no
container efêmero do seed; sem SMTP (`ENABLE_SMTP=0`, por isso as contas nascem com senha);
uploads em MinIO no EBS da instância, sem durabilidade; **sem backup de nada**, inclusive
Postgres; tudo numa caixa só. Acesso travado na aplicação: `ENABLE_SIGNUP=0`,
`ENABLE_MAGIC_LINK_LOGIN=0`, `DISABLE_WORKSPACE_CREATION=1`.

---

## 2. Duas armadilhas de rebuild que custaram tempo

**`docker compose run` usa a IMAGEM, não a árvore de trabalho.** A api tem `COPY plane
plane/` embutido no build. Um `git pull` no host não muda o código dentro do container.
Depois de trazer código novo é obrigatório:

```bash
docker compose build api worker beat-worker migrator   # mudanças de backend
docker compose build web                                # mudanças de frontend
docker compose up -d <serviços>
```

**O build do frontend não cabe em 4 GB.** O procedimento é subir a instância, buildar e
descer de volta:

```bash
aws ec2 stop-instances --instance-ids i-049f2527927bf4304 --region us-east-1
aws ec2 wait instance-stopped --instance-ids i-049f2527927bf4304 --region us-east-1
aws ec2 modify-instance-attribute --instance-id i-049f2527927bf4304 --instance-type Value=t3.xlarge --region us-east-1
aws ec2 start-instances --instance-ids i-049f2527927bf4304 --region us-east-1
# ... build ...
# e o inverso, com Value=t3.medium
```

Custa centavos por ciclo. Em t3.medium a stack em repouso usa **1,4 GB de 3,8 GB e swap
zero**, então o tamanho está certo para rodar — só não para compilar.

---

## 3. Ferramental de teste no sandbox

`/projects/sandbox/run-api-tests.sh` sobe Postgres **e Redis** e exporta o que a suíte
precisa. Três coisas foram descobertas pelo caminho e estão dentro do script:

- **Redis é obrigatório para `contract/`**: o throttle do DRF passa por `django_redis`, e sem
  ele endpoints throttled respondem 500 em vez do status real.
- **`WEB_URL`/`APP_BASE_URL` são obrigatórias**: `base_host` levanta `ImproperlyConfigured`, e
  como o caminho de criação de apontamento a chama para gravar a origem da atividade, **todo**
  teste que cria um apontamento responde 500. Isso lê exatamente como regressão no código sob
  teste e não é.
- **Container morre junto com a invocação do bash** neste sandbox, e `-p` rootless não
  funciona: o script usa `--network host` e faz tudo numa só chamada.

Sem essas três, a suíte `contract/` dava **153 falhas**. Com elas, 50 — e as 50 restantes
foram provadas ambientais rodando os mesmos arquivos **com e sem** as mudanças: 32 falhas
idênticas nos dois casos. **É assim que se afirma "zero regressão" aqui**, não por inspeção.

Navegador: o CLI `agent-browser` **perde a sessão entre invocações**. Use Playwright com o
chromium que já está em disco:

```python
p.chromium.launch(executable_path="/opt/playwright/chromium-1232/chrome-linux64/chrome",
                  args=["--no-sandbox"])
```

---

## 4. O que foi corrigido

| PR | Conteúdo |
|---|---|
| [#26](https://github.com/andresatziack/plane-lab/pull/26) | `seed_service_demo` + 17 testes: o cenário de referência semeado **pelo domínio** |
| [#27](https://github.com/andresatziack/plane-lab/pull/27) | painel de apontamentos também no peek |
| [#28](https://github.com/andresatziack/plane-lab/pull/28) | ACL dos catálogos, admin editando apontamento, `Cliente \| ...` do portal |
| [#29](https://github.com/andresatziack/plane-lab/pull/29) | horas legíveis, `Meu consumo` na sidebar, ícone de contorno |
| [#30](https://github.com/andresatziack/plane-lab/pull/30) | modal com teto relativo à janela, Solicitante |

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
   inexistente, e a tela **nunca funcionou para ninguém**.

3. **Admin sem botão de editar apontamento.** `canModify` era só autoria. A Fase 3 escreveu
   "por ora, apenas o autor", a Fase 7 levantou a regra no backend (`may_edit` retorna `True`
   para `can_manage_others` antes de olhar autoria) e o frontend ficou com a metade antiga. O
   endpoint `/service-member-permissions/me/` existia e **não tinha um único consumidor**.

4. **Cliente vendo `Cliente | ...`.** `IssueServiceClientProperty` resolvia o nome pela lista
   de Clientes do workspace, que é `[ADMIN, MEMBER]` justamente para um cliente não enumerar
   os outros. GUEST levava 403 e o chip caía no fallback. A linha passou a ser escondida dele.

5. **Horas com quatro decimais.** `contract_balance_statement` e `allowance_summary` devolviam
   decimal cru. Agora cada grandeza viaja em par, o decimal e o gêmeo `_display`, criados **no
   domínio** — são três consumidores, e deixar a formatação para cada tela é como `10.0000`
   chegou ao cliente. O precedente era o `amount_display` do dinheiro.

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
   `vh` ignora a barra de endereço e reintroduziria o problema no celular.

9. **Solicitante exibindo "Sem solicitante".** `MemberDropdown` resolve o rótulo procurando
   `value` dentro de `memberIds`; valor ausente da lista renderiza o *placeholder*, que diz
   exatamente isso. `guestIds` carrega assíncrono e em `/browse/` os membros ainda não vieram.

### O que se investigou e **não** era defeito

- **Cliente alterando Estado e Prioridade funciona.** `PATCH state_id → 204`, e ele recebe só
  `Todo, In Progress, Done, Cancelled` — Backlog fica de fora pelo allowlist, enquanto o
  técnico recebe as cinco. A D59/D60 no backend e a D64 no frontend estão corretas. O
  diagnóstico inicial de que estava quebrado veio de procurar `"State"` e `"Priority"` numa
  interface em português.
- **O modal não fecha ao clicar nos campos.** Testado com clique de mouse real em cada
  controle, arrasto com `mouseup` fora do painel, botão direito, date picker nativo e escolha
  de opção nos dois comboboxes, em três viewports. Nunca fechou. A lista dos comboboxes
  renderiza fora do `Dialog.Panel` — a pré-condição da hipótese existia — mas o headlessui
  trata o portal corretamente.

---

## 5. O que falta

### Item 7 — Lista de chamados no dashboard do cliente *(o maior)*

Hoje o cliente vê totais, gráfico por competência, tabela de competências, quebra por tipo de
hora, não faturável discriminado e bolsas ativas — e **nenhuma lista dos chamados** que
produziram aqueles números. Decidido com o usuário: **tabela paginada ao fim da página, com
link para cada ticket, e o clique na barra do gráfico filtrando a tabela**. Modal foi
descartado: esconde informação atrás de um gesto que ninguém descobre.

Precisa de endpoint. Pontos de partida: `ServiceClientPortalReportEndpoint`
(`apps/api/plane/app/views/service_reports/base.py:465`), o irmão interno que já pagina work
logs de um bucket (`base.py:416`) e `issue_client_totals` em
`plane/utils/service_portal.py:452`. A projeção do R11 é obrigatória — sem dinheiro e sem
`logged_hours` — e a tenancy é a da D67: um Cliente por vez, `project_ids` com exatamente um.
Frontend em `apps/web/core/components/service-reports/portal-consumption-dashboard.tsx`.

### Item 9 — pt-BR como idioma padrão

Aprovado nos três lugares: default do modelo (`Profile.language`, hoje `"en"`, em
`plane/db/models/user.py:250`) com migration, atualização dos perfis existentes, e o
`seed_service_demo` criando já em pt-BR. O locale `pt-BR` existe e é completo (46 KB de
`workspace-settings.json`, maior que o `en`).

### Item 10 — Seed com Cliente Avulso sem contrato

Os três Clientes do cenário têm contrato, então o caminho **Avulso com dashboard em reais**
não tem exemplo. Acrescentar um quarto Cliente **sem contrato nenhum**, com tabela de preço e
apontamentos Avulso. Mexe no `seed_service_demo`, e **rodar o seed rotaciona as senhas** — a
tabela da seção 1 precisa ser reescrita depois.

### Dívidas abertas

- **`ModalCore` é usado por 84 modais** e só o de apontamento foi verificado. O
  `overflow-y-auto` faz do painel um contêiner de rolagem, que recortaria um filho posicionado
  absolutamente. Nos formulários desta feature os dropdowns vão para portal fora do painel,
  mas os outros 83 merecem o olhar de quem os conhece.
- **Gate por campo nas list e spreadsheet views.** A D64 chegou ao detalhe e ao peek; em
  `base-list-root.tsx:95-98` e `base-spreadsheet-root.tsx:64-67` o gate ainda é
  `[ADMIN, MEMBER]`, então a edição inline de estado e prioridade não funciona para o cliente.
- **GUEST abrir chamado** segue fora de escopo por decisão do usuário. Hoje o caminho é o
  intake (Triage) ou o técnico abrindo com Solicitante (D62).
- **`apps/web` não tem infraestrutura de teste.** Nenhum arquivo de teste, nenhum vitest — só
  `apps/live` e `packages/codemods` têm. Os PRs #27 a #30 foram verificados por typecheck, lint
  e navegação com captura de tela. Criar a primeira suíte de frontend do monorepo é decisão do
  mantenedor. Se houver interesse, o guarda barato e no padrão do repo
  (`packages/i18n/scripts/sync-check.ts` rodado em CI) seria um script afirmando **paridade de
  montagem entre `issue-detail` e `peek-overview`** — codificaria a D64 e pegaria a próxima
  seção montada só no detalhe.

### Republicação

A instância roda a imagem **anterior aos PRs #28, #29 e #30**. Depois do item 10: resize para
t3.xlarge, `docker compose build web api worker beat-worker migrator`, resize de volta, seed,
e verificação com print nos dois papéis.

---

## 6. O achado que vale mais que as correções

**Nenhum dos nove defeitos seria pego pelos critérios de aceite das nove fases.** Não por
descuido na escrita deles, mas por causa da forma que eles têm.

Os critérios descrevem **telas**: "o campo Solicitante existe no detalhe e no peek", "o painel
de apontamentos mostra as três grandezas", "o dashboard discrimina garantia e cortesia". Todos
verdadeiros, todos verificados, e o produto ainda assim tinha uma tela que ninguém alcançava,
um CRUD invisível pelo caminho normal de navegação, um admin sem botões e a escala do banco
aparecendo na cara do cliente.

O que os defeitos têm em comum é que aparecem **entre** as telas: no payload leve que a
sidebar usa e o detalhe não, na chave de ACL que o guarda calcula e a constante não, no
componente montado num painel e não no gêmeo, na altura do modal contra a altura da janela.
Verificação por tela não cruza fronteiras; uso cruza.

Duas consequências práticas:

1. **Critérios de aceite deveriam descrever jornadas**, não telas. "Abrir um chamado a partir
   da lista de work items e registrar 1h" atravessa lista → peek → modal → salvar, e teria
   pego três dos nove. "Como admin, ajustar o multiplicador de um Tipo de Hora" teria pego o
   primeiro na hora.
2. **Toda asserção de ausência precisa de controle positivo.** Eu errei o diagnóstico três
   vezes nesta fase, sempre do mesmo jeito: `inner_text` no lugar de visibilidade real, um
   regex de `"delegate"` que casou com a linha de outro membro, e rótulos em inglês numa
   interface em português. Nos três casos "não encontrei" foi lido como "não existe". O que
   corrigiu foi sempre a mesma coisa: comparar com um caso que deveria dar o resultado
   oposto — o técnico ao lado do admin, o valor concedido ao lado do revogado — e olhar a
   captura de tela em vez de confiar na consulta.
