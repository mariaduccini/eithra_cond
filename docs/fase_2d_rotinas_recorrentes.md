# Fase 2D — Rotinas Recorrentes e Preventivas Programadas: Modelagem Proposta

> **Status**: código de domínio, apresentação e suíte de testes escritos, com
> checklists operacionais incorporados. A migration inicial
> (`apps/rotinas/migrations/0001_initial.py`) foi **regenerada** com os cinco
> models e **não foi aplicada** ao banco DEV. Nenhum processo automático foi
> iniciado. Aguardando autorização para `migrate`.
>
> **O que não entrou aqui**: scheduler, Celery, cron, notificações, WhatsApp,
> Gmail, IA, OCR, contratos, Painel "Hoje", transparência, Fase 1I, CRUD de Tipo
> de Local/Equipamento, gestão completa de Usuários/Acessos e Contratos de
> Fornecedor. Nada disso foi iniciado (§77).

## 1. Princípio que governa a fase

**"A rotina lembra. A pessoa decide."**

Uma rotina é a regra de algo que precisa voltar a acontecer — a limpeza da caixa
d'água, o teste mensal da iluminação de emergência, a revisão do gerador. Ela
não faz nada sozinha: prepara a ocorrência, mostra a próxima data e espera que
alguém assuma. Concluir uma ocorrência abre a próxima **porque a regra continua
valendo**, não porque o Eithra decidiu agir. Criar uma demanda ou uma manutenção
a partir de uma ocorrência só acontece quando a gestão pede — a sugestão aparece,
a pessoa confirma.

O que o Eithra alcança é **lembrar**. Nada dispara em segundo plano, nada é
consumido de estoque, nada é aberto sem confirmação.

## 2. Onde o código mora

**Um único app `apps/rotinas`** (verbose name *"Rotinas"*). O grafo de FKs
(`ExecucaoRotina` → `RotinaRecorrente`; a ocorrência aponta para `Demanda`,
`Manutencao`, `Condominio`) fica dentro do app, sem dependência circular e com
uma migration inicial coerente.

```
apps/rotinas/
  constants.py      # TipoRotina, Periodicidade, DiaSemana, StatusExecucao, ResultadoExecucao, StatusItemChecklist
  recorrencia.py    # o cálculo puro da próxima data (não conhece o modelo)
  models.py         # ModeloChecklist, ItemModeloChecklist, RotinaRecorrente, ExecucaoRotina, ItemExecucaoChecklist + QuerySets
  services.py       # criar/editar rotina, gerar/iniciar/concluir/ignorar/cancelar, tratar item do checklist, integrações
  permissoes.py     # a autorização, centralizada
  forms.py          # rotina, filtros, movimento de execução, tratamento de item, consequências
  views.py  urls.py  admin.py
  migrations/       # 0001_initial.py — gerada com os cinco models, ainda não aplicada
  tests/
templates/pages/rotinas/
  listagem.html  detalhe.html  execucoes.html  execucao_detalhe.html  acoes.html  acoes_execucao.html
```

