# Fase 1C — Entrada Segura do Balanço: Implementação

> Registro do que foi efetivamente implementado nesta fase: a entidade
> transitória `ImportacaoBalanco`, o storage externo privado, o upload validado,
> a listagem mínima e o descarte.
>
> **Nada de leitura do documento entrou aqui**: sem parser, sem OCR, sem IA, sem
> detecção de layout, sem validação matemática, sem conferência e sem criação
> automática de `BalancoMensal`. O arquivo é recebido e fica disponível para a
> próxima etapa do caminho.

## 1. Escopo entregue

- `ImportacaoBalanco` — model transitório, com migration própria
  (`balancos/0002_importacaobalanco.py`).
- `apps/balancos/storage.py` — contrato de storage privado e adaptador Supabase
  por HTTP puro, sem SDK.
- `apps/balancos/services.py` — sequência explícita do recebimento.
- `apps/balancos/permissoes.py` — quais papéis importam balanço.
- `apps/balancos/forms.py`, `views.py`, `urls.py` — tela de entrada.
- `templates/pages/balancos/importacoes.html` — tela Sneat + Bootstrap 5.
- Item de menu **Balanços**, contextual por papel.
- Settings e `.env.example`.
- Testes: 97 casos novos na 1C, mais os de fechamento operacional (1C.1);
  176 em `apps.balancos` + `apps.core`.
- Nenhuma alteração em `BalancoMensal`, `DocumentoBalanco`, `User`,
  `Condominio`, `AcessoUsuarioCondominio`, na autorização centralizada, no
  PostgreSQL, no Sneat ou na identidade visual.

## 2. Regra fundamental

`BalancoMensal` representa **balanço confirmado**. Logo:

```
upload de arquivo      ≠  BalancoMensal
processamento          ≠  BalancoMensal
resultado preliminar   ≠  BalancoMensal
```

O que nasce no upload é uma **tentativa de importação**. A promoção
`ImportacaoBalanco → BalancoMensal` pertence a uma fase futura e **não** foi
implementada aqui.

## 3. `ImportacaoBalanco` × `BalancoMensal`

| | `ImportacaoBalanco` | `BalancoMensal` |
| --- | --- | --- |
| Representa | um arquivo recebido | o mês confirmado pela gestão |
| Existe desde | o recebimento | a confirmação da importação |
| Tem competência | não | sim (primeiro dia do mês) |
| Tem situação financeira | não | sim (`VALIDADO`/`PUBLICADO`/`SUBSTITUIDO`) |
| Tem versão | não | sim (`versao`, `substitui`) |
| É temporário | sim (expira ou é descartado) | não (histórico) |
| Aparece ao morador | nunca | apenas se publicado |

As duas listagens **não** se misturam: a tela de Balanços desta fase mostra
somente importações.

### 3.1 Campos

| Campo | Tipo | Observação |
| --- | --- | --- |
| `id` | UUID (pk) | Gerado no sistema. |
| `condominio` | FK `Condominio` PROTECT | Obrigatório. |
| `criado_por` | FK `AUTH_USER_MODEL` PROTECT | Quem recebeu o arquivo. |
| `criado_em` / `atualizado_em` | DateTimeField | Automáticos. |
| `status` | CharField(choices `StatusImportacao`, default `recebido`) | Situação do recebimento. |
| `nome_original` | CharField(255) | Nome como a pessoa o conhece, sanitizado. |
| `mime_type` | CharField(120) | Sempre `application/pdf` nesta fase. |
| `tamanho_bytes` | PositiveBigIntegerField | Tamanho conferido no backend. |
| `sha256` | CharField(64) + `RegexValidator` | Hexadecimal minúsculo. |
| `storage_provider` | CharField(50) | Ex.: `supabase`. |
| `storage_bucket` | CharField(150) | Bucket privado do ambiente. |
| `storage_key` | CharField(500) | Referência do objeto. Nunca uma URL. |
| `schema_versao` | PositiveIntegerField (default 1) | Contrato do JSON. |
| `layout_detectado` | CharField(choices `LayoutBalanco`, default `desconhecido`) | Permanece `DESCONHECIDO`. |
| `resultado_extraido` | JSONField (dict) | Começa `{}` e nada o preenche nesta fase. |
| `inconsistencias` | JSONField (list) | Começa `[]`. |
| `erro_processamento` | TextField | Texto humano e curto; nunca segredo, URL assinada ou resposta bruta. |
| `expira_em` | DateTimeField, opcional | Reservado para a retenção futura. |
| `confirmado_em` | DateTimeField, opcional | Preenchido só na fase da confirmação. |
| `descartado_em` | DateTimeField, opcional | Preenchido no descarte. |

