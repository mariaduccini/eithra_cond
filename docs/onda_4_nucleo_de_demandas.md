# ONDA 4 — NÚCLEO DE DEMANDAS

Registro das FASES 25 a 28.

---

## FASE 25 — CONSOLIDAR DEMANDA COMO OBJETO OPERACIONAL

### Status

**CONCLUÍDA** (verificação — nenhum código alterado).

### Objetivo

Toda Demanda deve conseguir responder: o que aconteceu; onde; quando; origem;
responsável; estado; próxima ação; quem aguarda retorno; evidências; histórico.
Não adicionar campo apenas para satisfazer UI. Preferir relações e eventos
existentes.

### O que a verificação encontrou

O modelo `apps.demandas.models.Demanda` **já responde aos dez pontos**, sem
campo novo. Cada resposta tem lastro em dado ou relação existente:

| Pergunta | Responde com |
| --- | --- |
| O que aconteceu | `titulo`, `descricao`, `tipo_problema`, `impacto`, `risco` |
| Onde | `local` / `local_contexto` (`local_para_exibir`), `equipamento` |
| Quando | `criado_em`, `prazo`, `previsao`, `ultima_movimentacao` |
| Origem | `origem` (`OrigemDemanda`) |
| Responsável | `responsavel`; `responsavel_acompanhamento` (quem segue de perto) |
| Estado | `status` (`StatusDemanda`); `esta_encerrada` |
| Próxima ação | `proxima_acao` (campo existente) |
| Quem aguarda retorno | `aguardando_tipo` (inclui Fornecedor, Morador, Administração, Aprovação…) + `aguardando_descricao` (`aguardando_para_exibir`) |
| Evidências | `evidencias` → `EvidenciaDemanda` (foto/vídeo, presa à movimentação) |
| Histórico | `historico` → `HistoricoDemanda` (append-only, `ultima_movimentacao`) |

A tela de detalhe (`templates/pages/demandas/detalhe.html`) já **surfaça nove
dos dez**: local, registrado por, data de registro, equipamento, tipo de
problema, impacto, risco, acompanhamento (que reúne Aguardando, Próxima ação,
Previsão, Impedimento e Quem acompanha — ver `apps/demandas/views.py`
`_acompanhamento_para_tela`, linha ~1333), última movimentação, histórico e
evidências.

### Não alterado

- Nenhum campo adicionado ao modelo.
- Nenhuma migration.
- Nenhuma tela alterada.

O único ponto **não exibido** na tela é `origem` — existe no modelo e no
formulário, mas não aparece no detalhe. É deliberado: nesta fase a origem é
quase sempre `MANUAL`, e mostrá-la vazaria vocabulário técnico no lugar de
informação. Não é lacuna do objeto; é escolha de apresentação.

### Decisões

- A consolidação da Demanda como objeto operacional **não exigiu trabalho de
  modelo**: o que a fase pede já estava construído. Consertar o que já está
  certo seria atividade sem progresso.

### Pendências humanas

Nenhuma nesta fase.

### Riscos e limitações

- "Quem aguarda retorno" está coberto por `aguardando_tipo`/`descricao`; o
  *retorno à parte interessada* como estado próprio (não necessário / pendente
  / enviado / dispensado) é o escopo da FASE 28 e **não foi antecipado**.

---

## FASE 26 — PRÓXIMA AÇÃO

### Status

**CONCLUÍDA.**

### Objetivo

Toda demanda ativa responder, na tela, *Quem está com a bola?* e *O que acontece
agora?* — sem criar módulo de tarefas.

### O que a auditoria encontrou

O domínio **já respondia** às duas perguntas, com os campos e relações que
sempre teve:

| Pergunta | Responde com |
| --- | --- |
| Quem está com a bola | `aguardando_tipo` + `aguardando_descricao` (`aguardando_para_exibir`), `responsavel_acompanhamento` |
| O que acontece agora | `proxima_acao`, `previsao` (`previsao_vencida`), `impedimento` |
| Desde quando | `ultima_movimentacao` (derivada do histórico) |

A tela de detalhe já projetava isso em `_acompanhamento_para_tela`
(`apps/demandas/views.py`), e a listagem já mostrava o sinal compacto em
`_sinal_do_acompanhamento`. Portanto **não** se criou helper nem service novo: a
projeção existia em um contexto só, sem repetição que justificasse abstração —
criar uma agora seria abstração sem uso.

O que faltava era a **escolha rápida**: quem repete "Cobrar fornecedor" todo dia
redigitava a frase, e a próxima ação não podia ser ditada como os demais campos.

### Alterado

