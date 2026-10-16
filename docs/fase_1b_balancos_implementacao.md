# Fase 1B — Balanços Mensais: Implementação

> Registro do que foi efetivamente implementado nesta fase: models mínimos,
> migration, constraints, validações e testes. Nenhum upload, storage, parser,
> OCR, IA, tela ou publicação foi implementado.

## 1. Escopo entregue

- App `apps.balancos` com `models.py`, `constants.py`, `admin.py`, `apps.py`,
  `migrations/0001_initial.py` e `tests/test_models.py`.
- Dois models: `BalancoMensal` e `DocumentoBalanco`.
- `EventoBalanco` **adiado** (§10).
- Registro em `LOCAL_APPS`, em `config/settings/base.py`.
- Nenhuma alteração na fundação da Fase 0 (`accounts.User`, `Condominio`,
  `AcessoUsuarioCondominio`, autorização centralizada, settings, PostgreSQL).

## 2. Models implementados

### 2.1 `BalancoMensal`

Agregado canônico do documento financeiro mensal de um condomínio. Não é conta
bancária, lançamento, boleto, cobrança, fornecedor, nota fiscal, manutenção,
obra ou orçamento.

| Campo | Tipo | Observação |
| --- | --- | --- |
| `id` | UUID (pk) | Gerado no sistema. |
| `condominio` | FK `Condominio` PROTECT | Obrigatório. Preserva histórico. |
| `competencia` | DateField | **Sempre o primeiro dia do mês.** |
| `versao` | PositiveIntegerField (default 1) | Versão do mês. |
| `substitui` | self-FK PROTECT, opcional | Versão anterior do mesmo mês. |
| `status` | CharField(choices `StatusBalanco`) | Situação funcional. |
| `status_processamento` | CharField(choices `ProcessamentoBalanco`) | Estado técnico. |
| `layout_detectado` | CharField(choices `LayoutBalanco`) | Origem do arquivo. |
| `schema_versao` | PositiveIntegerField (default 1) | Contrato do JSON. |
| `dados_extraidos` | JSONField (dict) | O que o sistema entendeu. |
| `dados_validados` | JSONField (dict) | O que a gestão confirmou. |
| `inconsistencias` | JSONField (list) | Lista estruturada. |
| `resumo` | JSONField (dict) | Somente agregados úteis. |
| `criado_por` | FK `AUTH_USER_MODEL` PROTECT | Autor da entrada. |
| `criado_em` / `atualizado_em` | DateTimeField | Automáticos. |
| `validado_por` | FK `AUTH_USER_MODEL` SET_NULL, opcional | Quem validou. |
| `validado_em` | DateTimeField, opcional | Quando validou. |

Ordenação padrão: `-competencia`, `-versao`.

### 2.2 `DocumentoBalanco`

Metadados do arquivo original. O arquivo em si vive em storage externo privado;
aqui fica apenas a referência ao objeto.

| Campo | Tipo | Observação |
| --- | --- | --- |
| `id` | UUID (pk) | Gerado no sistema. |
| `balanco` | FK `BalancoMensal` PROTECT | Obrigatório. |
| `nome_original` | CharField(255) | Nome de origem do arquivo. |
| `storage_provider` | CharField(50) | Ex.: `supabase`. |
| `storage_bucket` | CharField(150) | Local de armazenamento. |
| `storage_key` | CharField(500) | Referência do objeto. |
| `mime_type` | CharField(120) | Tipo do arquivo. |
| `tamanho_bytes` | PositiveBigIntegerField | Tamanho. |
| `sha256` | CharField(64) + `RegexValidator` | Hexadecimal minúsculo. |
| `enviado_por` | FK `AUTH_USER_MODEL` PROTECT | Autor do envio. |
| `enviado_em` | DateTimeField | Momento do envio. |
| `ativo` | BooleanField | Desativação sem apagar. |

Ordenação padrão: `-enviado_em`. Sem `FileField`: o sistema **não** guarda o
arquivo no filesystem local.

## 3. Cardinalidades

- `Condominio` 1 — N `BalancoMensal`.
- `BalancoMensal` 1 — N `DocumentoBalanco`.
- `BalancoMensal` 1 — N `BalancoMensal` (auto-relação `substitui`, opcional,
  no máximo uma versão substituída por versão).
- `User` 1 — N `BalancoMensal` (criação) e 1 — N (validação, opcional).
- `User` 1 — N `DocumentoBalanco` (envio).

## 4. Estado funcional × Estado técnico

Separados de propósito: um diz o que o balanço **é** para a gestão; o outro diz
onde está o **processamento** do arquivo.

**Funcional** (`StatusBalanco`):

- `em_revisao` — Em revisão (padrão)
- `validado` — Validado
- `publicado` — Publicado aos moradores
- `substituido` — Substituído por versão corrigida

**Técnico** (`ProcessamentoBalanco`):

- `pendente` — Pendente (padrão)
- `processando` — Processando
- `concluido` — Concluído
- `erro` — Erro

