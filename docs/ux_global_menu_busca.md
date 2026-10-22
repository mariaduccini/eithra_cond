# Eithra V3 - UX Global de Navegacao

## 1. Objetivo desta rodada

Organizar a navegacao global para refletir tarefas e contextos do usuario, sem
alterar regra de negocio e sem criar funcionalidade falsa.

Escopo desta rodada:

- reorganizar o menu atual;
- reduzir concorrencia entre acao principal e catalogos auxiliares;
- usar a marca oficial ja existente;
- preparar a conversa sobre busca global sem fingir um campo funcional.

Fora de escopo nesta rodada:

- motor de busca global;
- foto de pessoa;
- Portaria;
- procuracao;
- novo modulo de documentos;
- alteracoes de schema;
- 2G.

## 2. Inventario do menu encontrado

### Topo

| Item | URL | App | Visivel para | Natureza |
| --- | --- | --- | --- | --- |
| Painel | `core:home` | `core` | todos autenticados | operacional |
| Demandas | `demandas:listagem` | `demandas` | quem pode ver alguma demanda | operacional |
| Manutencoes | `manutencoes:listagem` | `manutencoes` | quem pode ver alguma manutencao | operacional |
| Rotinas | `rotinas:listagem` | `rotinas` | quem pode ver alguma rotina | operacional |
| Estoque | `estoque:itens` | `estoque` | quem pode ver estoque em algum condominio | operacional |
| Compras | `compras:listagem` | `estoque/compras` | quem pode ver compras em algum condominio | operacional |
| Cadastros | grupo | `cadastros/comunidade/governanca` | quem pode ver cadastros em algum condominio | administracao / estrutura |
| Balancos | `balancos:importacoes` | `balancos` | quem pode importar balanco em algum condominio | administracao / estrutura |

### Grupo Cadastros encontrado antes da reorganizacao

| Item | URL | App | Natureza |
| --- | --- | --- | --- |
| Condominios | `cadastros:condominios` | `cadastros` | cadastro principal |
| Usuarios e acessos | `cadastros:acessos` | `cadastros` | cadastro principal |
| Areas e locais | `cadastros:locais` | `cadastros` | cadastro principal |
| Equipamentos | `cadastros:equipamentos` | `cadastros` | cadastro principal |
| Fornecedores | `cadastros:fornecedores` | `cadastros` | cadastro principal |
| Unidades | `comunidade:unidades` | `comunidade` | cadastro principal |
| Pessoas | `comunidade:pessoas` | `comunidade` | cadastro principal |
| Veiculos | `comunidade:veiculos` | `comunidade` | cadastro principal |
| Contratos | `governanca:contratos` | `governanca` | cadastro principal |
| Garantias | `governanca:garantias` | `governanca` | cadastro principal |
| Documentos | `governanca:documentos` | `governanca` | cadastro principal |

### Catalogos auxiliares encontrados fora do menu

| Item | URL | App | Natureza |
| --- | --- | --- | --- |
| Tipos de local | `cadastros:tipos_de_local` | `cadastros` | cadastro auxiliar |
| Tipos de equipamento | `cadastros:tipos_de_equipamento` | `cadastros` | cadastro auxiliar |
| Especialidades de fornecedor | `cadastros:especialidades` | `cadastros` | cadastro auxiliar |
| Tipos de contrato | `governanca:tipos_de_contrato` | `governanca` | cadastro auxiliar |
| Tipos de documento | `governanca:tipos_de_documento` | `governanca` | cadastro auxiliar |

## 3. Problemas de hierarquia encontrados

1. O submenu de `Cadastros` estava muito atomizado em telas especificas.
2. `Unidades`, `Pessoas` e `Veiculos` competiam como tres entradas paralelas,
   embora formem um unico contexto mental para quem navega.
3. Os catalogos auxiliares nao estavam no menu, mas apareciam como botoes nas
   telas consumidoras, competindo com a acao principal.
4. O menu atual ainda era mais proximo de "lista de telas" do que de "mapa de
   tarefas".
5. A busca global segue estrategica, mas ainda sem backend suficiente para um
   campo real no header.

## 4. Estrutura proposta

### Operacao

- Painel
- Demandas
- Manutencoes
- Rotinas
- Estoque
- Compras

### Administracao / Estrutura

- Cadastros
- Balancos

### Cadastros

- Condominios
- Pessoas e unidades
- Usuarios e acessos
- Areas e locais
- Equipamentos
- Fornecedores
- Contratos
- Garantias
- Documentos
- Tipos e categorias

Observacao:

