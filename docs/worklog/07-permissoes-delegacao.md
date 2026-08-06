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

Use o mecanismo existente, não construa um paralelo:

- Editar/excluir apontamento próprio → `allow_permission([ROLE.ADMIN],
  creator=True, model=Worklog)`, exatamente o padrão de `IssueComment`
- Criar apontamento → `allow_permission([ROLE.ADMIN, ROLE.MEMBER])`
- GUEST nunca entra em nenhuma rota de apontamento

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

1. Técnico A não consegue editar nem excluir apontamento do técnico B
2. Técnico A edita e exclui os próprios apontamentos
3. Admin edita e exclui apontamento de qualquer um
4. Admin concede a permissão de gerenciar apontamentos de terceiros ao técnico
   C, e C passa a poder editar apontamentos de qualquer técnico — sem ser Admin
5. Usuário com permissão de delegação registra apontamento com autor = técnico
   B, e a lista mostra ambos os nomes
6. Alterar o autor de um apontamento é registrado na auditoria e não altera
   saldo de pool
7. Alterar o tempo de um apontamento é registrado na auditoria e ajusta o saldo
8. Toda tentativa negada retorna erro de autorização adequado, na API e na UI
9. Apontamento excluído permanece recuperável no histórico de auditoria

## Entregar

Primeiro a proposta de como expressar as três capacidades elevadas (opção a, b ou
outra), com a justificativa. Depois implementação e testes de autorização,
incluindo os casos negativos.

Usar `pytest` com as factories de `plane/tests/factories.py`, seguindo o padrão de
`plane/tests/contract/app/`.
