# Fase 1A — Balanços Mensais: Decisões

> Registro de decisões da modelagem, classificação dos aprendizados da V2 e
> respostas objetivas da seção 32 do spec. Nada foi implementado.

## 1. Decisões desta análise

Legenda de status: **APROVADA** = já fixada pelas diretrizes do spec;
**PENDENTE** = requer aprovação explícita antes da implementação.

### D1 — `Condominio` é contexto suficiente de prédio — APROVADA
Para importar, revisar, validar e publicar um balanço, o condomínio basta.

### D2 — Não criar estrutura predial por antecipação — APROVADA
Sem `Unidade`, `Bloco`, `Torre`, `Pavimento`, `Área`, `Equipamento`,
`SistemaPredial` nesta fase.

### D3 — Lançamentos em arquitetura **híbrida** (opção C) — PENDENTE
Balanço relacional como agregado; lançamentos em estrutura normalizada (JSON
tipado) dentro do balanço; **resumos e totais como campos relacionais** para busca
e comparação. Promoção a relacional individual somente quando houver necessidade
comprovada (operação/fornecedor/reconciliação). Justificativa completa em
`fase_1a_balancos_modelagem_proposta.md` §4.

### D4 — Separar situação funcional de estado técnico — REVISADA na Fase 1B
Situação funcional: `VALIDADO`, `PUBLICADO`, `SUBSTITUIDO`. Estado técnico:
`PENDENTE`, `PROCESSANDO`, `CONCLUIDO`, `ERRO`.
A etapa `EM_REVISAO` foi **removida** por decisão de produto: a V3 inicial não
possui workflow formal de revisão. A gestão confirma a importação antes de
torná-la válida. Ver `v3_decisoes_arquiteturais.md` (C2, C3).

### D5 — Competência estruturada + unicidade condicional — APROVADA na Fase 1B
Competência como `DateField` no primeiro dia do mês (não string solta). No
máximo uma versão **vigente** — `VALIDADO` ou `PUBLICADO` — por (condomínio,
competência). Versionamento por `versao`. Ver `v3_decisoes_arquiteturais.md` (C1,
C4).

### D6 — Versionar, nunca sobrescrever — APROVADA
Substituição cria nova versão (`substitui` → anterior); a anterior vira
`SUBSTITUIDO` e é preservada com documento, histórico, autor e motivo.

### D7 — Documento original em storage externo privado — APROVADA
Nunca no filesystem da Vercel. Acesso somente por view autenticada + signed URL.
Campos: chave, nome original, MIME, tamanho, hash, data, usuário, condomínio,
competência, origem. (Storage não é implementado nesta fase.)

### D8 — Provenance obrigatória — APROVADA
Todo dado relevante sabe de onde veio; rastreável internamente, sem poluir a UI
principal.

### D9 — `FornecedorCandidato` é conceito de extração — APROVADA
Não é model persistente. Nome do PDF nunca vira fornecedor cadastrado.

### D10 — Publicação nunca automática — APROVADA
`VALIDADO` → preparar resumo → revisão da gestão → publicar.

### D11 — Projeção do morador restritiva por padrão (fail-closed) — APROVADA
Só entra na projeção o que for explicitamente publicável. Ver
`fase_1a_balancos_privacidade.md`.

### D12 — Serviços pequenos e coesos — APROVADA
Sem `balanco_service.py` gigante; sem dezenas de micro-serviços artificiais.

### D13 — Isolamento por condomínio via fundação existente — APROVADA
Reutilizar `apps/condominios/acesso.py` (`tem_acesso_ativo`, fail-closed). Proibido
`.first()`, ID fixo, nome, menu ou frontend como controle de acesso.

## 2. Consulta à V2 (seção 30) — classificação

Referência lida em modo somente-leitura: `D:\MARIA_BKP\github\eithra_v2`
(app `core`). A V2 **não** foi alterada. Nenhuma arquitetura é copiada
automaticamente.

