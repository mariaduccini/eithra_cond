# Fase 1A — Balanços Mensais: Contrato de Extração

> Contrato **interno**, independente do PDF de qualquer fornecedor. Nesta fase
> apenas se define o contrato; nenhum parser é implementado.

## 1. Princípio

O modelo interno **não depende do layout visual** de um fornecedor.

Proibido criar campos como:

```
pagina_empresa_x
linha_modelo_y
coluna_z
```

O contrato descreve **o que** é o dado (competência, lançamento, saldo,
inconsistência, confiança), não **onde** ele estava no PDF.

## 2. Famílias de formato

| Formato | Características | Tratamento |
| --- | --- | --- |
| `ATUAL_CANONICO` | PDF nativo, texto selecionável, layout estável | Extração direta de texto por regras + posição |
| `LEGADO` | Pode ter OCR, menor confiança, dados ausentes, menor detalhamento | Mesmo contrato, porém com confidence mais baixo e mais pendências |

O formato **atual é o principal**. O legado deve funcionar sem exigir um segundo
modelo de dados.

## 3. Objetos de transferência (DTOs imutáveis)

Todos são estruturas de leitura, imutáveis, criadas no pipeline. Nunca são
persistidos como "fato".

### 3.1 `Provenance`

Responde, para cada dado relevante:

- de qual documento veio;
- de qual competência;
- qual página/seção (quando disponível);
- foi extraído automaticamente?;
- foi corrigido por pessoa?;
- foi confirmado?;
- quando entrou no sistema;
- qual era o **valor original**;
- qual é o **valor validado**.

### 3.2 `LancamentoDespesaExtraido`

Intenção de campos: descrição/histórico, valor, data, categoria, subcategoria,
beneficiário bruto, CNPJ (quando houver), número de documento/NF, parcela,
provenance, confiança.

### 3.3 `LancamentoReceitaExtraido`

Intenção: descrição, valor, data, origem/tipo, provenance, confiança.

### 3.4 `ResumoFinanceiro`

Totais informados pelo documento: receitas totais, despesas totais, saldo
informado, saldo anterior, saldo final. Campo ausente permanece **ausente**.

### 3.5 `SaldoResumo`

Saldo por conta/banco (quando disponível): identificação, valor. **Uso interno.**

### 3.6 `InadimplenciaResumo`

Somente **agregado**: percentual/valor total. Nunca por unidade.

### 3.7 `FundoResumo`

Fundos (reserva, obras etc.) quando o documento discriminar.

### 3.8 `FornecedorCandidato`

Nome/CNPJ encontrado no documento. É **candidato**, não fornecedor cadastrado.
Carrega o tipo (operacional vs beneficiário) apenas como rótulo de extração.

### 3.9 `Inconsistencia`

Tipo + severidade + descrição humana + referência ao ponto (lançamento/bloco) +
`Provenance`. É a base da conferência por exceção.

### 3.10 `BalanceteExtraido` (raiz da extração)

Agrega: competência detectada, formato, resumo financeiro, lançamentos de despesa,
lançamentos de receita, saldos, fundos, inadimplência agregada, fornecedores
candidatos, inconsistências, completude e confiança global.

## 4. Níveis de confiança

Confiança é **interna**. Nunca é exibida como score ao usuário; vira frase humana
(ex.: "valor não confirmado").

| Nível | Significado | Consequência |
| --- | --- | --- |
| `ALTA` | Campo lido com clareza e coerente | Segue sem exigir conferência |
| `MEDIA` | Lido, mas ambíguo ou parcialmente inferido | Vira pendência leve |
| `BAIXA` | Duplo sentido, ilegível ou ausente | Vira exceção obrigatória |

## 5. Tipos de inconsistência (base da conferência por exceção)

- divergência matemática (total ≠ somatório);
- total de despesas ≠ soma dos lançamentos;
- totais por categoria divergentes;
- saldo informado ≠ saldo calculado;
- valor/moeda duvidosa;
- lançamento incompleto;
- categoria ambígua;
- beneficiário não identificado;
- duplicidade;
- competência divergente do conteúdo.

## 6. Normalização

Regras determinísticas antes de qualquer IA:

| Entrada | Normalização |
| --- | --- |
| Valor "1.234,56" / "R$ 1.234,56" | Decimal brasileiro |
| Data "05/08/2026" | Data ISO |
| Competência no documento | Estruturada (`ano`, `mês`) |
| Nome de beneficiário | Forma canônica estável (sem virar cadastro) |
| CNPJ | Formato canônico, quando necessário |
| Documento/NF | Número canônico |
| Parcela "3/6" | Número e total |
| Subcategoria | Rótulo estável |

## 7. Nunca inventar

- não se estima valor ausente;
- não se completa categoria por suposição;
- não se cria fornecedor a partir de texto;
- não se converte inferência em fato;
- ausência é representada como ausência, e gera pendência quando relevante.

## 8. Arquitetura de serviços (responsabilidades futuras)

Proposta de responsabilidades, **sem** criar um `balanco_service.py` gigante nem
micro-serviços artificiais:

| Serviço | Responsabilidade |
| --- | --- |
| `detector_layout` | Identificar formato (ATUAL_CANONICO/LEGADO) e competência |
| `extrator` | Produzir `BalanceteExtraido` bruto |
| `normalizador` | Converter valores/datas/categorias em forma canônica |
| `validador` | Regras de coerência por campo |
| `conciliador` | Reconciliação matemática (totais × lançamentos) |
| `preview` | Montar prévia + destaques + contagem de pendências |
| `projecao_morador` | Gerar a projeção pública (sem dado sensível) |

Reaproveitamento de ideia comprovada na V2 (`importador_financeiro`): o pipeline é
uma cadeia de etapas pequenas com DTOs imutáveis entre elas. Detalhes e
classificação em `fase_1a_balancos_decisoes.md`.

## 9. Papel da IA (futuro, fora desta fase)

Quando houver IA:

```
Entrada → Classificação → Extração → Normalização → Validação
        → Reconciliação → Confidence → Preview → Confirmação → Persistência
```

Regras:

- não usar LLM onde parser/regra determinística resolve;
- nenhuma inferência vira fato silenciosamente;
- toda saída de IA entra como **inferência rotulada**, sujeita a confirmação.
