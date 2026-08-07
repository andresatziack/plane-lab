# Fase 7 — Permissões de apontamento, delegação e auditoria

> O contexto mestre (`.kiro/steering/worklog-contexto.md`) é incluído
> automaticamente neste repositório. Depende da Fase 3.

## Objetivo

Controlar quem pode criar, editar e excluir apontamentos, permitir apontar em
nome de outro usuário, e garantir trilha de auditoria.

## O que o repositório oferece (já auditado)

Não precisa investigar — os fatos estão em `docs/worklog/ACHADOS-DO-CODIGO.md`:

- Existem **3 papéis**: `ADMIN=20`, `MEMBER=15`, `GUEST=5`
  (`plane/db/models/project.py:21`).
- **Não existe papel customizado, permission scheme nem permissão granular.**
  Autorização é comparação numérica de papel, feita pelo decorator
  `allow_permission(allowed_roles, level, creator, model)` em
  `plane/app/permissions/base.py:19`.
- O parâmetro `creator=True` é o mecanismo nativo de "só o autor" e já é usado
  assim em `IssueComment` (`app/views/issue/comment.py:109`).

### Como expressar as permissões desta fase

> **Corrigido depois da implementação.** A sugestão original abaixo era usar
> `allow_permission(creator=True, model=...)` para "só o autor". **Não foi seguida, e o
> motivo está duas linhas adiante no próprio briefing:** o `creator=True` libera a view
> inteira para o criador ignorando o papel, e é o bypass que a auditoria do fork sinalizou
> como problema de segurança existente. Reusá-lo espalharia o bug.
>
> Além disso ele chaveia em `created_by`, e a partir da delegação desta fase `created_by` é
> **quem digitou**, não quem executou. A R8 faz do autor o dono do registro, então um
> apontamento delegado ficaria sob controle de quem apenas o digitou.
>
> O que foi implementado: a autoridade resolve em
> `plane.utils.service_permission.resolve_capabilities`, chaveando em `author_id`, e
> `allow_permission` continua fazendo o que faz bem — barrar GUEST e conferir participação.
> As duas camadas compõem; a segunda não foi substituída.

- Criar apontamento → `allow_permission([ROLE.ADMIN, ROLE.MEMBER])` — **mantido**
- GUEST nunca entra em nenhuma rota de apontamento — **mantido, e reforçado**: a resolução
  de capacidades também devolve nada para GUEST, então as duas proteções são redundantes
- Editar/excluir → `validate_can_change(log, capabilities)`, **não** `creator=True`

**Atenção ao bypass do `creator=True`** (`permissions/base.py:24-40`): ele libera
a view inteira quando o usuário é o criador, exigindo apenas que seja
`WorkspaceMember` ativo — ignorando o papel. Isso significa que, sem validação
adicional, o autor de um apontamento poderia alterar **qualquer** campo dele,
inclusive os que mudam débito de pool e valor. Trate isso explicitamente na
seção 6.

### As três capacidades elevadas

Como não há permissões granulares, as capacidades da seção 2 não podem ser
"concedidas" por papel. Proponha e me apresente a alternativa de menor
complexidade antes de implementar. Duas opções plausíveis:

- **(a)** Um modelo pequeno de concessão por usuário e workspace (ex.:
  `WorklogPermission` com flags booleanas), checado em um decorator próprio que
  componha com o `allow_permission` existente
- **(b)** Reutilizar `WorkspaceMember` adicionando as flags lá

Prefira o caminho que **não** altere a semântica dos papéis existentes do Plane,
para não quebrar comportamento do core nem complicar merges com o upstream.

## Escopo

### 1. Regras base de permissão

| Ação | Autor do apontamento | Outro técnico | Admin |
|---|---|---|---|
| Criar apontamento próprio | sim | sim | sim |
| Ver apontamentos | sim | sim | sim |
| Editar apontamento próprio | sim | não | sim |
| Excluir apontamento próprio | sim | não | sim |
| Editar/excluir de qualquer um | — | apenas se concedido | sim |
| Criar em nome de outro usuário | — | apenas se concedido | sim |
| Alterar o autor de um apontamento | — | apenas se concedido | sim |

### 2. Permissões concedíveis

As três capacidades elevadas devem ser **concedíveis a usuários específicos**,
sem precisar torná-los Admin do workspace:
- gerenciar apontamentos de terceiros
- apontar em nome de outro usuário
- reatribuir o autor de um apontamento

Implementar como permissões no sistema de papéis do Plane, conforme a
investigação acima.

### 3. Delegação (apontar por outro)

