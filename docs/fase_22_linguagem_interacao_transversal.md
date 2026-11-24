# EITHRA V3 — FASE 22 — Linguagem de Interação Transversal

> Registro da fase de consolidação da linguagem de interação do Eithra.
>
> Objetivo: **não permitir que cada módulo desenvolva sua própria experiência.**
> O resultado desta fase é o **inventário dos componentes reutilizáveis já
> existentes** e a confirmação de que eles são únicos em todo o sistema.
>
> Não criar design system novo. Sneat + Bootstrap 5 + Boxicons continuam sendo
> a base; a **camada Eithra** (`static/css/eithra.css`) é apenas o acabamento
> aplicado por cima.

---

## 1. Princípio que rege a fase

Os mesmos princípios que já valem para o produto (ver
`docs/constituicao_produto_ux_arquitetura_eithra.md` §2, §9, §10, §39 e
`docs/ux_padrao_componentes_sneat_eithra.md`):

- **reutilizar > criar**
- **relacionar > duplicar**
- **clicar > digitar**
- **voz > formulário longo**
- **contexto > campos**

Uma capacidade transversal tem **um só dono** no código. Nenhum módulo
reimplementa voz, foto, confirmação, estado, busca ou feedback.

---

## 2. Mapa dos componentes reutilizáveis

| Padrão | Onde vive (dono único) | Estado |
| --- | --- | --- |
| Voz | `static/js/eithra-ditar.js` + `apps/core/audio.py` + `templates/pages/cadastros/_campo.html` | consolidado |
| Foto | `apps/portaria/fotos.py` + `apps/core/imagem.py` + `apps/core/storage.py` | consolidado |
| Confirmação | modal global em `templates/layouts/app.html` + `data-eithra-confirm*` nos formulários | consolidado |
| Estados de tela | `templates/components/state.html` | consolidado |
| Feedback (avisos) | `templates/components/messages.html` | consolidado |
| Busca / filtros / linha | `templates/components/busca.html` + `static/js/eithra-listagens.js` | consolidado |
| Ações e ações rápidas | regra §7 / §47 da Constituição + `⋮` nas grids | regra consolidada; sem partial |
| Próxima ação | — | **não existe ainda** (nasce na Fase 26) |
| Contexto | seções de contexto por tela + context processors | parcial |
| Timeline / histórico | `HistoricoDemanda` (por domínio) | por domínio; transversalização na Fase 40 |

---

## 3. VOZ

**Uma única capacidade compartilhada.** Não existe `demandas-ditar.js`,
`manutencoes-ditar.js` nem `projetos-ditar.js` — nem devem existir.

### O que já existe

- **Controlador único:** `static/js/eithra-ditar.js`. É agnóstico de domínio:
  grava a fala, manda o áudio para o endereço do botão e escreve o texto no
  campo. Não conhece demanda, portaria, chegada nem autorização. O contrato é
  data-attribute:
  - `[data-eithra-ditar]` com `data-eithra-ditar-url` e
    `data-eithra-ditar-campo`;
  - `[data-eithra-ditar-recado]` para o aviso de estado;
  - ouve o evento `eithra:descartar` para encerrar gravação quando o modal
    fecha;
  - só se revela onde o navegador souber gravar (nasce `hidden`).
- **Núcleo de áudio único:** `apps/core/audio.py` — "a única porta de entrada de
  áudio do sistema", que para na **transcrição fiel**. Não interpreta e não
  guarda o áudio.
- **Campo de formulário ditável:** `templates/pages/cadastros/_campo.html`
  aceita `voz_url` (e `voz_demanda` opcional); sem esse argumento, o campo
  segue idêntico.

### Quem já usa

- **Demandas:** `templates/pages/demandas/listagem.html` (relato) e
  `templates/pages/demandas/detalhe.html` (atualização), via o partial, contra
  `demandas:transcrever`.
- **Portaria:** modal de chegada via o partial contra `portaria:transcrever`, e
  o campo livre de relato do painel com botão próprio — mesmas classes e mesmo
  contrato de dados, sem controlador novo.

### Como dar voz a um campo novo (receita)

