# Fase 1A — Balanços Mensais: Modelagem Proposta

> Proposta conceitual. **Nenhum model é criado nesta fase.** Os esboços abaixo
> descrevem intenção de dados, não código a executar.

## 1. Pergunta obrigatória: `Condominio` é contexto suficiente?

**Sim.** Para importar, revisar, validar e publicar um balanço mensal, o
`Condominio` existente é contexto suficiente.

Justificativa:

- o balanço é um documento **do condomínio**, não de uma unidade, torre ou área;
- nenhum campo do fluxo de balanço exige hierarquia predial;
- inadimplência, quando aparecer, deve ser **agregada** (não individual) — ver
  `fase_1a_balancos_privacidade.md`;
- criar estrutura predial agora seria modelar por antecipação, violando o princípio
  da fase.

**Decisão registrada: NÃO criar novos models de Prédio na Fase 1A.**

## 2. O que NÃO deve existir agora

- `Unidade`, `Bloco`, `Torre`, `Pavimento`, `Área`;
- `Equipamento`, `SistemaPredial`;
- `Fornecedor` (cadastro);
- `Manutencao`, `Preventiva`, `Obra`;
- módulo de contas a pagar/receber;
- portal do morador e publicação real.

Todos só entram quando houver necessidade concreta de fluxo.

## 3. Entidades propostas

### 3.1 `BalancoMensal` (agregado raiz)

Representa um mês de um condomínio em uma versão.

| Campo (intenção) | Natureza | Observação |
| --- | --- | --- |
| `condominio` | FK | Isolamento por condomínio. Preservar histórico → evitar `CASCADE`. |
| `competencia_ano`, `competencia_mes` | inteiros | Competência **estruturada**, não string solta. Rótulo "2026-08"/"Agosto/2026" é derivado. |
| `versao` | inteiro | Começa em 1; incrementa na substituição. |
| `substitui` | FK para si | Versão anterior, quando houver. |
| `motivo_substituicao` | texto | Quando aplicável. |
| `situacao_funcional` | escolha | `VALIDADO`, `PUBLICADO`, `SUBSTITUIDO`. Revisado na Fase 1B: sem `EM_REVISAO`, pois não há workflow formal de revisão. |
| `estado_processamento` | escolha | `PENDENTE`, `PROCESSANDO`, `CONCLUIDO`, `ERRO` (interno). |
| `formato_documento` | escolha | `ATUAL_CANONICO`, `LEGADO`. |
| `documento` | FK | Documento original (ver 3.2). |
| `dados_extraidos` | JSON tipado | Saída da extração (com provenance e confidence). |
| `dados_validados` | JSON tipado | Valores confirmados pela gestão. É o oficial. |
| `pendencias` | JSON tipado | Exceções abertas/resolvidas. |
| `resumo_gestao` | texto/JSON | Diagnóstico do mês para a gestão. |
| `rascunho_transparencia` | JSON tipado | Versão simplificada, **não publicada**. |
| `validado_por`, `validado_em` | FK/data | `on_delete=SET_NULL` (preserva registro, não o responsável excluído). |
| `publicado_por`, `publicado_em` | FK/data | Idem. |
| `criado_por`, `criado_em`, `atualizado_em` | auditoria básica | — |

**Constraint de unicidade (proposta):**

- no máximo **um** `BalancoMensal` em estado `VALIDADO` ou `PUBLICADO` por
  (`condominio`, `competencia_ano`, `competencia_mes`);
- versiona-se por `versao` crescente; a versão anterior torna-se `SUBSTITUIDO`.

Isso é uma *constraint condicional* (não "unique condomínio + mês" simples), porque
o spec exige suportar reimportação, substituição e nova versão.

### 3.2 `DocumentoBalanco` (documento original + origem)

O PDF original vive em **storage externo privado** — nunca no filesystem da Vercel.

| Campo (intenção) | Observação |
| --- | --- |
| `condominio` | Isolamento. |
| `chave_storage` | Referência/chave do objeto no storage privado. |
| `nome_original` | Nome do arquivo enviado. |
| `mime_type` | `application/pdf` (validar por assinatura, não só extensão). |
| `tamanho_bytes` | — |
| `hash_sha256` | Detecção de duplicidade. |
| `origem` | `UPLOAD_MANUAL`, `EMAIL`, `ADMINISTRADORA`. |
| `importado_por`, `importado_em` | Rastreio. |
| `competencia_ano`, `competencia_mes` | Ajuda a vincular ao balanço. |

O acesso ao arquivo é por **view autenticada** que valida o vínculo com o
condomínio e emite **signed URL** temporária. Nunca URL pública direta.

### 3.3 `EventoBalanco` (trilha funcional)

Registro enxuto de eventos: `tipo`, `balanco`, `versao`, `autor`
(`on_delete=SET_NULL`), `quando`, `resumo`. Cobre upload, processamento, correção,
validação, substituição, publicação e futura despublicação.