- `apps/demandas/forms.py` — a tupla `SUGESTOES_DE_PROXIMA_ACAO` (já declarada)
  passa a alimentar o campo: em `AcompanhamentoDaDemandaForm.__init__`, o campo
  `proxima_acao` ganha o atributo `list` apontando para o `<datalist>` da tela.
- `apps/demandas/views.py` — `_render_detalhe` passa `sugestoes_de_proxima_acao`
  ao contexto da tela.
- `templates/pages/demandas/detalhe.html` — a próxima ação da janela do
  acompanhamento ganha o `<datalist>` com as sugestões e passa a aceitar voz
  (`voz_url`/`voz_demanda`), pelo mesmo microfone transversal dos demais campos.

### Não alterado

- `proxima_acao` continua sendo texto livre: as sugestões são atalho, **não**
  catálogo rígido. O campo não virou `ChoiceField` e nada é recusado por não
  estar na lista.
- Nenhum model, nenhuma migration, nenhuma tabela de tarefas.

### Arquitetura reutilizada

- Campo `proxima_acao` (modelo `Demanda`) — a fonte de verdade continua a mesma;
- Capacidade transversal de voz (`static/js/eithra-ditar.js` +
  `apps/core/audio.py` + partial `_campo.html`), sem JS específico;
- Padrão de `datalist` do próprio projeto (campo `local_texto`), pelo atributo
  `list`, sem inventar componente.

### Arquivos

| Arquivo | Motivo |
| --- | --- |
| `apps/demandas/forms.py` | Ligar as sugestões ao campo (`list`) |
| `apps/demandas/views.py` | Expor as sugestões ao contexto do detalhe |
| `templates/pages/demandas/detalhe.html` | `<datalist>` + voz na próxima ação |
| `apps/demandas/tests/test_proxima_acao.py` | Testes focados (novo) |

### Models / migrations / dados

Nenhum model, nenhuma migration. Dados alterados: apenas os que a própria
demanda já registra quando alguém salva o acompanhamento — nada novo é gravado
por causa das sugestões.

### Testes executados

| Comando | Resultado |
| --- | --- |
| `.\manage-local.ps1 check` | System check identified no issues (0 silenced) |
| `.\manage-local.ps1 makemigrations --check --dry-run` | No changes detected |
| `.\test-local.ps1 apps.demandas.tests.test_proxima_acao` | 9 testes OK |
| `.\test-local.ps1 apps.demandas.tests.test_acompanhamento_layout` | 11 testes OK (sem regressão) |
| `.\test-local.ps1 apps.demandas.tests.test_acompanhamento` | 27 testes OK |

Cobertura nova: o campo aponta para a lista; as sugestões são texto humano
único; o campo não é escolha fechada; o `<datalist>` renderiza com todas as
opções; texto livre continua sendo aceito e persistido; uma sugestão também é
aceita; com voz configurada, o campo pode ser ditado; sem voz, a lista continua.

### Dívida remanescente

- As escolhas rápidas "Concluir" e "Encaminhar" citadas no plano ainda **não**
  viraram sugestão: "Concluir" é transição de situação (outra janela,
  `demandas:mudar_status`), e "Encaminhar" só existe de fato como *Encaminhar
  para manutenção* — por isso a sugestão usa essa forma, e não uma palavra
  solta. Inventar um botão de "Concluir" aqui seria abrir uma segunda porta para
  o mesmo ciclo.

### Pendência humana real

Nenhuma.

---

## FASE 27 — COMPROMISSOS

### Status

**CONCLUÍDA** — auditoria; nenhum model novo (o domínio já representa o
compromisso).

### Objetivo

Investigar se o domínio já representa um compromisso — quem prometeu, o quê,
para quando, para quem, estado — e só criar model se não houver forma correta de
persistir. Antes disso, documentar por que não cabe em `HistoricoDemanda`, por que
não cabe em `proxima_acao`, se precisa de ciclo de vida próprio e que objetos
relacionaria.

### O que a auditoria encontrou

O domínio **já representa um compromisso por demanda**, com os campos que sempre
teve:

| Face do compromisso | Onde já está |
| --- | --- |
| Quem prometeu | `aguardando_tipo` (Fornecedor/Morador/Administração…) + `aguardando_descricao` (o nome, quando houver) |
| O quê | `aguardando_descricao`; `proxima_acao` (o próximo passo) |
| Para quando | `previsao` |
| Para quem (objeto) | a própria `Demanda` (FK) e o condomínio; o beneficiário aparece no aguardando |
| Estado | derivado: `previsao_vencida` e a mera existência de `aguardando_tipo` |

