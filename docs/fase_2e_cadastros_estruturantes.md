# Eithra V3 — Fase 2E · Cadastros Estruturantes para Implantação

> Documento de modelagem **pós-implementação e pré-aplicação**, na versão revisada
> pela arquitetura. A implementação está concluída e os testes do módulo passam.
> As três migrations previstas foram **geradas** (arquivos no repositório) e
> **nenhuma foi aplicada**: `migrate` não foi executado, e DEV continua com o
> schema anterior até autorização explícita.
>
> As seções 0 a 27 descrevem a modelagem aprovada. A seção 28 registra o que
> ficou implementado e a seção 29 o que falta fazer.

A Fase 2E completa os cadastros que faltam para o Eithra operar sem depender de
enums fixos, do Django Admin, de edição manual de banco ou de planilhas fora do
sistema. O que segue é a modelagem final — models, relações, cardinalidades,
`on_delete`, constraints, índices, grafo de dependências, autorização e alertas
derivados — já com os ajustes aprovados na revisão arquitetural.

---

## 0. Ajustes aprovados nesta revisão

| Ponto | Decisão |
|---|---|
| App dos cadastros estruturantes | **`apps/governanca`** (não vive em `cadastros`) |
| Tipo de local | Model chamado **`TipoLocalOperacional`** (evita colisão com o enum legado) |
| Garantia | Sem FKs de origem. Vínculos explícitos `GarantiaEquipamento`, `GarantiaManutencao`, `GarantiaItemCompra` |
| Documento | Sem FKs para entidades. Vínculos explícitos `DocumentoFornecedor`, `DocumentoContrato`, `DocumentoEquipamento`, `DocumentoGarantia` |
| Contrato ↔ rotina | **Entra nesta fase**, como `ContratoRotina`, do lado de governança |
| Histórico de acesso | `acesso` com `on_delete=PROTECT` (não `CASCADE`) |
| Datas | `data_fim >= data_inicio` como **`CheckConstraint`** em contrato e garantia |
| Usuários | "Adicionar acesso" (não "convite") |
| Campos legados | `LocalOperacional.tipo` e `Equipamento.tipo_texto` **não** são removidos |

---

## 1. Pré-check — estado inicial

