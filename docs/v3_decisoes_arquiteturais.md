# Eithra V3 — Decisões Arquiteturais

> Registro vivo das decisões **realmente aprovadas**. Este arquivo não descreve
> propostas: apenas decisões já fixadas. Decisões pendentes de aprovação ficam nos
> documentos de fase correspondentes (ex.:
> `fase_1a_balancos_decisoes.md`).

## Como usar

- Cada decisão tem um identificador, um status e uma justificativa curta.
- Uma decisão só entra aqui quando está aprovada e é estável.
- Propostas ainda em avaliação **não** entram neste arquivo.

## Fase 0 — Fundação

### A1 — `accounts.User` sem papel global — APROVADA
O papel pertence ao vínculo com o condomínio, nunca ao usuário. A cardinalidade
`User ↔ Condominio` é N:N via `AcessoUsuarioCondominio`.

### A2 — Autorização centralizada no backend — APROVADA
Toda autorização passa por `apps/condominios/acesso.py` (`tem_acesso_ativo`),
* fail-closed*. A interface não é controle de acesso.

### A3 — Registro não autorizado responde 404 — APROVADA
Para não revelar a existência do dado. 403 apenas quando a existência é legítima.

### A4 — PostgreSQL obrigatório em DEV, sem fallback para SQLite — APROVADA
`DATABASE_URL` obrigatória; ausência causa falha explícita.

### A5 — Sneat + Bootstrap 5 + Boxicons como base — APROVADA
Identidade Eithra via tokens (`--eithra-primary: #153F46`). Sem design system novo.
Sem componentes demo do template.

### A6 — `X_FRAME_OPTIONS = "DENY"` — APROVADA

### A7 — `SECURE_HSTS_PRELOAD = False` — APROVADA
Mantido como `False` até decisão consciente de preload no domínio definitivo.

## Fase 1A — Balanços Mensais

### B1 — `Condominio` é o contexto suficiente de prédio — APROVADA
Não se cria estrutura predial (`Unidade`, `Bloco`, `Torre`, `Pavimento`, `Área`,
`Equipamento`, `SistemaPredial`) por antecipação.

### B2 — Versionar, nunca sobrescrever — APROVADA
Substituição gera nova versão; a anterior é preservada e marcada `SUBSTITUIDO`,
com documento, histórico, autor e motivo.

### B3 — Documento original em storage externo privado — APROVADA
Nunca no filesystem da Vercel. Acesso apenas por view autenticada + signed URL
temporária. Nenhum arquivo sensível acessível por URL pública direta.

### B4 — Provenance obrigatória — APROVADA
Todo dado relevante é rastreável (documento, competência, página/seção, automático
× corrigido, valor original × validado), sem exposição na interface principal.

### B5 — `FornecedorCandidato` não é model — APROVADA
É conceito de extração/reconciliação. Nome do documento nunca vira fornecedor
cadastrado automaticamente.

### B6 — Publicação nunca automática — APROVADA
`VALIDADO` → preparar resumo → revisão da gestão → publicar. Controle editorial
sempre humano.

### B7 — Projeção do morador restritiva por padrão — APROVADA
Somente dados explicitamente publicáveis entram na projeção. Fail-closed: sem
certeza de classificação, o dado não é publicado.

### B8 — Serviços pequenos e coesos — APROVADA
Sem `balanco_service.py` gigante; sem micro-serviços artificiais.

## Fase 1B — Balanços Mensais: implementação

### C1 — Competência como `DateField` no primeiro dia do mês — APROVADA
Nunca string livre como `"08/2026"`. Garantida por `clean()` e pela constraint
`balanco_competencia_no_primeiro_dia`.

### C2 — Sem workflow formal de revisão de balanço — APROVADA
A V3 inicial não possui etapa de revisão. O ciclo é: o resultado da leitura é
apresentado à gestão, a gestão **confirma a importação** e só então o mês passa
a valer (`VALIDADO`); a publicação é opcional (`PUBLICADO`). Não há fila,
pendência, aprovação em etapas, revisor, estado de revisão nem tela de revisão.
Se surgir necessidade real, entra como nova decisão de produto.

