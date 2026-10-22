# Eithra V3 — Auditoria e Preparação da Carga Histórica (Gmail + Google Drive)

> **Documento vivo da preparação da carga histórica.** A etapa começou como
> auditoria e, depois do fechamento das decisões de domínio, passou a registrar
> também os ajustes estruturais locais necessários antes da carga.
>
> **Estado desta rodada:** `G-F3` (rótulo original) e `G-F4`
> (proveniência/idempotência) estão implementados localmente, com migrations
> **geradas e não aplicadas**. A proveniência foi reorganizada em duas peças — o
> item da fonte e o vínculo com o domínio —, porque a revisão mostrou que a
> identidade da fonte e a associação ao destino são conceitos diferentes.
> `G-F1` (vaga de garagem) foi **encerrado sem implementação**: a fonte real foi
> analisada e não identifica fisicamente cada vaga. Neon e produção não foram
> tocados. A Fase 2G não foi iniciada.
>
> **Documento parcial por bloqueio de fonte.** As fontes (Gmail e Google Drive) não
> são acessíveis a partir deste ambiente. As seções 4 a 19 do relatório pedido
> dependem de leitura dessas fontes e **não foram produzidas** — produzi-las exigiria
> inventar dados, o que é proibido. Ver §0. O que depende só do domínio existente
> (§20 a §24, e o mapa de §4) está completo.

---

## 0. Bloqueio de fonte — leitura obrigatória antes do restante

**Não há acesso a Gmail nem a Google Drive neste ambiente.**

Evidência levantada no repositório:

| Verificação | Resultado na abertura da etapa |
|---|---|
| Arquivos de fonte no repositório (`*.xlsx`, `*.csv`, `*.pdf`, `*.eml`, `*.mbox`) | **nenhum** |
| Conector, credencial, MCP ou integração Google no ambiente | **nenhum** |
| Campo de identificador externo (`id_externo`, `message_id`, `file_id`) em qualquer app | **nenhum** — criado depois, em G-F4 |
| Campo de proveniência fora de `balancos` (extração de balanço) | **nenhum** — criado depois, em G-F4 |
| Fixtures / seeds / `loaddata` com dados reais | **nenhum** |

> Os dois campos de proveniência que faltavam **foram criados nesta rodada** (G-F4,
> §20): `ItemImportacaoHistorica` e `VinculoImportacaoHistorica`. O acesso às fontes
> em si continua bloqueado — a criação do registro de proveniência não é leitura das
> fontes e não desbloqueia §3 nem §5–§19.

Consequência direta para o relatório pedido:

- **Não auditáveis sem as fontes:** itens 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
  15, 16, 17, 18, 19.
- **Auditáveis apenas com o domínio existente (entregues aqui):** itens 1 (parcial),
  4, 20, 21, 22, 23, 24, 25 e as confirmações 26 e 27.

As fontes listadas na seção 4 do pedido — planilhas, pastas, contratos, certificados,
ordens de serviço, e os temas de Gmail — estão registradas adiante como
**declaradas e não verificadas**. Nenhuma contagem delas foi reproduzida aqui.

**O que desbloqueia esta etapa** (qualquer das duas vias):

1. **Exportação para disco** — copiar para um diretório local as planilhas (`xlsx`),
   contratos, certificados, laudos, ARTs, RDOs, apólices, plantas, relatórios e OS,
   mais um export do Gmail pertinente (`.mbox`/`.eml` por assunto, ou o JSON do
   Google Takeout). Esta é a via recomendada: leitura pura, sem credencial, sem
   risco de a análise tocar na conta.
2. **Acesso autenticado de leitura** — escopo somente-leitura em Drive e Gmail.

Sem uma das duas, os itens acima permanecem bloqueados. Nenhuma tentativa indireta
foi feita — nem busca automatizada, nem inferência a partir de nome de arquivo.

**Decisão adotada:** a fonte real será fornecida por leitor externo autorizado, em
formato estruturado local. O Gmail e o Google Drive são analisados em ambiente
autorizado, fora do Eithra, que produz um pacote de migração em JSON. O Eithra não
cria integração Google, não pede OAuth nem service account, e não guarda dependência
de runtime das duas fontes — a leitura das fontes não acontece dentro do Eithra.

O pacote **preserva a fonte**; a normalização para os models do Eithra continua
sendo responsabilidade do importador/dry-run. Os arquivos podem conter dados reais e
por isso ficam em diretório local ignorado pelo Git, nunca em documentação versionada
nem em fixture permanente. **Nada será implementado enquanto o pacote não estiver
disponível.**

---

## 1. Resumo executivo

1. O domínio atual **comporta a maior parte** do que a planilha cadastral traz:
   unidade, pessoa (física e jurídica), vínculo Pessoa↔Unidade com vigência, veículo
   e os dois vínculos de veículo existem, com os campos de contato, documento e
   observação necessários. §4.
2. **O rótulo original do papel foi resolvido.** `VinculoPessoaUnidade` agora tem
   `rotulo_origem` opcional, preservando textos como `ESPOSA` e `PROCURADORA`
   sem explodir o enum canônico. §6 e §20/G-F3.
3. **Vaga de garagem foi encerrada sem implementação.** A fonte real foi analisada:
   ela comprova **quantidade e distribuição por nível/garagem**, mas **não**
   identifica fisicamente cada vaga. Criar `VagaGaragem` a partir dela exigiria
   inventar identidade, o que é proibido. §8 e §20/G-F1.
4. **Contato de emergência é gap real.** Não há como dizer "esta pessoa é contato de
   emergência daquela". §7 e §20/G-F2.
5. **A proveniência/idempotência mínima foi resolvida em duas peças.** O núcleo agora
   tem `LoteImportacaoHistorica`, `ItemImportacaoHistorica` (a identidade da fonte) e
   `VinculoImportacaoHistorica` (a associação da fonte a um registro do domínio). A
   unicidade do **item** é `condominio + origem + conta_origem + identificador_externo`;
   a do **vínculo** é `item + entidade_destino + identificador_destino`. §20/G-F4.
6. **A carga não pode usar inserção em massa.** A recusa de sobreposição de vínculo
   vive no *service*, por interseção de intervalos, e não em constraint de banco:
   `bulk_create` a contornaria silenciosamente. Regra de implementação obrigatória
   para o importador. §24.

Gmail e Drive ficam reafirmados como **fontes de migração, não dependências
permanentes do Eithra**.