| Verificação | Resultado |
|---|---|
| `git status` | apenas `?? static/img/eithra-mark.html` e `?? static/img/eithra-mark_old.svg` (não rastreados, mantidos fora) |
| Commit atual | `594543a fix: unify next routine execution semantics` (topo da Fase 2D) |
| Branch | `master` |
| `manage.py check` | `System check identified no issues (0 silenced).` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` |
| `showmigrations rotinas` | `[X] 0001_initial` — aplicada em DEV |

Nenhum arquivo de domínio foi criado ou alterado. Nenhuma migration foi gerada.

---

## 2. Objetivo e limites

**Entra nesta fase**

- CRUD de `TipoLocalOperacional`, `TipoEquipamento`, `EspecialidadeFornecedor`, `TipoContrato` e `TipoDocumento`.
- Evolução de `LocalOperacional` e `Equipamento` para usar esses tipos.
- Gestão real de Usuários e Acessos, com histórico auditável.
- Contratos de Fornecedor, Garantias e Documentos com validade.
- Condições e alertas **derivados**, sem tabela nem menu de alertas.

**Não entra nesta fase**

- Unidades, moradores, veículos, visitantes.
- Projetos/Obras.
- OCR, IA, notificações, WhatsApp, Gmail, scheduler, Painel Hoje.
- Nova classificação financeira de balanços e contas a pagar.
- Assinatura eletrônica de contratos.
- Upload de arquivos de documento (§14 — só a proposta técnica).

**Princípio dos alertas.** Não existe menu "Alertas", nem cadastro genérico, nem
tabela de alerta. Toda condição de vencimento é lida do próprio domínio, por
QuerySet (§20).

---

## 3. Onde cada coisa mora

| Onde | O que |
|---|---|
| `apps/cadastros` | `TipoLocalOperacional`, `TipoEquipamento`, `EspecialidadeFornecedor`, `FornecedorEspecialidade` + evolução de `LocalOperacional`, `Equipamento`, `Fornecedor` |
| `apps/condominios` | `HistoricoAcessoCondominio` |
| **`apps/governanca`** (novo) | `TipoContrato`, `ContratoFornecedor`, `ContratoEquipamento`, `ContratoLocal`, `ContratoRotina`, `Garantia`, `GarantiaEquipamento`, `GarantiaManutencao`, `GarantiaItemCompra`, `TipoDocumento`, `DocumentoOperacional`, `ArquivoDocumento`, `DocumentoFornecedor`, `DocumentoContrato`, `DocumentoEquipamento`, `DocumentoGarantia` |

---

## 4. Models novos — `apps/cadastros`

### 4.1 `TipoLocalOperacional`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | `default=uuid.uuid4, editable=False` |
| `condominio` | FK `Condominio` | `PROTECT`, `related_name="tipos_de_local"` |
| `nome` | CharField(120) | obrigatório |
| `descricao` | TextField | `blank`, `default=""` |
| `ativo` | Boolean | `default=True` |
| `criado_por` | FK `User` | `PROTECT`, `null/blank`, `related_name="tipos_de_local_cadastrados"` |
| `criado_em` / `atualizado_em` | DateTime | automáticos |

- `objects = TipoLocalOperacionalQuerySet.as_manager()` com `ativos()`, `do_condominio()`.
- `save()` normaliza nome/descrição com os helpers `_texto` / `_texto_longo` do módulo.
- `clean()` recusa nome vazio.
- O nome evita a colisão com o `TextChoices TipoLocal` de `apps/cadastros/constants.py`,
  que permanece intacto enquanto o campo legado existir.

### 4.2 `TipoEquipamento`

Mesma forma: `id`, `condominio` (PROTECT, `related_name="tipos_de_equipamento"`),
`nome` CharField(120), `descricao`, `ativo`, `criado_por`
(`related_name="tipos_de_equipamento_cadastrados"`), `criado_em`, `atualizado_em`.
Não há enum com esse nome hoje — sem colisão.

### 4.3 `EspecialidadeFornecedor`

`id`, `condominio` (PROTECT, `related_name="especialidades_de_fornecedor"`),
`nome` CharField(120), `descricao`, `ativo`, `criado_por`
(`related_name="especialidades_cadastradas"`), `criado_em`, `atualizado_em`.

### 4.4 `FornecedorEspecialidade` (vínculo N:N)

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `fornecedor` | FK `Fornecedor` | `CASCADE`, `related_name="especialidades"` |
| `especialidade` | FK `EspecialidadeFornecedor` | `PROTECT`, `related_name="fornecedores"` |
| `observacao` | CharField(120) | `blank`, `default=""` |
| `criado_em` | DateTime | automático |

Modelo explícito (não `ManyToManyField` implícito) para permitir `observacao` e
seguir a convenção do projeto. Nada de texto livre repetido no fornecedor.

---

## 5. Models novos — `apps/governanca`

### 5.1 `TipoContrato`

`id`, `condominio` (PROTECT, `related_name="tipos_de_contrato"`), `nome`
CharField(120), `descricao`, `ativo`, `criado_por`
(`related_name="tipos_de_contrato_cadastrados"`), `criado_em`, `atualizado_em`.

Configurável por condomínio, sem enum rígido. As sugestões (manutenção, prestação
de serviço, fornecimento, locação, seguro, outro) ficam como cadastro da gestão —
não há carga automática.

### 5.2 `ContratoFornecedor`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `condominio` | FK `Condominio` | `PROTECT`, `related_name="contratos"` |
| `fornecedor` | FK `Fornecedor` | `PROTECT`, **obrigatório**, `related_name="contratos"` |
| `tipo_contrato` | FK `TipoContrato` | `PROTECT`, `null/blank`, `related_name="contratos"` |
| `numero_referencia` | CharField(60) | `blank`, `default=""` |
| `objeto` | CharField(200) | obrigatório |
| `descricao` | TextField | `blank`, `default=""` |
| `data_inicio` | DateField | obrigatório |
| `data_fim` | DateField | `null/blank` (prazo indeterminado é válido) |
| `valor` | Decimal(12,2) | `null/blank` — valor operacional, não contábil |
| `periodicidade_cobranca` | CharField(choices) | `blank` — enum `PeriodicidadeCobranca` |
| `renovacao_automatica` | Boolean | `default=False` |
| `antecedencia_alerta_dias` | PositiveInteger | `default=60` |
| `prazo_aviso_rescisao_dias` | PositiveInteger | `null/blank` |
| `data_proximo_reajuste` | DateField | `null/blank` |
| `indice_reajuste_texto` | CharField(120) | `blank` — texto livre (IGP-M, IPCA…) |
| `responsavel_interno` | FK `User` | `SET_NULL`, `null/blank`, `related_name="contratos_sob_responsabilidade"` |
| `observacao` | TextField | `blank`, `default=""` |
| `ativo` | Boolean | `default=True` |
| `criado_por` | FK `User` | `PROTECT`, `related_name="contratos_cadastrados"` |
| `criado_em` / `atualizado_em` | DateTime | automáticos |

**Status temporal continua derivado** (§20) — `INATIVO`, `FUTURO`, `VENCIDO`,
`VENCENDO`, `VIGENTE`. Nada persistido.

### 5.3 `ContratoEquipamento` (N:N)

`id`, `contrato` (FK `ContratoFornecedor`, `CASCADE`, `related_name="equipamentos_vinculados"`),
`equipamento` (FK `Equipamento`, `PROTECT`, `related_name="contratos"`),
`observacao` CharField(120) `blank`, `criado_em`.

### 5.4 `ContratoLocal` (N:N)

`id`, `contrato` (FK `ContratoFornecedor`, `CASCADE`, `related_name="locais_vinculados"`),
`local` (FK `LocalOperacional`, `PROTECT`, `related_name="contratos"`),
`observacao` CharField(120) `blank`, `criado_em`.

### 5.5 `ContratoRotina` (N:N)

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `contrato` | FK `ContratoFornecedor` | `CASCADE`, `related_name="rotinas_vinculadas"` |
| `rotina` | FK `RotinaRecorrente` | `PROTECT`, `related_name="contratos"` |
| `observacao` | CharField(120) | `blank`, `default=""` |
| `criado_em` | DateTime | automático |

O relacionamento vive **em governança** — nenhuma FK de contrato é adicionada a
`RotinaRecorrente`. Isso permite responder "esta preventiva está coberta por qual
contrato?" sem que `rotinas` precise conhecer `governanca`.

### 5.6 `Garantia`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `condominio` | FK `Condominio` | `PROTECT`, `related_name="garantias"` |
| `fornecedor` | FK `Fornecedor` | `PROTECT`, `null/blank`, `related_name="garantias"` |
| `descricao` | CharField(200) | obrigatório |
| `numero_referencia` | CharField(80) | `blank`, `default=""` |
| `data_inicio` | DateField | obrigatório |
| `data_fim` | DateField | `null/blank` |
| `antecedencia_alerta_dias` | PositiveInteger | `default=30` |
| `observacao` | TextField | `blank`, `default=""` |
| `ativo` | Boolean | `default=True` |
| `criado_por` | FK `User` | `PROTECT`, `related_name="garantias_cadastradas"` |
| `criado_em` / `atualizado_em` | DateTime | automáticos |

**Sem FKs de origem e sem constraint de origem única.** O que a garantia cobre
fica nos vínculos (§5.7). `situacao` é derivada: `INATIVA`, `FUTURA`, `VENCIDA`,
`VENCENDO`, `VIGENTE`.

### 5.7 Vínculos da garantia

`GarantiaEquipamento`, `GarantiaManutencao`, `GarantiaItemCompra` — todos com a
mesma forma:

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `garantia` | FK `Garantia` | `CASCADE`, `related_name="equipamentos"` / `"manutencoes"` / `"itens_de_compra"` |
| objeto | FK do alvo | `PROTECT` (`equipamento` / `manutencao` / `item_compra`) |
| `observacao` | CharField(120) | `blank`, `default=""` |
| `criado_em` | DateTime | automático |

**Uma garantia pode cobrir mais de um objeto**, e mais de um tipo de objeto ao
mesmo tempo. Sem `GenericForeignKey`. Sem exigir origem única. No futuro,
`GarantiaProjeto` se acrescenta sem alterar `Garantia`.

### 5.8 `TipoDocumento`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `condominio` | FK `Condominio` | `PROTECT`, `related_name="tipos_de_documento"` |
| `nome` | CharField(120) | obrigatório |
| `exige_validade` | Boolean | `default=False` |
| `antecedencia_alerta_padrao_dias` | PositiveInteger | `default=30` |
| `descricao` | TextField | `blank`, `default=""` |
| `ativo` | Boolean | `default=True` |
| `criado_por` | FK `User` | `PROTECT`, `related_name="tipos_de_documento_cadastrados"` |
| `criado_em` / `atualizado_em` | DateTime | automáticos |

### 5.9 `DocumentoOperacional`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `condominio` | FK `Condominio` | `PROTECT`, `related_name="documentos"` |
| `tipo_documento` | FK `TipoDocumento` | `PROTECT`, `related_name="documentos"` |
| `titulo` | CharField(180) | obrigatório |
| `numero_referencia` | CharField(80) | `blank`, `default=""` |
| `data_emissao` | DateField | `null/blank` |
| `data_validade` | DateField | `null/blank` |
| `emissor` | CharField(180) | `blank` — texto |
| `observacao` | TextField | `blank`, `default=""` |
| `ativo` | Boolean | `default=True` |
| `criado_por` | FK `User` | `PROTECT`, `related_name="documentos_cadastrados"` |
| `criado_em` / `atualizado_em` | DateTime | automáticos |

**Sem FKs para entidades.** O vínculo fica nas tabelas dedicadas (§5.10). O
`condominio` sozinho já é um vínculo legítimo — uma convenção ou um regimento não
pertence a mais nada.

### 5.10 Vínculos do documento

`DocumentoFornecedor`, `DocumentoContrato`, `DocumentoEquipamento`,
`DocumentoGarantia` — todos com a mesma forma:

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `documento` | FK `DocumentoOperacional` | `CASCADE`, `related_name` por entidade |
| entidade | FK do alvo | `PROTECT` |
| `criado_em` | DateTime | automático |

**Um documento pode estar relacionado simultaneamente a mais de um contexto** —
um certificado que vale para o contrato **e** para o equipamento, por exemplo —
sem duplicar o arquivo. Sem `GenericForeignKey`. No futuro, `DocumentoProjeto` se
acrescenta sem alterar `DocumentoOperacional`.

`relacionado_a` é derivado para a tela: a primeira entidade vinculada na ordem
contrato → equipamento → garantia → fornecedor, ou "Condomínio" quando não há.

### 5.11 `ArquivoDocumento`

Filho 1:N de `DocumentoOperacional`. Metadados do arquivo privado, no mesmo padrão
de `DocumentoBalanco`.

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `documento` | FK `DocumentoOperacional` | `CASCADE`, `related_name="arquivos"` |
| `nome_original` | CharField(255) | |
| `storage_provider` | CharField(50) | `blank`, `default=""` |
| `storage_bucket` | CharField(150) | `blank`, `default=""` |
| `storage_key` | CharField(500) | referência do objeto |
| `mime_type` | CharField(120) | |
| `tamanho_bytes` | PositiveBigInteger | `default=0` |
| `sha256` | CharField(64) | validador de formato já existente |
| `enviado_por` | FK `User` | `PROTECT` |
| `enviado_em` | DateTime | `default=timezone.now` |
| `ativo` | Boolean | `default=True` |

Sem `FileField`, sem URL pública, sem URL assinada persistida.

---

## 6. Model novo — `apps/condominios`

### 6.1 `HistoricoAcessoCondominio`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | UUID pk | |
| `acesso` | FK `AcessoUsuarioCondominio` | **`PROTECT`**, `related_name="historico"` |
| `usuario_executor` | FK `User` | `PROTECT`, `related_name="eventos_de_acesso"` |
| `evento` | CharField(choices) | `CRIADO`, `PAPEL_ALTERADO`, `ATIVADO`, `INATIVADO` |
| `dados_anteriores` | JSONField | `null/blank` |
| `dados_novos` | JSONField | `null/blank` |
| `data_hora` | DateTime | `default=timezone.now` |

Append-only: a aplicação nunca reescreve nem apaga uma linha. `PROTECT` em
`acesso` é deliberado — o histórico **não** pode desaparecer porque alguém tentou
remover o vínculo. Na operação normal, `AcessoUsuarioCondominio` é desativado,
nunca excluído (§17, §19).

---

## 7. Models existentes alterados

### 7.1 `LocalOperacional`

- **Novo:** `tipo_local` FK `TipoLocalOperacional`, `PROTECT`, `null/blank`, `related_name="locais"`.
- **Mantido:** `tipo` (CharField com o enum legado). **Não removido nesta fase.**

### 7.2 `Equipamento`

- **Novo:** `tipo_equipamento` FK `TipoEquipamento`, `PROTECT`, `null/blank`, `related_name="equipamentos"`.
- **Novo:** `data_aquisicao` DateField `null/blank`, `data_instalacao` DateField `null/blank`.
- **Mantido:** `tipo_texto`. **Não removido nesta fase.**
- **Não adicionado:** `garantia_ate`. Garantia é domínio próprio.

### 7.3 `Fornecedor`

Ganha o lado N:N via `FornecedorEspecialidade` e a disponibilização aos
moradores. **Não há cadastro novo, marketplace nem duplicação:** o mesmo
`Fornecedor` continua sendo a única fonte de verdade, e a especialidade
continua sendo `EspecialidadeFornecedor`.

- **Novos (decisão da gestão):** `indicado_para_moradores` (bool, `default=False`),
  `ativo_para_moradores` (bool, `default=False`).
- **Novos (o que o morador lê — próprios, nunca copiados do interno):**
  `descricao_publica` (text), `telefone_publico` (char), `whatsapp_publico`
  (char), `site_publico` (url), `observacao_publica` (text).
- **Novos (proveniência da liberação):** `validado_em` (datetime, `null/blank`),
  `validado_por` (FK `User`, `SET_NULL`, `null/blank`,
  `related_name="fornecedores_indicados"`).
- **Lido, não guardado:** `disponivel_para_moradores` (property).
- **Não recebe** `data_vencimento` (fornecedor não vence) nem `email_publico`
  (contato público mínimo: telefone, WhatsApp e site). `email`, `telefone`,
  `contato` e `observacao` **não** viram dados públicos automaticamente.

### 7.4 `AcessoUsuarioCondominio`

Nenhum campo novo. Ganha o histórico e a gestão real pela interface.

### 7.5 `User`

Nenhum campo novo. A gestão opera sobre a identidade existente, sem tocar em
credenciais.

---

## 8. Relacionamentos e cardinalidades

| Relação | Cardinalidade |
|---|---|
| `Condominio` → `TipoLocalOperacional` | 1 : N |
| `TipoLocalOperacional` → `LocalOperacional.tipo_local` | 1 : N (opcional) |
| `Condominio` → `TipoEquipamento` | 1 : N |
| `TipoEquipamento` → `Equipamento.tipo_equipamento` | 1 : N (opcional) |
| `Condominio` → `EspecialidadeFornecedor` | 1 : N |
| `Fornecedor` ↔ `EspecialidadeFornecedor` | N : N (`FornecedorEspecialidade`) |
| `Condominio` → `TipoContrato` | 1 : N |
| `Fornecedor` → `ContratoFornecedor` | 1 : N (obrigatório) |
| `TipoContrato` → `ContratoFornecedor` | 1 : N (opcional) |
| `User` → `ContratoFornecedor.responsavel_interno` | 1 : N (opcional) |
| `ContratoFornecedor` ↔ `Equipamento` | N : N (`ContratoEquipamento`) |
| `ContratoFornecedor` ↔ `LocalOperacional` | N : N (`ContratoLocal`) |
| `ContratoFornecedor` ↔ `RotinaRecorrente` | N : N (`ContratoRotina`) |
| `Condominio` → `Garantia` | 1 : N |
| `Fornecedor` → `Garantia` | 1 : N (opcional) |
| `Garantia` ↔ `Equipamento` | N : N (`GarantiaEquipamento`) |
| `Garantia` ↔ `Manutencao` | N : N (`GarantiaManutencao`) |
| `Garantia` ↔ `ItemCompra` | N : N (`GarantiaItemCompra`) |
| `Condominio` → `TipoDocumento` | 1 : N |
| `TipoDocumento` → `DocumentoOperacional` | 1 : N |
| `DocumentoOperacional` → `ArquivoDocumento` | 1 : N |
| `DocumentoOperacional` ↔ `Fornecedor` | N : N (`DocumentoFornecedor`) |
| `DocumentoOperacional` ↔ `ContratoFornecedor` | N : N (`DocumentoContrato`) |
| `DocumentoOperacional` ↔ `Equipamento` | N : N (`DocumentoEquipamento`) |
| `DocumentoOperacional` ↔ `Garantia` | N : N (`DocumentoGarantia`) |
| `User` → `AcessoUsuarioCondominio` | 1 : N |
| `Condominio` → `AcessoUsuarioCondominio` | 1 : N |
| `AcessoUsuarioCondominio` → `HistoricoAcessoCondominio` | 1 : N |

Nenhuma relação 1:1 nesta fase.

---

## 9. Diagrama relacional textual

```
Condominio
├ TipoLocalOperacional
│  └ LocalOperacional            (LocalOperacional.tipo_local, opcional)
├ TipoEquipamento
│  └ Equipamento                 (Equipamento.tipo_equipamento, opcional)
├ EspecialidadeFornecedor
│  └ FornecedorEspecialidade ── Fornecedor
├ TipoContrato
│  └ ContratoFornecedor          (fornecedor obrigatório)
│     ├ ContratoEquipamento  ── Equipamento
│     ├ ContratoLocal        ── LocalOperacional
│     └ ContratoRotina       ── RotinaRecorrente
├ Garantia
│  ├ GarantiaEquipamento     ── Equipamento
│  ├ GarantiaManutencao      ── Manutencao
│  └ GarantiaItemCompra      ── ItemCompra
├ TipoDocumento
│  └ DocumentoOperacional
│     ├ ArquivoDocumento     (1:N, arquivo privado)
│     ├ DocumentoFornecedor  ── Fornecedor
│     ├ DocumentoContrato    ── ContratoFornecedor
│     ├ DocumentoEquipamento ── Equipamento
│     └ DocumentoGarantia    ── Garantia
└ AcessoUsuarioCondominio
   └ HistoricoAcessoCondominio