### C3 — Situação funcional separada do estado técnico — APROVADA
`StatusBalanco` (`validado`, `publicado`, `substituido`) e
`ProcessamentoBalanco` (`pendente`, `processando`, `concluido`, `erro`) são
campos distintos. `ERRO` nunca é situação funcional, e nenhum estado técnico
substitui a confirmação humana.

### C4 — Versão vigente derivada, sem campo redundante — APROVADA
`VALIDADO` e `PUBLICADO` são versão vigente; `SUBSTITUIDO` não é.
`UniqueConstraint` condicional (`~Q(status=SUBSTITUIDO)`) garante uma única
versão vigente por (condomínio, competência). Não se cria `eh_atual`.

### C5 — Unicidade do `sha256` por balanço, não global — APROVADA
O mesmo arquivo pode existir legitimamente em contextos distintos. O índice
`documento_sha256_idx` apenas apoia a descoberta de repetição.

### C6 — `EventoBalanco` adiado — APROVADA
Não há produtor de evento nesta fase. Preferiu-se adiar a criar entidade
genérica prematura. Os marcos atuais são cobertos por
`criado_por`/`criado_em`, `validado_por`/`validado_em` e pela cadeia `substitui`.

### C7 — `DocumentoBalanco` sem versionamento próprio — APROVADA
O versionamento de `BalancoMensal` já resolve o ciclo de vida: uma versão
corrigida é um novo balanço, com os seus próprios documentos. `DocumentoBalanco`
não tem versão própria nem `substitui`.

## Fase 1C — Entrada segura do balanço

### D1 — `ImportacaoBalanco` é entidade transitória, separada do balanço — APROVADA
Upload de arquivo, processamento e resultado preliminar **não** são
`BalancoMensal`. O que nasce no recebimento é uma tentativa de importação; o mês
só passa a valer com a confirmação da gestão, em fase futura. Nenhum balanço é
criado no upload.

### D2 — Storage privado por contrato, adaptador Supabase sem SDK — APROVADA
O domínio fala com `StorageProvider` (`enviar`, `remover`, `url_assinada`) e o
provedor concreto fica atrás dele: trocar de provedor não exige mexer no
domínio. O adaptador Supabase usa **HTTP puro** (biblioteca padrão), sem SDK.
Persistem-se apenas `provider`, `bucket` e `object_key`; URL pública, signed URL,
token e service role **nunca** são gravados (recusado por `clean()`).

### D3 — Bucket privado por ambiente — APROVADA
DEV e PROD usam buckets separados, nunca o mesmo. Falta de configuração provoca
erro explícito nomeando as variáveis ausentes; não existe queda silenciosa para
filesystem local.

### D4 — Object key apenas com identificadores — APROVADA
`balancos/<condominio_uuid>/<importacao_uuid>/documento.pdf`. Sem nome de
arquivo, nome de condomínio em texto, competência não confirmada ou dado
pessoal no caminho do objeto.

### D5 — Duplicidade por `condominio` + `sha256` — APROVADA
Sem constraint global de hash. O mesmo arquivo pode existir legitimamente em
condomínios distintos. Importações descartadas e com falha de recebimento não
contam como repetição.

### D6 — Limite de upload configurável — APROVADA
`BALANCOS_MAX_UPLOAD_BYTES`, 50 MB por padrão. Folgado para balanços reais com
muitas páginas e suficiente para limitar abuso.

### D7 — Papéis que importam balanço: ADMIN e SINDICA — APROVADA
Supervisão, operação, portaria e morador não importam. A regra vive em
`apps/balancos/permissoes.py`; a decisão de acesso ao condomínio continua
centralizada em `apps/condominios/acesso.py`. O menu não é controle de acesso.

### D8 — Consistência por gravação antes do envio, com compensação — APROVADA
Não existe transação ACID entre banco e storage externo. O registro é gravado
antes do envio; falha de envio resulta em `ERRO` (nunca falso sucesso); falha de
gravação após o envio dispara remoção compensatória do objeto.

### D9 — Descarte preserva a memória e libera o arquivo — APROVADA
Descartar marca `DESCARTADO` com `descartado_em` em vez de apagar o registro. A
remoção do objeto é *best-effort*: o descarte não fica refém do storage.

### D10 — Acesso ao arquivo por signed URL curta, sob demanda — APROVADA
Nenhum endereço fixo: cada visita autorizada gera um acesso temporário
(`BALANCOS_URL_ACESSO_SEGUNDOS`, 60 s por padrão). Nada é persistido.