Os três exemplos do plano são expressáveis sem campo novo:

- "Fornecedor retorna amanhã." → `aguardando_tipo=Fornecedor`, `previsao=amanhã`,
  `proxima_acao="Fornecedor retorna"`;
- "Morador será informado sexta." → `aguardando_tipo=Morador`, `previsao=sexta`,
  `proxima_acao="Informar o morador"`;
- "Orçamento chega dia 20." → `aguardando_tipo=Fornecedor`, `previsao=dia 20`,
  `proxima_acao="Orçamento chega"`.

### Checklist exigido (antes de criar qualquer model)

- **Por que não cabe em `HistoricoDemanda`** — o histórico é a memória do que
  *aconteceu*, append-only. Ele guarda o *registro* da promessa (evento
  `ACOMPANHAMENTO`, com `previsao`/`aguardando` no antes e no depois) e é o lugar
  certo para isso. O que o histórico não faz é manter um compromisso *aberto* —
  uma promessa no futuro, com estado vivo. Isso é o que `previsao`/`aguardando`
  na demanda já seguram.
- **Por que não cabe em `proxima_acao`** — `proxima_acao` é uma frase única, sem
  data, sem dono e sem estado: não comporta prazo nem ciclo. E não precisa:
  `previsao` traz a data e `aguardando` traz quem. `proxima_acao` é o complemento
  ("o que acontece agora"), não o repositório do compromisso.
- **Por que precisaria de ciclo de vida próprio** — só precisaria se a demanda
  tivesse **mais de um compromisso simultâneo com vidas independentes** (aguardar
  o fornecedor *e* avisar o morador, cada um com sua data e seu desfecho). Hoje o
  domínio pede **um** compromisso por vez, e esse caso único é derivável. Não há
  tela nem regra que exija mais de um.
- **Que objetos relacionaria** — a `Demanda` (1:1 com o compromisso vigente) e,
  futuramente, uma agenda. Nada disso é usado hoje.

### Decisão

Não criar model. Nada foi alterado: o compromisso é **projeção** de campos que já
existem, do mesmo jeito que a exceção da Home e o retorno da FASE 28. Preferir
relação e projeção a duplicação.

### Fontes futuras que justificariam um Compromisso próprio

- Demanda precisando de **mais de um** compromisso simultâneo, com ciclo
  independente;
- compromissos **sem** demanda, hoje não pedidos;
- o compromisso como entidade própria no **Motor de Atenção** — hoje ele entraria
  pela `previsao` do acompanhamento, quando essa fonte for incorporada às
  exceções.

### Arquivos, models, migrations e dados

Nenhum arquivo alterado. Nenhum model, nenhuma migration. Nenhum dado alterado.

### Testes

Nenhum teste novo: nada foi alterado. A leitura do compromisso é a mesma que a
FASE 26 já cobre (Aguardando, Próxima ação, Previsão, Quem acompanha).

### Pendência humana real

Nenhuma.

---

## FASE 28 — RETORNO À PARTE INTERESSADA

### Status

**CONCLUÍDA — versão baseada no que o domínio já tem.**

### Objetivo

Saber: existe alguém aguardando retorno? quem? qual foi o último retorno? quando?
por qual canal? quem registrou? Estados simples: não necessário / pendente /
enviado / dispensado.

### O que a auditoria encontrou

- **Existe alguém aguardando retorno? quem?** → `aguardando_tipo` +
  `aguardando_descricao` (`aguardando_para_exibir`), que já existem.
- **Qual foi o último retorno? quando? quem registrou?** → o **comunicado** que a
  demanda já registra no próprio histórico (`TipoEventoDemanda.COMUNICADO`,
  escrito por `registrar_comunicado` quando um `Comunicacao` com origem nesta
  demanda é gerado). É o único aviso que existe hoje.
- **Estados**: *não necessário*, *pendente* e *enviado* são **deriváveis** do
  aguardando e do histórico; *dispensado* **não é** (ver dívida).

### Alterado

- `apps/demandas/views.py` — `_retorno_para_tela(demanda)` e
  `_inicio_da_espera(demanda)`; o recorte entra no detalhe (`_demanda_para_detalhe`)
  como a chave `retorno`.
- `templates/pages/demandas/detalhe.html` — a linha do retorno na seção de
  Acompanhamento (*Pendente* / *Enviado*), e nada quando não há espera.

### Como o estado é derivado

- **Não necessário**: ninguém aguardando (`aguardando_tipo` vazio e sem
  `aguardando_descricao`).