Sentido único de dependência entre apps:
   governanca ──► cadastros, manutencoes, estoque, rotinas, condominios, accounts
   (nunca o inverso) — sem ciclo.
```

---

## 10. FKs e `on_delete` — todas as decisões

Princípio: `PROTECT` quando a referência faz parte do histórico; `CASCADE` só
quando o filho não tem sentido sem o pai; `SET_NULL` quando o histórico sobrevive
sem a referência.

| FK | `on_delete` | Por quê |
|---|---|---|
| Todos os `condominio` | `PROTECT` | Fronteira de isolamento. |
| `LocalOperacional.tipo_local` → `TipoLocalOperacional` | `PROTECT` | Tipo em uso não se apaga; sai por `ativo=False`. |
| `Equipamento.tipo_equipamento` → `TipoEquipamento` | `PROTECT` | Idem. |
| `FornecedorEspecialidade.fornecedor` → `Fornecedor` | `CASCADE` | Vínculo sem sentido sem o fornecedor. |
| `FornecedorEspecialidade.especialidade` → `EspecialidadeFornecedor` | `PROTECT` | Catálogo não some por causa de um vínculo. |
| `ContratoFornecedor.fornecedor` → `Fornecedor` | `PROTECT` | Histórico relevante. |
| `ContratoFornecedor.tipo_contrato` → `TipoContrato` | `PROTECT` | Catálogo. |
| `ContratoFornecedor.responsavel_interno` → `User` | `SET_NULL` | O contrato sobrevive sem a pessoa. |
| `ContratoFornecedor.criado_por` → `User` | `PROTECT` | Rastreabilidade. |
| `ContratoEquipamento.contrato` → `ContratoFornecedor` | `CASCADE` | Vínculo sem sentido sem o contrato. |
| `ContratoEquipamento.equipamento` → `Equipamento` | `PROTECT` | O alvo não some por causa de um vínculo. |
| `ContratoLocal.contrato` / `.local` | `CASCADE` / `PROTECT` | Mesmo critério. |
| `ContratoRotina.contrato` → `ContratoFornecedor` | `CASCADE` | Mesmo critério. |
| `ContratoRotina.rotina` → `RotinaRecorrente` | `PROTECT` | A rotina é dado operacional; não some por causa do vínculo. |
| `Garantia.fornecedor` → `Fornecedor` | `PROTECT` | Histórico. |
| `Garantia.criado_por` → `User` | `PROTECT` | Rastreabilidade. |
| `GarantiaEquipamento.garantia` / `GarantiaManutencao.garantia` / `GarantiaItemCompra.garantia` | `CASCADE` | Vínculo sem sentido sem a garantia. |
| `GarantiaEquipamento.equipamento` → `Equipamento` | `PROTECT` | O alvo não some por causa de um vínculo. |
| `GarantiaManutencao.manutencao` → `Manutencao` | `PROTECT` | Idem. |
| `GarantiaItemCompra.item_compra` → `ItemCompra` | `PROTECT` | Idem. |
| `DocumentoOperacional.tipo_documento` → `TipoDocumento` | `PROTECT` | Catálogo. |
| `DocumentoOperacional.criado_por` → `User` | `PROTECT` | Rastreabilidade. |
| `ArquivoDocumento.documento` → `DocumentoOperacional` | `CASCADE` | Arquivo não existe sem o documento. |
| `ArquivoDocumento.enviado_por` → `User` | `PROTECT` | Rastreabilidade. |
| `DocumentoFornecedor.documento` / `DocumentoContrato.documento` / `DocumentoEquipamento.documento` / `DocumentoGarantia.documento` | `CASCADE` | Vínculo sem sentido sem o documento. |
| `DocumentoFornecedor.fornecedor` / `DocumentoContrato.contrato` / `DocumentoEquipamento.equipamento` / `DocumentoGarantia.garantia` | `PROTECT` | O alvo não some por causa de um vínculo. |
| `HistoricoAcessoCondominio.acesso` → `AcessoUsuarioCondominio` | **`PROTECT`** | O histórico não pode desaparecer por causa da remoção do vínculo. |
| `HistoricoAcessoCondominio.usuario_executor` → `User` | `PROTECT` | Quem fez a alteração nunca é apagado em silêncio. |

---

## 11. Constraints

**Unicidade case-insensitive por condomínio**
(`UniqueConstraint(Lower("nome"), "condominio")`): `TipoLocalOperacional`,
`TipoEquipamento`, `EspecialidadeFornecedor`, `TipoContrato`, `TipoDocumento`.

**Unicidade de vínculo** (`UniqueConstraint`): `FornecedorEspecialidade(fornecedor, especialidade)`,
`ContratoEquipamento(contrato, equipamento)`, `ContratoLocal(contrato, local)`,
`ContratoRotina(contrato, rotina)`, `GarantiaEquipamento(garantia, equipamento)`,
`GarantiaManutencao(garantia, manutencao)`, `GarantiaItemCompra(garantia, item_compra)`,
`DocumentoFornecedor(documento, fornecedor)`, `DocumentoContrato(documento, contrato)`,
`DocumentoEquipamento(documento, equipamento)`, `DocumentoGarantia(documento, garantia)`.

**CheckConstraint — no banco**
- `ContratoFornecedor`: `data_fim >= data_inicio` quando ambas existirem.
- `Garantia`: `data_fim >= data_inicio` quando ambas existirem.
- `antecedencia_alerta_dias >= 0` em contrato, garantia e tipo de documento.
- `prazo_aviso_rescisao_dias >= 0` quando informado.
- `valor >= 0` quando informado.
- `data_proximo_reajuste` coerente com a vigência quando informado, se a expressão
  couber em `CheckConstraint` sem ambiguidade — a confirmar na implementação.

Aqui não vale a cautela de "só em `clean()`": a regra é simples, não evolui com o
tempo e o banco protege com segurança. Onde a regra depende de outra tabela —
validade obrigatória conforme `TipoDocumento`, origem em outro cadastro — a
validação continua em `clean()` e no service, porque `CheckConstraint` não alcança.

**Unicidade condicional de documento**
`UniqueConstraint(condominio, tipo_documento, numero_referencia)` com
`condition=~Q(numero_referencia="")` — só quando há número informado. Em
`ArquivoDocumento`, `UniqueConstraint(documento, sha256)`.

**Mesmo condomínio em toda relação** — validado em `clean()` de cada model e no
service, nunca sobrescrito em silêncio (§22).

---

## 12. Índices

Apenas onde há consulta real. Nenhum duplica o índice automático de FK.

| Model | Índice |
|---|---|
| `TipoLocalOperacional`, `TipoEquipamento`, `EspecialidadeFornecedor`, `TipoContrato`, `TipoDocumento` | `(condominio, ativo)` |
| `LocalOperacional` | `(condominio, tipo_local)` — novo; os atuais permanecem |
| `Equipamento` | `(condominio, tipo_equipamento)` — novo; os atuais permanecem |
| `ContratoFornecedor` | `(condominio, ativo)`, `(condominio, data_fim)`, `(fornecedor, ativo)`, `(data_proximo_reajuste)`, `(condominio, tipo_contrato)` |
| `Garantia` | `(condominio, data_fim)`, `(fornecedor, ativo)`, `(condominio, ativo)` |
| `DocumentoOperacional` | `(condominio, data_validade)`, `(tipo_documento, ativo)`, `(condominio, ativo)` |
| `ArquivoDocumento` | `(sha256)` |
| `HistoricoAcessoCondominio` | `(acesso, data_hora)` |

---

## 13. Grafo de dependências dos apps (§23)

**Estado de hoje** (extraído dos `models.py`):

```
condominios  →  (constants próprias)                    folha
accounts     →  (só django)                             folha
core         →  (só django)                             folha
cadastros    →  condominios
demandas     →  condominios
balancos     →  condominios
manutencoes  →  cadastros, condominios, core, demandas
estoque      →  cadastros, condominios, manutencoes
rotinas      →  cadastros, condominios, core, demandas, manutencoes
```

**Com a Fase 2E:**

```
governanca   →  condominios, cadastros, manutencoes, estoque, rotinas, accounts
```

Regras observadas:

1. `governanca` pode importar os outros seis; **nenhum** deles importa `governanca`.
   As relações adicionais ficam sempre do lado de governança — é o que garante
   ausência de ciclo.
2. `cadastros` recebe os quatro models de tipo/vínculo; continua dependendo apenas
   de `condominios`.
3. `condominios` recebe `HistoricoAcessoCondominio`; continua sem depender de
   nenhum app de negócio.
4. `manutencoes`, `estoque` e `rotinas` **não mudam de grafo**: não ganham FK de
   governança. `ContratoRotina`, `GarantiaManutencao` e `GarantiaItemCompra` são
   linhas do lado de governança, apontando para eles.

Verificação: como `governanca` é folha no sentido inverso (ninguém o importa), não
há possibilidade de importação circular. O risco está eliminado.

---

## 14. Estratégia de Garantias (§8 da entrega)

- Model principal `Garantia` **sem** FKs de origem e **sem** constraint de origem
  única. Ele descreve a garantia em si: prazo, fornecedor, descrição.
- O que é coberto fica em `GarantiaEquipamento`, `GarantiaManutencao`,
  `GarantiaItemCompra` — cada uma com unicidade do par e `observacao` opcional.
- Uma garantia cobre **mais de um objeto**, e pode misturar tipos (o mesmo seguro
  que cobre a bomba nova e o serviço que a instalou).
- Sem `GenericForeignKey`: o conjunto de alvos é pequeno e estável, e a integridade
  referencial fica no banco.
- Um alvo pode ter várias garantias — a manutenção coberta pela garantia de
  serviço e, mais tarde, pela garantia estendida do fabricante.
- `GarantiaProjeto` entra depois sem tocar em `Garantia`.
- `situacao` sempre derivada: `INATIVA`, `FUTURA`, `VENCIDA`, `VENCENDO`, `VIGENTE`.

## 15. Estratégia de Documentos (§9 da entrega)

- Model principal `DocumentoOperacional` **sem** FKs para entidades. Ele descreve o
  documento: tipo, título, número, emissão, validade, emissor.
- O vínculo fica em `DocumentoFornecedor`, `DocumentoContrato`,
  `DocumentoEquipamento`, `DocumentoGarantia`, cada uma com unicidade do par.
- Um documento pode estar em **vários contextos ao mesmo tempo**, e o arquivo não é
  duplicado — o que evita ter o mesmo PDF em três cadastros.
- Sem `GenericForeignKey`. `DocumentoProjeto` entra depois sem tocar no principal.
- Sem vínculo nenhum, o documento continua válido: pertence ao condomínio.
- `relacionado_a` é derivado na leitura; nunca persistido.
- `ArquivoDocumento` é filho 1:N: um documento pode ter original, aditivo e
  comprovante como arquivos distintos.

## 16. ContratoRotina (§10 da entrega)

- Tabela própria, do lado de governança, com `contrato`, `rotina`, `observacao` e
  `criado_em`.
- `UniqueConstraint(contrato, rotina)` e validação de mesmo condomínio.
- **Nenhuma FK de contrato em `RotinaRecorrente`** — `rotinas` não passa a conhecer
  `governanca`, e o grafo permanece acíclico.
- Responde "esta preventiva está coberta por qual contrato?" pela leitura do lado
  de governança.
- Sem acoplamento obrigatório: uma rotina pode existir sem contrato, e um contrato
  sem rotina.

## 17. Gestão de acesso (§11 da entrega)

- Nome da funcionalidade: **"Adicionar acesso"** — não "convite".
- Fluxo A: o e-mail já existe como `User` → criar o vínculo com o condomínio.
- Fluxo B: o e-mail não existe → criar a identidade com senha inutilizável
  (`set_unusable_password()`), criar o vínculo e entregar à gestão o **endereço de
  primeiro acesso** (`accounts:primeiro_acesso`): a própria pessoa define a senha.
- Não se gera senha, não se exibe senha e não se define senha para outra pessoa. O
  único endereço que circula é o de primeiro acesso — de uso único e com prazo —, e
  quem o entrega é a gestão, pelo canal que já usa com a pessoa.
- Pode-se: adicionar acesso, escolher papel, ativar/desativar o vínculo, alterar
  papel e consultar vínculos.
- `User.is_active` ≠ `AcessoUsuarioCondominio.ativo`. Desativar acesso ao condomínio
  altera **o vínculo**. `User.is_active` não é alterado automaticamente.
- Impedir: autoelevação de papel; atuação fora do condomínio autorizado; e deixar o
  condomínio sem ADMIN/SÍNDICA ativo (o último vínculo de gestão não pode ser
  inativado nem rebaixado — mensagem humana, nunca 500).
- O histórico é gravado **na mesma `transaction.atomic()`** da alteração do vínculo.
- Fail closed: sem certeza sobre a autorização, o acesso é negado.

## 18. Storage privado (§12 da entrega) — proposta técnica

O upload **não** é implementado nesta fase. A proposta abaixo precisa de
aprovação antes de virar código.

**Situação atual.** `apps/balancos/storage.py` tem o contrato (`StorageProvider`),
a implementação Supabase e `obter_provider()`, mas está preso ao domínio de
balanços: lê `BALANCOS_STORAGE_PROVIDER` e `BALANCOS_STORAGE_BUCKET`.

**Proposta.** Promover a infraestrutura para uma camada compartilhada, sem
acoplar documentos a balanços:

1. Mover o módulo para `apps/core/storage.py`, preservando `StorageProvider`,
   `SupabaseStorageProvider` e `ErroDeStorage` como estão — mesmas garantias
   (tempo limite em um só ponto, sem retry genérico, credencial só no backend, erro
   que não vaza cabeçalho, token, URL assinada nem corpo do provedor).
2. `obter_provider(bucket=None)` recebe o bucket por parâmetro; a configuração de
   credencial (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) continua global, e cada
   domínio aponta o seu bucket: `BALANCOS_STORAGE_BUCKET` e
   `DOCUMENTOS_STORAGE_BUCKET`.
3. `apps/balancos/storage.py` passa a **reexportar** de `core`, para não quebrar o
   que já existe (`erros_de_referencia_de_storage` e os testes de storage). Sem
   mudança de comportamento para balanços.
4. `governanca` importa de `core` — nunca de `balancos`.

**Regras que valem para documentos**, quando o upload for implementado:

- Bucket privado, sem URL pública e sem filesystem público.
- Validar o MIME real por **magic bytes**, além de extensão e tamanho; mesma
  disciplina já usada nas evidências visuais.
- Chave do objeto derivada e não adivinhável:
  `documentos/{condominio_id}/{documento_id}/{uuid4}.{ext}`.
- Nunca persistir URL assinada; gerar sob demanda, com validade curta, só para quem
  tem autorização confirmada no backend.
- `sha256` para reconhecer o mesmo arquivo; sem unicidade global.
- Falha do provedor **não** cai para disco local — a operação é recusada com erro
  explícito.

## 19. Estratégia dos campos legados (§13 da entrega)

Nada é removido nesta fase. Nos dois casos: adicionar o FK nullable, permitir
preenchimento, preparar o backfill e manter compatibilidade.

**`LocalOperacional.tipo` → `TipoLocalOperacional`**

1. Criar o model e o campo `tipo_local` (`null/blank`).
2. Para cada condomínio, criar os `TipoLocalOperacional` correspondentes aos valores
   hoje em uso no campo `tipo`, mais os que a gestão quiser.
3. Preencher `tipo_local` a partir de `tipo`, quando houver correspondência
   inequívoca. **`outro` e valores customizados ficam sem tipo** até decisão humana —
   nada é inferido automaticamente.
4. Migrar telas, formulários e filtros para `tipo_local`.
5. Só então, em migration própria e depois de dados reais homologados, remover
   `tipo` e o enum legado.

**`Equipamento.tipo_texto` → `TipoEquipamento`**

Mesmo desenho, com uma diferença: sendo texto livre, o de-para não é automático.
Lista-se os valores distintos de `tipo_texto` por condomínio e a gestão confirma o
de-para; o sistema não consolida sinônimos sozinho. `tipo_texto` permanece guardado
como origem do dado até a remoção futura.

**Uso hoje mapeado** (a revisar na implementação): `apps/cadastros/models.py`,
`admin.py`, `forms.py`, `views.py` (`local.get_tipo_display()`,
`equipamento.tipo_texto`), `templates/pages/cadastros/locais.html`,
`templates/pages/cadastros/equipamentos.html`, `tests/test_models.py`,
`tests/test_services.py`, `tests/test_views.py`, o índice `local_cond_tipo_idx` e a
migration `cadastros/0001_initial`. Nas demais apps, `LocalOperacional` e
`Equipamento` são referenciados só por FK — sem uso do campo legado.

## 20. Alertas derivados (§14 da entrega)

Nenhuma tabela, nenhum menu, nenhum cadastro de alerta.

**`ContratoFornecedorQuerySet`**
`ativos()`, `do_condominio()`, `vigentes(hoje=None)`, `vencendo(hoje=None)`,
`vencidos(hoje=None)`, `reajustes_proximos(hoje=None, dias=30)`,
`prazos_rescisao_proximos(hoje=None)`.

**`GarantiaQuerySet`**
`ativos()`, `do_condominio()`, `vigentes(hoje=None)`, `vencendo(hoje=None)`,
`vencidas(hoje=None)`.

**`DocumentoOperacionalQuerySet`**
`ativos()`, `do_condominio()`, `com_validade()`, `vencendo(hoje=None)`,
`vencidos(hoje=None)`, `sem_validade()`.

**`FornecedorQuerySet`**
`ativos()`, `do_condominio()`, `indicados_para_moradores()`,
`indicacao_sem_revalidacao(janela_dias, referencia=None)`.

| Domínio | Situação | Regra |
|---|---|---|
| Fornecedor | aparece aos moradores | `ativo = True` **e** `indicado_para_moradores = True` **e** `ativo_para_moradores = True` |
| Fornecedor | precisa revalidar | publicado e (`validado_em` nulo ou mais antigo que a janela informada) |

A janela de revalidação é **parâmetro** do helper, não regra fixa no banco: o
intervalo defensável depende do que foi indicado. Nenhum campo `precisa_revalidar`
e nenhum model de alerta — a resposta é sempre derivada de `validado_em`.

**Situações derivadas**

| Domínio | Situação | Regra |
|---|---|---|
| Contrato | `INATIVO` | `ativo = False` |
| | `FUTURO` | `data_inicio > hoje` |
| | `VENCIDO` | `data_fim < hoje` |
| | `VENCENDO` | `data_fim` entre `hoje` e `hoje + antecedencia_alerta_dias` |
| | `VIGENTE` | demais casos |
| Contrato | `PRAZO DE DECISÃO PRÓXIMO` | `data_fim - prazo_aviso_rescisao_dias <= hoje`, com contrato vigente |
| Garantia | `INATIVA` / `FUTURA` / `VENCIDA` / `VENCENDO` / `VIGENTE` | mesma lógica, sobre `data_fim` |
| Documento | `SEM VALIDADE` | `data_validade` nulo |
| | `VENCIDO` / `VENCENDO` / `VIGENTE` | sobre `data_validade`, com a antecedência do documento ou o padrão do tipo |

## 21. Menu

```
Cadastros
├ Condomínio
├ Usuários e acessos
├ Áreas e locais        → (contextual) Tipos de local
├ Equipamentos          → (contextual) Tipos de equipamento
├ Fornecedores          → (contextual) Especialidades
├ Contratos             → (contextual) Tipos de contrato
├ Garantias
└ Documentos            → (contextual) Tipos de documento
```

Só Contratos, Garantias e Documentos entram como filhos do menu. Tipos e
especialidades ficam contextuais, dentro da tela a que pertencem — sem item de menu
para tabela auxiliar. Cada item continua perguntando ao domínio se aparece, e
continua **não** autorizando: quem decide acesso é a view.

## 22. Autorização

| Papel | O que pode |
|---|---|
| ADMIN / SÍNDICA | CRUD completo de todos os cadastros estruturantes e gestão de acessos. |
| SUPERVISORA | Leitura dos tipos; criar/editar Contratos, Garantias e Documentos; consultar usuários, sem promover a papéis críticos nem mexer em identidade global. |
| OPERACIONAL | Não administra cadastro estrutural. Vê o necessário à execução, quando apropriado. |
| PORTEIRO / MORADOR | 404 nos cadastros administrativos. |

Toda autorização vem do vínculo ativo com o condomínio, decidida no backend. 404
quando não se enxerga o módulo; 403 quando se enxerga e não se decide. Fail closed.

**Disponibilização de fornecedor aos moradores.** As quatro decisões — editar o
que o morador lê, indicar, liberar e revalidar — são a mesma responsabilidade de
responder pelo condomínio: ficam em `PAPEIS_QUE_GERENCIAM` (ADMIN / SÍNDICA). A
SUPERVISORA vê os dados públicos junto com o cadastro, mas não decide sobre eles.
Liberar (`ativo_para_moradores = True`) exige `indicado_para_moradores = True` e
registra `validado_em` / `validado_por` no service de domínio, sob
`transaction.atomic()` — nunca espalhado pela view.

## 23. Cross-condomínio

Toda relação valida mesmo condomínio. Pontos de atenção explícitos:
`TipoLocalOperacional ↔ Local`, `TipoEquipamento ↔ Equipamento`,
`Fornecedor ↔ Especialidade`, `Contrato ↔ Fornecedor`, `Contrato ↔ Equipamento`,
`Contrato ↔ Local`, `Contrato ↔ Rotina`, `Garantia ↔ objetos`,
`Documento ↔ entidades`, `Usuário ↔ acesso`. Validação em `clean()` e no service.

## 24. Impacto nos módulos existentes

| Módulo | Impacto |
|---|---|
| `cadastros` | **Alterado.** Quatro models novos, dois campos novos em `LocalOperacional` e `Equipamento`, índices novos. `forms.py`, `views.py`, `urls.py`, `admin.py`, templates e testes ajustados. Regra de negócio existente preservada. |
| `condominios` | **Acrescenta** `HistoricoAcessoCondominio`. Sem mudança de campos em `AcessoUsuarioCondominio`. |
| `governanca` | **Novo app**, com todos os models de contrato, garantia e documento. |
| `manutencoes` | **Sem alteração de schema.** Passa a ser referenciado por `GarantiaManutencao` (leitura do lado de governança). Nada muda na manutenção. |
| `rotinas` | **Sem alteração.** Nenhuma FK nova em `RotinaRecorrente`. |
| `estoque` / compras | **Sem alteração.** `ItemCompra` passa a ser referenciado por `GarantiaItemCompra`. |
| `demandas` | **Sem alteração.** |
| `balancos` | **Sem alteração.** `storage.py` passa a reexportar de `core`, sem mudar comportamento. |
| `accounts` | **Sem alteração de campos.** |
| `core` | Recebe `storage.py` compartilhado; `context_processors.py` ganha Contratos, Garantias e Documentos no grupo Cadastros. |

**Balanços — dependências futuras.** Nada é criado agora. Fica registrado que, na
fase financeira própria, um lançamento validado poderá se relacionar a Grupo
Financeiro, Categoria Financeira, Fornecedor, Equipamento, Local, Manutenção,
Demanda, `ExecucaoRotina`, Contrato e Projeto futuro.

**Unidades / pessoas / veículos.** Não implementados; registrados como a próxima
fase estrutural.

## 25. Migrations previstas (§16 da entrega)

Nenhuma é executada agora. A sequência prevista, quando autorizada — os nomes
abaixo são os arquivos realmente gerados:

| # | Migration | Conteúdo | Risco |
|---|---|---|---|
| 1 | `cadastros/0002_tipos_e_fornecedor_para_moradores` | `TipoLocalOperacional`, `TipoEquipamento`, `EspecialidadeFornecedor`, `FornecedorEspecialidade`; campos `tipo_local`, `tipo_equipamento`, `data_aquisicao`, `data_instalacao`; campos de disponibilização do `Fornecedor` (`indicado_para_moradores`, `ativo_para_moradores`, `descricao_publica`, `telefone_publico`, `whatsapp_publico`, `site_publico`, `observacao_publica`, `validado_em`, `validado_por`); índices novos | Nulo — puramente aditivo, tudo `null/blank` ou com `default` |
| 2 | `condominios/0003_historicoacessocondominio` | `HistoricoAcessoCondominio` | Nulo — tabela nova |
| 3 | `governanca/0001_initial` | Todos os models de contrato, garantia e documento | Nulo — app novo, tabelas novas |
| 4 | *(dados, sem schema)* | Backfill confirmado de `tipo_local` e `tipo_equipamento` | Dados — exige conferência caso a caso |
| 5 | *(fase posterior)* | Remoção de `LocalOperacional.tipo` e `Equipamento.tipo_texto` | Alto — só depois de dados reais homologados |

As migrations 1 a 3 são independentes entre si em conteúdo, mas devem ser
aplicadas na ordem acima para respeitar o grafo de dependências. A migration 5
não pertence a esta fase.

## 26. Riscos restantes

1. **Storage compartilhado**: mover `apps/balancos/storage.py` para `core` toca
   código que já funciona. Mitigação: reexportar de `balancos`, rodar os testes de
   storage antes e depois, sem mudança de comportamento.
2. **Backfill de `LocalOperacional.tipo`**: `outro` e custom não têm correspondência
   automática. Mitigação: deixar sem tipo até decisão humana.
3. **Backfill de `Equipamento.tipo_texto`**: texto livre, sem normalização
   confiável. Mitigação: de-para confirmado pela gestão; campo legado preservado.
4. **Duas fontes de verdade enquanto dura a coexistência**: `tipo` e `tipo_local`
   podem divergir. Mitigação: leitura prioriza `tipo_local`, com queda para o
   legado; a escrita nova usa só o FK.
5. **Adicionar acesso cria identidade sem senha utilizável**: quem entra é a própria
   pessoa, pelo endereço de primeiro acesso. Resolvido depois desta fase: a gestão
   entrega o endereço, ele vale por `PASSWORD_RESET_TIMEOUT` e deixa de funcionar
   assim que a senha é definida. Sem depender de e-mail configurado.
6. **Último administrador**: inativar ou rebaixar o último ADMIN/SÍNDICA deixaria o
   condomínio sem gestão. Mitigação: regra no service, com mensagem humana.
7. **`HistoricoAcessoCondominio.acesso` com `PROTECT`**: impede apagar o vínculo
   diretamente. É exatamente o desejado; exige que o fluxo de desativação seja o
   único caminho normal — e é.
8. **Validade obrigatória por tipo**: depende de outra tabela; `CheckConstraint` não
   alcança. Mitigação: `clean()` e service.
9. **Unicidade de documento**: uma constraint ampla bloquearia dois documentos sem
   número. Mitigação: unicidade condicional (`condition=~Q(numero_referencia="")`).
10. **`data_fim >= data_inicio` em `CheckConstraint`**: impede corrigir um registro
    legado incoerente sem passar pelo banco. Mitigação: aceito — a incoerência é
    erro, não estado válido.
11. **Garantia sem vínculo**: o model permite uma garantia que não cobre nada.
    Mitigação: exigir ao menos um vínculo no formulário e no service, sem
    `CheckConstraint` (que não alcança outra tabela).
12. **Volume do menu**: sete itens em Cadastros. Mitigação: tipos contextuais, sem
    item de menu para tabela auxiliar.

## 27. Testes planejados

- **Tipos de local:** CRUD, unicidade case-insensitive, ativo/inativo, vínculo com `LocalOperacional`.
- **Tipos de equipamento:** CRUD, unicidade, vínculo com `Equipamento`.
- **Acessos:** adicionar acesso (fluxo A e fluxo B), mudar papel, ativar/inativar,
  auditoria, regra do último administrador, segurança (não elevar o próprio papel,
  não editar senha, não acessar vínculo de outro condomínio, CSRF, POST-only),
  cross-condomínio.
- **Contratos:** vigência, vencendo, vencido, reajuste, prazo de rescisão,
  fornecedor, equipamentos, locais e rotinas cobertos, cross-condomínio.
- **Garantias:** vínculo com equipamento, manutenção e item de compra; múltiplos
  vínculos; vencendo; vencida.
- **Documentos:** tipo, validade, vencendo, vencido, múltiplos contextos, arquivo
  privado.
- **Alertas:** sempre derivados; nenhuma tabela ou cadastro genérico.

## 28. Implementação — o que existe agora

A Fase 2E foi implementada conforme a modelagem aprovada, com os dois ajustes
finais decididos antes de começar: `HistoricoAcessoCondominio.acesso` com
`PROTECT`, e a recusa de garantia ativa que não cobre nada — esta no domínio,
com `transaction.atomic()`, e **não** como `CheckConstraint` entre tabelas.

### 28.1 Arquivos por app

| App | Arquivos |
|---|---|
| `core` | `storage.py` (infraestrutura de storage privado, `obter_provider`); `context_processors.py` (grupo Cadastros com 8 filhos) |
| `cadastros` | `models.py` (tipos, vínculos, `tipo_local`, `tipo_equipamento`, datas de aquisição e instalação), `constants.py`, `forms.py`, `services.py`, `views.py`, `urls.py`, `admin.py` |
| `condominios` | `models.py` (`HistoricoAcessoCondominio`), `services.py` (acesso: criar, mudar papel, ativar/inativar), `admin.py` (histórico append-only) |
| `governanca` | `models.py`, `constants.py`, `permissoes.py`, `services.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`, `migrations/__init__.py` |
| `balancos` | `storage.py` — apenas reexporta o provider de `core` |

### 28.2 Telas

- Contratos, Garantias e Documentos com listagem, criação, edição, ativação e
  inativação; catálogos contextuais de tipo de contrato e tipo de documento
  abertos ao lado das respectivas listas (sem item de menu próprio).
- Tipos de local, tipos de equipamento e especialidades de fornecedor no mesmo
  padrão contextual, dentro de Cadastros.
- Usuários e acessos: adicionar acesso, mudar papel, retirar e devolver acesso,
  com o vínculo desativado e nunca apagado.

### 28.3 Regras que vivem no domínio

- Único nome por condomínio, sem distinguir maiúsculas de minúsculas, nos quatro
  catálogos novos.
- Garantia ativa precisa cobrir ao menos um equipamento, manutenção ou item de
  compra; a conferência acontece ao ativar e na edição, dentro da transação.
- O condomínio não fica sem gestão: o último vínculo ativo de ADMIN/SÍNDICA não
  pode ser retirado nem rebaixado; ninguém muda o próprio papel.
- Cada operação de acesso grava o histórico na mesma transação da alteração.
- Situações de contrato, garantia e documento (vigente, vencendo, vencido) são
  derivadas na consulta — não existe tabela, campo ou menu de alertas.

### 28.4 Migrations geradas (aplicadas em DEV)

| Migration | Conteúdo | Estado em DEV |
|---|---|---|
| `cadastros/0002_tipos_e_fornecedor_para_moradores` | tipos de local e de equipamento, especialidade, vínculo fornecedor↔especialidade, campos novos em equipamento e local, campos de disponibilização do fornecedor, índices e constraints de unicidade | `[X]` aplicada |
| `condominios/0003_historicoacessocondominio` | `HistoricoAcessoCondominio`, com `acesso` em `PROTECT` | `[X]` aplicada |
| `governanca/0001_initial` | contrato, garantia, documento e todos os vínculos, arquivo, índices e constraints | `[X]` aplicada |

Aplicação autorizada e executada **somente no DEV remoto** (Neon). Nenhuma
alteração de infraestrutura, nenhuma aplicação em produção, nenhum PostgreSQL
local.

### 28.5 Verificações executadas

| Verificação | Resultado |
|---|---|
| `manage.py check` | `System check identified no issues (0 silenced).` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` |
| `manage.py test apps.cadastros apps.condominios apps.governanca --keepdb` | **202 testes, `OK`** (pós-migrate) |
| Homologação `eithra_homologacao_2e.py` (§9–§32), em transação revertida | **101 aprovados, 0 falhas** |
| `showmigrations cadastros condominios governanca` | as três migrations acima em `[X]` — aplicadas |
| **`manage.py test` — suíte completa, PostgreSQL local** | **1110 testes, `OK`** |

