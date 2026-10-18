# Fase 2A — Núcleo de Demandas Operacionais: Implementação

> Registro do que foi efetivamente implementado nesta fase: o primeiro domínio
> operacional da V3. A demanda é uma unidade de trabalho ou decisão — algo com
> consequência operacional que precisa de acompanhamento.
>
> **O que não entrou aqui**: manutenção, preventiva, fornecedor, contrato,
> rotina, painel "Hoje", evidências/fotos, transparência, IA, WhatsApp, Gmail,
> notificações e automações. Nenhuma integração é ingerida automaticamente: a
> criação é sempre manual nesta fase.
>
> **Migration**: `demandas/0001_initial.py` foi gerada e **não aplicada** ao
> banco. A aplicação depende de autorização separada.

## 1. Escopo entregue

- `apps/demandas/models.py` — `Demanda`, `HistoricoDemanda`, `DemandaQuerySet`.
- `apps/demandas/constants.py` — os enums do domínio.
- `apps/demandas/services.py` — criação, edição, mudança de situação e
  observação, cada uma em `transaction.atomic`.
- `apps/demandas/permissoes.py` — a autorização, centralizada.
- `apps/demandas/forms.py`, `views.py`, `urls.py`, `admin.py`.
- `templates/pages/demandas/listagem.html` e `detalhe.html` — telas Sneat +
  Bootstrap 5 + Boxicons.
- Item de menu **Demandas**, contextual por papel, entre Painel e Balanços.
- Testes: 85 casos novos em `apps.demandas` (models, permissões, serviços e
  telas).
- Nenhuma alteração em `User`, `Condominio`, `AcessoUsuarioCondominio`, na
  autorização centralizada, no PostgreSQL, no Sneat ou na identidade visual.

## 2. Conceito

Demanda é "algo com consequência operacional que precisa de acompanhamento".
Não é conversa, comentário, informação sem ação, feed nem mensagem social — por
isso não existe thread, curtida, menção nem anexo social.

Uma demanda representa uma unidade clara de trabalho ou decisão. O princípio que
a governa é o do Eithra inteiro: *o usuário informa o necessário; o Eithra
organiza responsabilidade, prazo, contexto e consequência.*

## 3. Situação

Cinco situações bastam para acompanhar trabalho de verdade. Não há revisão, fila
nem etapas de atendimento.

| Valor | Rótulo | Significado |
| --- | --- | --- |
| `aberta` | Aberta | Registrada, ainda sem andamento. Situação inicial. |
| `em_andamento` | Em andamento | Alguém está agindo sobre ela. |
| `aguardando` | Aguardando | Depende de terceiro ou de decisão — não é atraso. |
| `concluida` | Concluída | Resolvida. Grava `concluido_em`. |
| `cancelada` | Cancelada | Não será resolvida. Grava `cancelado_em`. |

`CONCLUIDA` e `CANCELADA` encerram a demanda: depois delas nada mais depende de
uma ação. A situação diz o que aconteceu; `concluido_em` e `cancelado_em` dizem
quando, e as duas coisas nunca se contradizem (ver §7).

## 4. Prioridade

Quatro degraus, com `NORMAL` como padrão — sem matriz de impacto e urgência, e
sem deixar que tudo vire urgente.

`baixa` · `normal` · `alta` · `urgente`

A prioridade pesa na ordenação da lista e no destaque visual (moderado apenas
para urgente), não na autorização.

## 5. Origem

Registra de onde a demanda veio. Nesta fase a criação é sempre manual; os demais
valores existem para registrar procedência quando houver integração, e nenhuma
delas é ingerida automaticamente ainda.

`manual` (Registrada pela gestão, padrão) · `morador` · `portaria` · `email` ·
`whatsapp` · `sistema` · `outra`

## 6. Campos

### 6.1 `Demanda`

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUID (pk) | `default=uuid.uuid4`. |
| `condominio` | FK → `Condominio` | `PROTECT`. Limite de isolamento. |
| `titulo` | CharField(200) | Obrigatório. O que a demanda precisa resolver. |
| `descricao` | TextField | Opcional. Parágrafos preservados. |
| `status` | CharField(20) | `StatusDemanda`, padrão `aberta`. |
| `prioridade` | CharField(20) | `PrioridadeDemanda`, padrão `normal`. |
| `origem` | CharField(20) | `OrigemDemanda`, padrão `manual`. |
| `responsavel` | FK → `User` | `SET_NULL`, nulo. "Sem responsável" quando vazio. |
| `criado_por` | FK → `User` | `PROTECT`. Rastreabilidade de quem registrou. |
| `criado_em` | DateTime | `auto_now_add`. |
| `atualizado_em` | DateTime | `auto_now`. |
| `prazo` | DateField | Nulo. Sem SLA, sem notificação nesta fase. |
| `concluido_em` | DateTime | Nulo. Preenchido ao concluir. |
| `cancelado_em` | DateTime | Nulo. Preenchido ao cancelar. |
| `local_contexto` | CharField(200) | Opcional. Texto curto (piscina, garagem, elevador...). |
| `ativo` | Boolean | Padrão `True`. Desativar retira da operação sem apagar histórico. |