Evidência concreta consultada: `core/models.py` (`BalancoMensal`,
`StatusBalancoMensalChoices`, `PublicacaoMorador`), `core/services/balanco_mensal_service.py`
e o pacote `core/services/importador_financeiro/` (`dtos.py`, `detector_layout.py`,
`parsers.py`, `normalizadores.py`, `conciliacao.py`, `matching_fornecedor.py`,
`preview.py`, `projecao_morador.py`, `reconciliacao_operacional.py`).

### REUTILIZAR CONCEITO

| Achado na V2 | Motivo |
| --- | --- |
| Pipeline em etapas pequenas com DTOs **imutáveis** (`dtos.py`: `Provenance`, `BalanceteExtraido`, `LancamentoDespesaExtraido`, `Inconsistencia`) | Já materializa a separação extraído × validado que o spec exige, com baixo acoplamento |
| `Provenance` como objeto próprio | Atende diretamente à seção 7 |
| `Inconsistencia` como base da conferência por exceção (`conciliacao.verificar_*`) | Atende à seção 17 sem exigir varredura humana |
| `FornecedorCandidato` **não persistente** (`TipoFornecedorCandidato`, `matching_fornecedor`) | Exatamente o que a seção 14 pede |
| `projecao_morador` + `garantir_privacidade` | Já separa a saída pública por projeção (seção 18) |
| `preview` com contagem de pendências e destaques | Atende à seção 17 |
| `comparar_balancetes` (determinístico) | Atende à seção 20 (comparação sem ML) |
| `dados_extraidos` **≠** `dados_validados` | Já é a distinção central da seção 6 |
| `versao` + `substitui` + `criar_versao_corrigida` | Atende às seções 9 e 23 |
| `normalizadores` (valor BR, data, competência, CNPJ, NF, parcela) | Base determinística p/ a seção 6 |

### ADAPTAR

| Achado na V2 | Adaptação necessária | Motivo |
| --- | --- | --- |
| `competencia` como `CharField "MM/AAAA"` + regex | Competência **estruturada** (ano + mês) | Evitar mês/ano em string solta (seção 9) |
| `StatusBalancoMensalChoices` com 9 estados | Separar **funcional** (3) e **técnico** (4) | Evitar excesso de status (seção 10) |
| `BalancoMensal.condominio` com `on_delete=CASCADE` | Preservar histórico (`PROTECT`/`SET_NULL`) | Regra de preservação histórica |
| `arquivo_pdf = FileField` + `pdf_publico` booleano | Storage externo privado + chave + signed URL + classificação explícita | Seção 8 e privacidade |
| Ausência de unicidade por competência | Constraint **condicional** (VALIDADO/PUBLICADO) | Seção 9 e 24 |
| `PublicacaoMorador` acoplada (M2M de unidades, alvo, visibilidade) | Nesta fase só `rascunho_transparencia`; publicação real fora do escopo | Seção 29 |
| Automatização de recebimento por Gmail (threading, sem Celery) | Tratar apenas como **conceito futuro**; não implementar | Evitar arquitetura prematura |

### DESCARTAR

| Achado na V2 | Motivo |
| --- | --- |
| `_fallback_condominio_25` (retorna o primeiro condomínio ativo por pk) | Viola isolamento; sem fallback silencioso (fail-closed) |
| Consultas por `.first()` / ID fixo / nome do condomínio | Proibido pela seção 26 |
| Qualquer campo acoplado ao layout do PDF | Proibido pela seção 5 |
| Estados ligados a "aguardando administradora/documento" no fluxo inicial | Ruído; o ciclo começa em importação real |
| PDF original publicado por simples booleano | Publicação exige revisão editorial |

## 3. Relatório — respostas objetivas (seção 32)

1. **`Condominio` é suficiente como contexto de prédio para Balanços?**
   Sim. Nenhum fluxo de balanço exige hierarquia predial.

2. **Quais entidades realmente precisam existir?**
   `BalancoMensal` (agregado), `DocumentoBalanco` (arquivo original + origem) e
   `EventoBalanco` (trilha funcional). Nada além disso nesta fase.

3. **Quais NÃO devem existir agora?**
   `Unidade`, `Bloco`, `Torre`, `Pavimento`, `Área`, `Equipamento`,
   `SistemaPredial`, `Fornecedor` (cadastro), `Manutencao`/`Preventiva`/`Obra`,
   contas a pagar/receber, portal do morador, publicação real.