Nenhum settings oficial do Eithra usa SQLite — inclusive `config.settings.test`,
que também roda sobre PostgreSQL via `DATABASE_URL`. O caminho seguro para o
desenvolvimento local está em [ambiente_postgresql_local.md](ambiente_postgresql_local.md).

#### Lote `apps.balancos` + `apps.core` — validado no PostgreSQL local

O lote `apps.balancos` + `apps.core` não havia concluído no DEV remoto por
instabilidade de rede (queda de DNS do Neon e PgBouncer encerrando a conexão),
não por falha de teste. Ele foi então executado contra o **PostgreSQL local**
— banco de teste criado e destruído pelo próprio Django — e passou.

A validação local foi feita pela **suíte completa do projeto** (não apenas o
lote), o que cobre `apps.balancos` e `apps.core` e ainda confirma que nenhum
app fora do escopo da fase regrediu:

```
Found 1110 test(s).
Ran 1110 tests in 2282.311s
OK
```

Resultado: **1110 testes, `OK`**, com criação e destruição normal do banco de
teste PostgreSQL. Com isso, a instabilidade do Neon deixa de ser pendência
técnica da Fase 2E: era limitação de infraestrutura de acesso remoto, e o
código está aprovado tanto no DEV remoto quanto localmente.

Nenhum app fora de `cadastros`, `condominios` e `governanca` produziu migration:
`makemigrations --check --dry-run` no projeto inteiro não apontou mudança
pendente.