Constraints: `importacao_tamanho_nao_negativo` e
`importacao_um_unico_desfecho` (confirmação e descarte não coexistem).
Índices: `importacao_condominio_sha_idx` e `importacao_cond_status_idx`.
**Não há unicidade** de `sha256`: a repetição é sinalizada, não impedida pelo
banco.

## 4. Estados da importação

`StatusImportacao`: `RECEBIDO`, `PROCESSANDO`, `PRONTO`, `ERRO`, `CONFIRMADO`,
`DESCARTADO`.

Nesta fase o fluxo termina em `PRONTO` — "o arquivo está disponível para a
próxima etapa do pipeline". `PROCESSANDO` descreve a leitura futura e
`CONFIRMADO` o desfecho futuro; nenhum dos dois é produzido agora. Não existe
workflow de revisão, e `EM_REVISAO` não voltou.

## 5. Storage privado

O PDF nunca fica no filesystem da aplicação e nunca é servido por URL pública.

- **Provedor**: Supabase Storage, bucket privado, por **HTTP puro** (biblioteca
  padrão, sem SDK) — uma dependência a menos e o mesmo contrato.
- **Contrato**: `StorageProvider` (`nome`, `bucket`, `enviar`, `remover`,
  `url_assinada`). O domínio não conhece o provedor concreto.
- **Persistido**: apenas `provider`, `bucket`, `object_key` e metadados.
- **Nunca persistido**: URL pública, signed URL, token, service role ou
  segredo. `clean()` recusa referência que comece com `http://` ou `https://`.
- **Falha explícita**: se o storage estiver configurado e as variáveis
  obrigatórias faltarem, o envio é recusado informando **exatamente** quais
  variáveis faltam. Não há queda silenciosa para disco local.

### 5.1 Tempo limite e repetição

- **Tempo limite**: toda conversa com o provedor passa por um único ponto
  (`_abrir`), onde o limite de 30 s é aplicado. Não existe chamada sem limite —
  a garantia é estrutural, não depende de quem escreve a chamada.
- **Sem repetição automática**: não há retry genérico. Repetir um envio sem
  garantia de idempotência poderia criar um segundo objeto do mesmo arquivo, o
  que é pior do que falhar. A única repetição admitida é a do caso sabidamente
  idempotente — o provedor informar que o objeto já existe, quando o envio é
  refeito substituindo. Qualquer outra falha é reportada.
- **Falhas tratadas**: tempo esgotado, conexão recusada, 4xx, 5xx e resposta
  ilegível viram `ErroDeStorage` com mensagem para a gestão. Quando ausente é
  tolerado: o `404` na remoção significa que o objeto já não existe.
- **Nada de segredo na mensagem**: o motivo original fica encadeado na exceção
  (para depuração) e não dentro do texto. Cabeçalho `Authorization`, `service
  role key`, `DATABASE_URL`, URL assinada completa e corpo de resposta do
  provedor não entram na mensagem nem em registro permanente.
- **Sem registro de segredo**: não existe `logging` no domínio de balanços. O
  que a pessoa vê é o estado da importação (`ERRO` com texto humano), não o
  log do provedor.

### 5.2 Bucket e ambientes

Buckets separados por ambiente; nunca o mesmo bucket em DEV e PROD. Sugestão:
`eithra-v3-balancos-dev`. DEV e PROD diferem pelo valor de
`BALANCOS_STORAGE_BUCKET` e pelas credenciais, que ficam apenas no ambiente.
O bucket é **privado**: não se torna público para facilitar teste.

### 5.3 Object key

```
balancos/<condominio_uuid>/<importacao_uuid>/documento.pdf
```

Somente identificadores: sem nome de arquivo, sem nome de condomínio em texto,
sem competência ainda não confirmada e sem dado pessoal. O identificador da
importação separa cada tentativa, então duas remessas do mesmo arquivo não se
sobrescrevem.

## 6. Configuração

| Setting | Origem | Default |
| --- | --- | --- |
| `BALANCOS_STORAGE_PROVIDER` | env | `supabase` |
| `BALANCOS_STORAGE_BUCKET` | env | *(vazio — obrigatório)* |
| `BALANCOS_MAX_UPLOAD_BYTES` | env | `52428800` (50 MB) |
| `BALANCOS_URL_ACESSO_SEGUNDOS` | env | `60` |
| `SUPABASE_URL` | env | *(vazio — obrigatório)* |
| `SUPABASE_SERVICE_ROLE_KEY` | env | *(vazio — obrigatório)* |