Recusados nesta fase, por falta de uso claro: `categoria`, `solicitante` e
`observacao_interna`. Observação não é campo — é um evento do histórico (§8).

### 6.2 `HistoricoDemanda`

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUID (pk) | |
| `demanda` | FK → `Demanda` | `CASCADE`. O histórico pertence à demanda. |
| `usuario` | FK → `User` | `PROTECT`. Quem provocou o evento. |
| `data_hora` | DateTime | `default=timezone.now`. |
| `tipo_evento` | CharField(20) | `TipoEventoDemanda`. |
| `descricao` | CharField(255) | O evento em linguagem humana. |
| `dados_anteriores` | JSONField | Nulo. Como estava antes, quando faz sentido. |
| `dados_novos` | JSONField | Nulo. Como ficou depois. |

O histórico é append-only: nunca é reescrito pela interface, e o admin também
não permite excluir (`has_delete_permission → False`).

## 7. Constraints e índices

**Constraints de coerência** (§40) — a situação e as datas têm de concordar:

- `demanda_concluida_registra_quando`: `CONCLUIDA` ⟺ `concluido_em` preenchido.
- `demanda_cancelada_registra_quando`: `CANCELADA` ⟺ `cancelado_em` preenchido.

As mesmas regras também são validadas no domínio (`Demanda.clean()`), com
mensagens humanas. Não há constraint sobre `prazo` — prazo é opcional e livre.

**Índices** (§39) — os quatro combinados que a lista usa:

- `condominio + status` (`demanda_cond_status_idx`);
- `condominio + prazo` (`demanda_cond_prazo_idx`);
- `condominio + prioridade` (`demanda_cond_prior_idx`);
- `responsavel + status` (`demanda_resp_status_idx`).

Em `HistoricoDemanda`: `demanda + data_hora` (`hist_demanda_data_idx`).

## 8. Histórico

Toda alteração relevante vira uma linha, e só o que muda o entendimento da
demanda: sem ruído técnico, sem registrar o ato de abrir e salvar o formulário.

Tipos de evento: `criada` · `status` · `responsavel` · `prioridade` · `prazo` ·
`edicao` · `observacao` · `concluida` · `cancelada`.

O histórico responde quem criou, quem mudou, quando e o que mudou (auditoria,
§12). A observação é um evento — é assim que a operação participa sem responder
pelo condomínio. Na tela, o histórico é uma timeline simples em linguagem humana
("Responsável: Gestão → Operacional"); nunca se mostra JSON.

## 9. Autorização

Centralizada em `permissoes.py`, decidida sempre pelo vínculo ativo da pessoa com
o condomínio — nunca pelo menu ou por um identificador vindo do navegador.
Esconder não é autorizar. Sem certeza, a resposta é negar (*fail closed*).

| Papel | Ver | Criar | Editar conteúdo | Mover situação | Cancelar |
| --- | --- | --- | --- | --- | --- |
| `ADMIN` | sim | sim | sim | sim (todas) | sim |
| `SINDICA` | sim | sim | sim | sim (todas) | sim |
| `SUPERVISORA` | sim | sim | não | todas menos `cancelada` | não |
| `OPERACIONAL` | sim | não | não | `aberta`/`em_andamento`/`aguardando`; +`concluida` se for responsável | não |
| `PORTEIRO` | não | não | não | não | não |
| `MORADOR` | não | não | não | não | não |

Regras que sustentam a tabela:

- `PAPEIS_QUE_GERENCIAM` = {ADMIN, SINDICA} — respondem pelo condomínio.
- `PAPEIS_QUE_CRIAM` = gerenciam + SUPERVISORA.
- `PAPEIS_QUE_OPERAM` = {OPERACIONAL}.
- `PAPEIS_QUE_VEEM` = criam ∪ operam.
- `status_permitidos_para(usuario, demanda)` é a resposta única para "que
  situações esta pessoa pode dar a esta demanda agora": uma demanda encerrada só
  volta a andar por quem gerencia.