### 28.6 O que ficou de fora, de propósito

Faixa de backfill de `LocalOperacional.tipo` e `Equipamento.tipo_texto`: a
leitura prioriza o FK novo com queda para o legado, e a escrita nova usa só o
FK. Os campos legados continuam no banco e nenhum de-para automático foi
executado. Unidades, moradores e veículos, projetos e obras, OCR, notificações,
painel do dia, assinatura eletrônica e classificação financeira nova não fazem
parte desta fase.

### 28.7 Fornecedores indicados aos moradores (ajuste final)

Fornecedores indicados aos moradores reutilizam o cadastro de `Fornecedor` e
`EspecialidadeFornecedor`, com dados públicos explicitamente separados dos dados
administrativos — sem cadastro paralelo, sem marketplace, sem duplicação.

- **Elegibilidade (derivada):** `Fornecedor.objects.indicados_para_moradores()`
  devolve o fornecedor somente quando `ativo`, `indicado_para_moradores` e
  `ativo_para_moradores` são verdadeiros, todos ao mesmo tempo. Tirar qualquer uma
  das três já muda a leitura seguinte — nada disso é guardado.
- **Validação humana:** liberar (`ativo_para_moradores = True`) exige
  `indicado_para_moradores = True` e registra `validado_em` / `validado_por`. A
  regra vive no service (`_gravar_fornecedor` / `_assinar_liberacao`), sob
  `transaction.atomic()`. A assinatura acontece só na passagem para "disponível":
  corrigir a descrição depois não reinicia o prazo de revalidação.