No formulário de apontamento, quem tem a permissão vê um campo **"Autor"** que
permite selecionar outro membro do workspace. Nesse caso:
- `autor` = o técnico que executou o trabalho
- `criador` = quem registrou o apontamento
- ambos exibidos na lista, de forma que fique claro que houve delegação
  (ex.: "João, registrado por Maria")

### 4. Reatribuição

Admin ou usuário com a permissão pode alterar o autor de um apontamento
existente. A alteração fica registrada na auditoria com autor anterior, novo
autor, quem alterou e quando.

### 5. Auditoria

Trilha completa e imutável de:
- criação, com todos os valores iniciais
- cada alteração, com campo, valor anterior, valor novo, quem e quando
- exclusão, preservando o conteúdo do apontamento excluído

A trilha deve ser visível na UI para Admin. Apontamento excluído não deve
desaparecer do histórico contábil — considerar exclusão lógica.

### 6. Interação com débito de pool

Alterar o autor não muda débito. Alterar tempo, tipo de hora, tipo de
atendimento ou data **muda** débito e valor, e deve disparar o estorno e
reaplicação da Fase 4. Registrar isso na auditoria.

Apontamento em período fechado só é editável por Admin, com auditoria.

## Critérios de aceite

| # | Critério | Status | Onde é provado |
| --- | --- | --- | --- |
| 1 | Técnico A não consegue editar nem excluir apontamento do técnico B | **atendido** | `TestBaseAuthority::test_a_technician_cannot_edit_another_technicians_log` e `..._delete_...` |
| 2 | Técnico A edita e exclui os próprios apontamentos | **atendido** | `TestBaseAuthority::test_the_author_edits_their_own`, `..._deletes_their_own` |
| 3 | Admin edita e exclui apontamento de qualquer um | **atendido** | `TestBaseAuthority::test_an_admin_edits_anybodys_log`, `..._deletes_...` |
| 4 | Admin concede gerenciar apontamentos de terceiros ao técnico C, e C passa a poder editar de qualquer técnico — sem ser Admin | **atendido** | `TestGrantedManageOthers`, incluindo o "antes" da concessão e a asserção de que o papel de C continua 15 |
| 5 | Usuário com permissão de delegação registra apontamento com autor = técnico B, e a lista mostra ambos os nomes | **atendido** | `TestDelegation::test_a_delegated_log_carries_both_names` |
| 6 | Alterar o autor é registrado na auditoria e não altera saldo de pool | **atendido** | `TestReassignment::test_reassignment_is_audited_with_all_four_facts` e `TestEditsThatMoveMoney::test_reassignment_writes_no_ledger_row_even_where_a_pool_exists` |
| 7 | Alterar o tempo é registrado na auditoria e ajusta o saldo | **atendido** | `TestEditsThatMoveMoney::test_changing_the_duration_is_audited_and_adjusts_the_pool_balance` |
| 8 | Toda tentativa negada retorna erro de autorização adequado | **atendido, com a convenção fixada** | toda classe de recusa afirma o código; a convenção 403/400 é a **D47** |
| 9 | Apontamento excluído permanece recuperável no histórico de auditoria | **atendido** | `TestDeletionRemainsInHistory` |

### O que foi além do briefing

- **GUEST não pode sustentar nenhuma das três capacidades**, em três camadas
  (`TestGuestsCannotHoldCapabilities`). O briefing não pedia; sem isso, um usuário de
  cliente com `can_delegate` atribuiria trabalho a um técnico. Ver **D45**.
- **Período fechado é ADMIN-only inclusive para reatribuição**, com o limite documentado
  (`TestClosedPeriodIsAdminOnly`). Ver **D46**.
- **A recusa de período fechado passou a dizer o que fazer**, não só o que não pode:
  `remediation: RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE`. Ver **D42**, reescrita como
  pergunta.

### Dívida que esta fase cria, por fase que a herda

| Fase | Dívida |
| --- | --- |
| **8** | A UI das concessões não existe. A API está pronta (`GET`/`PATCH .../service-member-permissions/`, mais `.../me/` para o formulário decidir se mostra o campo "Autor"), e a Fase 8 já mexe na tela de membros do workspace |
| **8** | O campo "Autor" no formulário de apontamento é backend-only até aqui. `was_delegated` e `.../me/` existem para a tela consumir |
| **A definir** | A pergunta da **D42**: reabrir período ou lançar ajuste na competência aberta. Enquanto não respondida, um ADMIN não altera horas de apontamento em período fechado |

## Entregar

Primeiro a proposta de como expressar as três capacidades elevadas (opção a, b ou
outra), com a justificativa. Depois implementação e testes de autorização,
incluindo os casos negativos.

Usar `pytest` com as factories de `plane/tests/factories.py`, seguindo o padrão de
`plane/tests/contract/app/`.
