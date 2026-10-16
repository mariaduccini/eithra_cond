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

### C2 — Sem workflow formal de revisão de balanço — APROVADA
A V3 inicial não possui etapa de revisão. O ciclo é: o resultado da leitura é
apresentado à gestão, a gestão **confirma a importação** e só então o mês passa
a valer (`VALIDADO`); a publicação é opcional (`PUBLICADO`). Não há fila,
pendência, aprovação em etapas, revisor, estado de revisão nem tela de revisão.
Se surgir necessidade real, entra como nova decisão de produto.

### C3 — Situação funcional separada do estado técnico — APROVADA
`StatusBalanco` (`validado`, `publicado`, `substituido`) e
`ProcessamentoBalanco` (`pendente`, `processando`, `concluido`, `erro`) são
campos distintos. `ERRO` nunca é situação funcional, e nenhum estado técnico
substitui a confirmação humana.

### C4 — Versão vigente derivada, sem campo redundante — APROVADA
`VALIDADO` e `PUBLICADO` são versão vigente; `SUBSTITUIDO` não é.
`UniqueConstraint` condicional (`~Q(status=SUBSTITUIDO)`) garante uma única
versão vigente por (condomínio, competência). Não se cria `eh_atual`.

### C5 — Unicidade do `sha256` por balanço, não global — APROVADA
O mesmo arquivo pode existir legitimamente em contextos distintos. O índice
`documento_sha256_idx` apenas apoia a descoberta de repetição.

### C6 — `EventoBalanco` adiado — APROVADA
Não há produtor de evento nesta fase. Preferiu-se adiar a criar entidade
genérica prematura. Os marcos atuais são cobertos por
`criado_por`/`criado_em`, `validado_por`/`validado_em` e pela cadeia `substitui`.

### C7 — `DocumentoBalanco` sem versionamento próprio — APROVADA
O versionamento de `BalancoMensal` já resolve o ciclo de vida: uma versão
corrigida é um novo balanço, com os seus próprios documentos. `DocumentoBalanco`
não tem versão própria nem `substitui`.

## Fase 1C — Entrada segura do balanço

### D1 — `ImportacaoBalanco` é entidade transitória, separada do balanço — APROVADA
Upload de arquivo, processamento e resultado preliminar **não** são
`BalancoMensal`. O que nasce no recebimento é uma tentativa de importação; o mês
só passa a valer com a confirmação da gestão, em fase futura. Nenhum balanço é
criado no upload.

### D2 — Storage privado por contrato, adaptador Supabase sem SDK — APROVADA
O domínio fala com `StorageProvider` (`enviar`, `remover`, `url_assinada`) e o
provedor concreto fica atrás dele: trocar de provedor não exige mexer no
domínio. O adaptador Supabase usa **HTTP puro** (biblioteca padrão), sem SDK.
Persistem-se apenas `provider`, `bucket` e `object_key`; URL pública, signed URL,
token e service role **nunca** são gravados (recusado por `clean()`).

### D3 — Bucket privado por ambiente — APROVADA
DEV e PROD usam buckets separados, nunca o mesmo. Falta de configuração provoca
erro explícito nomeando as variáveis ausentes; não existe queda silenciosa para
filesystem local.

### D4 — Object key apenas com identificadores — APROVADA
`balancos/<condominio_uuid>/<importacao_uuid>/documento.pdf`. Sem nome de
arquivo, nome de condomínio em texto, competência não confirmada ou dado
pessoal no caminho do objeto.

### D5 — Duplicidade por `condominio` + `sha256` — APROVADA
Sem constraint global de hash. O mesmo arquivo pode existir legitimamente em
condomínios distintos. Importações descartadas e com falha de recebimento não
contam como repetição.

### D6 — Limite de upload configurável — APROVADA
`BALANCOS_MAX_UPLOAD_BYTES`, 50 MB por padrão. Folgado para balanços reais com
muitas páginas e suficiente para limitar abuso.

### D7 — Papéis que importam balanço: ADMIN e SINDICA — APROVADA
Supervisão, operação, portaria e morador não importam. A regra vive em
`apps/balancos/permissoes.py`; a decisão de acesso ao condomínio continua
centralizada em `apps/condominios/acesso.py`. O menu não é controle de acesso.

### D8 — Consistência por gravação antes do envio, com compensação — APROVADA
Não existe transação ACID entre banco e storage externo. O registro é gravado
antes do envio; falha de envio resulta em `ERRO` (nunca falso sucesso); falha de
gravação após o envio dispara remoção compensatória do objeto.

### D9 — Descarte preserva a memória e libera o arquivo — APROVADA
Descartar marca `DESCARTADO` com `descartado_em` em vez de apagar o registro. A
remoção do objeto é *best-effort*: o descarte não fica refém do storage.

### D10 — Acesso ao arquivo por signed URL curta, sob demanda — APROVADA
Nenhum endereço fixo: cada visita autorizada gera um acesso temporário
(`BALANCOS_URL_ACESSO_SEGUNDOS`, 60 s por padrão). Nada é persistido.

## Decisões pendentes de aprovação (não arquivadas aqui)

- Arquitetura de persistência dos lançamentos (relacional × JSON × híbrido).
- Categorias publicáveis por padrão.

Referência: `docs/fase_1a_balancos_decisoes.md`,
`docs/fase_1b_balancos_implementacao.md` e
`docs/fase_1c_entrada_balanco.md`.