- **Revalidação derivada:** `indicacao_sem_revalidacao(janela_dias, referencia)`
  aponta as indicações publicadas cujo `validado_em` passou da janela — ou que não
  têm validação registrada. A janela é parâmetro; não há campo `precisa_revalidar`
  nem model de alerta.
- **Separação de dados:** `email`, `telefone`, `contato` e `observacao` permanecem
  internos. O morador lê apenas `descricao_publica`, `telefone_publico`,
  `whatsapp_publico`, `site_publico` e `observacao_publica`.
- **Inativação:** `ativo = False` nunca aparece aos moradores;
  `ativo_para_moradores = False` retira da indicação sem apagar o cadastro.
- **Interface:** seção "Disponibilização para moradores" nos modais de criação e de
  edição do fornecedor (Cadastros → Fornecedores), recolhida — progressive
  disclosure —, sem novo item de menu. A lista mostra o selo "Indicado aos
  moradores" quando o fornecedor está publicado.
- **Escopo:** a área do morador (busca, contratação, avaliações, favoritos,
  notificações) **não** faz parte desta fase — apenas o domínio fica preparado.
- **Proveniência:** `validado_em` e `validado_por` são o registro mínimo; nenhum
  histórico específico foi criado para este requisito.
- **Testes:** `apps/cadastros/tests/test_fornecedor_moradores.py` cobre elegibilidade,
  regra da indicação, `validado_em`/`validado_por`, separação de dados, reuso de
  especialidades, cross-condomínio, permissões e revalidação derivada.

