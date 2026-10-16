# Fase 1D — Detector de layout do balanço

## Objetivo

Identificar automaticamente o formato do PDF recebido em `ImportacaoBalanco`,
sem extrair informação financeira, sem OCR, sem IA e sem criar
`BalancoMensal`.

## Serviço criado

Arquivo: `apps/balancos/detector.py`

Serviços principais:

- `detectar_layout_balanco(importacao, *, provider=None)`
- `detectar_layout_do_pdf(conteudo, *, tempo_download_ms=0)`

O detector é chamado no fim de `receber_importacao`, depois que o PDF já foi
guardado no storage privado.

## Biblioteca de leitura PDF

- `pypdf==6.1.1`

Uso restrito à camada textual do PDF. Não há OCR, IA, parser financeiro nem
chamada externa além do acesso ao próprio storage privado.

## Fluxo implementado

1. upload do PDF para bucket privado;
2. `ImportacaoBalanco` fica em `PROCESSANDO`;
3. o detector gera signed URL curta em memória;
4. baixa o PDF com timeout de 30 segundos;
5. lê apenas a abertura estrutural do documento;
6. coleta evidências determinísticas;
7. grava `layout_detectado`;
8. finaliza com `status=PRONTO`;
9. em falha técnica, grava `status=ERRO` com mensagem segura.

## Layouts suportados

### Atual canônico

Critérios combinados:

- múltiplos marcadores semânticos estruturais;
- densidade textual por página com texto;
- maioria das páginas analisadas com texto legível.

Marcadores considerados:

- índice;
- demonstrativo de contas;
- resumo de contas;
- receitas;
- despesas;
- resumo financeiro;
- saldo bancário;
- saldo contábil;
- extratos;
- realizado;
- receitas detalhadas;
- inadimplência;
- despesas detalhadas;
- documentos anexos.

Uma palavra isolada nunca basta. Os marcadores têm pesos diferentes: `índice`,
`demonstrativo de contas`, `receitas detalhadas` e `despesas detalhadas` pesam
mais; `documentos anexos` pesa pouco, porque indicar anexos não é o mesmo que o
layout ser decidido pelos anexos.

### Legado

Critérios combinados:

- marcador histórico `balancete`;
- pouco texto extraível;
- poucos sinais do layout atual;
- sinal visual simples, quando disponível, pela contagem de imagens/XObjects.

Sem evidência suficiente, o legado não é forçado.

### Desconhecido

Usado quando:

- não há evidência suficiente;
- os sinais entram em conflito;
- o PDF tem pouco texto e não permite concluir com segurança;
- o documento é válido, mas não se parece com um balanço reconhecido.

`DESCONHECIDO` não é erro técnico.

## Confidence

`confidence` é determinístico e calculado por peso de evidências.

- layout atual: pesos dos marcadores + densidade textual;
- legado: marcador histórico + escassez de texto + sinais visuais + ausência de
  sinais fortes do atual;
- desconhecido: valor conservador derivado da força parcial dos sinais.

Não há score estatístico, probabilidade treinada nem IA.

## Estratégia de páginas e de pesos

Configuração:

- `BALANCOS_DETECTOR_MAX_PAGINAS=15` por padrão

O detector não varre o PDF inteiro e **não espalha a amostra pelo arquivo**. Ele
lê a abertura estrutural: as primeiras `BALANCOS_DETECTOR_MAX_PAGINAS` páginas,
onde ficam o índice, o resumo e as primeiras seções financeiras. Um PDF parcial,
com menos páginas que o limite, é lido por inteiro.

Três cuidados guiam essa escolha:

- **a quantidade de páginas do PDF não é característica de layout.** Um mesmo
  modelo pode vir curto, longo, parcialmente digitalizado ou com muitos
  comprovantes anexados. Por isso o total de páginas tem peso zero: nenhum
  critério de classificação o usa;
- **anexos e comprovantes não podem determinar o formato.** Uma amostra
  espalhada misturaria páginas de comprovante ao julgamento; concentrar a leitura
  na abertura mantém esses anexos fora da decisão;
- **a densidade textual é medida por página com texto**, não pelo total de
  caracteres. Assim um PDF longo com muitos anexos escaneados não parece "pobre
  em texto", nem um PDF curto parece "rico", só por causa do tamanho.

Evidência estrutural suficiente classifica mesmo em PDF parcial. Sem evidência
suficiente, o resultado é `DESCONHECIDO` — nunca um palpite.

### Pesos

Formato atual (`PESOS_ATUAL_CANONICO`):