- `Contratos`, `Garantias` e `Documentos` permaneceram porque ja existem telas
  uteis e em uso real.
- `Documentos` nao foi criado como modulo vazio; ele ja existe e continua
  acessivel.

## 5. Estrutura implementada nesta rodada

### Menu lateral

O grupo `Cadastros` passou a mostrar:

1. `Condominios`
2. `Pessoas e unidades`
3. `Usuarios e acessos`
4. `Areas e locais`
5. `Equipamentos`
6. `Fornecedores`
7. `Contratos`
8. `Garantias`
9. `Documentos`
10. `Tipos e categorias`

### Agrupadores novos

- `Pessoas e unidades`
  - leva para uma pagina de navegacao com atalhos para:
    - Unidades
    - Pessoas
    - Veiculos
- `Tipos e categorias`
  - leva para uma pagina de navegacao com atalhos para:
    - Tipos de local
    - Tipos de equipamento
    - Especialidades de fornecedor
    - Tipos de contrato
    - Tipos de documento

### Ativo do menu

O destaque do submenu passou a reconhecer o contexto aberto:

- qualquer rota de `comunidade:*` acende `Pessoas e unidades`;
- qualquer rota dos catalogos auxiliares acende `Tipos e categorias`.

## 6. Acoes auxiliares retiradas das telas consumidoras

Foram removidos os atalhos auxiliares destas listas:

- `Areas e locais` -> removido botao `Tipos de local`
- `Equipamentos` -> removido botao `Tipos de equipamento`
- `Fornecedores` -> removido botao `Especialidades`
- `Contratos` -> removido botao `Tipos de contrato`
- `Documentos` -> removido botao `Tipos de documento`

Resultado:

- a acao principal volta a ser a criacao do registro consumidor;
- o catalogo continua existente, mas agrupado em `Tipos e categorias`.

## 7. Marca Eithra na sidebar

Arquivo oficial encontrado no projeto:

- `static/img/eithra-mark.svg`

Decisao implementada:

- sidebar expandida -> simbolo + texto `Eithra`;
- sidebar recolhida -> somente simbolo;
- sem esticar imagem;
- mantendo Sneat + Bootstrap.

Nao foi gerada nova marca.

## 8. Busca global

Nao implementada nesta rodada.

Decisao de UX registrada:

- a busca global pertence ao `header`, nao ao menu lateral;
- o campo nao deve aparecer antes de existir backend funcional;
- o espaco natural para a busca fica no header, entre o toggle lateral e o
  menu da conta, com comportamento proprio para mobile em fase futura.

Escopo futuro esperado:

- pessoa;
- morador;
- unidade;
- placa;
- veiculo;
- fornecedor;
- local;
- equipamento;
- demanda;
- manutencao;
- rotina;
- contrato;
- documento;
- procuracao;
- itens operacionais da futura Portaria.

Principio:

> O usuario busca a coisa. O Eithra encontra o contexto.

## 9. Foto de pessoa

Requisito futuro registrado:

- foto opcional de identificacao visual;
- uso futuro em ficha da pessoa, busca global, consulta por unidade e Portaria;
- sem reconhecimento facial;
- sem biometria;
- sem integracao com camera ou CFTV nesta fase.

Nenhuma implementacao nesta rodada.

## 10. Portaria

Portaria continua futura e nao foi iniciada.

Requisitos de UX registrados:

- busca operacional rapida;
- pessoa, unidade, veiculo, codigo ou situacao como ponto de entrada;
- visitantes, entregas, autorizacoes e procurações em camada operacional;
- o porteiro nao precisa saber em qual modulo procurar.

Nenhum item de menu vazio foi criado.

## 11. Procuracao

Requisito arquitetural futuro registrado:

- outorgante;
- procurador;
- unidade ou pessoa representada;
- escopo;
- vigencia;
- documento;
- eventual revogacao.

Decisao importante:

- procuracao nao sera reduzida a um simples tipo de vinculo.

Nenhuma modelagem foi implementada nesta rodada.

## 12. Documentos institucionais

Nao foi criado novo item de menu vazio.

Destino futuro registrado para navegacao amigavel:

- convencao;
- regimento interno;
- atas;
- contratos;
- apolices;
- laudos;
- certificados;
- ART;
- RDO.

## 13. Principios registrados para a navegacao

- Cadastros auxiliares nao competem como acao primaria nas telas consumidoras.
- O menu representa contextos e tarefas do usuario, nao tabelas do banco.
- O usuario busca a coisa; o Eithra encontra o contexto.
- Na futura Portaria, a busca parte do fato operacional, nao do modulo.