O `.env.example` lista apenas os nomes, sem valores.

## 7. Validação do arquivo e limite

Ordem aplicada em `validar_arquivo`, antes de qualquer gravação:

1. arquivo presente;
2. extensão `.pdf`;
3. arquivo não vazio;
4. tamanho dentro do limite configurado;
5. `Content-Type` informado compatível, quando informado;
6. **assinatura do PDF** (`%PDF-`) lida do próprio arquivo.

Extensão e MIME do navegador não são prova: o que decide é o começo do arquivo.
O limite é configurável porque balanços reais podem ter muitas páginas — 50 MB
é folgado para o caso real e continua limitando abuso.

## 8. Hash e duplicidade

- SHA-256 calculado sobre o conteúdo antes do envio, gravado em hexadecimal
  minúsculo.
- Duplicidade considerada **por `condominio` + `sha256`**, sem constraint global.
- Não contam como repetição: importações `DESCARTADO` (já recusadas pela gestão)
  e `ERRO` (o arquivo nem chegou; a pessoa vai querer tentar de novo).
- O mesmo hash em outro condomínio é outro documento.
- Ao detectar repetição: nada novo é gravado, nenhum objeto novo é enviado, a
  tela informa a repetição e aponta a importação existente.

## 9. Autorização e multi-condomínio

- Papéis que importam: **ADMIN** e **SINDICA**. `SUPERVISORA`, `OPERACIONAL`,
  `PORTEIRO` e `MORADOR` não importam. O conjunto vive em
  `apps/balancos/permissoes.py`.
- A pergunta "esta pessoa acessa este condomínio?" continua em
  `apps/condominios/acesso.py` — uma resposta só para a mesma pergunta.
- Vínculo inativo ou condomínio inativo não autoriza.
- Quem não tem nenhum condomínio elegível recebe **404**, não 403: a existência
  da tela não é revelada.
- O condomínio nunca vem do navegador: o conjunto permitido é montado a partir
  do vínculo, e a escolha é validada contra ele.
- Com mais de um condomínio elegível, a pessoa escolhe explicitamente. Com
  exatamente um, o campo continua sendo validado, mas não é pedido de novo.
  Não existe `.first()` nem suposição de condomínio único.

### 9.1 Navegação contextual

O item **Balanços** é um módulo administrativo: aparece apenas para ADMIN e
SINDICA. Quem não tem esse papel não vê o item — e, se chegar ao endereço por
conta própria, continua recebendo 404.

Isso é resolvido em um único lugar, `apps/core/context_processors.py`: cada item
do menu pode declarar `visivel_para`, e a resposta vem de
`pode_importar_em_algum_condominio` — a **mesma** função que a view usa. Não há
lista de papéis paralela na navegação, nem `{% if user... %}` espalhado pelos
templates.

O menu **não** é controle de acesso. Ele existe para não oferecer um caminho que
terminaria em 404; a autorização continua na view, testada com e sem o menu
presente. A experiência do morador para balanços publicados é de outra fase e
não foi criada aqui: esta rota é administrativa e não é dele.

## 10. Consistência entre banco e storage

Não existe transação que cubra banco e storage externo. A estratégia é
explícita, e não finge atomicidade:

1. o registro é gravado **antes** do envio — é ele que dá identidade à
   importação e define a chave do objeto;
2. falha no envio → a importação fica com `status = ERRO` e o motivo em texto
   humano. Nunca como sucesso. Nada precisa ser removido;
3. envio concluído e falha ao gravar o desfecho → o objeto é removido na
   medida do possível, para não deixar arquivo sem dono, e o erro é propagado;
4. arquivo inválido → nenhum registro é criado.

O comportamento é idempotente na repetição: reenviar o mesmo conteúdo encontra a
importação existente e não cria uma segunda.

## 11. Acesso temporário e descarte

- **Ver o arquivo**: rota autenticada, resolvida dentro dos condomínios
  autorizados, que gera um acesso curto sob demanda (`BALANCOS_URL_ACESSO_SEGUNDOS`,
  60 s por padrão) e redireciona. Nada é persistido; a próxima visita gera
  outro acesso.
- **Descartar**: `status = DESCARTADO` e `descartado_em`, com remoção
  *best-effort* do objeto. Se a remoção falhar, o descarte permanece gravado e o
  objeto pode ser limpo depois — o descarte não fica refém do storage.
  O registro **não** é apagado: fica a memória de que aquele arquivo foi
  recebido e recusado. Importação já confirmada não é descartada.