Não existe estado `RASCUNHO`: `EM_REVISAO` já cobre a fase anterior à validação.
Estados técnicos não contaminam o estado funcional.

`LayoutBalanco`: `desconhecido` (padrão), `atual_canonico`, `legado`. É
informação de entrada, não define a estrutura do model e não gera models
distintos.

## 5. Competência

Representada como `DateField` no **primeiro dia do mês** (`2026-08-01`). Nunca
string livre como `"08/2026"`.

Garantia em duas camadas:

- `clean()` recusa dia diferente de 1 com mensagem em linguagem humana;
- `CheckConstraint` `balanco_competencia_no_primeiro_dia` (`competencia__day=1`)
  impede a persistência mesmo quando `clean()` é ignorado.

## 6. Versionamento e versão canônica

O mesmo mês pode ter várias versões. Ao corrigir, cria-se uma nova versão que
substitui a anterior; a anterior permanece, marcada como `substituido`. Nada é
sobrescrito.

A versão **em vigor** é derivada, sem campo redundante:

- `balanco_unico_por_competencia_e_versao` — impede duplicar a mesma versão;
- `balanco_em_vigor_unico_por_competencia` — `UniqueConstraint` **condicional**
  (`condition=~Q(status=SUBSTITUIDO)`): no máximo um balanço em vigor por
  (condomínio, competência).

Assim, "qual versão é a atual" é sempre `status != substituido`, e é impossível
existirem duas versões atuais ao mesmo tempo. A decisão de §8 do spec
("evitar campo redundante se isso puder ser derivado com segurança") foi
atendida sem criar `eh_atual`.

`_erros_de_substituicao()` em `clean()` recusa:

- substituição de balanço de outro condomínio;
- substituição de balanço de outra competência;
- versão nova menor ou igual à substituída;
- auto-substituição.

## 7. Constraints e índices

**`BalancoMensal` — constraints**

| Nome | Tipo | Regra |
| --- | --- | --- |
| `balanco_unico_por_competencia_e_versao` | Unique | condomínio + competência + versão |
| `balanco_em_vigor_unico_por_competencia` | Unique condicional | condomínio + competência, exceto `substituido` |
| `balanco_versao_minima` | Check | `versao >= 1` |
| `balanco_competencia_no_primeiro_dia` | Check | `competencia.day == 1` |

**`BalancoMensal` — índices**

- `balanco_condominio_status_idx` — (condomínio, status).

**`DocumentoBalanco` — constraints**

| Nome | Tipo | Regra |
| --- | --- | --- |
| `documento_unico_por_balanco_e_hash` | Unique | balanço + sha256 |
| `documento_tamanho_nao_negativo` | Check | `tamanho_bytes >= 0` |

**`DocumentoBalanco` — índices**

- `documento_sha256_idx` — (sha256), apenas apoio à busca por arquivo repetido.

Nenhum campo JSON foi indexado. Nenhuma otimização prematura.

### Escopo de unicidade do `sha256`

A unicidade é **por balanço** (`documento_unico_por_balanco_e_hash`), não global.
O mesmo PDF pode existir legitimamente em condomínios ou meses distintos; o
índice apenas acelera a descoberta de repetição. Decidir se é duplicidade é da
gestão, não do banco.

## 8. Validações implementadas

Todas em `clean()` / `validators` / `constraints` — nenhuma lógica pesada em
`save()`.

**`BalancoMensal.clean()`**

- competência no primeiro dia do mês;
- versão ≥ 1;
- coerência da substituição (§6);
- `validado_em` e `validado_por` obrigatórios em `validado`/`publicado`;
- `validado_em` proibido em `em_revisao`.

**`DocumentoBalanco`**

- `sha256` com `RegexValidator` (`^[0-9a-f]{64}$`);
- `clean()` recusa URL pública/assinada (`http://`, `https://`) em
  `storage_provider`, `storage_bucket` ou `storage_key`;
- `clean()` recusa tamanho negativo.

## 9. Preservação histórica

- `condominio` → **PROTECT**: apagar um condomínio com balanços é bloqueado.
- `criado_por` e `enviado_por` → **PROTECT**: o autor do registro não é apagado
  por engano junto com o histórico.
- `validado_por` → **SET_NULL**: a validação sobrevive à remoção do vínculo do
  validador.
- `substitui` e `DocumentoBalanco.balanco` → **PROTECT**.
- Nenhum `CASCADE` no histórico financeiro.

Como usuários administrativos não são fisicamente apagados, `PROTECT` em
`criado_por` não vira impedimento operacional — apenas protege a trilha.

## 10. `EventoBalanco` — ADIADO