### D11 — Navegação contextual decidida pelo domínio — APROVADA
O item administrativo de Balanços só é oferecido a quem pode usá-lo (ADMIN e
SINDICA). A visibilidade não é uma segunda lista de papéis: o menu chama
`pode_importar_em_algum_condominio`, a mesma regra que a view aplica. A decisão
fica em `apps/core/context_processors.py`, em um item com `visivel_para` — nada
de condição de papel espalhada pelos templates. Esconder o item é conveniência
de navegação e **não** substitui a autorização: a rota continua respondendo 404
a quem não pode, e continua respondendo 200 a quem pode, com ou sem menu.

### D12 — Sem repetição automática de chamadas ao storage — APROVADA
Não há retry genérico. Repetir um envio sem garantia de idempotência poderia
criar um segundo objeto do mesmo arquivo — pior do que falhar. A única
repetição admitida é a do caso sabidamente idempotente: o provedor informar que
o objeto já existe, quando o envio é refeito substituindo. Qualquer outra falha
é reportada de forma explícita. Toda conversa com o provedor passa por um único
ponto, onde o tempo limite é aplicado — não existe chamada sem limite.

### D13 — Detecção de layout determinística com `pypdf` — APROVADA
A Fase 1D identifica o formato do PDF sem OCR e sem IA. O detector usa
`pypdf`, lê apenas a camada textual do documento e decide por regras
determinísticas baseadas em marcadores estruturais, densidade textual e sinais
visuais simples. `resultado_extraido` e `inconsistencias` permanecem vazios.

### D14 — Evidências técnicas retornam do serviço, não do banco — APROVADA
As evidências de detecção (marcadores encontrados, páginas analisadas,
proporção de texto, caracteres extraídos) são retornadas pelo serviço para
auditoria e testes, mas não são persistidas em campo novo. Isso preserva a
preferência da fase por zero migration e evita poluir o registro transitório
com diagnóstico técnico.

### D15 — Amostragem distribuída de páginas para conter custo — APROVADA
A identificação de layout não lê o documento inteiro por padrão. O detector
inspeciona até `BALANCOS_DETECTOR_MAX_PAGINAS` páginas, distribuídas ao longo
do PDF. O objetivo é reconhecer o formato com custo local previsível, sem
varrer centenas de páginas quando uma amostra já basta.

### D16 — A abertura do documento entra sempre na amostra — APROVADA
Substitui parcialmente D15. A abertura do balanço concentra o índice e o
resumo, que são os sinais mais fortes do formato atual. Um balanço real de 175
páginas foi classificado como desconhecido porque a amostra uniforme saltava da
página 1 para a 13 e deixava justamente essas páginas de fora. Agora as
primeiras `BALANCOS_DETECTOR_PAGINAS_INICIAIS` páginas entram inteiras e o
orçamento restante é distribuído até o fim do arquivo. Não há score novo, nem
mudança de limiar: apenas a escolha das páginas lidas.

### D17 — A quantidade de páginas não classifica o layout — APROVADA
Substitui parcialmente D15 e D16 e remove o espalhamento residual da amostra. O
número de páginas não é característica de layout: um mesmo modelo pode vir
curto, longo, parcialmente digitalizado ou com muitos comprovantes anexados, e o
balancete pode ocupar só parte do PDF. Por isso: (a) o total de páginas tem peso
zero em qualquer critério; (b) a amostragem passa a ser exclusivamente a abertura
estrutural — as primeiras `BALANCOS_DETECTOR_MAX_PAGINAS` páginas —, deixando
anexos e comprovantes fora do julgamento; (c) a densidade textual é medida por
página com texto, e não por total de caracteres, para não depender do tamanho do
arquivo. A variável `BALANCOS_DETECTOR_PAGINAS_INICIAIS` deixa de existir. Um
PDF parcial é classificado quando a abertura traz evidência estrutural
suficiente; sem ela, o resultado permanece `DESCONHECIDO`. Não entram OCR nem
IA, `resultado_extraido` continua `{}` e nenhum `BalancoMensal` é criado.

## Fase 1E — Extração financeira do modelo atual