---

## 2. Fontes

### 2.1 Verificadas

Somente o próprio repositório: `apps/` (models, constants, services), `docs/`,
`config/settings`. É o que sustenta §4 e §20–§24.

### 2.2 Declaradas na abertura da etapa — não verificadas neste ambiente

> **Exceção:** a **planilha de garagem** foi analisada externamente pela proprietária.
> A evidência de conjunto que ela forneceu está registrada abaixo e é a única fonte
> com dado direto nesta rodada. As demais seguem declaradas e não verificadas aqui.

**Google Drive** (nomes como declarados): `Lista RVE atualizada.xlsx`;
`Relação vagas Garagem`; pasta `GESTÃO RVE 2025/2026`; `CONTRATOS 2025/2026`;
`GERADOR`; `BOMBAS`; `AUTOMAÇÃO+BOMBAS 2026`; `AVCB`; `CARREGADOR VEICULAR`;
certificados; contratos; apólices; relatórios; ordens de serviço; plantas; ARTs;
RDOs.

**Planilha cadastral** (declarado): ~47 unidades; ~100 registros de pessoas; ~89 com
contato; dezenas de veículos; papéis `PROPRIETÁRIO`, `INQUILINO`, `ESPOSA`, `ESPOSO`,
`FILHO`, `FILHA`, `IRMÃO`, `IRMÃ`, `MÃE`, `PAI`, `COMPANHEIRO`, `RESPONSÁVEL`,
`PROCURADORA`, `DIARISTA`, `EMPREGADA`, `CONTATO DE EMERGÊNCIA`.

**Planilha de garagem** — declarada na abertura e **analisada externamente pela
proprietária** depois disso. É a única fonte sobre a qual há evidência direta
registrada nesta rodada:

| Verificação | Resultado |
|---|---|
| Unidades na fonte | **47** |
| Vagas declaradas no total | **68** |
| Distribuição por nível | **G1, G2, G3 e G4**, com combinações como `G2/G4`, `G2/G2`, `G1/G1`, `G4/G4/G4` |
| Identificador físico individual da vaga (número) | **ausente na fonte** |

Cada linha associa uma unidade a uma quantidade e a um nível (ou combinação de
níveis). Nada na fonte distingue "vaga nº 1" de "vaga nº 2" — só existe a contagem
por nível. Nenhum dado pessoal dessa planilha (nome, telefone, placa) é
reproduzido aqui. Ver §8.

**Gmail** (declarado): Atlas/elevadores; piscina; gerador; bombas; reservatórios;
portões; automação; fachada; infiltrações; AVCB; SPDA; extintores; luminárias de
emergência; climatização; limpeza; jardinagem; contratos; garantias; ordens de
serviço; notas fiscais; orçamentos; ocorrências; vendas de unidade; nada-consta;
trocas de inquilino.

Nada acima foi lido, contado, somado ou cruzado. Trata-se do inventário de entrada,
não de resultado de análise.

---

## 3. Cadastros encontrados

**Não produzível nesta etapa** — depende de leitura das fontes (§0). O que se pode
afirmar com segurança é o que o *domínio* aceita receber, e é o que §4 registra.

---

## 4. Mapa Unidade → Pessoa → Vínculo → Veículo → Vaga

Este mapa é do **domínio existente**, não das fontes. São os campos reais onde cada
informação da planilha encontraria lugar.

### 4.1 Unidade — `apps/comunidade/models.py` (`Unidade`)

| Campo | Tipo | Obrigatório |
|---|---|---|
| `condominio` | FK `Condominio` (PROTECT) | sim |
| `identificador` | CharField(30) | **sim** |
| `bloco_torre` | CharField(60) | não |
| `andar` | CharField(20) | não |
| `complemento` | CharField(120) | não |
| `observacao_interna` | TextField | não |
| `origem` | CharField — `OrigemInformacao` | default `CADASTRO` |
| `ativa` | BooleanField | default `True` |
| `criado_por` / `criado_em` / `atualizado_em` | — | auditoria |

Derivados de leitura: `rotulo`, `vinculos_vigentes()`, `pessoas_atuais()`,
`moradores_atuais()`, `proprietarios_atuais()`, `veiculos_relacionados()`.

Leitura: as 47 unidades entram por `identificador` (obrigatório) + `bloco_torre` /
`andar` / `complemento` (opcionais). Não há campo de metragem, matrícula, fração
ideal ou tipo de uso (residencial/comercial) — se a planilha trouxer isso, não há
onde guardar (§20, gap candidato).

### 4.2 Pessoa — `Pessoa`

| Campo | Tipo | Obrigatório |
|---|---|---|
| `condominio` | FK (PROTECT) | sim |
| `tipo_pessoa` | CharField(20) — `TipoPessoa`: `PF`, `PJ`, `NAO_INFORMADO` | sim |
| `nome` | CharField(180) | sim |
| `nome_social` | CharField(180) | não |
| `telefone` | CharField(20) | não |
| `whatsapp` | CharField(20) | não |
| `email` | EmailField | não |
| `documento` | CharField(20) — só dígitos; 11 (PF) ou 14 (PJ) | não |
| `observacao_interna` | TextField | não |
| `usuario` | FK `User` (SET_NULL) | não |
| `origem` | `OrigemInformacao` | default `CADASTRO` |
| `ativa` | BooleanField | default `True` |

Leitura: pessoa jurídica entra por `Pessoa` com `tipo_pessoa=PJ` e documento de 14
dígitos — é o caminho previsto para a empresa proprietária de sala/loja. O documento
é **opcional** e **não único**: a duplicidade é avisada, nunca fundida
(`possiveis_duplicidades`, comparação determinística de e-mail, telefone, WhatsApp e
documento normalizados).

### 4.3 Vínculo Pessoa ↔ Unidade — `VinculoPessoaUnidade`

| Campo | Tipo | Obrigatório |
|---|---|---|
| `condominio` | FK (PROTECT) | sim |
| `pessoa` | FK `Pessoa` (PROTECT) | sim |
| `unidade` | FK `Unidade` (PROTECT) | sim |
| `tipo_vinculo` | CharField(30) — enum de 6 valores | default `MORADOR` |
| `data_inicio` | DateField | **sim** |
| `data_fim` | DateField | não (em branco = em vigor) |
| `observacao_interna` | TextField | não |
| `criado_por` | FK `User` (SET_NULL) | não |