1. o módulo expõe **um** endpoint fino que chama `apps/core/audio.py`
   (a interpretação do texto continua no domínio);
2. passa a rota para o partial: `{% include "pages/cadastros/_campo.html" with
   campo=formulario.x voz_url=url_da_transcricao %}`;
3. inclui `static/js/eithra-ditar.js` (uma vez por tela, onde houver campo de
   voz) e o `<p data-eithra-ditar-recado>`.

> A interpretação do texto é do domínio — ex.: `apps/portaria/numero_falado.py`
> interpreta números ditados. Isso **não** é controlador de voz, é leitura.

---

## 4. FOTO

**Mesma lógica conceitual: a foto pertence ao evento/objeto correto.** Não há
sistema de anexos paralelo.

### O que já existe

- **Régua da imagem compartilhada:** `apps/core/imagem.py` é a única validação —
  confere pelo **conteúdo real**, nunca pela extensão nem pelo que o navegador
  informou.
- **Storage compartilhado:** `apps/core/storage.py` (provider + URL assinada).
- **Dono por domínio:** cada domínio prende a imagem ao seu objeto com o mesmo
  formato de referência (`..._storage_provider`, `..._storage_bucket`,
  `..._storage_key`, `..._mime_type`, `..._tamanho_bytes`):
  - `apps/portaria/fotos.py` — foto da **chegada**, presa ao `AcessoPortaria`;
  - `apps/demandas/evidencias.py`, `apps/manutencoes/evidencias.py` — evidências;
  - `apps/governanca/arquivos.py`, `apps/comunicacao/arquivos.py`,
    `apps/balancos/evidencias.py` — documentos/arquivos;
  - `apps/accounts/avatar.py`, `apps/condominios/logo.py` — imagens de perfil.

### Regras preservadas

- a foto **não decide** nada no domínio (anexar não abre/encerra passagem);
- o arquivo vai para bucket **privado**; o acesso é gerado sob demanda;
- foto é **sempre opcional** — sem arquivo, nada é gravado;
- nenhum domínio cria um segundo mecanismo de anexo.

---

## 5. CONFIRMAÇÃO

Um **único** modal de confirmação no shell (`templates/layouts/app.html`,
`#eithra-confirm-modal`), acionado por formulário com `[data-eithra-confirm]`:

- `data-eithra-confirm-title`, `-message`, `-label`, `-class`,
  `data-eithra-cancel-label`;
- o JS apenas **apresenta** a confirmação e reenvia o formulário; a **regra e a
  permissão continuam no backend** (Constituição §47.9).

É o mesmo componente para excluir, registrar demanda, registrar autorização e
qualquer ação crítica. Nenhum módulo monta seu próprio "tem certeza?".

---

## 6. ESTADOS DE TELA

`templates/components/state.html` é o **bloco único** para vazio, carregando,
erro ou sucesso de uma listagem/seção. Contrato:

- `state_icon`, `state_title`, `state_message`, `state_tone`
  (`danger`/`warning`/`success`/`info`), `state_busy`,
  `state_action_view`/`state_action_url`/`state_action_label`,
  `state_action_tone`;
- **o estado nunca depende só da cor** — ícone e texto dizem o que acontece.

CSS em `static/css/eithra.css` (`.eithra-state*`). Nenhum módulo escreve seu
próprio bloco de "nada por aqui".

---

## 7. FEEDBACK (AVISOS)

`templates/components/messages.html` renderiza as mensagens do framework
(`django.contrib.messages`) como alertas Sneat, com ícone por tom. Um só lugar
para "registramos…", "não foi possível…", "nada foi criado".

---

## 8. BUSCA, FILTROS E A LINHA QUE ABRE

`templates/components/busca.html` + `static/js/eithra-listagens.js`:

- a busca se atualiza sozinha depois da última tecla (`[data-eithra-busca]`);
- os filtros aplicam sozinhos (`[data-eithra-filtros]`);
- a linha/cartão com `[data-href]` abre o registro — e o botão "visualizar"
  deixa de existir (§47.1);
- navegação por teclado continua pelo link do título;
- trocar busca/filtro volta para a primeira página.

Comportamento **uma vez em todo o Eithra**, não reescrito por tela.