- **Pendente**: alguém aguardando e nenhum comunicado depois que a espera começou.
- **Enviado**: há comunicado com data igual ou posterior ao início da espera
  atual. O início da espera é o último acompanhamento que mudou *quem se espera*
  — ou a criação, quando a espera nasceu com a demanda. É isso que impede um aviso
  antigo de responder por uma espera nova.

### Não alterado

- Nenhum model, nenhuma migration, nenhuma tabela de retorno.
- Nada é gravado por causa da leitura — há teste que prova que o histórico e
  `atualizado_em` não mudam ao ler.
- Nenhum botão novo: "Retorno enviado" é o próprio **Gerar comunicado** que a
  demanda já oferece, e nenhum botão fictício foi inventado.

### Arquitetura reutilizada

- `aguardando_tipo`/`aguardando_descricao` (`Demanda`) e `historico`
  (`HistoricoDemanda`);
- `Comunicacao` + `TipoEventoDemanda.COMUNICADO` — a capacidade transversal de
  comunicação continuou dona do fato; a demanda só lembra que ele existiu;
- o padrão de **leitura derivada** da Home (exceções): nada guardado, tudo
  calculado na hora.

### Models / migrations / dados

Nenhum model, nenhuma migration. Nenhum dado alterado.

### Arquivos

| Arquivo | Motivo |
| --- | --- |
| `apps/demandas/views.py` | Derivar o retorno do aguardando + histórico (`_retorno_para_tela`) |
| `templates/pages/demandas/detalhe.html` | Mostrar a linha do retorno na seção de Acompanhamento |
| `apps/demandas/tests/test_retorno.py` | Testes focados (novo) |

### Testes executados

| Comando | Resultado |
| --- | --- |
| `.\manage-local.ps1 check` | System check identified no issues (0 silenced) |
| `.\manage-local.ps1 makemigrations --check --dry-run` | No changes detected |
| `.\test-local.ps1 apps.demandas.tests.test_retorno` | 9 testes OK (novo) |
| `.\test-local.ps1 apps.demandas.tests.test_acompanhamento_layout` | 11 OK (sem regressão) |
| `.\test-local.ps1 apps.demandas.tests.test_acompanhamento` | 27 OK (sem regressão) |
| `.\test-local.ps1 apps.demandas.tests.test_proxima_acao` | 9 OK (sem regressão) |

Cobertura nova: sem espera o retorno é "não necessário"; quem espera torna o
retorno "pendente"; comunicado após a espera marca "enviado" (com quando e quem);
comunicado **anterior** à espera não conta; espera **redefinida** exige retorno
novo; a leitura **não grava nada**; e a linha aparece (ou não) no detalhe.

### Encerramento da onda

| Comando | Resultado |
| --- | --- |
| `.\test-local.ps1 apps.comunicacao` | 66 OK |
| `.\test-local.ps1 apps.core` | 445 rodados, **1 falha pré-existente** |
| `.\test-local.ps1 apps.demandas` | 433 rodados, **3 falhas pré-existentes** |

As quatro falhas **não** vêm das FASES 26–28 e não foram tocadas aqui:

- `apps.core.tests.test_home_demandas.LimiteDasDemandasTests.test_mostra_no_maximo_cinco_demandas`
  — a Home passou a ter **dois** blocos de demanda (as exceções da FASE 24 e a
  lista de demandas), e o teste do limite antigo não conta o segundo bloco.
- `apps.demandas.tests.test_views.RegistroEmTresEtapasTests` (3 testes) — esperam
  o rótulo antigo de prioridade ("Normal"); o domínio passou a nomear "Média" e
  "Crítica" (migration `0009`), e os testes ficaram para trás.

São dívidas de fases anteriores, registradas como achado do fechamento — não
motivo para alterar o escopo desta onda.

### Dívida remanescente

- **Canal de envio**: o comunicado não guarda canal (guarda público), então "por
  qual canal" ainda não é respondível. Fonte futura: um campo de canal no
  comunicado, do domínio de comunicação.
- **"Dispensado"**: exigiria um sinal persistido ("decidi não avisar mesmo havendo
  espera") que não se deriva de `aguardando_tipo` nem do histórico. Não foi
  inventado.
- **Precisão do "enviado"**: qualquer comunicado posterior ao início da espera
  marca "enviado", ainda que o público do card não seja exatamente quem se espera
  (ex.: card a moradores enquanto se aguarda um fornecedor). É o melhor que o
  sinal atual permite; um vínculo retorno↔espera seria fonte futura.

### Pendência humana real

Decidir se "dispensar retorno" vira conceito de domínio (com sinal próprio) ou se
basta limpar o aguardando. Não bloqueia esta entrega.

