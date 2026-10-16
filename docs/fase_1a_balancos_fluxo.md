# Fase 1A — Balanços Mensais: Fluxo

> Pipeline conceitual. Nenhuma etapa está implementada nesta fase.

## 1. Pipeline canônico

```
PDF ORIGINAL
  ↓  identificação do documento      (condomínio, competência, tipo de formato)
  ↓  detecção de layout              (ATUAL_CANONICO | LEGADO)
  ↓  extração                        (leitura estruturada bruta)
  ↓  normalização                    (valores, datas, categorias, fornecedores)
  ↓  validação                       (regras de coerência por campo)
  ↓  reconciliação                   (totais × somatórios; contábil × operacional)
  ↓  confidence                      (grau de confiança por campo e por bloco)
  ↓  preview                         (prévia legível para a gestão)
  ↓  correção humana                 (só no que é exceção)
  ↓  confirmação                     (validação explícita)
  ↓  persistência validada           (dado oficial, versionado)
  ↓  resumo para gestão              (diagnóstico do mês)
  ↓  rascunho de transparência       (versão simplificada, não publicada)
  ↓  publicação futura               (controle editorial humano)
```

## 2. Fronteiras de estado da informação

O fluxo separa quatro naturezas que **nunca** se confundem:

| Natureza | Onde vive | Pode virar oficial? |
| --- | --- | --- |
| **Dado do documento** | Arquivo original (storage externo privado) | Nunca sozinho |
| **Dado extraído** | Objeto de extração do balanço | Não |
| **Inferência** | Objeto de extração, rotulada como inferência | Só após validação humana |
| **Dado validado** | Objeto validado do balanço | Sim (é o oficial) |

Regra dura: **inferência nunca é salva silenciosamente como fato confirmado.**

## 3. Estados do balanço

> **Revisado na Fase 1B.** A etapa funcional `EM_REVISAO` foi removida por
> decisão de produto: a V3 inicial não possui workflow formal de revisão. O
> resultado da leitura é apresentado à gestão e a gestão **confirma a
> importação**; não havendo confirmação, nada é gravado. Ver
> `fase_1b_balancos_implementacao.md` §4 e `v3_decisoes_arquiteturais.md` (C2).

Proposta: **separar situação funcional de estado técnico de processamento**, em
vez de um único enum com 9 valores.

### 3.1 Situação funcional (o que a gestão vê)

```
confirmação da gestão  →  VALIDADO  →  PUBLICADO
                              │            │
                              └────────────┴→  SUBSTITUIDO
                                               (chegou versão corrigida)
```

| Situação | Significado para a gestão |
| --- | --- |
| `VALIDADO` | Confirmado por pessoa; é a versão vigente da competência. |
| `PUBLICADO` | Resumo aprovado e disponibilizado aos moradores. |
| `SUBSTITUIDO` | Deixou de ser vigente porque chegou uma versão corrigida. |

### 3.2 Estado técnico de processamento (interno, não exibido como rótulo técnico)

```
PENDENTE  →  PROCESSANDO  →  CONCLUIDO
                     ↓
                    ERRO
```

Isso evita "excesso de status" e separa o que é linguagem humana do que é
mecânica interna. A interface traduz o estado técnico em frase simples
(ex.: "Interpretando o documento…", "Não foi possível ler este documento.").
Falha de leitura não muda a situação do mês nem substitui a confirmação humana.

## 4. Versionamento e substituição

Cenário canônico:

```
Agosto confirmado (v1)  →  validado
Administradora envia PDF corrigido
  →  documento processado e apresentado sem invalidar a v1
  →  confirmação da substituição (transação atômica):
     v1 passa a SUBSTITUIDO (preservada, nunca sobrescrita)
     v2 nasce VALIDADO, ligada à v1 (substitui = v1)
```

Preserva-se sempre:

- versão anterior;
- documento anterior;
- histórico de leitura;
- quem substituiu;
- quando;
- motivo (quando informado).

**Nunca sobrescrever silenciosamente.**

## 5. Duplicidade

Detecção por combinação de sinais:

- condomínio;
- competência;
- hash do arquivo (SHA-256);
- versão;
- indicadores documentais (nº do balancete, período, total).

Comportamento:

| Situação | Tratamento |
| --- | --- |
| **Mesmo PDF** (hash idêntico) já importado | Recusar com alerta claro; não criar novo registro. |
| **PDF diferente** da mesma competência | Tratar como **possível nova versão**, não como duplicata nem como balanço independente. A gestão decide: nova versão ou documento distinto. |
| **Competência inexistente** | Fluxo normal de primeira importação. |

## 6. Revisão por exceção

O usuário não revisa páginas nem linhas. O sistema prioriza o que importa:

- divergência matemática (total ≠ somatório);
- categoria ambígua;
- fornecedor não identificado;
- valor/moeda duvidosa;
- lançamento incompleto;
- duplicidade;
- conflito entre extração e validação;
- diferença entre saldo informado e saldo calculado;
- baixa confiança.

Se tudo consistir:

> "Importação pronta para validação."

E o sistema segue sem exigir digitação.

## 7. Trilha funcional (auditoria)

Eventos que precisam de rastreio de quem/o quê/quando:

| Evento | Guarda |
| --- | --- |
| Upload | quem importou, quando, qual arquivo |
| Processamento | quando, resultado, falha |
| Correção humana | campo, valor original, valor corrigido, autor |
| Validação | quem, quando |
| Substituição | quem, quando, motivo, versão anterior |
| Publicação | quem, quando, resumo publicado |
| Despublicação (futura) | quem, quando, motivo |

Nesta fase apenas se define **o que** será registrado. A implementação completa da
auditoria não ocorre na Fase 1A.

## 8. Reconciliação contábil × operacional

Um lançamento de despesa do balanço pode corresponder a uma manutenção, preventiva,
obra, melhoria ou fornecedor. Isso **não** cria duas despesas.

```
registro contábil  ↔  evento operacional
```

Regra: nunca somar os dois como custos distintos se representam a mesma despesa.
Nesta fase modela-se apenas a **estratégia** de reconciliação; a operação ainda não
existe na V3.

## 9. Ponto de parada do fluxo

O fluxo para quando:

- há informação suficiente para sugerir o próximo passo; ou
- apenas inspeção física/contato humano pode reduzir a incerteza; ou
- é necessária atuação de profissional habilitado.

Silêncio e parada são resultados válidos.
