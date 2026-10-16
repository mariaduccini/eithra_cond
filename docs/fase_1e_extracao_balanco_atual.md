# Fase 1E — Extração financeira do modelo atual

## Objetivo

Ler os dados financeiros de um balanço já classificado como `ATUAL_CANONICO`,
a partir do PDF que já está guardado no storage privado, **sem OCR, sem IA e sem
criar `BalancoMensal`**. O resultado é apresentado à gestão para conferência; o
mês só passa a valer quando a gestão confirmar, em fase futura.

Esta fase é a leitura: ela entende o documento e mostra o que entendeu. Não
decide, não publica e não cria nada no lugar da gestão.

## Escopo respeitado

Deliberadamente **não** entra nesta fase:

- formato legado (o documento legado devolve um motivo, não uma leitura);
- OCR;
- IA;
- publicação para morador;
- criação de fornecedores;
- criação de manutenção;
- criação de `BalancoMensal`.

## Módulos criados

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/balancos/contratos.py` | Contrato versionado `BalancoExtraidoV1`, tipos, serialização e validação de forma |
| `apps/balancos/normalizadores.py` | Conversão determinística de valor, data, competência, documento e parcela |
| `apps/balancos/categorias.py` | Catálogo do Eithra (18 categorias) e regras de categorização |
| `apps/balancos/natureza_despesa.py` | Regras de natureza (recorrente, variável, extraordinária, a confirmar) |
| `apps/balancos/validacao_financeira.py` | Conferência matemática e pontos de atenção |
| `apps/balancos/extrator_atual.py` | Leitura do PDF do modelo atual e gravação do resultado |

Integração: `apps/balancos/services.py` chama `extrair_balanco_atual` logo após
o detector, quando `layout_detectado == ATUAL_CANONICO`.

## Contrato — `BalancoExtraidoV1`

O resultado da leitura não é um dicionário solto: é um contrato tipado
(dataclasses imutáveis) com `SCHEMA_VERSAO = 1`. Três compromissos estão
embutidos na estrutura:

- **ausência é ausência** — campo que o documento não traz fica `None`; não se
  estima nem se completa por suposição;
- **dinheiro é texto decimal** — valores viajam como `Decimal` convertido em
  texto, nunca `float`, para que voltem idênticos do banco;
- **todo dado relevante tem procedência** — página, seção, método e um trecho
  curto (até 160 caracteres) de origem; o contrato registra de onde veio, não
  copia o documento para dentro do banco.

### Campos

- `competencia`: `YYYY-MM-01` (o mês), ou `None`;
- `periodo`: `inicio` / `fim`, quando o documento declara um intervalo;
- `receitas_total`, `despesas_total`, `saldo_anterior`, `saldo_final`:
  totalizadores declarados;
- `saldos`: lista de `SaldoExtraido` (um por conta/banco, com a identificação
  escrita no documento);
- `receitas`: lista de `ReceitaExtraida`;
- `despesas`: lista de `DespesaExtraida`;
- `categorias`: agregado por categoria do Eithra, separado por comportamento;
- `inadimplencia_agregada`: valor, percentual e quantidade — **somente em
  agregado do condomínio, nunca por unidade**;
- `resumo`: contagens e somas derivadas do que foi lido;
- `provenance`: método, páginas lidas, total de páginas e seções encontradas;
- `confianca`: determinística (alta, média, baixa).

### Serialização e validação

- `para_dicionario` / `de_dicionario`: ida e volta para JSON puro (tuplas viram
  listas; enums permanecem texto). `de_dicionario` é tolerante com o que ainda
  não existe — uma importação recebida antes desta fase tem
  `resultado_extraido` vazio;
- `validar_dicionario`: confere a forma antes de confiar; levanta
  `ErroDeContrato` em vez de tentar consertar. A competência, quando existe,
  precisa estar em ISO e no primeiro dia do mês.

## O que é extraído e como

A leitura é determinística e guiada pela estrutura do documento, usando apenas a
camada textual via `pypdf`. Ela percorre a **abertura** do PDF (até
`BALANCOS_EXTRACAO_MAX_PAGINAS`, 40 por padrão) e localiza os blocos:

`receitas`, `despesas`, `resumo`, `saldos`, `inadimplencia` e `anexos`. O bloco
`anexos` é deliberadamente ignorado: ler cem comprovantes não melhora o
resultado e custa caro.

Quatro compromissos governam a leitura:

- **só o modelo atual** — documento legado ou desconhecido devolve o motivo, sem
  adivinhar o que não se sabe ler;
- **seções, não páginas** — o demonstrativo fica na abertura; anexos ficam fora;
- **dinheiro só com contexto** — um número solto não é valor; só vira lançamento
  a linha que traz valor com cara de dinheiro dentro de um bloco de receitas ou
  de despesas;
- **ausência é ausência** — o que o documento não declara fica vazio, e a dúvida
  vira ponto de atenção, nunca certeza inventada.

### Competência

Uma linha que rotula a competência ("Competência: Agosto/2026") vale mais do que
uma menção solta, porque é onde o documento afirma o mês. Só quando não há
rótulo a leitura considera menções soltas — nunca em linhas que trazem valor,
para não confundir o mês de um lançamento com a competência do balanço. Quando o
documento traz mais de uma competência possível, o mês **fica em aberto** e vira
ponto de atenção (nunca se escolhe em silêncio).

### Campos do lançamento

- data, quando a própria linha declara uma;
- fornecedor/beneficiário **candidato** (nome com sufixo de empresa) — é
  indício, não cadastro: o Eithra não cria fornecedor nesta fase;
- documento (nota/NF/recibo) e parcela (`3/6`), quando presentes;
- categoria original do documento (agrupamento) preservada ao lado da do Eithra;
- valor e descrição normalizada.

## Categorização

Catálogo pequeno de propósito (18 categorias): uma lista longa parece detalhada
e vira inútil. Duas regras governam:

- **nada é substituído em silêncio** — a categoria original do documento
  continua guardada ao lado da categoria do Eithra;
- **não se adivinha** — sem evidência textual, a resposta é `NAO_CLASSIFICADO`
  ("A classificar"), que é uma resposta legítima e não um erro.

O agrupamento do próprio documento é considerado primeiro (a administradora já
classificou melhor do que uma heurística); sem ele, a descrição decide. A
comparação exige **palavra inteira** — sem isso, `agua` acharia "aguardando" e
`obra` acharia "obra-prima". A ordem das regras é a própria regra: o específico
vem antes do genérico ("manutenção do elevador" é Elevadores; "taxa de
administração" é Administração).

Categorias: Pessoal, Água, Energia, Gás, Elevadores, Limpeza, Portaria e
segurança, Manutenção, Serviços, Administração, Seguros, Tributos e taxas,
Obras e melhorias, Consumo e materiais, Tecnologia, Extraordinárias, Outros e
A classificar.

## Natureza da despesa

Recorrência não se confunde com variação do valor. Quatro respostas, com rótulos
humanos:

| Estado | Rótulo na tela | Quando |
| --- | --- | --- |
| `RECORRENTE_FIXA` | Recorrente | Compromisso mensal com valor previsível ("mensalidade", "contrato", "parcela mensal") |
| `RECORRENTE_VARIAVEL` | Recorrente variável | Chega todo mês, mas o valor acompanha o consumo (água, energia, gás) |
| `EXTRAORDINARIA` | Extra | Aconteceu, não é previsível ("troca", "reparo", "obra") |
| `NAO_DETERMINADA` | A confirmar | Não há evidência suficiente, ou há sinais contraditórios |

A classificação é textual e determinística. Não se conclui que algo é recorrente
só porque o fornecedor é sempre o mesmo — **quem é recorrente é a despesa, não
quem a presta**. Sinais contraditórios ("troca de peça" e "parcela mensal" na
mesma linha) resultam em `NAO_DETERMINADA`: preferir a dúvida honesta a uma
certeza inventada é o que mantém a conferência confiável.

## Validações financeiras

A conferência é determinística e opera sobre o contrato já montado — nenhuma
leitura de PDF acontece nesse módulo. A regra é a mesma em todo o Eithra: só se
aponta o que se pode afirmar. **Silêncio é resultado válido.**

Conferências:

- soma das despesas lançadas × total declarado;
- soma das receitas lançadas × total declarado;
- `saldo anterior + receitas − despesas = saldo final` (apenas com conta única);
- possíveis duplicidades (mesma descrição, valor e data).

As diferenças são toleradas até `TOLERANCIA = 0.05` porque cada linha já foi
arredondada ao centavo: um resíduo de arredondamento não é divergência, e
transformá-lo em ponto de atenção treinaria a gestão a ignorar os avisos.

### Pontos de atenção (inconsistências)

Tipos nomeados com rótulo humano, cada um com severidade e procedência:

| Tipo | Rótulo | Severidade típica |
| --- | --- | --- |
| `TOTAL_DIVERGENTE` | Total divergente | média |
| `COMPETENCIA_CONFLITANTE` | Competência conflitante | alta |
| `VALOR_INVALIDO` | Valor inválido | baixa |
| `LINHA_INCOMPLETA` | Linha incompleta | baixa |
| `CATEGORIA_NAO_DETERMINADA` | Categoria a classificar | — |
| `NATUREZA_NAO_DETERMINADA` | Natureza a confirmar | — |
| `FORNECEDOR_AMBIGUO` | Fornecedor ambíguo | — |
| `POSSIVEL_DUPLICIDADE` | Possível duplicidade | baixa |

Uma inconsistência não é erro de sistema: é o resultado honesto de uma leitura
que encontrou algo duvidoso. É a base da **conferência por exceção**.

## Confiança

Determinística, tirada do que foi lido — nunca estimada e nunca exibida como
número para a gestão:

- ponto de atenção de severidade alta → **baixa**;
- sem lançamento algum → **média** (não se leu o bastante para confiar de todo, e
  não há erro que justifique desconfiar);
- competência presente e nenhum ponto de atenção → **alta**;
- caso contrário → **média**.

## Proveniência

Todo dado relevante carrega `Procedencia`: página, seção, método ("camada
textual do PDF") e um trecho curto da linha de origem (até 160 caracteres). O
trecho é curto de propósito: registra de onde veio, não copia o documento. O
`provenance` da raiz guarda método, páginas lidas, total de páginas e seções
encontradas.

## Fluxo e idempotência

1. a importação já foi recebida e o layout detectado;
2. se `ATUAL_CANONICO`, a leitura roda sobre o PDF privado (reaproveitando o
   caminho de download já homologado, sem uma segunda implementação);
3. em sucesso, grava `resultado_extraido`, `inconsistencias`, `schema_versao` e
   finaliza com `status = PRONTO`;
4. em falha técnica, grava `status = ERRO` com mensagem segura (sem URL
   assinada, token ou caminho de objeto) e propaga o erro.

A leitura é idempotente: reexecutar sobre o mesmo arquivo produz o mesmo
resultado e não cria objeto novo. Quando quem chama já tem o conteúdo em mãos (o
caminho do upload tem), ele é passado e evita um segundo acesso ao storage.

Nenhum `BalancoMensal` nasce aqui.

## Apresentação à gestão (tela de detalhe)

A tela de detalhe da importação (`balancos:detalhe`) mostra o que foi lido, sem
termo técnico:

- "Dados lidos", com subtítulo "Dados lidos · N pontos precisam de atenção"
  quando houver pontos de atenção;
- totais (receitas, despesas, saldo anterior, saldo final);
- resumo de despesas recorrentes e extraordinárias, com "A confirmar" quando
  aplicável;
- principais categorias em badges;
- tabela de despesas (desktop) e cards (mobile) com categoria, natureza e valor;
- agregado por categoria: `Categoria | Recorrente | Extra | Total` (a coluna
  "A confirmar" aparece quando houver valor a confirmar);
- tabela de receitas, lista de saldos e lista de pontos de atenção;
- quando o layout não é o atual, mostra o motivo: para o legado, "Este formato
  será processado pela leitura de documentos legados."

A tela **não** oferece botão de confirmação nesta fase: não se cria CTA falso
para uma ação que ainda não existe.

## Segurança

- bucket continua privado; acesso por signed URL temporária, sob demanda, nunca
  persistida;
- nenhuma credencial é registrada em log ou armazenada;
- mensagens de erro são para a gestão e não expõem URL assinada, token, service
  role, caminho de objeto ou resposta bruta do provedor;
- a inadimplência entra apenas em agregado do condomínio — nenhum dado por
  unidade no contrato;
- autorização da tela de detalhe é a mesma das demais rotas de balanço
  (centralizada no backend; registro não autorizado responde 404).

## Limitações desta fase

- não há OCR nem IA;
- o legado não é lido (fica para uma fase própria);
- a natureza e a categoria são sugestões determinísticas; sem histórico, muitas
  linhas ficam "A confirmar" / "A classificar", o que é a resposta honesta;
- a extração depende da camada textual: PDFs digitalizados sem texto não são
  lidos;
- a validação com um exemplar real de `ATUAL_CANONICO` depende de artefato local
  fora do Git. Não havendo exemplar disponível no ambiente, permanece como
  limitação registrada;
- PDFs reais não devem ser versionados no repositório.

## Decisões adiadas

- persistência dos lançamentos (relacional × JSON × híbrido);
- categorias publicáveis por padrão;
- confirmação da importação e criação de `BalancoMensal` (fase futura);
- experiência do morador para balanços publicados.

## Testes cobertos

PDFs sintéticos (`apps/balancos/tests/test_extracao_atual.py`), sem rede e sem
arquivo real:

- modelo atual completo (competência, receitas, despesas, saldos, inadimplência);
- competência conflitante;
- total divergente;
- despesa repetida (possível duplicidade);
- linha sem descrição;
- agrupamento (categoria original preservada);
- documento e parcela na própria linha;
- documento com anexos (anexos ignorados);
- documento sem seções;
- ida e volta do contrato (serialização);
- gravação do resultado sem criar `BalancoMensal`.

Tela (`apps/balancos/tests/test_importacao_views.py`, classe `DetalheTests`):

- entrada obrigatória;
- o que foi lido é exibido;
- nenhum `BalancoMensal` é criado;
- não há oferta de confirmação nesta fase;
- importação de outro condomínio é negada (404);
- morador não alcança o detalhe (404);
- importação inexistente é negada (404);
- formato não reconhecido mostra o motivo, sem dados;
- formato legado aponta para a leitura futura;
- nenhuma credencial aparece no detalhe.