## 29. Homologação e defeito corrigido

A homologação funcional da fase rodou contra o DEV remoto, dentro de uma única
transação com `set_rollback(True)` — nenhum registro de teste ficou no banco.
Cobriu §9–§32: tipos de local e de equipamento, especialidades, fornecedor
indicado pela tela, regras de indicação, permissões, usuários e acessos,
alteração de acesso, regra do último gestor, contratos, cobertura, alertas
derivados, garantias (com cobertura única e múltipla, e recusa sem cobertura),
documentos, validade, relacionamentos, arquivo privado, regressão de Balanços,
menu, linguagem e dados de homologação. Resultado: **101 aprovados, 0 falhas**.

### 29.1 Defeito real encontrado e corrigido

A homologação revelou **um defeito de domínio**: liberar um fornecedor aos
moradores **pela tela de edição** não registrava a autoria da liberação —
`validado_em` e `validado_por` ficavam vazios.

- **Sintoma:** após editar um fornecedor e marcar "disponível para moradores",
  o fornecedor passava a aparecer em `indicados_para_moradores()`, mas sem
  registro de quem respondeu nem de quando.
- **Causa-raiz:** `editar_fornecedor` lia o estado anterior
  (`disponivel_antes`) do objeto em memória. Como o `ModelForm` já havia
  escrevido os campos limpos na própria instância durante `is_valid()`, o valor
  lido já era o novo; a passagem `False → True` nunca era detectada e a
  assinatura da liberação era pulada.
