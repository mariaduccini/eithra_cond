# Eithra V3 — Decisões Arquiteturais

> Registro vivo das decisões **realmente aprovadas**. Este arquivo não descreve
> propostas: apenas decisões já fixadas. Decisões pendentes de aprovação ficam nos
> documentos de fase correspondentes (ex.:
> `fase_1a_balancos_decisoes.md`).

## Como usar

- Cada decisão tem um identificador, um status e uma justificativa curta.
- Uma decisão só entra aqui quando está aprovada e é estável.
- Propostas ainda em avaliação **não** entram neste arquivo.

## Fase 0 — Fundação

### A1 — `accounts.User` sem papel global — APROVADA
O papel pertence ao vínculo com o condomínio, nunca ao usuário. A cardinalidade
`User ↔ Condominio` é N:N via `AcessoUsuarioCondominio`.

### A2 — Autorização centralizada no backend — APROVADA
Toda autorização passa por `apps/condominios/acesso.py` (`tem_acesso_ativo`),
* fail-closed*. A interface não é controle de acesso.

### A3 — Registro não autorizado responde 404 — APROVADA
Para não revelar a existência do dado. 403 apenas quando a existência é legítima.

### A4 — PostgreSQL obrigatório em DEV, sem fallback para SQLite — APROVADA
`DATABASE_URL` obrigatória; ausência causa falha explícita.

### A5 — Sneat + Bootstrap 5 + Boxicons como base — APROVADA
Identidade Eithra via tokens (`--eithra-primary: #153F46`). Sem design system novo.
Sem componentes demo do template.

### A6 — `X_FRAME_OPTIONS = "DENY"` — APROVADA

### A7 — `SECURE_HSTS_PRELOAD = False` — APROVADA
Mantido como `False` até decisão consciente de preload no domínio definitivo.

## Fase 1A — Balanços Mensais

### B1 — `Condominio` é o contexto suficiente de prédio — APROVADA
Não se cria estrutura predial (`Unidade`, `Bloco`, `Torre`, `Pavimento`, `Área`,
`Equipamento`, `SistemaPredial`) por antecipação.

### B2 — Versionar, nunca sobrescrever — APROVADA
Substituição gera nova versão; a anterior é preservada e marcada `SUBSTITUIDO`,
com documento, histórico, autor e motivo.

### B3 — Documento original em storage externo privado — APROVADA
Nunca no filesystem da Vercel. Acesso apenas por view autenticada + signed URL
temporária. Nenhum arquivo sensível acessível por URL pública direta.

### B4 — Provenance obrigatória — APROVADA
Todo dado relevante é rastreável (documento, competência, página/seção, automático
× corrigido, valor original × validado), sem exposição na interface principal.

### B5 — `FornecedorCandidato` não é model — APROVADA
É conceito de extração/reconciliação. Nome do documento nunca vira fornecedor
cadastrado automaticamente.

### B6 — Publicação nunca automática — APROVADA
`VALIDADO` → preparar resumo → revisão da gestão → publicar. Controle editorial
sempre humano.

### B7 — Projeção do morador restritiva por padrão — APROVADA
Somente dados explicitamente publicáveis entram na projeção. Fail-closed: sem
certeza de classificação, o dado não é publicado.

### B8 — Serviços pequenos e coesos — APROVADA
Sem `balanco_service.py` gigante; sem micro-serviços artificiais.

## Fase 1B — Balanços Mensais: implementação

### C1 — Competência como `DateField` no primeiro dia do mês — APROVADA
Nunca string livre como `"08/2026"`. Garantida por `clean()` e pela constraint
`balanco_competencia_no_primeiro_dia`.

### C2 — Estado funcional separado do estado técnico — APROVADA
`StatusBalanco` (funcional) e `ProcessamentoBalanco` (técnico) são campos
distintos. `ERRO` nunca é estado funcional.

### C3 — Versão em vigor derivada, sem campo redundante — APROVADA
`UniqueConstraint` condicional (`~Q(status=SUBSTITUIDO)`) garante uma única
versão em vigor por (condomínio, competência). Não se cria `eh_atual`.

### C4 — Unicidade do `sha256` por balanço, não global — APROVADA
O mesmo arquivo pode existir legitimamente em contextos distintos. O índice
`documento_sha256_idx` apenas apoia a descoberta de repetição.

### C5 — `EventoBalanco` adiado — APROVADA
Não há produtor de evento nesta fase. Preferiu-se adiar a criar entidade
genérica prematura. Os marcos atuais são cobertos por
`criado_por`/`criado_em`, `validado_por`/`validado_em` e pela cadeia `substitui`.

### C6 — `DocumentoBalanco` sem versionamento próprio — APROVADA
O versionamento de `BalancoMensal` já resolve o ciclo de vida: uma versão
corrigida é um novo balanço, com os seus próprios documentos. `DocumentoBalanco`
não tem versão própria nem `substitui`.

## Decisões pendentes de aprovação (não arquivadas aqui)

- Arquitetura de persistência dos lançamentos (relacional × JSON × híbrido).
- Provedor de storage externo privado.
- Categorias publicáveis por padrão.

Referência: `docs/fase_1a_balancos_decisoes.md` e
`docs/fase_1b_balancos_implementacao.md`.