### E1 — Contrato de leitura versionado — APROVADA
O resultado da extração é o contrato tipado `BalancoExtraidoV1`
(`SCHEMA_VERSAO = 1`), com dataclasses imutáveis, e não um dicionário solto. O
contrato é independente do layout visual de qualquer administradora. Quando o
formato dos dados mudar de forma incompatível, a versão muda e o resultado
antigo continua legível pelo seu próprio contrato.

### E2 — Extração reaproveita o PDF já recebido — APROVADA
A leitura roda sobre o mesmo arquivo privado do recebimento, reaproveitando o
caminho de download já homologado na Fase 1D. Quando quem chama já tem o
conteúdo em mãos (o caminho do upload tem), ele é passado e evita um segundo
acesso ao storage. Não há segunda implementação do acesso ao PDF.

### E3 — A categoria original nunca é substituída — APROVADA
O agrupamento do documento é preservado ao lado da categoria do Eithra
(`categoria_original` + `categoria_eithra`). Considera-se primeiro o que a
administradora já classificou; sem ele, a descrição decide. Sem evidência
textual, a resposta é `NAO_CLASSIFICADO` ("A classificar"), que é uma resposta
legítima e não um erro. A comparação exige palavra inteira: a classificação
errada é pior do que a ausência de classificação.

### E4 — Natureza é comportamento, não valor — APROVADA
`RECORRENTE_FIXA`, `RECORRENTE_VARIAVEL`, `EXTRAORDINARIA` e `NAO_DETERMINADA`
(na tela: "Recorrente", "Recorrente variável", "Extra", "A confirmar").
Recorrência não se confunde com variação do valor: uma conta de energia é
recorrente e varia; a troca de uma peça é extraordinária mesmo repetida. Quem é
recorrente é a despesa, não quem a presta. Sinais contraditórios resultam em
`NAO_DETERMINADA` — a dúvida honesta vale mais que a certeza inventada.

### E5 — Conferência por exceção, com tolerância de arredondamento — APROVADA
A validação financeira é determinística e opera sobre o contrato já montado, sem
relê o PDF. Só se aponta o que se pode afirmar: quando o documento não declara um
total, ou a estrutura contábil não descreve a equação simples (várias contas,
fundos), **nada é apontado** — silêncio é resultado válido. Diferenças são
toleradas até `TOLERANCIA = 0.05`, porque cada linha já foi arredondada ao
centavo; transformar resíduo de arredondamento em aviso treinaria a gestão a
ignorar os avisos.

### E6 — Dinheiro é texto decimal, nunca `float` — APROVADA
Valores viajam como `Decimal` convertido em texto e chegam ao JSON como string.
Um centavo perdido em arredondamento binário apareceria depois como divergência
de total e a conferência perderia sentido.

### E7 — Ausência é ausência — APROVADA
Campo que o documento não traz fica `None`. Não se estima, não se completa por
suposição e não se converte inferência em fato. Quando a leitura encontra algo
duvidoso, ela cria um ponto de atenção em vez de inventar uma certeza.

### E8 — Extensão sem migration — APROVADA
O resultado é gravado nos campos já existentes de `ImportacaoBalanco`
(`resultado_extraido`, `inconsistencias`, `schema_versao`). Nenhuma migration é
criada nesta fase. As evidências técnicas continuam fora do banco (ver D14).

### E9 — Inadimplência apenas em agregado — APROVADA
O contrato carrega inadimplência do mês somente em total (valor, percentual,
quantidade). Nenhum dado por unidade entra no contrato — é o total do condomínio
que a gestão acompanha aqui.

### E10 — Nenhum `BalancoMensal` nasce na leitura — APROVADA
A importação continua sendo o objeto transitório. A leitura não confirma, não
publica e não cria o mês: o mês só passa a valer com a confirmação da gestão, em
fase futura.

### E11 — Sem ação de confirmação nesta fase — APROVADA
A tela de detalhe apresenta o que foi lido e os pontos de atenção, mas **não**
oferece botão de confirmação. Não se cria CTA falso para uma ação que ainda não
existe.

### E12 — Linguagem humana na tela, nada de termo técnico — APROVADA
Nenhum código, `score`, nome de campo ou detalhe de sistema é exibido. Os estados
aparecem como "Recorrente", "Extra", "A confirmar", "A classificar"; o bloco
principal é "Dados lidos" ("Dados lidos · N pontos precisam de atenção"). O
formato legado explica o que vai acontecer: "Este formato será processado pela
leitura de documentos legados."