- Responsável só pode ser quem tem vínculo ativo e enxerga o módulo
  (`usuarios_elegiveis_como_responsavel`). Não se atribui pessoa de outro
  condomínio.

**Respostas HTTP** (§14): quem não enxerga o módulo não descobre que a demanda
existe — `404`. Quem enxerga mas não responde por ela é recusado com `403` — a
existência já é legítima, a decisão não é.

## 10. Fluxo

1. **Criar** — modal enxuto: título (obrigatório), condomínio (obrigatório),
   descrição, prioridade, responsável, prazo e local em *progressive disclosure*.
   Nasce `aberta` e grava o primeiro evento (`criada`).
2. **Acompanhar** — lista ordenada por atenção: urgentes, depois vencidas,
   depois por prioridade e prazo mais próximo; sem data no fim.
3. **Editar** (quem gerencia) — só o que realmente mudou vira evento.
4. **Mover situação** (conforme a tabela do §9) — concluir grava `concluido_em`;
   cancelar grava `cancelado_em`; voltar a andar limpa as duas.
5. **Observar** (quem enxerga) — acrescenta um evento ao histórico, sem mexer
   nos campos da demanda.

Criar, editar, mover e observar gravam o histórico na **mesma transação**: ou a
demanda e sua memória mudam juntas, ou nada muda (§34).

## 11. Filtros e ordenação

Filtros essenciais (§23): situação, prioridade, responsável, prazo e busca
textual. Busca em título, descrição e `local_contexto` — simples, sem full-text.

Filtro rápido **"Só as minhas"**: `responsavel = request.user` (§25).

Prazo como situação calculada, nunca persistida (§26): **Vencida** quando
`prazo < hoje` e status não é encerrado; **Vence hoje**; **Próxima** (até 7
dias). A lista é de trabalho, não de arquivo: exibe no máximo 50 demandas.

Desktop: tabela (Demanda, Responsável, Prazo, Prioridade, Situação, Ações) com a
linha clicável. Mobile: cards compactos, sem tabela horizontal (§17, §18). Ações
por linha: 1–2 ícones sem borda; desperdício de espaço zero (§20).

## 12. Preparação para o futuro (sem implementar)

`DemandaQuerySet` já responde as perguntas que o painel "Hoje" fará mais adiante
— `vencidas`, `vencendo_hoje`, `proximas`, `urgentes`, `minhas`, `sem_prazo`,
`abertas`, `na_ordem_de_atencao`. Os métodos ficam no domínio, não nas views,
para que a resposta seja a mesma em qualquer tela. **O painel não foi
implementado nesta fase** (§27).

## 13. Decisões adiadas

- **Categoria, solicitante e observação interna** — recusados por falta de uso
  claro. Observação virou evento, não campo.
- **Painel "Hoje"** — os *querysets* existem; a tela, não.
- **SLA e notificações** — prazo é data simples; nenhum aviso é disparado.
- **Modelos de área/equipamento** — `local_contexto` é texto nesta fase;
  poderá virar relação estruturada.
- **Ingestão automática** — `origem` guarda o valor; nenhuma integração
  (morador, portaria, e-mail, WhatsApp) está ligada.

## 14. Relações futuras (documentadas, não implementadas)

- **Demanda → Manutenção**: uma demanda poderá originar uma manutenção. Sem FK
  nesta fase (§28).
- **Demanda → Financeiro**: poderá ter despesa relacionada; sem vínculo direto
  com `BalancoMensal` agora (§29).
- **Demanda → Fornecedor**: poderá demandar orçamento/fornecedor; sem model de
  fornecedor agora (§30).
- **Demanda → Evidência**: poderá receber fotos; sem model agora (§31).

## 15. Critério de aceite (§42)

A gestão consegue, sem manutenção, fornecedor, financeiro ou IA: criar demanda,
atribuir responsável, definir prazo e prioridade, acompanhar a situação,
concluir/cancelar, filtrar, localizar vencidas, consultar o histórico e saber
quem alterou o quê. Verificado por 85 testes em `apps.demandas`.

## 16. Estado da migration

`demandas/0001_initial.py` cria `Demanda`, `HistoricoDemanda`, os quatro índices
de `Demanda`, as duas *check constraints* e o índice de `HistoricoDemanda`.

**A migration não foi aplicada ao banco.** A aplicação fica para uma autorização
separada (§38, §44).

> Contexto herdado: a Fase 1I (Evidências Visuais) permanece **pausada**, com a
> migration `0003_evidenciavisualbalanco.py` gerada e não aplicada.
