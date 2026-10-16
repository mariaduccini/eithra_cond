# Fase 1A — Balanços Mensais: UX

> Descrição conceitual de telas. **Nenhuma UI, template ou página é criada nesta
> fase.** Sneat + Bootstrap 5 continuam sendo a base; nenhum design system novo.

## 1. Princípio

> "Complexidade dentro do sistema, não na tela."

O usuário vê: o que importa, o que exige ação, o que mudou. O resto fica acessível
por *progressive disclosure*.

## 2. Mapa de telas

```
Balanços (lista)
 ├─ Importar balanço            (modal)
 ├─ Detalhe do balanço          (página)
 │    ├─ Resumo
 │    ├─ Receitas
 │    ├─ Despesas
 │    ├─ Categorias
 │    ├─ Inconsistências
 │    ├─ Documento original
 │    └─ Histórico
 ├─ Preparar resumo p/ moradores (página)
 └─ Publicar                    (confirmação)
```

Fora do escopo nesta fase: portal do morador, dashboard complexo, módulo de
fornecedores.

## 3. Páginas

| Página | Papel |
| --- | --- |
| **Balanços (lista)** | Todas as competências do condomínio, com situação e pendências |
| **Detalhe do balanço** | Um balanço, com abas de leitura |
| **Preparar resumo para moradores** | Edição do texto aprovado e escolha do que publicar |
| **Histórico** | Versões anteriores e eventos do balanço |

### Lista de balanços — colunas

```
Competência | Receitas | Despesas | Saldo | Situação | Pendências | Ações
```

- linha **clicável** (sem botão "Visualizar");
- situação em **badge discreto**, com linguagem humana (ex.: "Validado",
  "Publicado aos moradores", "Substituído");
- filtros **compactos** (competência, situação);
- ações: 1–2 → ícones sem borda; 3+ → menu de três pontos.

## 4. Modais

Modais **compactos**, sem cards internos, uma coluna, *progressive disclosure*,
somente campos necessários.

| Modal | Conteúdo |
| --- | --- |
| Importar balanço | Selecionar arquivo, competência (pré-preenchida quando detectável), confirmar |
| Resolver pendência | Descrição humana da exceção, valor original, valor sugerido, ação |
| Substituir versão | Motivo (quando aplicável), confirmação explícita |
| Confirmar publicação | Prévia do resumo do morador, confirmação |
| Ver documento original | Abertura via signed URL temporária |

Ação crítica (validar, substituir, publicar) usa **botão textual explícito**,
nunca ícone.

## 5. Hierarquia de ações

| Quantidade de ações | Padrão |
| --- | --- |
| 1 | Ícone sem borda + tooltip |
| 2 | Dois ícones sem borda |
| 3+ | Menu de três pontos |
| Ação crítica | Botão textual explícito |

## 6. Estados (linguagem humana)

> **Revisado na Fase 1B.** Não existe estado nem tela de revisão: a gestão
> confirma a importação antes de o mês valer. Ver
> `fase_1b_balancos_implementacao.md` §4.

| Situação / estado técnico | Frase na tela |
| --- | --- |
| `PENDENTE` + `PROCESSANDO` | "Interpretando o documento…" |
| `VALIDADO` | "Validado" |
| `PUBLICADO` | "Publicado aos moradores" |
| `SUBSTITUIDO` | "Substituído por versão corrigida" |
| `ERRO` | "Não foi possível ler este documento" |

Nunca exibir JSON, payload, ID interno, nome de model, score ou detalhe de sistema.

## 7. Confirmação da importação (experiência)

> **Revisado na Fase 1B.** Não há fila nem tela de revisão: o resultado da
> leitura é apresentado uma vez e a gestão confirma a importação. Ver
> `fase_1b_balancos_implementacao.md` §4.

- a tela de confirmação mostra o resultado da leitura e destaca **apenas** o que
  é excepcional;
- cada exceção: o que é, por que apareceu, o que fazer;
- se tudo consistir: **"Importação pronta para confirmação."** (sem digitação
  obrigatória);
- a gestão confirma com um gesto; corrigir é exceção, não regra.

## 8. Detalhe do balanço — abas

| Aba | Conteúdo |
| --- | --- |
| Resumo | Números principais + diagnóstico humano do mês |
| Receitas | Composição das receitas |
| Despesas | Composição das despesas (visão de gestão) |
| Categorias | Distribuição e maiores categorias |
| Inconsistências | Pendências abertas/resolvidas |
| Documento original | Acesso ao PDF via signed URL |
| Histórico | Versões, quem alterou, quando |

A aba **Documento original** é a fonte de verdade; as demais são leitura
estruturada do mesmo.

## 9. Comparação entre meses (futuro)

Espaço discreto para: mês atual × anterior, variação das principais categorias,
despesa extraordinária, recorrência. **Determinístico primeiro.** Sem dashboard
complexo, sem machine learning.

## 10. Mobile

Embora a gestão use desktop, o fluxo precisa ser utilizável no celular:

```
grid desktop  →  cards compactos mobile
```

- importação e confirmação continuam operáveis em telas pequenas;
- **não** reproduzir tabelas largas; viram lista de cards;
- foco em uma exceção por vez na confirmação da importação;
- coluna centralizada em telas maiores (~680–760 px) quando fizer sentido.

## 11. Conformidade visual

- tema Sneat + Bootstrap 5 (não criar novo design system);
- Boxicons para ícones;
- identidade Eithra via tokens (`--eithra-primary: #153F46`);
- contraste WCAG 2.2 AA;
- sem rolagem horizontal em 1440 / 1024 / 768 / 390 px.