### 3.4 `FornecedorCandidato` — **não** é model persistente

É um **conceito de extração/reconciliação**: um nome/CNPJ encontrado no documento.
Vive dentro de `dados_extraidos`. Distingue:

- **fornecedor operacional** (cadastro, futuro);
- **beneficiário do documento** (o que o PDF diz).

Nenhum nome do PDF vira fornecedor cadastrado automaticamente.

## 4. Decisão obrigatória — Lançamentos financeiros (seção 12)

### Opções avaliadas

| Critério | A. Relacional individual | B. JSON normalizado no balanço | C. Híbrido |
| --- | --- | --- | --- |
| Volume mensal (centenas de linhas por balanço, ~12/ano/condomínio) | Bom | Bom | Bom |
| Busca por lançamento | Forte | Fraca | Média |
| Comparação mensal (categorias, totais) | Forte | Média | Forte (via campos-resumo) |
| Relação futura com Fornecedor | Forte | Fraca | Média→Forte |
| Relação futura com Manutenção/Obra | Forte | Fraca | Média |
| Correção e versionamento | Complexo (linhas mutáveis) | Simples | Simples |
| Auditabilidade (original × validado) | Difícil | Boa | Boa |
| Simplicidade | Baixa | Alta | Média |
| Custo de evolução | Alto | Baixo | Baixo |

### Recomendação: **C — Híbrido**

Estratégia:

1. **Agora (Fase 1B+):** balanço como agregado relacional (`BalancoMensal`), com os
   lançamentos guardados em **estrutura normalizada dentro de `dados_extraidos` e
   `dados_validados`** (JSON tipado, validado por schema). Os **totais e resumos**
   (receitas, despesas, saldo, por categoria) ficam como **campos relacionais** no
   balanço, para busca, ordenação e comparação mensal.
2. **Depois (quando houver necessidade comprovada):** promover os lançamentos a
   models relacionais individuais, quando existirem operação, fornecedor cadastrado
   e reconciliação exigindo *join*.

Justificativa:

- não escolher A "porque é mais normalizado" — seria custo sem benefício imediato;
- manter lançamentos em JSON preserva **integridade da extração original** e
  simplifica correção/versionamento;
- ter os resumos como campos relacionais entrega **busca e comparação** já na
  primeira versão;
- o caminho de promoção para relacional fica aberto, sem refazer a fundação.

### Vista de dados (conceitual)

```
BalancoMensal
 ├─ condominio            (isolamento)
 ├─ competencia           (ano, mês)
 ├─ versao / substitui
 ├─ estados               (funcional + processamento)
 ├─ documento ─────────→ DocumentoBalanco  (storage privado)
 ├─ dados_extraidos       (lançamentos + provenance + confidence)
 ├─ dados_validados       (lançamentos confirmados)
 ├─ resumos relacionais   (receitas, despesas, saldo, categorias)
 ├─ pendencias
 ├─ resumo_gestao
 └─ rascunho_transparencia
        ↑
     EventoBalanco (auditoria)
```

## 5. Isolamento e segurança de dados

- toda informação pertence a **um** condomínio;
- nenhuma consulta pode depender de `.first()`, ID fixo, nome, menu ou frontend;
- autorização reutiliza `apps/condominios/acesso.py` (`tem_acesso_ativo`);
- princípio *fail-closed*: sem autorização confirmada, acesso negado;
- balanço de outro condomínio é **inexistente** para quem não tem vínculo ativo
  (resposta 404).

## 6. Compatibilidade com a V2 (referência)

A V2 já possui `BalancoMensal` com `competencia` (MM/AAAA), `versao`, `substitui`,
`dados_extraidos`, `dados_validados` e um pacote `importador_financeiro` com
`detector_layout`, `parsers`, `normalizadores`, `conciliacao`, `matching_fornecedor`,
`preview` e `projecao_morador`. A classificação detalhada
(REUTILIZAR CONCEITO / ADAPTAR / DESCARTAR) está em
`fase_1a_balancos_decisoes.md`.

## 7. Riscos arquiteturais

1. **Acoplar o modelo ao layout** de um fornecedor específico (evitar campos
   `pagina_empresa_x`, `linha_modelo_y`).
2. **Escolher relacional prematuro** e pagar custo de migração sem necessidade.
3. **Resolver duplicidade só com "unique condomínio + mês"**, impedindo versão
   corrigida.
4. **Publicação automática** após validação — proibida.
5. **Vazamento de dado sensível** no resumo do morador (projeção feita por padrão
   permissivo).
6. **Guardar o PDF em filesystem efêmero** (Vercel), perdendo o original.
7. **Fallback de condomínio** (pegar "o primeiro") — reaparece como risco se
   copiado da V2.