Derivados: `vigente`, `encerrado`. Querysets: `vigentes()`, `encerrados()`,
`da_unidade()`, `da_pessoa()`, `do_tipo()`.

### 4.4 Veículo — `Veiculo` e vínculos

| Model | Campos | Observação |
|---|---|---|
| `Veiculo` | `condominio`, `placa`(**obrigatória**, única por condomínio, maiúscula, sem separadores), `marca`, `modelo`, `cor`, `observacao_interna`, `origem`, `ativo` | validação rígida de placa: `[A-Z]{3}[0-9][0-9A-Z][0-9]{2}` (padrão antigo e Mercosul) |
| `VinculoVeiculoPessoa` | `veiculo`, `pessoa`, `data_inicio`(obrigatório), `data_fim`, `observacao` | unicidade temporal por (veículo + pessoa) |
| `VinculoVeiculoUnidade` | `veiculo`, `unidade`, `data_inicio`(obrigatório), `data_fim`, `observacao` | unicidade temporal por (veículo + unidade) |

Leitura: `"COMPASS - SJT 1I75"` normaliza para `SJT1I75` (a regex aceita), mas
`COMPASS` é dado de `modelo`, não de placa — a separação é do importador, não do
modelo. **Placa incompleta ou fora da regex é recusada pelo modelo**, e não aceita em
silêncio: esses casos são, por construção, revisão manual.

### 4.5 Vaga de garagem — **não existe, por decisão**

| Verificação | Resultado |
|---|---|
| Model de vaga | não existe em nenhum app — e **não foi criado nesta rodada** |
| Campo de vaga em `Veiculo`, `Unidade` ou nos vínculos | não existe |
| Representação indireta possível | `LocalOperacional` com `tipo=GARAGEM` descreve **um lugar** ("Garagem G1"), não uma vaga numerada nem sua atribuição a uma unidade |

As 68 vagas declaradas em quatro níveis permanecem sem entidade que as receba.
Não criar registros físicos sintéticos para representá-las: a fonte comprova
quantidade e nível, não identidade física. Ver §8 e §20/G-F1.

---

## 5. Divergências entre fontes

**Não produzível** (§0). Método que será aplicado quando as fontes chegarem, para
que a comparação seja determinística e não interpretativa:

| Cruzamento | Chave de comparação |
|---|---|
| Planilha cadastral ↔ planilha de garagem | `Unidade.identificador` |
| Cadastral ↔ contratos/apólices | nome ou documento (CNPJ/CPF normalizados) do fornecedor/locador |
| Cadastral ↔ e-mails de troca de inquilino | unidade + pessoa + data declarada |
| Vagas declaradas (68) ↔ vagas por unidade | unidade; a diferença entre o total declarado e a soma extraída é relatada como discrepância, não reconciliada em silêncio |
| Unidades da planilha ↔ unidades reais do condomínio | `identificador` — faltantes e excedentes listados separadamente |

---

## 6. Pessoas e vínculos — normalização do papel

O enum atual (`TipoVinculoPessoaUnidade`): `PROPRIETARIO`, `MORADOR`, `LOCATARIO`,
`FAMILIAR`, `FUNCIONARIO_UNIDADE`, `OUTRO`.

Mapeamento proposto para os rótulos declarados:

| Rótulo na planilha | Destino no domínio | Racional |
|---|---|---|
| `PROPRIETÁRIO` / `PROPRIETÁRIA` | `PROPRIETARIO` | direto |
| `INQUILINO` / `INQUILINA` | `LOCATARIO` | é o mesmo conceito; `LOCATARIO` já é o vocabulário da casa |
| `ESPOSA`, `ESPOSO`, `FILHO`, `FILHA`, `IRMÃO`, `IRMÃ`, `MÃE`, `PAI`, `COMPANHEIRO` | `FAMILIAR` | o domínio não distingue grau de parentesco — e não deve (G4/G10) |
| `EMPREGADA`, `DIARISTA` | `FUNCIONARIO_UNIDADE` | semanticamente adequado: trabalhador da unidade |
| `RESPONSÁVEL`, `PROCURADORA` | `OUTRO` + rótulo original preservado | não há equivalente; ver §20/G-F3 |
| `CONTATO DE EMERGÊNCIA` | **não é papel** | ver §7 |

Regra que a carga deve obedecer: `FAMILIAR ≠ autorizado` e
`FUNCIONARIO_UNIDADE ≠ autorizado`. Nenhum campo de autorização de acesso é criado
ou preenchido (G10).

---

## 7. Contato de emergência

**Gap real** (§20/G-F2). O domínio não tem como registrar que uma pessoa é o contato
de emergência de outra, nem para a unidade, nem para o condomínio.

O que existe: `Pessoa.telefone`, `Pessoa.whatsapp`, `Pessoa.email`,
`Pessoa.observacao_interna`. Ou seja: o telefone de emergência **da própria pessoa**
cabe; a relação "fulano é o contato de beltrano" **não cabe** — e é exatamente o que
a planilha parece declarar, já que `CONTATO DE EMERGÊNCIA` aparece como rótulo ao
lado de pessoas que também têm um papel (pai, mãe, cônjuge).

Isso confirma o princípio do pedido: contato de emergência é conceito **diferente** de
tipo de vínculo, e uma pessoa pode ser `FAMILIAR` **e** contato de emergência ao
mesmo tempo. Não implementado, conforme instruído.

---

## 8. Vagas de garagem

**Fonte real analisada; decisão fechada em não implementar** (§20/G-F1).

A planilha de garagem foi analisada fora deste ambiente. O que ela contém:

| Coluna da fonte | Conteúdo | Uso |
|---|---|---|
| Unidade / apartamento | identificador da unidade | cruzar com `Unidade.identificador` |
| Quantidade de vagas | número inteiro | contagem por unidade |
| Garagem / nível | `G1`, `G2`, `G3`, `G4` — e combinações como `G2/G4`, `G2/G2`, `G1/G1`, `G4/G4/G4` | distribuição por nível |

Evidência de conjunto: **47 unidades**, **68 vagas** declaradas, distribuídas entre
**G1, G2, G3 e G4**.

**O que a fonte NÃO fornece:** identificador físico individual da vaga. Ela diz, por
exemplo, "unidade X, quantidade 3, garagem `G2/G2/G3`" — o que permite afirmar
quantas vagas a unidade tem e em que níveis, mas **não** permite dizer "esta é a vaga
física nº 1, esta é a nº 2". Uma vaga individual exigiria uma identidade que a fonte
não dá.