A presença do item **"Rotinas"** no menu é decidida por
`pode_ver_alguma_rotina` em [context_processors.py](file:///d:/github/eithra_v3/apps/core/context_processors.py),
com a mesma regra que o backend usa. O menu não autoriza — a view decide.

## 3. Enums

Ficam em [constants.py](file:///d:/github/eithra_v3/apps/rotinas/constants.py). O
vocabulário só diz respeito a este domínio. A **prioridade** não é recriada: é
importada de `apps.core.constants.Prioridade`, a mesma usada por demandas e
manutenções — não há um terceiro degrau de urgência.

### TipoRotina
`operacional` · `inspecao` · `limpeza` · `preventiva` · `administrativa` ·
`seguranca` · `outra`

### Periodicidade
`diaria` · `semanal` · `mensal` · `anual` · `intervalo_dias`

`DIARIA` e `INTERVALO_DIAS` fazem a mesma conta (somar dias); a diferença é só a
leitura: "todo dia" com intervalo 1, "a cada N dias" com outro número. Manter as
duas evita um caminho de cálculo paralelo para dizer a mesma coisa.

### DiaSemana (`IntegerChoices`)
`segunda=0` … `domingo=6` — o número do calendário, não o nome traduzido, para o
cálculo não depender de idioma.

### StatusExecucao
`pendente` · `em_andamento` · `concluida` · `ignorada` · `cancelada`

- `SITUACOES_PENDENTES` = {PENDENTE, EM_ANDAMENTO}
- `SITUACOES_ENCERRADAS` = {CONCLUIDA, IGNORADA, CANCELADA}

### ResultadoExecucao
`ok` · `atencao` · `problema` · `nao_aplicavel`

- `RESULTADOS_QUE_PEDEM_ATENCAO` = {ATENCAO, PROBLEMA} — quando faz sentido
  oferecer a abertura de uma demanda.

### StatusItemChecklist
`pendente` · `concluido` · `nao_aplicavel`

Três estados bastam: a tarefa ainda não foi tratada, foi feita, ou não se
aplicava naquela ocorrência. Não há estado intermediário. O checklist **não
decide o resultado da ocorrência**.

- `STATUS_QUE_TRATAM_O_ITEM` = {CONCLUIDO, NAO_APLICAVEL} — uma tarefa
  obrigatória ainda pendente **segura a conclusão**; as demais, não.

## 4. `RotinaRecorrente`

A regra de uma atividade que se repete, com o ritmo e o contexto dela.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | `default=uuid.uuid4, editable=False` |
| `condominio` | FK `Condominio` PROTECT | `related_name="rotinas"` |
| `titulo` | CharField(200) | normalizado (`save`) |
| `descricao` | TextField blank | parágrafos preservados |
| `tipo` | CharField choices | default `operacional` |
| `periodicidade_tipo` | CharField choices | default `mensal` |
| `intervalo` | PositiveIntegerField | default 1 |
| `dia_semana` | PositiveSmallIntegerField null | choices `DiaSemana` |
| `dia_mes` | PositiveSmallIntegerField null | 1–31 |
| `mes_ano` | PositiveSmallIntegerField null | 1–12 |
| `data_inicio` | DateField | nenhuma ocorrência nasce antes |
| `data_fim` | DateField null/blank | sem fim, ela segue |
| `proxima_execucao` | DateField null/blank | escrita **apenas pelo domínio** |
| `responsavel` | FK User SET_NULL null/blank | vira o responsável de cada ocorrência |
| `local` | FK `LocalOperacional` PROTECT null/blank | |
| `equipamento` | FK `Equipamento` PROTECT null/blank | |
| `fornecedor` | FK `Fornecedor` PROTECT null/blank | |
| `prioridade` | CharField choices | de `apps.core.constants` |
| `gera_manutencao` | BooleanField | sugere, nunca cria sozinho |
| `ativo` | BooleanField | desativar para de gerar, sem apagar |
| `criado_por` | FK User PROTECT | rastreabilidade |
| `criado_em` / `atualizado_em` | DateTimeField | |

`objects = RotinaRecorrenteQuerySet.as_manager()`.

**Regras de coerência** (`clean()`): título obrigatório; `intervalo ≥ 1`;
`dia_mes` 1–31; `mes_ano` 1–12; `dia_semana` 0–6; `data_fim ≥ data_inicio`; local,
equipamento e fornecedor pertencem ao **mesmo condomínio** da rotina; quando o
equipamento tem local definido e a rotina escolheu outro, a gravação é recusada
(divergência bloqueia — nunca corrige em silêncio).

`proxima_execucao` é persistida **para tornar a operação leve**, mas não é campo
de digitação: quem escreve nela é `services.py`, e **toda escrita passa por uma
função só** — `_recalcular_proxima_execucao(rotina)`.

O sentido do campo é **um só**: a próxima ocorrência operacional a ser tratada.

1. Se existe ocorrência **em aberto** (PENDENTE ou EM_ANDAMENTO), o campo aponta
   para a **mais antiga** delas. Preparar uma ocorrência adiantada **não** move o
   campo: quem espera alguém continua sendo a mais antiga.
2. Sem nada em aberto, o campo recebe a **próxima data válida da regra**.
3. Sem o que esperar — rotina **inativa**, **fim de vigência** alcançado ou
   nenhuma data futura válida —, o campo fica **vazio**.

O valor depende do **estado da rotina**, nunca de qual operação pediu a conta.
Por isso `proxima_execucao` é somente leitura no Django Admin: não há caminho de
escrita à mão.

## 5. Periodicidade e cálculo da próxima data

A conta vive em [recorrencia.py](file:///d:/github/eithra_v3/apps/rotinas/recorrencia.py),
em **uma função só**: `calcular_proxima_data(...)`. A mesma resposta vale para a
tela, para o serviço e para o teste. O módulo **não conhece o modelo** — recebe a
regra em palavras simples e devolve a data (ou `None`).

Regras de borda tratadas explicitamente:

- **Dia 31 em mês curto** — a ocorrência cai no **último dia** do mês (`min(dia,
  último_dia_do_mês)`): 31 em fevereiro vira 28 ou 29.
- **29 de fevereiro em ano não bissexto** — a ocorrência anual cai em 28/02.
- `a_partir_de` é **sempre exclusivo**: a data devolvida é estritamente posterior
  à referência. Sem referência, a conta começa na `data_inicio`.
- Devolve `None` quando a ocorrência cairia **depois da `data_fim`** — a rotina
  não tem mais o que gerar.

Os intervalos (`intervalo = N`) valem para semanal (N semanas), mensal (N meses)
e anual (N anos). Aritmética de mês/ano é feita em `_somar_meses`, sem depender
de `relativedelta`.

## 6. `ExecucaoRotina`

A ocorrência concreta da regra: a data em que caiu, quem respondeu por ela
naquele momento e o que se encontrou.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `rotina` | FK `RotinaRecorrente` CASCADE | `related_name="execucoes"` |
| `condominio` | FK `Condominio` PROTECT | `related_name="execucoes_de_rotina"` |
| `data_prevista` | DateField | o dia em que a ocorrência cai |
| `status` | CharField choices | default `pendente` |
| `responsavel` | FK User SET_NULL null/blank | **copiado da rotina** quando gerada |
| `iniciado_em` | DateTimeField null/blank | |
| `concluido_em` | DateTimeField null/blank | |
| `observacao_execucao` | TextField blank | obrigatório ao ignorar/cancelar |
| `resultado` | CharField choices blank | **obrigatório** para concluir |
| `demanda_gerada` | FK `Demanda` PROTECT null/blank | vínculo único |
| `manutencao_gerada` | FK `Manutencao` PROTECT null/blank | vínculo único |
| `criado_em` / `atualizado_em` | DateTimeField | |

`objects = ExecucaoRotinaQuerySet.as_manager()`.

**A ocorrência preserva o responsável que tinha no momento em que foi preparada.**
Mudar o responsável da rotina depois **não reescreve o passado.**

Não existe histórico separado: **a lista de execuções já é o histórico.**

## 7. Ciclo de vida da ocorrência

Desfechos poucos, e nenhum apaga o que houve:

| Situação | Como se chega | O que registra |
| --- | --- | --- |
| `PENDENTE` | a ocorrência nasce assim | — |
| `EM_ANDAMENTO` | `iniciar_execucao` | `iniciado_em` (gravado uma vez, nunca sobrescrito) |
| `CONCLUIDA` | `concluir_execucao` | `resultado` + `concluido_em`; abre a próxima ocorrência |
| `IGNORADA` | `ignorar_execucao` | justificativa; a rotina **segue em frente** |
| `CANCELADA` | `cancelar_execucao` | motivo; **não cancela a rotina** |

- Começar só acontece uma vez: voltar a `iniciar_execucao` em uma ocorrência já
  iniciada é recusado pelo domínio (`ErroDeRotina`), não pelo banco.
- Uma ocorrência **encerrada** não muda mais de situação (`situacoes_permitidas_para`
  devolve o conjunto vazio).
- Ignorar e cancelar **exigem justificativa** — dizer "não se aplicava" sem dizer
  por quê não ajuda quem lê o histórico depois.
- Concluir, ignorar e cancelar **reassentam a próxima ocorrência**: a regra
  continua valendo.

## 8. Atraso — derivado, nunca persistido

`esta_atrasada` é **propriedade calculada**: pendente (PENDENTE ou EM_ANDAMENTO)
com `data_prevista` anterior a hoje. Não existe campo de "atrasado", de "vencido"
nem "situação de prazo" no banco. O mesmo vale para a rotina: sua regra de atraso
olha apenas `proxima_execucao` diante de hoje — e esse campo já aponta para a
ocorrência em aberto mais antiga (§4), de modo que a rotina está atrasada
exatamente quando a ocorrência que espera alguém venceu.

## 9. Autorização

Centralizada em [permissoes.py](file:///d:/github/eithra_v3/apps/rotinas/permissoes.py).
A resposta vem sempre do **vínculo ativo** da pessoa com o condomínio, nunca do
menu, do formulário ou de um identificador trazido pelo navegador. **404 quando
não enxerga o módulo; 403 quando enxerga mas não decide.** Sem certeza, a
resposta é **negar**.

Conjuntos de papéis:

- `PAPEIS_QUE_GERENCIAM` = {ADMIN, SINDICA}
- `PAPEIS_QUE_CRIAM` = GERENCIAM ∪ {SUPERVISORA}
- `PAPEIS_QUE_OPERAM` = {OPERACIONAL}
- `PAPEIS_QUE_VEEM` = CRIAM ∪ OPERAM

| Decisão | ADMIN | SINDICA | SUPERVISORA | OPERACIONAL | PORTEIRO | MORADOR |
| --- | --- | --- | --- | --- | --- | --- |
| Ver o módulo | sim | sim | sim | sim (só suas ocorrências) | não | não |
| Criar rotina | sim | sim | sim | não | não | não |
| Ver o cadastro da rotina | sim | sim | sim | não | não | não |
| Editar a regra | sim | sim | sim | não | não | não |
| Gerar próxima ocorrência | sim | sim | sim | não | não | não |
| Ativar / inativar a rotina | sim | sim | **não** | não | não | não |
| Ver ocorrência | todas | todas | todas | **só se responsável** | não | não |
| Iniciar / concluir / ignorar | sim | sim | sim | **só se responsável** | não | não |
| Cancelar ocorrência | sim | sim | **não** | **não** | não | não |
| Abrir Demanda / Manutenção | sim | sim | sim | não | não | não |

O que muda por papel, além do condomínio:

- **Supervisora** opera o dia a dia (cria, edita, gera, inicia, conclui, ignora),
  mas **não** desativa rotina estrutural nem cancela ocorrência — as duas
  decisões seguem sendo de quem responde pelo condomínio.
- **Operação** não administra rotina nenhuma e não enxerga o cadastro da regra.
  O que ela alcança são as **ocorrências atribuídas a ela, e só elas**: iniciar,
  concluir e registrar o que encontrou.
- **Portaria e morador** não entram nesta fase.

`usuarios_elegiveis_como_responsavel(condominio)` só admite quem tem vínculo
ativo, `is_active=True` e papel em `PAPEIS_QUE_VEEM` — o responsável precisa
poder acompanhar o que assume.

## 10. Geração de execução, idempotência e concorrência

`gerar_proxima_execucao(rotina)`:

1. Trava a rotina com `select_for_update()`.
2. Se a rotina não está ativa, **não prepara nada**.
3. Calcula a próxima data **depois da última já criada**.
4. `get_or_create` por `(rotina, data_prevista)` — **a mesma data nunca gera duas
   ocorrências**.
5. Recalcula `proxima_execucao` pela regra do campo (§4). A ocorrência preparada
   **não é, por si, a próxima a tratar**: quem decide isso é o que ficou em
   aberto.

A geração é **incremental, uma ocorrência por vez**: não há catch-up massivo de
ocorrências atrasadas, nem geração automática em lote.

**Idempotência:** chamar a geração duas vezes para a mesma rotina não duplica nada
— o `get_or_create` e a constraint `rotina_execucao_unica` são a garantia.

**Concorrência:** o `select_for_update` serializa duas requisições simultâneas que
tentem gerar a mesma ocorrência; a constraint de unicidade é a última barreira,
caso o travamento seja contornado. Tudo dentro de `transaction.atomic`.

`criar_rotina` e `editar_rotina`: a primeira já prepara a primeira ocorrência
(a rotina não fica só na intenção); a segunda reassenta apenas a próxima ainda
por preparar — **uma ocorrência já em aberto não é movida nem descartada pela
edição**.

## 11. Integração com Demanda

`abrir_demanda_da_execucao(...)` — nada é automático: quem encontrou o problema
descreve, e a demanda nasce com **procedência registrada** (`OrigemDemanda.SISTEMA`)
e título padrão `f"Rotina: {titulo}"`. O vínculo `demanda_gerada` é **único**: uma
ocorrência já ligada a uma demanda não gera outra. A view oferece a ação quando o
resultado pede atenção; o registro só acontece na confirmação da gestão.

## 12. Integração com Manutenção

`criar_manutencao_da_execucao(...)` — cria uma manutenção **preventiva**
(`TipoManutencao.PREVENTIVA`), herdando o contexto da rotina: local, equipamento,
fornecedor, responsável e prioridade; título padrão `f"Preventiva: {titulo}"` e
`data_prevista` da ocorrência. O vínculo `manutencao_gerada` também é **único**.
**O Eithra prepara; a pessoa confirma.**

## 13. Por que não consome estoque diretamente

Uma rotina descreve **quando** algo deve acontecer; ela não sabe **quanto** de
material a atividade vai usar, nem se o material existe. Consumir estoque a partir
de uma rotina seria transformar uma intenção em saída de estoque sem que ninguém
tenha confirmado a retirada real — e o saldo deixaria de refletir a operação.

Por isso, nesta fase, a rotina **não movimenta estoque nem cria compras**. Se uma
rotina leva a uma manutenção preventiva, e essa manutenção retira material, a
retirada acontece pelo fluxo próprio de Estoque, com o vínculo de procedência à
manutenção — exatamente como já vale hoje para qualquer manutenção. Isso mantém a
regra da Fase 2C intacta: *"o saldo não é digitado; o saldo é consequência das
movimentações."*

## 14. Índices (§72)

**`RotinaRecorrente`**

- `condominio + ativo` → `rotina_cond_ativo_idx`
- `condominio + proxima_execucao` → `rotina_cond_proxima_idx`
- `responsavel + ativo` → `rotina_resp_ativo_idx`
- `equipamento + ativo` → `rotina_equip_ativo_idx`

**`ExecucaoRotina`**

- `condominio + data_prevista` → `execucao_cond_data_idx`
- `rotina + data_prevista` → `execucao_rotina_data_idx`
- `responsavel + status` → `execucao_resp_status_idx`
- `condominio + status` → `execucao_cond_status_idx`

Nenhum índice redundante: as colunas escolhidas são as que a lista, os recortes
de ocorrências e o futuro Painel "Hoje" consultam.

## 15. Constraints (§73)

**`RotinaRecorrente`**

- `rotina_intervalo_positivo` — `intervalo > 0`.
- `rotina_dia_mes_valido` — `dia_mes IS NULL OR 1 ≤ dia_mes ≤ 31`.
- `rotina_mes_ano_valido` — `mes_ano IS NULL OR 1 ≤ mes_ano ≤ 12`.
- `data_fim ≥ data_inicio` — validado em `clean()` (não como constraint de banco,
  para permitir evolução progressiva do registro, como nas demais fases).

**`ExecucaoRotina`**

- `rotina_execucao_unica` — `UniqueConstraint(rotina, data_prevista)`. É a última
  barreira contra duas requisições gerando a mesma ocorrência.
- `execucao_concluida_registra_quando` — `CONCLUIDA ↔ concluido_em` (concluída
  registra quando; nenhuma outra registra `concluido_em`).
- `execucao_em_andamento_tem_inicio` — `EM_ANDAMENTO ⇒ iniciado_em` (o caso
  simples).

A prioridade válida é garantida pelas `choices`, não por constraint adicional.

## 16. Transações

Toda operação de domínio é `@transaction.atomic`: ou a ocorrência e a rotina
mudam juntas, ou nada muda. Em particular, `concluir`/`ignorar`/`cancelar`
gravam o desfecho **e** preparam a próxima ocorrência na mesma transação;
`abrir_demanda_da_execucao` e `criar_manutencao_da_execucao` criam a consequência
**e** registram o vínculo na ocorrência na mesma transação.

## 17. Impacto em módulos existentes

- **Sem migração em outros apps.** Nenhum model de `demandas`, `manutencoes`,
  `estoque` ou `cadastros` foi alterado; nenhuma FK nova foi adicionada a eles.
- **Demandas / Manutenções**: apenas **consumidos** por `services.py` via
  `criar_demanda` e `criar_manutencao` — a mesma interface que os módulos já
  expõem. Nenhuma regra de negócio desses apps foi tocada.
- **Estoque / Compras**: **não há integração nesta fase** (§13).
- **Menu** (`apps/core/context_processors.py`): item "Rotinas" inserido após
  "Manutenções", visível por `pode_ver_alguma_rotina`.
- **Condomínios**: `permissoes.py` reutiliza `condominios_do_usuario` e
  `AcessoUsuarioCondominio`; nada foi alterado lá.

## 18. Menu e telas

Ordem do menu: Painel, Demandas, Manutenções, **Rotinas**, Estoque, Compras,
Cadastros, Balanços.

Telas (Sneat/Bootstrap 5, mobile-first):

- **Listagem** (`listagem.html`) — tabela no desktop (`d-none d-lg-block`) e
  **cards** no mobile (`d-lg-none`); filtros compactos (tipo, vencidas, próximas,
  inativas, local, equipamento) e busca por título/descrição; linha clicável.
- **Detalhe** (`detalhe.html`) — a regra e o histórico de ocorrências.
- **Execuções** (`execucoes.html`) — a lista de ocorrências, com os recortes
  `atrasadas`, `proximas` e `hoje`; tabela no desktop e cards no mobile.
- **Ações** (`acoes.html` / `acoes_execucao.html`) — parciais dos movimentos
  disponíveis, montadas por `_acoes_da_rotina` / `_acoes_da_execucao`.

Hierarquia de ações conforme o padrão do projeto: 1 ação = ícone com tooltip;
2 ações = dois ícones; 3+ ações = menu de três pontos; ação crítica (cancelar) =
botão textual explícito com confirmação.

## 19. Querysets e Painel "Hoje" (§76)

**Não implementado.** Mas os QuerySets já respondem às perguntas que o Painel
"Hoje" (Fase 2F) vai fazer — as execuções **atrasadas**, **de hoje** e
**próximas**, nesta ordem:

- `ExecucaoRotinaQuerySet.atrasadas()`, `.de_hoje()`, `.proximas()` e
  `.na_ordem_de_atencao()`.
- `RotinaRecorrenteQuerySet.vencidas()` e `.proximas()`.

Nada monta o painel agora; a base de consulta está pronta e testada.

## 20. Testes previstos (§§64–§70)

Suíte escrita em `apps/rotinas/tests/`:

- `test_recorrencia.py` (§64) — cálculo de data, incluindo bordas (dia 31, 29 de
  fevereiro, fim de vigência).
- `test_models.py` (§65) — rotina e execução, incluindo unicidade via
  `IntegrityError` + `transaction.atomic`.
- `test_services.py` (§§66–§68) — criação, edição, geração, movimentos,
  concorrência (via `mock.patch` sobre `_calcular_proxima`) e integrações.
- `test_permissoes.py` (§69) — ADMIN, SINDICA, SUPERVISORA, OPERACIONAL
  responsável e não responsável, PORTEIRO, MORADOR, outro condomínio.
- `test_views.py` (§70) — listagem, detalhe, filtros, busca, próximas, atrasadas,
  minhas, criação, edição, gerar execução, iniciar, concluir, ignorar, cancelar,
  criar Demanda, criar Manutenção, menu e mobile.
- `test_checklists.py` (§15 da spec) — os cinco models, o snapshot, o bloqueio da
  conclusão, o progresso derivado, o recorte `com_checklist_pendente` e a
  autorização por perfil (ver §23.12).

A suíte de `apps.rotinas` soma **190 testes** (os 148 do domínio aprovado mais os
42 do checklist).

## 21. Automação futura (§75) — documentar apenas

Futuro, **não implementar agora**:

```
scheduler
  → identifica rotina vencendo
  → gera execução
  → notifica responsável
```

O domínio já está preparado para isso sem reescrita: a geração é idempotente e
concorrente-segura (`select_for_update` + `get_or_create` + constraint de
unicidade), e os QuerySets de atraso/próximas já existem. O que falta é apenas o
gatilho externo — que **não** será ligado nesta fase.

## 22. Não iniciar (§77)

Não iniciar: scheduler; Celery; cron; notificações; WhatsApp; Gmail; IA; OCR;
contratos; Painel "Hoje" (§76); transparência; Fase 1I.

## 23. Checklists operacionais

Nem toda atividade se resume a "feito". A limpeza diária de uma área tem piso,
mesas, lixeiras, banheiro, insumos e a verificação de anormalidades — e a gestão
precisa saber o que estava previsto, o que foi feito, o que ficou pendente, quem
fez, quando, com que observação e em que percentual. O checklist é isso, e nada
mais: uma lista que a ocorrência carrega, **opcional** por natureza.

### 23.1 `ModeloChecklist` — o conjunto de tarefas

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | `default=uuid.uuid4, editable=False` |
| `condominio` | FK `Condominio` PROTECT | `related_name="checklists_modelo"` |
| `nome` | CharField(200) | normalizado (`save`) |
| `descricao` | TextField blank | parágrafos preservados |
| `ativo` | BooleanField | desativar para de oferecê-lo, sem apagar |
| `criado_por` | FK User PROTECT | `related_name="checklists_criados"` |
| `criado_em` / `atualizado_em` | DateTimeField | |

`objects = ModeloChecklistQuerySet.as_manager()`, com `.do_condominio()` (conjunto
vazio sem condomínio resolvido) e `.ativos()`. `clean()` recusa um checklist sem
nome. **Não há menu principal próprio**: o checklist é gerido dentro de Rotinas,
por ação contextual.

### 23.2 `ItemModeloChecklist` — a tarefa, na ordem

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `checklist` | FK `ModeloChecklist` CASCADE | `related_name="itens"` |
| `descricao` | CharField(200) | normalizada (`save`); `clean()` recusa vazia |
| `ordem` | PositiveIntegerField | default 0 |
| `obrigatorio` | BooleanField | default **True** |
| `ativo` | BooleanField | default True — só os ativos são copiados |
| `criado_em` / `atualizado_em` | DateTimeField | |

`ordering = ["checklist", "ordem", "criado_em"]`; índice `item_checklist_ativo_idx`
em `(checklist, ativo)`. A descrição é **por tarefa** — nunca um campo único
descrevendo o checklist inteiro.

### 23.3 `RotinaRecorrente.checklist_modelo`

FK `ModeloChecklist`, `on_delete=PROTECT`, `null=True`, `blank=True`,
`related_name="rotinas"`. `clean()` recusa checklist de outro condomínio.
**Rotina sem checklist continua válida** — nem toda atividade precisa de lista.

### 23.4 `ItemExecucaoChecklist` — o retrato da ocorrência

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `execucao` | FK `ExecucaoRotina` CASCADE | `related_name="itens"` |
| `item_modelo` | FK `ItemModeloChecklist` SET_NULL null/blank | apenas rastro |
| `descricao` | CharField(200) | **retrato** |
| `ordem` | PositiveIntegerField | **retrato** |
| `obrigatorio` | BooleanField | **retrato** |
| `status` | CharField choices | default `pendente` |
| `observacao` | TextField blank | |
| `concluido_por` | FK User SET_NULL null/blank | quem tratou |
| `concluido_em` | DateTimeField null/blank | quando tratou |
| `criado_em` / `atualizado_em` | DateTimeField | |

`ordering = ["execucao", "ordem", "criado_em"]`; índice `item_exec_status_idx` em
`(execucao, status)`; constraint `item_execucao_tratado_registra_quando` —
pendente **não** registra `concluido_em`; tratada (concluída ou não aplicável)
registra.

### 23.5 Snapshot — a cópia que preserva o histórico

`_copiar_checklist(rotina, execucao)` roda **dentro de `gerar_proxima_execucao`**,
na mesma transação: copia por `bulk_create` os itens **ativos** do modelo, com
descrição, ordem e obrigatoriedade como estavam naquele dia. A ocorrência **não
consulta o modelo depois**. Mudar, acrescentar ou apagar tarefas do modelo não
reescreve uma ocorrência já criada — inclusive se o `item_modelo` for apagado,
porque `SET_NULL` deixa o retrato de pé. Rotina sem checklist não copia nada.

### 23.6 Conclusão com checklist

`concluir_execucao` recusa enquanto houver tarefa **obrigatória** `PENDENTE`: cada
obrigatória precisa estar `CONCLUIDO` ou `NAO_APLICAVEL`. Tarefa **opcional**
pendente **não** segura a conclusão — fica registrada como o que não foi feito. O
checklist **não decide o resultado**: todos os itens podem estar concluídos e a
pessoa ainda assim registrar `ATENCAO` ou `PROBLEMA`. O `ResultadoExecucao`
continua sendo a palavra final da ocorrência.

### 23.7 Progresso derivado

`ExecucaoRotina.progresso_do_checklist` calcula na hora — **nunca persistido** —
`total`, `concluidos`, `pendentes`, `nao_aplicaveis` e `percentual`
(tratados = concluídos + não aplicáveis, sobre o total). Sem checklist, tudo
zerado. A consulta usa `prefetch_related("itens")`, e as propriedades
`tem_checklist` e `tem_item_obrigatorio_pendente` leem `self.itens.all()`,
evitando N+1.

### 23.8 Tratamento da tarefa

`tratar_item_checklist(item, usuario, status=None, observacao="")`: com `status`
em `{CONCLUIDO, NAO_APLICAVEL}` grava quem tratou e quando (`concluido_em`); só
com `observacao`, a tarefa continua **pendente** — a nota não a dá por tratada nem
libera a conclusão. Ocorrência encerrada não aceita tratamento (`ErroDeRotina`).
`pode_tratar_item(usuario, execucao)` é a autorização: quem pode movimentar a
ocorrência aberta pode tratar as tarefas; encerrada, ninguém.

### 23.9 Autorização do checklist

**ADMIN/SÍNDICA/SUPERVISORA** veem o checklist previsto, o progresso, as
pendências, o executor, os horários e o resultado. O **OPERACIONAL** responsável
executa as tarefas da **própria ocorrência** e não alcança o cadastro do modelo
(as views de cadastro devolvem 404 para ele). Quem não enxerga a ocorrência
também não trata os itens dela.

### 23.10 Alertas e recortes — derivados, sem menu

**Não há menu "Alertas" nem cadastro de alertas** nesta fase. O que existe são
consultas derivadas, prontas para o Painel "Hoje" (Fase 2F):

- `ExecucaoRotinaQuerySet.atrasadas()`, `.de_hoje()`, `.proximas()`,
  `.com_checklist_pendente()` — pendentes com checklist e ao menos uma tarefa por
  tratar (obrigatória ou opcional).
- `RotinaRecorrenteQuerySet.vencidas()`, `.proximas()`, `.preventivas_proximas()`,
  `.sem_responsavel()`.

Na lista de execuções, o recorte **"Checklist pendente"** aparece ao lado de
`atrasadas`, `hoje` e `proximas` — sem calendário complexo.

### 23.11 Telas do checklist

Na `execucao_detalhe.html`, o checklist aparece como lista de tarefas com ações
objetivas: concluir a tarefa, marcar como não aplicável e anotar quando for o
caso — sem formulário gigante. A lista de execuções marca a ocorrência que tem
checklist e mostra o progresso derivado.

### 23.12 Testes do checklist

`apps/rotinas/tests/test_checklists.py` cobre: criação do modelo e dos itens
ordenados; recusa de nome/tarefa vazios; `.do_condominio()` sem vazamento;
`.ativos()` fora do desativado; vínculo rotina → checklist e a rotina sem
checklist; cross-condomínio recusado no modelo **e** no serviço; snapshot na
geração; alteração posterior no modelo **não** muda a ocorrência antiga; tarefa
nova não aparece na ocorrência antiga; apagar o `item_modelo` não apaga o retrato;
concluir item (quem/quando); não aplicável; só-anotação mantém pendente;
obrigatória pendente **bloqueia** a conclusão; opcional pendente **não** bloqueia;
checklist não decide resultado; progresso derivado; recorte
`com_checklist_pendente`; e a autorização por perfil (operacional responsável,
operacional não responsável 404, operacional não abre cadastro 404, supervisora,
morador 404, ocorrência encerrada 403).

## 24. Migrations (§71)

A fase exige **models novos**. A migration inicial
(`apps/rotinas/migrations/0001_initial.py`) foi **regenerada** para refletir todo
o domínio 2D aprovado — os cinco models (`RotinaRecorrente`, `ExecucaoRotina`,
`ModeloChecklist`, `ItemModeloChecklist`, `ItemExecucaoChecklist`), com os
respectivos índices e constraints. Não foi criada uma `0002` artificial: como a
`0001` nunca foi aplicada, ela é a migration inicial final.

`python manage.py makemigrations --check --dry-run` → **No changes detected**.
`python manage.py check` → sem problemas.

**A migration NÃO foi aplicada. PARE antes de `migrate`. Aguardar autorização.**

## 25. Entrega pré-migration (§78)

Antes de qualquer migration, entregue:

1. model `RotinaRecorrente` (§4).
2. model `ExecucaoRotina` (§6).
3. model `ModeloChecklist` (§23.1).
4. model `ItemModeloChecklist` (§23.2).
5. model `ItemExecucaoChecklist` (§23.4).
6. enums (§3) — `TipoRotina`, `Periodicidade`, `DiaSemana`, `StatusExecucao`,
   `ResultadoExecucao`, `StatusItemChecklist`.
7. periodicidades (§5) — diária, semanal, mensal, anual, intervalo de dias.
8. cálculo da próxima data (§5) — `recorrencia.py`, com as bordas tratadas.
9. snapshot do checklist (§23.5) — `_copiar_checklist`, retrato por ocorrência.
10. conclusão com checklist (§23.6) — obrigatória pendente bloqueia; opcional não.
11. autorização (§§9 e 23.9) — `permissoes.py`, com `pode_tratar_item`.
12. progresso derivado (§23.7) — `progresso_do_checklist`, nunca persistido.
13. alertas derivados (§23.10) — os recortes do checklist e do atraso, sem menu.
14. geração de execução (§10) — `gerar_proxima_execucao`.
15. idempotência (§10) — `get_or_create` + `rotina_execucao_unica`.
16. concorrência (§10) — `select_for_update` dentro de `transaction.atomic`.
17. conclusão (§7) — `concluir_execucao` exige resultado e reassenta a próxima.
18. ignorar / cancelar (§7) — exigem justificativa; a rotina segue.
19. criação de Demanda (§11) — `abrir_demanda_da_execucao`, vínculo único.
20. criação de Manutenção (§12) — `criar_manutencao_da_execucao`, vínculo único.
21. integração com Estoque (§13) — **não há** nesta fase, por decisão.
22. integração com Compras (§13) — **não há** nesta fase, por decisão.
23. impacto nos módulos existentes (§17) — nenhum model alterado; apenas consumo
    de `criar_demanda`/`criar_manutencao`; item de menu novo.
24. migration inicial regenerada (§24) — cinco models, não aplicada.
25. confirmação de que scheduler/notificações **não foram iniciados** (§§21–22).

**PARE.**

**Aguarde autorização.**

---

### Confirmação final

A migration inicial foi regenerada com os cinco models e **não foi aplicada**.
`check` e `makemigrations --check` passam sem pendências. Nenhum processo
automático foi iniciado — aguardando autorização explícita para `migrate`.