- **Correção:** o "antes" passou a ser lido do banco
  (`Fornecedor.objects.filter(pk=...)`), e não do objeto recebido.
- **Regressão coberta:** `test_sindica_libera_pela_edicao_e_fica_registrado`
  cobre o fluxo pela tela de edição — o teste anterior só cobria a criação.

A leitura pela criação sempre esteve correta; o defeito vivia apenas no caminho
de edição.

### 29.2 Conferência de responsividade (desktop / mobile)

As telas novas seguem o mesmo padrão de responsividade do restante do sistema:
a lista é uma **tabela no desktop** (`table-responsive d-none d-lg-block`) e
vira **cartões empilhados no mobile** (`list-group list-group-flush d-lg-none`).
Não há tabela sem o par correspondente — a regra mobile-first da Constituição
("tabelas devem virar cartões quando necessário") é atendida em todas elas.

| Tela nova | Tabela (≥ lg) | Cartões (< lg) |
|---|---|---|
| `cadastros/tipos_de_local.html` | sim | sim |
| `cadastros/tipos_de_equipamento.html` | sim | sim |
| `cadastros/especialidades.html` | sim | sim |
| `cadastros/acessos.html` | sim | sim |
| `cadastros/locais.html` | sim | sim |
| `cadastros/equipamentos.html` | sim | sim |
| `cadastros/fornecedores.html` | sim | sim |
| `governanca/contratos.html` | sim | sim |
| `governanca/garantias.html` | sim | sim |
| `governanca/documentos.html` | sim | sim |
| `governanca/tipos_de_contrato.html` | sim | sim |
| `governanca/tipos_de_documento.html` | sim | sim |

Também conferido: `viewport` presente em `templates/base.html`; cabeçalhos de
página com `flex-wrap` (o título e os botões quebram linha em tela estreita);
modais em `modal-dialog-centered`, com uma coluna e progressive disclosure; e
ações no mobile com rótulo textual, não apenas ícone.

**Método e limite desta conferência.** A verificação foi **estrutural**, feita
sobre os templates: confirma que o par responsivo existe e que nenhuma lista
nova depende de tabela em tela estreita. **Não substitui a conferência visual
de acabamento** — quebra de linha, alinhamento de ícones, altura de modal,
toque —, que é julgamento humano com o sistema aberto no navegador. Nenhum
bloqueio funcional foi encontrado; o que fica é a conferência de acabamento,
nas telas acima, em larguras de desktop e de celular.

## 30. Fechamento da fase

A Fase 2E está **aplicada e homologada em DEV remoto** e **validada
localmente**:

- as três migrations estão em `[X]` no DEV remoto;
- `check` limpo e nenhuma migration pendente em todo o projeto;
- lote `cadastros` + `condominios` + `governanca`: **202 testes, `OK`**;
- suíte completa no **PostgreSQL local**: **1110 testes, `OK`**;
- homologação funcional em transação revertida: **101 aprovados, 0 falhas**;
- um defeito de domínio encontrado e corrigido, com teste de regressão
  (§29.1);
- responsividade conferida estruturalmente em todas as telas novas (§29.2).

Permanece em aberto apenas a **conferência visual de acabamento** das telas
listadas em §29.2 — sem bloqueio funcional conhecido.

Depois disso, e só então, a decisão sobre backfill e remoção dos campos
legados. Unidades, moradores e veículos, projetos e obras, OCR, notificações,
painel do dia, assinatura eletrônica e classificação financeira nova continuam
fora desta fase.