4. **Qual é o modelo conceitual recomendado?**
   Balanço como agregado relacional versionado, com documento original separado,
   lançamentos em estrutura normalizada interna e resumos relacionais, mais trilha
   de eventos. Detalhe em `fase_1a_balancos_modelagem_proposta.md`.

5. **Lançamentos: relacional, JSON ou híbrido?**
   **Híbrido (C)**: JSON normalizado para lançamentos; relacional para resumos,
   totais e chaves. Promoção a relacional sob demanda comprovada.

6. **Como preservar o documento original?**
   Storage externo privado, com chave/hash/metadados; acesso por view autenticada +
   signed URL temporária. Nunca no filesystem da Vercel; nunca URL pública.

7. **Como preservar provenance?**
   Objeto de provenance por dado relevante (documento, competência, página/seção,
   automático × corrigido, quando entrou, valor original × validado).

8. **Como representar extraído vs validado?**
   Dois conjuntos distintos no balanço: `dados_extraidos` (com confidence e
   pendências) e `dados_validados` (oficial). O oficial é sempre explícito.

9. **Como tratar versões do mesmo mês?**
   `versao` crescente + `substitui` (FK para si). Versão anterior é preservada e
   marcada `SUBSTITUIDO`. Nunca sobrescrever.

10. **Como evitar duplicidade?**
    Cruzar condomínio + competência + hash + versão + indicadores documentais.
    Hash idêntico → recusa com alerta. PDF diferente da mesma competência →
    possível nova versão, decidida pela gestão.

11. **Como reconciliar futuramente com operação?**
    Conceito `registro contábil ↔ evento operacional`. Nesta fase, apenas a
    estratégia; nunca somar o mesmo custo duas vezes.

12. **Como tratar nomes de fornecedores encontrados?**
    Como `FornecedorCandidato` (extração), distinguindo fornecedor operacional de
    beneficiário do documento. Nunca criar cadastro automaticamente.

13. **Quais dados são internos?**
    `GESTAO`, `SENSIVEL` e `NAO_PUBLICAVEL` — ver
    `fase_1a_balancos_privacidade.md` §3.

14. **Quais podem ser publicados?**
    Somente `PUBLICAVEL`, e apenas após revisão editorial: competência, totais de
    receitas/despesas, saldo, principais categorias, principais realizações,
    resumo aprovado, comparação simples.

15. **Como será a conferência por exceção?** — REVISADA na Fase 1B
    Não há fila nem tela de revisão. Na confirmação da importação, o sistema
    destaca apenas o que é excepcional: divergências matemáticas, categoria
    ambígua, fornecedor não identificado, valor duvidoso, lançamento incompleto,
    duplicidade, conflito e baixa confiança. Consistente → "pronto para
    confirmação".

16. **Qual fluxo de telas futuro?**
    Balanços (lista) → Detalhe (abas) → confirmação da importação → Preparar
    resumo → Publicar. Ver `fase_1a_balancos_ux.md`.

17. **Quais componentes seriam páginas?**
    Lista de balanços; detalhe do balanço; preparar resumo para moradores;
    histórico.

18. **Quais seriam modais?**
    Importar balanço; resolver pendência; substituir versão; confirmar publicação;
    ver documento original.

19. **Quais riscos arquiteturais existem?**
    Acoplar ao layout do fornecedor; relacional prematuro; unicidade simples
    bloqueando versão; publicação automática; vazamento de dado sensível; PDF em
    filesystem efêmero; reaparecimento de fallback de condomínio.

20. **Quais decisões precisam de aprovação antes da implementação?**
    - **D3** — arquitetura híbrida dos lançamentos (recomendada).
    - **D4** — separação de estados funcional × técnico.
    - **D5** — competência estruturada + constraint condicional.
    - Escolha concreta do **storage externo privado** (provedor/credenciais).
    - Definição de **quais categorias** são publicáveis por padrão (calibração da
      projeção do morador).

## 4. Próximo passo

Nenhum. Esta fase **para** após documentação + relatório. A implementação (migrations,
models, views, templates, importador, storage) só inicia após aprovação da
modelagem e das decisões pendentes acima.