**Decisão desta rodada:** manter `VagaGaragem` **não implementada**. Não criar
registros físicos sintéticos para representar vagas sem número conhecido.

> A fonte atual comprova quantidade e distribuição por nível/garagem, mas não
> identifica fisicamente cada vaga. Uma entidade `VagaGaragem` individual não deve ser
> criada a partir desta fonte sem inventar identidade.

**Alternativas a avaliar no futuro** — nenhuma implementada agora:

1. alocação de vagas por unidade e nível;
2. direito/quantidade de vagas;
3. `VagaGaragem` individual, caso uma fonte futura forneça identificação física.

Nenhum model, campo ou migration de vaga foi criado.

---

## 9. Veículos

O modelo comporta placa, marca, modelo, cor, observação e os dois vínculos. As
questões do pedido — vigência, duplicidade, placas incompletas, veículos sem
proprietário — só podem ser respondidas com a planilha em mãos:

| Questão | Como o domínio responde quando o dado chegar |
|---|---|
| Vigência | `data_inicio` obrigatória; `data_fim` em branco = em vigor |
| Duplicidade | `placa` é **única por condomínio** — a segunda ocorrência da mesma placa é recusada pelo banco, não silenciada |
| Placa incompleta/inválida | recusada pela validação de formato → encaminhada à revisão manual, nunca completada por inferência |
| Sem proprietário identificado | o veículo existe sem vínculo — `VinculoVeiculoPessoa` é opcional por natureza |

---

## 10. Fornecedores

Onde cada informação entra (`Fornecedor`, `apps/cadastros/models.py`):

`nome_razao_social` (obrigatório), `nome_fantasia`, `tipo_pessoa`, `documento`
(CNPJ/CPF), `email`, `telefone`, `contato`, `observacao`, `ativo`, mais o bloco de
disponibilização para moradores (`indicado_para_moradores`, `ativo_para_moradores`,
`descricao_publica`, `telefone_publico`, `whatsapp_publico`, `site_publico`,
`observacao_publica`, `validado_em`, `validado_por`).

Ligações já existentes: `FornecedorEspecialidade` (N:N com `EspecialidadeFornecedor`),
`ContratoFornecedor`, `Garantia`, `DocumentoFornecedor`, e os FKs `fornecedor` em
`Manutencao` e `RotinaRecorrente` — ou seja, vocação para "equipamentos atendidos" e
"locais atendidos" já existe por caminho indireto (contrato → equipamento/local).

A separação pedida — fornecedor confirmado × contato comercial × intermediário ×
administradora × prestador eventual — **não tem campo hoje**. Se a carga precisar
dessa distinção, é gap (§20, gap candidato). Dados públicos e internos estão
separados por construção, o que atende ao princípio de §5.

---

## 11. Equipamentos e locais

Onde entra (`Equipamento`): `local` (FK opcional), `nome` (obrigatório), `tipo_texto`
(texto livre, legado) **e** `tipo_equipamento` (FK para catálogo `TipoEquipamento`,
opcional), `data_aquisicao`, `data_instalacao`, `fabricante`, `modelo`,
`numero_serie`, `patrimonio`, `observacao`, `ativo`.

Onde entra (`LocalOperacional`): `nome` (obrigatório), `tipo` (classificação antiga,
`TipoLocal`, mantida) **e** `tipo_local` (FK para catálogo, opcional), `descricao`,
`ativo`.

Leitura relevante para o pedido: **o domínio já tem o padrão de "texto livre ao lado
de campo tipado"** (`Equipamento.tipo_texto` + `tipo_equipamento`;
`LocalOperacional.tipo` legado + `tipo_local`). É esse precedente que sustenta a
proposta de §20/G-F3 — preservar o rótulo original sem criar enum por sinônimo.