- Ações na tela: `Importar balanço` (principal), `visualizar arquivo` e
  `descartar` (ícones). Não existem validar, publicar, substituir, reprocessar
  ou editar dados — pertencem a fases futuras.

## 12. Migration

`apps/balancos/migrations/0002_importacaobalanco.py` — cria apenas
`ImportacaoBalanco`. Nenhuma migration anterior foi alterada e nenhum `--fake`
foi usado. Fluxo executado: `makemigrations` → `migrate --plan` → `migrate`.

## 13. Testes

97 casos novos na 1C, distribuídos em:

- `tests/test_importacao_models.py` — registro, valores iniciais,
  recusa de URL persistida, desfechos exclusivos, repetição permitida;
- `tests/test_importacao_services.py` — validação de arquivo, hash, metadados,
  chave sem dado sensível, duplicidade, falha de storage, acesso temporário,
  descarte;
- `tests/test_storage.py` — contrato HTTP do provedor (envio, conflito,
  remoção, acesso assinado), tempo limite em toda chamada, falhas de rede e
  ausência de credencial na mensagem, e escolha do provedor com falha explícita;
- `tests/test_importacao_views.py` — entrada, papéis autorizados e não
  autorizados, isolamento entre condomínios, listagem, acesso ao arquivo,
  descarte, ausência de segredo na resposta e a navegação contextual.

Fechamento operacional (1C.1) acrescentou regressão para: Balanços
administrativo ausente para MORADOR, PORTEIRO, OPERACIONAL e SUPERVISORA;
presente para ADMIN e SINDICA; ausente sem vínculo e com vínculo inativo;
visibilidade decidida pelo domínio e não por lista paralela; proteção da rota
inalterada com ou sem menu; tempo limite em envio, reenvio, remoção e acesso
temporário; e mensagem de erro sem credencial.

Todo o tráfego de storage nos testes passa por um provedor de mentira
(`tests/apoio.py`). Nenhum teste unitário conversa com o Supabase real.

## 13.1 Teste de falha

O cenário de falha do provedor é exercitado com provedor de mentira programado
para recusar o envio — sem tocar no bucket nem na configuração real. Verifica
que a resposta não é de sucesso, que a importação fica em `ERRO` com texto
humano, que nenhuma credencial aparece na resposta e que nenhum `BalancoMensal`
é criado.

## 14. Teste integrado DEV

**Bloqueado**: faltam `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e
`BALANCOS_STORAGE_BUCKET` no ambiente de desenvolvimento. Nenhuma credencial foi
inventada e nenhum bucket público foi criado para contornar a falta. A validação
manual contra o bucket DEV real (envio, objeto no bucket, bucket privado,
metadados persistidos, acesso público negado e acesso assinado funcionando)
deve ser feita quando as três variáveis existirem.

Checklist a executar quando o ambiente estiver completo:

| Verificação | Como |
| --- | --- |
| Upload real | Enviar um PDF sintético pela tela, autenticado como ADMIN ou SINDICA. |
| Objeto no bucket | Confirmar o objeto na chave `balancos/<condomínio>/<importação>/documento.pdf`. |
| Bucket privado | Confirmar que o bucket não é público. |
| Acesso anônimo negado | Buscar o objeto sem credencial e esperar recusa. |
| Metadata no banco | Confirmar `sha256`, `tamanho_bytes`, `storage_key` e `status`. |
| Importação coerente | `layout_detectado` desconhecido, `resultado_extraido` vazio. |
| Nenhum balanço criado | `BalancoMensal.objects.count()` inalterado. |
| Acesso temporário | Abrir pela tela e confirmar que funciona na validade e não é persistido. |

## 15. Retenção futura

A importação é transitória. Ficam projetadas, para uma fase de limpeza:

- expiração pelo campo `expira_em`;
- limpeza de importações `DESCARTADO` antigas;
- limpeza de importações `ERRO` antigas.

Nenhum job foi implementado nesta fase. Um arquivo ligado a um balanço
confirmado nunca deve ser removido por essas rotinas.

## 16. O que não entrou — e não deve entrar aqui

Parser, OCR, IA, detecção de layout, validação matemática, conferência,
confirmação da importação, criação de `BalancoMensal`, publicação e qualquer
substituição. `layout_detectado` permanece `DESCONHECIDO`, `resultado_extraido`
permanece `{}` e `inconsistencias` permanece `[]` — sem fingir processamento
inexistente.