---

## 9. AÇÕES E AÇÕES RÁPIDAS

**Regra consolidada** (§7 e §47 da Constituição), sem partial compartilhado —
cada grid monta o seu conforme a regra:

- 1–2 ações: ícones sem borda;
- 3 ou mais: `⋮` (`bx-dots-vertical-rounded`) com menu curto, a destrutiva
  separada no fim;
- ação crítica: texto explícito + confirmação (§5);
- **na grid, a coluna de ações é só `⋮`.**

As **ações rápidas** (Resolvido / Continua / Piorou / Chamar fornecedor /
Aguardar / Encaminhar) pertencem às Fases 23, 24 e 26. Quando nascerem, devem
nascer **transversais** — uma única forma de ação rápida, não uma por módulo.

---

## 10. PRÓXIMA AÇÃO, CONTEXTO E TIMELINE

Ainda **não** existem como componente transversal, e isso é intencional nesta
fase. O compromisso registrado:

- **Próxima ação** — nasce na Fase 26 ("quem está com a bola?"), como conceito
  reutilizável, nunca como módulo de tarefas isolado.
- **Contexto** — hoje aparece como seções de contexto por tela e via context
  processors; a consolidação vem com as Homes (Fases 23/24) e o núcleo de
  Demandas (Fase 25).
- **Timeline / histórico** — hoje é `HistoricoDemanda` (por domínio); a
  Constituição §40 prevê que o histórico seja transversal. A transversalização
  acontece na Fase 40, sem criar um "feed de tudo".

Nada disso deve ser criado **por módulo** quando chegar a vez.

---

## 11. Pontos generalizados e verificações desta fase

Esta fase **não introduziu código novo**. Isso é o resultado correto segundo o
princípio `reutilizar > criar`: a camada transversal já existia e foi verificada
como única.

Verificações feitas:

1. **Um só controlador de voz** — `static/js/eithra-ditar.js` não cita nenhum
   domínio (demanda, portaria, chegada, autorização). Confirmado por leitura
   integral.
2. **Um só núcleo de áudio** — todos os consumidores importam
   `apps.core.audio`; não há transcrição paralela.
3. **Uma só régua de imagem e um só storage** — todos os módulos de foto/arquivo
   importam `apps.core.imagem` e `apps.core.storage`.
4. **Uma só confirmação** — `[data-eithra-confirm]` e o modal do shell; nenhum
   "confirm" próprio por módulo.
5. **Um só bloco de estado e um só de avisos** — `components/state.html` e
   `components/messages.html`.
6. **Um só comportamento de busca/filtro/linha** — em `eithra-listagens.js`.
7. **Nenhum partial duplicado por módulo** — o campo de formulário ditável é o
   mesmo (`pages/cadastros/_campo.html`), usado por Demandas e Portaria.

Ponto registrado, **não** alterado por risco/benefício: o painel da Portaria
desenha o botão de voz do relato livre à mão (mesmas classes e mesmo contrato do
controlador). Como o painel foi recém-homologado e o markup é fiel ao contrato,
extrair um partial agora tocaria tela estável sem ganho real — fica como
consolidação opcional futura, se houver uma terceira ocorrência.

---

## 12. O que não fazer

- não criar `*-ditar.js` por módulo;
- não criar segundo sistema de anexos/fotos;
- não criar "confirm" próprio por tela;
- não criar bloco de estado ou alerta próprio por tela;
- não reescrever busca/filtro por tela;
- não criar design system paralelo ao Sneat (§39);
- não transformar os padrões futuros (próxima ação, timeline) em módulos
  isolados.

---

## 13. Conclusão

A linguagem de interação do Eithra **já é transversal**: voz, foto,
confirmação, estados, feedback e listagens têm dono único e contrato estável.
A Fase 22 cumpriu o objetivo ao **confirmar essa unicidade e registrá-la**,
deixando explícito o compromisso de que próxima ação, contexto e timeline nasçam
transversais nas fases seguintes. Não houve necessidade de generalizar código:
generalizar o que já era único seria complexidade sem propósito.

**Status: CONCLUÍDA.** Sem alteração de código; sem migration; sem novos models.