Adiado nesta fase. Motivo: **não existe produtor de evento**. Nada nesta fase
dispara `UPLOAD`, `PROCESSAMENTO_*`, `CORRECAO_HUMANA`, `VALIDACAO`,
`SUBSTITUICAO` ou `PUBLICACAO`. Criar a tabela agora seria uma entidade genérica
sem uso — exatamente o que a §20 do spec desaconselha ("é preferível adiar do
que criar entidade genérica prematuramente").

O adiamento **não perde informação**: `criado_por`/`criado_em`,
`validado_por`/`validado_em` e a cadeia `substitui` já registram os marcos
relevantes do ciclo atual. Quando o fluxo de upload/validação existir, o evento
será criado com tipo fechado (`UPLOAD`, `PROCESSAMENTO_*`, `CORRECAO_HUMANA`,
`VALIDACAO`, `SUBSTITUICAO`, `PUBLICACAO`, `DESPUBLICACAO`), nunca como log
genérico.

## 11. Estratégia de storage (futura)

O model está pronto e independente do provedor: `storage_provider`,
`storage_bucket`, `storage_key`. O arquivo nunca vai para o filesystem local
nem para o repositório.

Regras já fixadas e refletidas no código:

- não persistir URL pública, signed URL, token, senha ou service role;
- persistir apenas a referência do objeto;
- signed URL gerada sob demanda, no backend, com validade curta;
- acesso somente por autorização no contexto do condomínio.

Supabase Storage **não** é implementado nesta fase.

## 12. Schema JSON versionado

`dados_extraidos`, `dados_validados`, `inconsistencias` e `resumo` não são JSON
sem contrato: `schema_versao` (inicial `1`) identifica o contrato.

Contratos previstos (ainda não implementados como DTO nesta fase):

- `BalancoExtraidoV1` — o que o sistema entendeu do documento, antes da
  confirmação humana. Nunca tratado como oficial.
- `BalancoValidadoV1` — o estado confirmado/corrigido pela gestão.

`inconsistencias` guarda lista estruturada; cada item deverá representar
código, severidade, mensagem, campo/referência, origem, resolvida e resolução
humana. Não se cria model `Inconsistencia` agora.

`resumo` guarda apenas agregados úteis (ex.: totais, saldo, principais
categorias), sem duplicar indiscriminadamente os dados.

**Separação garantida:** `dados_extraidos` e `dados_validados` são campos
distintos; nada copia extraído → validado automaticamente.

## 13. Privacidade e autorização

- Nenhum modelo é acessível ao morador. Não há endpoint, portal ou PDF público.
- `VALIDADO` ≠ `PUBLICADO` automaticamente: a publicação não existe nesta fase.
- Não se expõe `dados_extraidos`, `dados_validados`, `inconsistencias`,
  `storage_key` nem `sha256` fora do contexto autorizado.
- O admin registrado é **apenas inspeção técnica** (`has_delete_permission`
  desabilitado, `readonly_fields` para IDs/timestamps, sem ações em massa, sem
  publicação ou substituição automática). Nenhuma view foi criada.
- Nenhum filtro usa `.first()`, `condominio_id` fixo ou "primeiro condomínio
  disponível": a autorização futura passa pelo contexto do condomínio via
  `apps/condominios/acesso.py`.

## 14. Migration

`apps/balancos/migrations/0001_initial.py`, gerada por `makemigrations` e
**não editada manualmente**. Depende de `condominios.0001_initial` e de
`settings.AUTH_USER_MODEL`.

Sequência executada: `check` → `makemigrations --check --dry-run` →
`makemigrations` → revisão → `migrate --plan` → `migrate`. **Não foi usado
`--fake`.**

## 15. Testes

`apps/balancos/tests/test_models.py`. Todos com dados genéricos (Condomínio A,
Condomínio B, Usuário Gestão, Documento A) — sem nome, e-mail, valor, PDF ou
dado real.

Cobertura das 24 exigências da §32 do spec: criação válida, competência
normalizada, competência inválida (domínio e banco), versão inicial, versão
inválida, versões 1 e 2 coexistindo, duplicidade de versão, versão em vigor
única, substituição no mesmo condomínio, outro condomínio, outra competência,
auto-substituição, estados funcionais, estados de processamento, três layouts,
`schema_versao`, separação extraído/validado, documento vinculado, hash
persistido e formato do hash, unicidade do hash por balanço, unicidade do hash
em balanços distintos, storage sem URL pública, isolamento por condomínio, FK
com o modelo de pessoa do projeto, e preservação histórica (`PROTECT`/`SET_NULL`
e bloqueio de remoção do condomínio).

## 16. Decisões adiadas (resumo)

| Item | Situação | Motivo |
| --- | --- | --- |
| `EventoBalanco` | Adiado | Sem produtor de evento nesta fase. |
| Upload e Supabase Storage | Adiado | Fora do escopo da 1B. |
| Parser / OCR / Detector de layout | Adiado | Fora do escopo da 1B. |
| IA / OpenAI | Adiado | Fora do escopo da 1B. |
| DTOs `BalancoExtraidoV1` / `BalancoValidadoV1` | Adiado | Contrato registrado; implementação futura. |
| Model `Inconsistencia` | Adiado | Lista JSON basta por ora. |
| `PublicacaoMorador` | Adiado | Fora do escopo da 1B. |
| Versionamento documental | Não criado | O versionamento de `BalancoMensal` já resolve o ciclo de vida. |