## Fase 2A — Demandas operacionais

### F1 — Demanda é unidade operacional, não sistema de chamados — APROVADA
Uma demanda é "algo com consequência operacional que precisa de acompanhamento".
Não há fila, SLA, etapas de atendimento, workflow BPM, thread, curtida nem anexo
social. Também ficam de fora, por falta de uso claro, `categoria`, `solicitante`
e `observacao_interna`.

### F2 — Ciclo de situação curto e único — APROVADA
`StatusDemanda` tem cinco valores (`aberta`, `em_andamento`, `aguardando`,
`concluida`, `cancelada`) e não existe estado de revisão. `aguardando` registra
dependência de terceiro e não é lido como atraso.

### F3 — Coerência entre situação e datas garantida — APROVADA
`concluido_em` acompanha `CONCLUIDA` e `cancelado_em` acompanha `CANCELADA`, nas
duas direções. Garantido por `CheckConstraint` (`demanda_concluida_registra_quando`
e `demanda_cancelada_registra_quando`) e validado no domínio por `clean()`. Não
há constraint sobre `prazo`, que é opcional e livre.

### F4 — Histórico append-only é a memória da demanda — APROVADA
`HistoricoDemanda` registra decisão, não ruído: só o que muda o entendimento da
demanda vira evento, e o ato de abrir e salvar o formulário não gera linha. O
histórico nunca é reescrito nem excluído pela interface (`has_delete_permission`
→ `False` no admin). Observação é um **evento** (`observacao`), não um campo.

### F5 — Autorização centralizada, com resposta única por regra — APROVADA
A autorização vive em `apps/demandas/permissoes.py`, decidida pelo vínculo ativo
com o condomínio, *fail-closed*. `status_permitidos_para(usuario, demanda)` é a
única resposta para "que situações esta pessoa pode dar a esta demanda agora":
a regra não se espalha pelas views em uma dúzia de `if` de papel. A pergunta
"esta pessoa acessa este condomínio?" continua em `apps/condominios/acesso.py`.

### F6 — Papéis que enxergam o módulo nesta fase — APROVADA
ADMIN e SINDICA gerenciam; SUPERVISORA cria e move a situação (sem cancelar nem
editar conteúdo alheio); OPERACIONAL vê, move a situação e conclui o que é seu,
sem cancelar. PORTEIRO e MORADOR não entram no módulo agora.

### F7 — "Vencida" é situação calculada, nunca campo — APROVADA
Vencida = `prazo < hoje` e status não encerrado; "vence hoje" e "próxima" (até 7
dias) seguem a mesma lógica. Nenhum campo persistido de vencimento — o tempo é
derivado, não gravado.

### F8 — Sem relações prematuras — APROVADA
Nada de FK ou model novo para manutenção, financeiro, fornecedor ou evidência
nesta fase. As relações (Demanda → Manutenção, → Despesa, → Fornecedor/Orçamento,
→ Evidência) ficam apenas documentadas em `docs/fase_2a_demandas.md`.

### F9 — `local_contexto` é texto nesta fase — APROVADA
O local é um texto curto (piscina, garagem, elevador...), sem model de área ou
equipamento. Poderá virar relação estruturada mais adiante, como já previsto em
B1.

### F10 — Preparação para o painel "Hoje" sem implementá-lo — APROVADA
`DemandaQuerySet` já responde `vencidas`, `vencendo_hoje`, `proximas`,
`urgentes`, `minhas` e `na_ordem_de_atencao`. As perguntas ficam no domínio, não
nas views; a tela do painel não entra nesta fase.

## Decisões pendentes de aprovação (não arquivadas aqui)

- Arquitetura de persistência dos lançamentos (relacional × JSON × híbrido).
- Categorias publicáveis por padrão.
- Experiência do morador para balanços publicados (não criar nesta fase).

Referência: `docs/fase_1a_balancos_decisoes.md`,
`docs/fase_1b_balancos_implementacao.md`,
`docs/fase_1c_entrada_balanco.md`,
`docs/fase_1d_detector_layout_balanco.md`,
`docs/fase_1e_extracao_balanco_atual.md` e
`docs/fase_2a_demandas.md`.