Consolidação de aliases ("portão externo" / "portão externo garagem" / "portão saída
garagem"): o domínio **não** consolida nada automaticamente. Se não for possível
afirmar que são o mesmo equipamento, os registros entram separados com a fonte
preservada, e a duplicidade fica apontada — nunca fundida em silêncio.

---

## 12. Contratos, garantias e documentos

`ContratoFornecedor`: `fornecedor` **obrigatório**, `tipo_contrato` opcional,
`numero_referencia` opcional, `objeto` **obrigatório** (200), `descricao`,
`data_inicio` **obrigatório**, `data_fim` opcional ("em branco, prazo
indeterminado"), `valor`, `periodicidade_cobranca` (`PeriodicidadeCobranca`:
mensal, bimestral, trimestral, semestral, anual, única, outro),
`renovacao_automatica`, `antecedencia_alerta_dias`, `prazo_aviso_rescisao_dias`,
`data_proximo_reajuste`, `indice_reajuste_texto`, `responsavel_interno`,
`observacao`, `ativo`.

`Garantia`: `fornecedor` **opcional**, `descricao` obrigatória, `numero_referencia`,
`data_inicio` obrigatório, `data_fim`, `antecedencia_alerta_dias`, `observacao`,
`ativo`.

`DocumentoOperacional`: `tipo_documento` (FK para catálogo `TipoDocumento`),
`titulo` (obrigatório), `numero_referencia`, `data_emissao`, `data_validade`,
`emissor` (texto livre), `observacao`, `ativo`. O arquivo vive em `ArquivoDocumento`
(`nome_original`, `storage_provider`, `storage_bucket`, `storage_key`, `mime_type`,
`tamanho_bytes`, **`sha256`**, `enviado_por`, `enviado_em`, `ativo`), com unicidade
por (documento, `sha256`).

Duas leituras que interessam à carga:

1. **Tipo de documento é dado, não código.** Não há enum de `CONTRATO`, `APÓLICE`,
   `LAUDO`, `ART`, `RDO`: `TipoDocumento` é catálogo por condomínio, mantido pela
   gestão, com `exige_validade` e `antecedencia_alerta_padrao_dias`. A carga deve
   **mapear para os tipos existentes** e propor apenas os que faltarem — não criar
   enum novo (§22 do pedido). Nenhum tipo padrão existe em código hoje.
2. **`emissor` é texto, não vínculo.** O emitente de um documento não é ligado por
   FK; a ligação com fornecedor/contrato/equipamento/garantia é feita por
   `DocumentoFornecedor`, `DocumentoContrato`, `DocumentoEquipamento`,
   `DocumentoGarantia`. Isso permite carregar documento cujo emitente ainda não é
   fornecedor cadastrado, sem inventar fornecedor.

`ArquivoDocumento` **já tem `sha256`** — a deduplicação por hash do pedido tem onde se
apoiar. **O complemento desta rodada** é o registro de proveniência (§20/G-F4): o item
da fonte diz *de onde o arquivo veio antes de entrar no Eithra* (a mensagem do Gmail, o
ID do arquivo no Drive) e o vínculo diz a *quais* registros do domínio ele foi
associado. O `sha256` continua sendo **deduplicação, não identidade**: o mesmo binário
pode ter duas proveniências distintas, e ambas são preservadas.

---

## 13. Arquivos

Classificação pedida (A–E) será aplicada **quando as fontes forem lidas**. O domínio
já oferece o destino correto:

- Incorporação (A) → `ArquivoDocumento` em storage privado, com `sha256`, sob
  `DocumentoOperacional` (ou `EvidenciaVisualBalanco`, quando for balanço).
- Somente referenciado (B) → registro do documento **sem** arquivo; nunca um arquivo
  vazio e nunca um registro marcado como importado sem o binário obtido.
- Duplicado (C) → reconhecido por `sha256` no mesmo documento.
- Indisponível (D) / precisa revisão (E) → tratados pelo encaminhamento de §20.

---

## 14. Histórico operacional

Consolidação por assunto, como pedido, tem onde acontecer: **`Manutencao` é o assunto
operacional**, e `HistoricoManutencao` é a linha de eventos
(`tipo_evento`: criada, status, responsável, prioridade, previsão, edição, serviço
realizado, concluída, cancelada).

Campos de `Manutencao` disponíveis: `demanda_origem` (procedência sem alterar a
demanda), `tipo` (`CORRETIVA`/`PREVENTIVA`), `titulo` (obrigatório, 200),
`descricao_problema`, `descricao_servico` (obrigatória para concluir),
`status` (`ABERTA`, `PROGRAMADA`, `EM_ANDAMENTO`, `AGUARDANDO`, `CONCLUIDA`,
`CANCELADA`), `prioridade`, `local`, `equipamento`, `fornecedor`, `responsavel`,
`data_prevista`, `data_inicio`, `data_conclusao`, `custo_previsto`, `custo_real`.

`Demanda` cobre o relato que ainda não é serviço: `titulo`, `descricao`, `status`,
`prioridade`, `origem` (`MANUAL`, `MORADOR`, `PORTARIA`, **`EMAIL`**, `WHATSAPP`,
`SISTEMA`, `OUTRA`), `prazo`, `local_contexto`, com histórico próprio. O
entrelaçamento é explícito: `Manutencao.demanda_origem` e
`ExecucaoRotina.demanda_gerada` / `manutencao_gerada`.

Consequência prática: **a cadeia do pedido** (falha do portão → ocorrência →
orçamento → autorização → OS → reparo → NF → garantia) tem onde existir como **um
assunto** (`Manutencao`) **com vários eventos**, e o que não é evento
(orçamento, NF) entra como `DocumentoOperacional` ligado por
`DocumentoEquipamento`/`DocumentoFornecedor`. Nada disso foi criado nesta etapa.

---

## 15. Linha do tempo

Datas distintas que o domínio **já preserva** sem colapsar: `Demanda.criado_em`,
`prazo`, `concluido_em`, `cancelado_em`; `Manutencao.data_prevista`, `data_inicio`,
`data_conclusao`, `criado_em`, `atualizado_em`; `HistoricoManutencao.data_hora`;
`DocumentoOperacional.data_emissao`, `data_validade`;
`ArquivoDocumento.enviado_em`.

**O que falta:** data da mensagem original, data da ocorrência relatada, data do
serviço quando difere da conclusão registrada e data de pagamento. Dessas, a data de
pagamento não tem campo em nenhum lugar; as demais só têm abrigo em texto livre. É
gap candidato (§20/G-F6).

---

## 16. Realizado × pendente

O domínio tem onde representar, sem criar status:

| Conceito do pedido | Representação existente |
|---|---|
| REALIZADO | `Manutencao.status = CONCLUIDA` + `data_conclusao` + `descricao_servico` preenchida (exigência do service) |
| PENDENTE | qualquer outro `StatusManutencao`, ou `Demanda` em `ABERTA`/`EM_ANDAMENTO`/`AGUARDANDO` |

`REVISAO_MANUAL` **não existe** — e não deve ser criado, conforme o pedido. A marca
complementar não cabe em status; vai em `observacao`/`descricao`, junto do texto
padrão `"Conclusão não identificada nas fontes analisadas."`, ou no relatório da
carga, sem tocar no domínio. Nenhum status foi criado.

---

## 17. Execução parcial

Não há campo próprio, e o pedido não exige criar um. A representação possível com o
que existe: registrar o que foi comprovado em `descricao_servico` e o que não foi em
`observacao`/`descricao_problema`, com a observação explícita. Se a frequência desse
caso justificar campo próprio, é decisão a tomar **depois** de ver o volume real nas
fontes — não por antecipação.

---

## 18. Recorrência

O domínio preserva o histórico sem reescrever (`HistoricoManutencao` append-only;
vínculos nunca apagados), então "não considerar resolvido porque houve visita
posterior" é **regra de leitura do importador**, não regra de modelagem. Nada a
alterar.

**Gap candidato:** o vocabulário de `TipoEventoManutencao` não distingue
*continuação*, *reincidência*, *novo problema* e *preventiva* — e o pedido proíbe
assumir. Sem essa distinção, uma visita posterior fica como manutenção nova e a
recorrência só é visível por leitura conjunta. Ver §20/G-F5.

---

## 19. Atlas / elevadores

**Não produzível** (§0): depende dos JOBs. A regra de identidade já está fixada e
será aplicada quando os documentos chegarem:

- identidade do assunto = `origem + equipamento + JOB`;
- mais de uma folha/documento do mesmo JOB **não** gera dois assuntos (caso
  conhecido: JOB 7130592032 com mais de uma folha);
- os códigos `MNT`, `REP`, `CBK` e afins são separados, e nenhum prefixo é
  interpretado automaticamente como estado final.

---

## 20. Gaps do modelo

### G-F1 — Vaga de garagem como entidade própria — **encerrado sem implementação**

1. **Problema real:** não há onde registrar as vagas nem a quem estavam atribuídas.
2. **Dado que provoca:** **68 vagas** em **4 níveis** (G1–G4) para **47 unidades**,
   declaradas em planilha própria.
3. **O que a fonte real comprova:** quantidade de vagas por unidade e distribuição por
   nível/garagem (com combinações como `G2/G4`, `G2/G2`, `G1/G1`, `G4/G4/G4`).
4. **O que a fonte real NÃO fornece:** identificador físico individual da vaga. Sem
   número, não há vaga individual — só quantidade por unidade e nível.
5. **Decisão desta rodada — encerrado sem implementação:** manter `VagaGaragem` **não
   implementada**; **não** criar registros físicos sintéticos para representar vagas
   sem número conhecido.
6. **Regra fechada (auditoria):**

   > A fonte atual comprova quantidade e distribuição por nível/garagem, mas não
   > identifica fisicamente cada vaga. Uma entidade `VagaGaragem` individual não deve
   > ser criada a partir desta fonte sem inventar identidade.

7. **Alternativas a avaliar no futuro** (nenhuma implementada agora): alocação de
   vagas por unidade e nível; direito/quantidade de vagas; ou `VagaGaragem`
   individual, caso outra fonte futura forneça identificação física.
8. **Impacto:** nenhum nesta rodada. **Migration:** nenhuma.

### G-F2 — Contato de emergência não existe — **gap real**

1. **Problema real:** não há como declarar quem acionar em emergência.
2. **Dado que provoca:** `CONTATO DE EMERGÊNCIA` como rótulo na planilha, em pessoas
   que também têm papel familiar.
3. **Modelo atual:** `Pessoa.telefone` / `whatsapp` / `email` são da própria pessoa.
4. **Por que não comporta:** o dado é uma **relação entre duas pessoas** (ou entre
   pessoa e unidade), e o modelo só tem atributos da pessoa.
5. **Ajuste mínimo proposto:** avaliar depois de ver quantas ocorrências existem. Se a
   relação for com a unidade, o abrigo natural é um vínculo próprio; se for com a
   pessoa, também. **Não implementar por antecipação** — o pedido é explícito.
6. **Impacto:** baixo a médio, se decidido.
7. **Migration:** sim, se houver entidade nova.
8. **Antes da carga:** não bloqueia — o dado pode entrar como observação e ser
   promovido depois, sem perda.

### G-F3 — Rótulo original do papel preservado em campo estruturado — **resolvido**

1. **Problema real:** `PROCURADORA` e `RESPONSÁVEL` só cabem em `OUTRO`, e o texto
   original não tem campo próprio.
2. **Dado que provoca:** rótulos da planilha sem equivalente no enum.
3. **Implementação desta rodada:** `VinculoPessoaUnidade.rotulo_origem`
   (`CharField`, opcional) ao lado de `tipo_vinculo`.
4. **Por que não comporta corretamente:** o pedido exige *preservar o texto original*;
   em `observacao_interna` ele é preservado mas não é consultável nem distinguível de
   uma anotação qualquer — e `OUTRO` sozinho não conta a história.
5. **Regra aplicada:** `tipo_vinculo` guarda a natureza normalizada; `rotulo_origem`
   preserva o texto da fonte. Ex.: `FAMILIAR` + `ESPOSA`.
6. **Impacto:** baixo — campo opcional, aditivo, sem efeito nos registros existentes.
7. **Migration:** sim — gerada nesta rodada e ainda não aplicada.
8. **Antes da carga:** resolvido.

### G-F4 — Proveniência e idempotência persistidas em banco — **resolvido**

1. **Problema real:** não é possível provar de onde um cadastro veio, nem repetir a
   carga com segurança.
2. **Dado que provoca:** ~47 unidades, ~100 pessoas, ~89 contatos, dezenas de
   veículos, mais contratos, garantias, documentos e histórico — todos de fontes
   externas, com risco real de recarga.
3. **Implementação desta rodada:** `LoteImportacaoHistorica`,
   `ItemImportacaoHistorica` e `VinculoImportacaoHistorica`, no núcleo do projeto.
   A revisão mostrou que a **identidade da fonte** e a **associação ao destino** são
   conceitos diferentes: uma mesma mensagem ou arquivo pode explicar várias entidades
   do domínio (uma Demanda, uma Manutenção, um DocumentoOperacional) e sua identidade
   externa continua a mesma. Por isso a proveniência ficou em duas peças — o item da
   fonte e o vínculo com o domínio —, sem `GenericForeignKey` (ver itens 5 e 6).
4. **Por que não comporta:** `origem` diz *que* veio de importação, não *de onde*; e
   nada impede o mesmo cadastro entrar duas vezes numa segunda execução. O precedente
   do projeto (`ImportacaoBalanco`: fonte, `sha256`, `status`, desfecho) existe para
   balanços e **não** foi estendido a cadastros.
5. **Regra aplicada — idempotência do item:** a chave é
   `condominio + origem + conta_origem + identificador_externo`
   (Gmail: conta + `message_id`; Drive: origem + `file_id`). Reprocessamento atualiza
   a mesma linha, em vez de duplicá-la. `conta_origem` entrou na chave porque a mesma
   `message_id` de contas diferentes é fonte diferente.
6. **Regra aplicada — vínculo com o domínio:** model próprio
   (`VinculoImportacaoHistorica`), com chave `item + entidade_destino +
   identificador_destino`. A mesma fonte pode ter vários vínculos sem duplicar a
   identidade da fonte; `destino_id` (UUID) liga o vínculo ao registro do domínio
   quando ele existe.
7. **`hash_fonte` não é identidade:** ajuda a reconhecer o mesmo binário, mas não
   substitui a chave e **não apaga proveniências distintas** do mesmo binário.
8. **Impacto:** médio — afeta a forma de carregar, não o domínio de negócio.
9. **Migration:** sim — gerada nesta rodada e ainda não aplicada.
10. **Antes da carga:** resolvido.

#### G-F4.1 — Decisões finais de desenho da proveniência

> **A proveniência histórica é uma trilha de auditoria e idempotência. Ela não
> substitui relacionamentos do domínio e não é dependência funcional do Eithra.**

**`destino_id` é `UUIDField` — válido para todos os destinos previstos.** O
inventário foi conferido pelo próprio Django (`apps.get_models()`): **todo model de
domínio do Eithra tem PK `UUIDField`**. Os únicos PKs não-UUID do projeto são os de
bibliotecas do Django (`admin.LogEntry`, `auth.Group`, `auth.Permission`,
`contenttypes.ContentType`, `sessions.Session`), e **nenhum deles é destino** de
`entidade_destino`.

| Valor de `entidade_destino` | Model | App | PK |
|---|---|---|---|
| `UNIDADE` | `Unidade` | `apps.comunidade` | UUIDField |
| `PESSOA` | `Pessoa` | `apps.comunidade` | UUIDField |
| `VINCULO_PESSOA_UNIDADE` | `VinculoPessoaUnidade` | `apps.comunidade` | UUIDField |
| `VEICULO` | `Veiculo` | `apps.comunidade` | UUIDField |
| `VINCULO_VEICULO_PESSOA` | `VinculoVeiculoPessoa` | `apps.comunidade` | UUIDField |
| `VINCULO_VEICULO_UNIDADE` | `VinculoVeiculoUnidade` | `apps.comunidade` | UUIDField |
| `VAGA_GARAGEM` | — (não implementado, G-F1) | — | — |
| `VINCULO_VAGA_UNIDADE` | — (não implementado, G-F1) | — | — |
| `FORNECEDOR` | `Fornecedor` | `apps.cadastros` | UUIDField |
| `LOCAL_OPERACIONAL` | `LocalOperacional` | `apps.cadastros` | UUIDField |
| `EQUIPAMENTO` | `Equipamento` | `apps.cadastros` | UUIDField |
| `CONTRATO_FORNECEDOR` | `ContratoFornecedor` | `apps.governanca` | UUIDField |
| `GARANTIA` | `Garantia` | `apps.governanca` | UUIDField |
| `DOCUMENTO_OPERACIONAL` | `DocumentoOperacional` | `apps.governanca` | UUIDField |
| `ROTINA` | `RotinaRecorrente` | `apps.rotinas` | UUIDField |
| `MANUTENCAO` | `Manutencao` | `apps.manutencoes` | UUIDField |
| `DEMANDA` | `Demanda` | `apps.demandas` | UUIDField |

**Vocabulário de `entidade_destino`:** `TextChoices`
(`EntidadeImportacaoHistorica`, em `apps/core/constants.py`) — vocabulário
controlado, não texto livre. `MANUTENCAO`, `manutencao` e `Manutenção` não podem
representar coisas diferentes porque só existe uma grafia válida: o valor canônico
(`"manutencao"`) e o rótulo humano ("Manutenção"). O campo é `CharField` com
`choices` e todo registro passa por `full_clean()`, onde a escolha é conferida.
Catálogo em banco não foi criado — `TextChoices` basta para um vocabulário fechado.

**Papel de cada campo:**

- `destino_id` = **PK técnica do registro do Eithra**. Só existe **depois** que o
  registro do domínio foi criado; é rastreabilidade, não identidade de carga.
- `identificador_destino` = **identidade lógica/estável** que o importador conhece
  **antes** de o registro existir (o `identificador` da unidade, o documento da
  pessoa, etc.).

**A chave do vínculo fica em `identificador_destino`, e não em `destino_id`** — a
razão é objetiva: `destino_id` não existe no primeiro processamento e só passa a
existir no segundo. Uma chave que muda entre a primeira e a segunda execução não
serve para idempotência. `identificador_destino` é estável nas duas.

**Contrato do importador (e o limite da garantia):** quando um item de fonte se
refere a **mais de um** registro do mesmo tipo, o importador **precisa** declarar um
`identificador_destino` distinto e estável para cada um. Sem identificador, o item é
entendido como referindo-se a **um único** registro daquele tipo — o que é correto
para a maioria dos casos. Entidades ambíguas nunca são fundidas em silêncio; quando
não houver identidade estável, o vínculo entra com revisão manual marcada.

**Ausência intencional de integridade referencial.** O vínculo **não** usa
`GenericForeignKey` nem FKs nullable para os dezessete destinos. As consequências
são aceitas de propósito:

- a exclusão de uma entidade do domínio **não apaga** a fonte histórica — `item` e
  `lote` são `PROTECT`, e o vínculo é evidência, não dependência;
- um vínculo pode, no futuro, apontar para uma entidade que já não existe: ele
  registra **que aquela importação aconteceu e a quê ela se referia**, e continua
  legível como histórico;
- nenhuma operação normal do Eithra consulta `VinculoImportacaoHistorica` para
  funcionar. A proveniência é leitura de auditoria, nunca pré-requisito de domínio.

**Normalização da identidade externa:** a chave é gravada já normalizada
(espaços colapsados e das pontas removidos) **antes** de chegar à constraint, de
modo que um espaço acidental não cria duplicidade. O conteúdo de `message_id` e
`file_id` não é alterado semanticamente. A forma canônica de `conta_origem`
(minúsculas, por exemplo) é responsabilidade de quem declara — o modelo não adivinha
se a fonte distingue maiúsculas, e inventar essa regra seria pior que exigir o valor
canônico.

### G-F5 — Vocabulário de eventos não distingue recorrência — **gap candidato**

`TipoEventoManutencao` tem 9 valores e nenhum deles expressa "continuação",
"reincidência", "novo problema" ou "preventiva decorrente". Impacto baixo; a decisão
depende do volume real. **Não implementar agora.**

### G-F6 — Datas da fonte e eventos de orçamento/aprovação/NF/pagamento — **gap candidato**

Não há campo para "data da mensagem", "data da ocorrência" e "data de pagamento";
e o vocabulário de eventos não cobre orçamento, aprovação, NF e pagamento.
Orçamento e NF cabem como documento; aprovação e pagamento, hoje, só em texto livre.
Avaliar com o volume real. **Não implementar agora.**

### Não são gaps (verificados e descartados)

| Hipótese levantada | Verificação |
|---|---|
| "Falta hash nos arquivos de documento" | **falso** — `ArquivoDocumento.sha256` existe, com unicidade por (documento, hash) |
| "Falta rótulo de tipo de documento" | **não é enum por decisão** — `TipoDocumento` é catálogo por condomínio, com `exige_validade` e prazo de alerta |
| "Pessoa precisa de campo de contato de emergência próprio" | **não** — o problema é a relação, não o contato (§20/G-F2) |
| "Contrato sem fornecedor não carrega" | **verdadeiro e por design** — `ContratoFornecedor.fornecedor` é obrigatório (§12); o contrato fica pendente até o fornecedor existir |
| "Pessoa jurídica precisa de model próprio" | **falso** — `Pessoa` com `tipo_pessoa=PJ` e documento de 14 dígitos cobre |

---

## 21. Ajustes de schema recomendados

Depois do fechamento das decisões, o estado dos ajustes ficou assim:

| # | Ajuste | Obrigatório antes da carga? | Migration |
|---|---|---|---|
| 1 | G-F1 — `VagaGaragem` + `VinculoVagaUnidade` | **não** — a fonte não identifica a vaga; **encerrado sem implementação** | nenhuma |
| 2 | G-F3 — campo de rótulo original no vínculo | resolvido | sim, gerada nesta rodada |
| 3 | G-F4 — proveniência / idempotência (item + vínculo) | resolvido | sim, gerada nesta rodada |
| 4 | G-F2 — contato de emergência | não (pode entrar como observação) | sim, se criado |
| 5 | G-F5 / G-F6 — recorrência e datas da fonte | não — depende do volume | a avaliar |

`G-F3` e `G-F4` já têm implementação local e migrations geradas (**não aplicadas**).
`G-F1` foi **encerrado sem implementação**: a fonte real comprova quantidade e nível,
mas não a identidade física de cada vaga.

---

## 22. Itens que podem ser importados sem alteração de schema

Com o domínio como está, cabem sem nenhum ajuste: **Unidade**, **Pessoa** (física e
jurídica), **VinculoPessoaUnidade**, **Veiculo**, **VinculoVeiculoPessoa**,
**VinculoVeiculoUnidade**, **Fornecedor** + especialidades, **LocalOperacional**,
**Equipamento**, **ContratoFornecedor** (exige fornecedor identificado),
**Garantia**, **DocumentoOperacional** + **ArquivoDocumento** (+ relação com
fornecedor/contrato/equipamento/garantia), **RotinaRecorrente** + checklists,
**Manutencao** + histórico, **Demanda** + histórico.

Consequência: **a maior parte da carga não exige mudar o domínio.** Os ajustes de §21
são de borda, não de fundação.

## 23. Itens que exigem ajuste antes da carga

- **Vaga de garagem** (68 vagas declaradas) — **não é ajuste desta rodada**. A fonte
  comprova quantidade e distribuição por nível/garagem, mas não identifica fisicamente
  cada vaga; por isso nenhuma entidade foi criada. A carga importa o que é comprovável
  (unidade, pessoa, veículos e seus vínculos) e a dimensão "vaga" fica registrada como
  decisão futura, sem inventar identidade.
- **Contato de emergência** — continua fora desta rodada; se necessário para a
  primeira carga, volta como ajuste separado.

---

## 24. Proposta de sequência de importação

Ordem que respeita as dependências do domínio e permite conferência a cada passo:

1. **Unidades** — base de tudo; `identificador` obrigatório, 47 declaradas.
2. **Pessoas** — sem vínculo, com contatos; duplicidades apontadas, nunca fundidas.
3. **Vínculos Pessoa ↔ Unidade** — **passando pelo service**, e não por
   `bulk_create`: a recusa de sobreposição é por interseção de intervalos e vive no
   service, não em constraint de banco. Inserção em massa a contornaria em silêncio.
4. **Veículos** — placas normalizadas; as inválidas vão para revisão manual, nunca
   completadas por inferência.
5. **Vínculos de veículo** (pessoa e unidade), também pelo service.
6. **Locais e equipamentos** — consolidação de aliases só quando inequívoca; o resto
   fica apontado como possível duplicidade.
7. **Fornecedores** + especialidades.
8. **Contratos e garantias** — contratos dependem de fornecedor identificado.
9. **Documentos e arquivos** — documento antes do arquivo; arquivo só com binário
   obtido, com `sha256`; nada marcado como importado sem o arquivo.
10. **Rotinas** e seus checklists.
11. **Histórico operacional consolidado** — assunto primeiro, eventos depois, com as
    datas distintas preservadas.
12. **Relatório de desfecho** — importados, pendentes, revisão manual, duplicidades.

Etapas 5 a 11 são retomáveis: cada uma deve poder ser repetida sem duplicar o que já
entrou — o que reafirma G-F4.

---

## 25. Riscos

| Risco | Mitigação |
|---|---|
| Duplicação em recarga (sem controle de origem) | G-F4 antes da carga; chave externa + hash |
| Perda de fidelidade do papel original | G-F3; ou `observacao_interna` com prefixo, assumindo a perda |
| Vaga de garagem ficar de fora e exigir recarga | decidir G-F1 antes |
| Contornar a recusa de sobreposição por inserção em massa | carga obrigatoriamente via service |
| Placa/pessoa inválida aceita em silêncio | validação do modelo já recusa; nada é completado por inferência |
| Documento marcado como importado sem o arquivo existir | só marcar com o binário obtido e o `sha256` calculado |
| Inferência virando fato cadastral | classificações A/B/C do pedido + revisão manual como marca complementar, nunca status |
| Dados pessoais em log, teste ou documento | §24 do pedido: contagens e exemplos genéricos |

---

## 26. Confirmação: nada foi gravado

Nenhuma gravação em banco **de produção ou de Neon**. Nesta rodada houve somente
ajustes locais de código, testes e geração de migrations **não aplicadas**.
Nenhum anexo foi importado. Nenhuma fixture ou seed foi criada. Gmail e Drive
seguem sem importação e sem conexão de runtime. **Nenhum registro de vaga foi
criado** — a fonte não fornece identidade física (§8).

## 27. Confirmação: Neon e produção não foram alterados

Nenhuma conexão com Neon DEV foi feita nesta etapa. Nenhuma conexão com produção.
Nenhum `migrate` foi executado fora do banco local de testes. Nenhum push foi
feito. Nenhuma variável de ambiente ou credencial foi alterada.

---

## Decisões pendentes da proprietária do produto

1. **Como as fontes chegam** — **decidido:** por leitor externo autorizado, em
   pacote JSON local (§0). Os itens de §4–§19 seguem pendentes até o pacote existir;
   a normalização para o domínio é do importador/dry-run do Eithra.
2. **Vaga de garagem — qual conceito adotar, quando (e se) for implementado:**
   alocação de vagas por unidade e nível; direito/quantidade de vagas; ou
   `VagaGaragem` individual, caso uma fonte futura traga identificação física.
   **Nada a implementar agora** — a fonte atual não identifica a vaga.

Nada será implementado antes dessas respostas.