| Sinal | Peso |
| --- | --- |
| índice | 3 |
| demonstrativo de contas | 3 |
| receitas detalhadas | 3 |
| despesas detalhadas | 3 |
| resumo de contas | 2 |
| resumo financeiro | 2 |
| saldo bancário | 2 |
| saldo contábil | 2 |
| inadimplência | 2 |
| receitas | 1 |
| despesas | 1 |
| extratos | 1 |
| realizado | 1 |
| documentos anexos | 1 |
| densidade textual ≥ 200 caracteres/página com texto | +2 |
| maioria das páginas analisadas com texto (≥ 60%) | +2 |

Legado (`PESOS_LEGADO`):

| Sinal | Peso |
| --- | --- |
| marcador histórico `balancete` | 4 |
| densidade textual ≤ 120 caracteres/página com texto | 2 |
| poucas páginas com texto (≤ 40%) | 1 |
| predominância visual (imagens/XObjects) | 2 |
| ausência de sinais fortes do formato atual | 1 |

Total de páginas: peso **zero** em ambos.

Limiares de decisão:

- atual: `score ≥ 9`, pelo menos 4 marcadores e vantagem de 2 pontos sobre o legado;
- legado: `score ≥ 7`, com marcador histórico presente e vantagem de 1 ponto;
- conflito (atual ≥ 7, legado ≥ 6 e diferença ≤ 2): `DESCONHECIDO`.

## Evidências

As evidências retornadas são curtas e auditáveis, por exemplo:

- marcadores encontrados;
- número de páginas analisadas;
- proporção de páginas com texto;
- densidade textual por página com texto.

Elas **não** são persistidas em `resultado_extraido`, não entram em
`inconsistencias` e não geram migration nova.

## Persistência

Permanece igual:

- `resultado_extraido = {}`
- `inconsistencias = []`

Mudam apenas:

- `status`
- `layout_detectado`
- `erro_processamento`, quando houver falha técnica

## Idempotência

Executar o detector novamente sobre a mesma importação:

- não cria novo objeto;
- não cria `BalancoMensal`;
- não altera o storage;
- produz a mesma classificação para o mesmo PDF.

## Segurança

- bucket continua privado;
- signed URL é temporária e nunca persistida;
- nenhuma credencial é registrada;
- mensagens de erro não exibem URL assinada, token ou service role;
- o detector depende do storage privado homologado, sem fallback local.

## Homologação real

Um balanço real do formato atual, com 175 páginas, foi usado apenas localmente
(sem versionamento e sem cópia para fixtures) para conferir o detector contra o
storage privado.

Primeira execução, com a amostragem puramente uniforme:

- `layout_detectado = DESCONHECIDO`
- 15 de 175 páginas amostradas
- apenas 2 marcadores do modelo atual entraram na amostra

A causa não era a leitura do PDF: a varredura completa do documento encontrou
todos os marcadores do formato atual. A amostra uniforme saltava a abertura, e
é lá que o índice e o resumo estão. Da primeira página até a seguinte, o salto
ia da página 1 para a 13.

Uma amostra espalhada num PDF longo, além de saltar as páginas do índice e do
resumo, ainda misturava comprovantes anexados ao julgamento e fazia o resultado
depender do tamanho do arquivo. A estratégia atual concentra a leitura na
abertura estrutural e trata a quantidade de páginas como irrelevante para o
layout.

O arquivo foi descartado do bucket após a conferência: o objeto saiu do storage
privado e a importação ficou registrada como `DESCARTADO`.

## Limitações desta fase

- não há OCR;
- não há IA;
- o legado pode permanecer `DESCONHECIDO` se a camada textual for insuficiente;
- PDFs reais não devem ser versionados no repositório;
- a validação com exemplares reais depende de artefatos locais fora do Git;
- o exemplar real do formato legado ainda não foi conferido.

## Testes cobertos

Casos sintéticos:

- atual canônico;
- atual curto;
- atual longo;
- atual com muitos anexos (comprovantes digitalizados);
- atual parcial (só parte do balancete digitalizada);
- legado curto;
- legado longo;
- desconhecido;
- texto insuficiente;
- marcadores conflitantes;
- PDF corrompido;
- falha de acesso ao storage;
- idempotência;
- limite de páginas analisadas;
- amostra restrita à abertura, e documento inteiro quando curto;
- documento longo reconhecido pelos sinais da abertura;
- anexos não mudam o layout do formato atual;
- nenhum `BalancoMensal` criado;
- `resultado_extraido` e `inconsistencias` preservados;
- timeout explícito no download privado;
- erro sem vazamento de signed URL.

