# Eithra — Auditoria transversal da jornada operacional

**Escopo:** Demandas, Compras, Estoque, Manutenções, Rotinas, Preventivas e Fornecedores.
**Modo:** somente leitura. Nenhum arquivo de código, teste, migration ou branding foi alterado.
**Método:** mapeamento de modelos, serviços, views, formulários e telas, com evidência em `arquivo:linha`.
Onde uma afirmação não pôde ser comprovada no código, ela está marcada como **não verificado**.

---

## A. MAPA ATUAL

### A.1 Relações comprovadas no código

```
Condomínio (raiz de isolamento de todas as entidades abaixo)

Demanda ──(FK Manutencao.demanda_origem, PROTECT, one-way)──▶ Manutencao
Demanda ──(link ?demanda= na tela)──▶ Manutenção / Comunicado   [não persistido como FK nesta leitura]
Demanda ──(FK)──▶ LocalOperacional, Equipamento, responsável (User)

Manutencao ──(FK opcional)──▶ Fornecedor
Manutencao ──(FK opcional)──▶ PlanoPreventivo
Manutencao ──(FK opcional)──▶ LocalOperacional, Equipamento
PlanoPreventivo ──(FK opcional)──▶ Fornecedor, LocalOperacional, Equipamento

Compra ──(FK opcional)──▶ Fornecedor
Compra ──(FK opcional, informativa)──▶ Manutencao (manutencao_origem)
ItemCompra ──(FK opcional)──▶ ItemEstoque
ItemCompra ──(FK opcional)──▶ Manutencao
MovimentacaoEstoque ──(FK)──▶ ItemEstoque, ItemCompra (entrada), Manutencao (saída)

RotinaRecorrente ──(FK opcional)──▶ LocalOperacional, Equipamento, Fornecedor, ModeloChecklist
RotinaRecorrente ──(1:N)──▶ ExecucaoRotina ──(1:N)──▶ ItemExecucaoChecklist

Fornecedor ──(M2M via FornecedorEspecialidade)──▶ EspecialidadeFornecedor
Fornecedor ──(FK reversa)──▶ ContratoFornecedor ──(M2M)──▶ Equipamento / LocalOperacional
Fornecedor ──(FK reversa)──▶ Garantia, DocumentoFornecedor
```

### A.2 Relações que **não existem** hoje

| Par | Situação |
|---|---|
| Demanda ↔ Compra | **Ausente.** Não há FK, nem serviço, nem menção a "demanda" no app `estoque`. |
| Demanda ↔ Estoque | **Ausente.** |
| Demanda ↔ Fornecedor | **Ausente** como FK. O fornecedor é citado em texto livre no relato e reconhecido por NLP (`apps/demandas/movimentacao.py:317`), sem vínculo persistido. |
| Demanda ↔ Projeto | **Ausente** (não verificado FK; a estrutura AVCB vive em Projeto→Etapa→Entrega, separada). |
| Manutencao ↔ Compra/Estoque (efeito) | **Ausente.** O `manutencao_origem` da Compra é informativo — comentário do modelo: "Não altera a manutenção" (`apps/estoque/models.py:634`). |
| Manutencao → Demanda (retorno) | **Ausente.** Concluir/cancelar manutenção não atualiza a Demanda de origem (`apps/manutencoes/services.py:325-327`). |
| Fornecedor ↔ Equipamento / Local (direto) | **Ausente.** Só indireto via contrato ou via manutenção. |

### A.3 Cobertura por módulo

| Módulo | Modelo(s) | Entrada de menu | Voz | Vínculo com Demanda |
|---|---|---|---|---|
| Demandas | `Demanda`, `HistoricoDemanda`, `EvidenciaDemanda` | "Demandas" (topo) | Sim | — |
| Manutenções | `Manutencao` + 7 modelos | "Manutenções" (topo) | **Não** | Sim (FK, one-way, manual) |
| Compras | `Compra`, `ItemCompra` | "Compras" (topo) | Não | Não |
| Estoque | `ItemEstoque`, `MovimentacaoEstoque` | "Estoque" (topo) | Não | Não |
| Rotinas | `RotinaRecorrente`, `ExecucaoRotina`, `ModeloChecklist`… | "Rotinas" (topo) | Não | Não |
| Preventivas | `PlanoPreventivo`, `CriterioConferenciaPreventiva`… | dentro de "Manutenções" | Não | Não (proibido por constraint) |
| Fornecedores | `Fornecedor`, `EspecialidadeFornecedor` | filho de "Cadastros" | Não | Não |

Ordem do menu lateral (`apps/core/context_processors.py:32-172`):
Painel → Portaria → Demandas → Manutenções → Comunicação → Rotinas → Estoque → Compras → Cadastros (grupo: Condomínios, Pessoas e unidades, Usuários e acessos, Áreas e locais, Equipamentos, **Fornecedores**, Contratos, Garantias, Documentos, Tipos e categorias) → Balanços.

---

## B. SOBREPOSIÇÕES

### B.1 Entidades e motores redundantes

1. **Rotina × PlanoPreventivo — duas máquinas de recorrência.** Ambos guardam `titulo`, `descricao`, `local`, `equipamento`, `fornecedor`, `responsavel`, `ativo` e uma data da próxima ocorrência; ambos delegam o cálculo a `apps/core/recorrencia.py`. Diferem em:
   - periodicidade: `RotinaRecorrente` tem `Periodicidade` rica (dia da semana / dia do mês / mês); `PlanoPreventivo` tem só `DIAS|MESES|ANOS` (`apps/manutencoes/constants.py:25-36`);
   - ocorrência concreta: `ExecucaoRotina` (`apps/rotinas/models.py:644-865`) × `Manutencao` preventiva (`apps/manutencoes/models.py:602-1053`);
   - checklist: `ModeloChecklist`/`ItemModeloChecklist` × `ModeloConferenciaPreventiva`/`CriterioConferenciaPreventiva`;
   - geração: manual (`apps/rotinas/views.py:428-439`) × comando `gerar_preventivas` (`apps/manutencoes/management/commands/gerar_preventivas.py`).
   Evidência: `apps/rotinas/models.py:285-568`, `apps/manutencoes/models.py:248-449`.

2. **Duas entradas de menu para "coisa que se repete".** "Rotinas" (topo) e "Planos preventivos" (dentro de Manutenções, `apps/manutencoes/urls.py` /planos/).

3. **Duas bibliotecas de checklist/conferência** com a mesma forma (modelo → itens → cópia na ocorrência), em apps diferentes.

4. **Dois vocabulários de especialidade/ativo não ligados.** `EspecialidadeFornecedor` ("quem atende X") e `TipoEquipamento` ("o que é o ativo") são listas independentes, sem vínculo estrutural (`apps/cadastros/models.py:253-329` e `:531`).

### B.2 Duplicação de código de inteligência

| Item | Cópia 1 | Cópia 2 | Nota |
|---|---|---|---|
| Enum `Confianca`/`Estado` | `apps/core/interpretacao.py:23-55` | `apps/demandas/derivacao.py:166-178` | Comentário no núcleo registra a duplicação. |
| Normalização de texto | `apps/core/texto.py:1-68` | `apps/demandas/derivacao.py:476-494` | `apps/core/texto.py:12-15` registra que a segunda nasceu antes. |
| Endpoint de transcrição | `/demandas/transcrever/` | `/portaria/transcrever/` | Ambos chamam o mesmo `apps/core/audio.py` — há 2 rotas para 1 serviço. |

### B.3 Telas que expõem estrutura interna

- **Listagem de Demandas** (`templates/pages/demandas/listagem.html`): 7 indicadores em 2 grupos e **11 filtros** (`apps/demandas/views.py:1762-1842`, template).
- **Listagem de Manutenções** (`templates/pages/manutencoes/listagem.html`): **11 filtros**, sem indicadores acionáveis no topo (só "{{ total }} em vista").
- **Cadastro de Fornecedor**: 24 campos no modelo, distribuídos em 3 blocos — 4 visíveis, 5 em "Mais detalhes", 7 em "Disponibilização para moradores" (`templates/pages/cadastros/fornecedores.html:150-217`).
- **Formulário de Manutenção** pergunta `descricao_servico`, `fornecedor`, `custo_previsto` mesmo quando a Demanda de origem já traz contexto suficiente.

---

## C. JORNADA RECOMENDADA

Uma só porta de entrada — a necessidade — conduzindo a próxima ação, sem exigir que o usuário escolha módulo.

```
Necessidade (voz / texto / toque)
        │
        ▼
   Demanda  ◀── núcleo já existente (proxima_acao, aguardando_tipo, previsao, prazo_proxima_acao)
        │
        ├─ resposta é interna e imediata        → Concluir  (evidência)
        ├─ depende de terceiro                  → Aguardar  (aguardando_tipo + previsao)
        ├─ exige serviço técnico                → Manutenção (herda local/equipamento/problema)
        │        └─ se faltar peça/material     → Compra → Estoque
        ├─ é compra de material                 → Compra → Estoque
        └─ precisa de fornecedor                → Fornecedor (por especialidade/contrato)
```

Princípios preservados: o motor determinístico (classificação, prioridade, exceções) continua decidindo o que o sistema sabe; a IA entra por exceção (`apps/core/llm.py`, só quando a leitura determinística não resolve); nada é salvo sem confirmação.

Regra de ouro: **uma necessidade = uma Demanda**. Manutenção, Compra e Fornecedor são *consequências* dela, não módulos que o usuário precisa procurar.

---

## D. TABELA DE DECISÕES

| Situação informada | Próxima ação sugerida | Entidade utilizada | Já existe? |
|---|---|---|---|
| "A bomba da piscina está com problema" | Abrir/atualizar Demanda; sugerir Manutenção no mesmo ato | `Demanda` (+ `Manutencao` por link `?demanda=`) | Parcial — link existe, sugestão automática não |
| Equipamento quebrou | Manutenção corretiva herdando local/equipamento/problema | `Manutencao` (`demanda_origem`) | Sim (herança parcial em `apps/manutencoes/views.py:1381-1396`) |
| Fornecedor prometeu retornar | `Aguardar` com `aguardando_tipo=fornecedor` + `previsao` | `Demanda` | Sim |
| Preciso comprar material | Registrar necessidade → Compra (rascunho) → Estoque | `Demanda` → `Compra` → `ItemCompra` → `MovimentacaoEstoque` | Compra/Estoque sim; ponte com Demanda **não existe** |
| Item já existe no estoque | Retirar do estoque para o serviço | `MovimentacaoEstoque` (`saida_manutencao`) | Sim |
| Morador precisa responder | `Aguardar` com `aguardando_tipo=morador` | `Demanda` | Sim |
| Serviço recorrente não-técnico (limpeza, ronda) | Programar atividade recorrente | `RotinaRecorrente` | Sim |
| Inspeção periódica de equipamento | Plano preventivo → gera Manutenção preventiva | `PlanoPreventivo` | Sim |
| "Quem atende elevador?" | Fornecedor por especialidade/contrato | `FornecedorEspecialidade` / `vigencia_dos_vinculos()` | Dados sim; consulta consolidada **não existe** |
| Campainha/portaria relata ocorrência | Demanda a partir do relato | `Demanda` (`origem=PORTARIA`) | Sim |
| E-mail com autorização/ocorrência | Confirmar em `/decisoes/<uuid>/` → cria Demanda/Autorização | `email_acoes.confirmar_acao_email` | Sim |

---

## E. O QUE NÃO MUDAR

1. **`Demanda` como núcleo operacional.** `proxima_acao`, `prazo_proxima_acao`, `aguardando_tipo`, `aguardando_descricao`, `previsao`, `impedimento` já cobrem "aguardar / retornar / compromisso / próxima ação" sem status novo. Não criar "Pausado". (`apps/demandas/models.py:267-319`)
2. **Separação semântica de prazos.** `prazo_formal_vencido` × `proxima_acao_vencida` × `previsao_vencida` — manter as três naturezas distintas. (`apps/demandas/models.py:428-463`)
3. **`HistoricoDemanda` append-only** e `EvidenciaDemanda` (SHA-256 + storage privado): o padrão de memória está correto. (`apps/demandas/models.py:478-655`)
4. **`Equipamento` e `LocalOperacional` como entidades canônicas** de ativo e lugar; a memória projetada (demandas/manutenções/contratos/evidências) está no lugar certo (`apps/cadastros/memoria.py`).
5. **Isolamento por condomínio** em `clean()` e nas permissões — preservar integralmente.
6. **Motor de voz único** (`static/js/eithra-ditar.js`) e serviço de áudio único (`apps/core/audio.py`): reutilizar, não recriar.
7. **Contrato de interpretação** do núcleo (`Confianca`, `Estado`, `Entidade`, `Intencao` — `apps/core/interpretacao.py`) e o fluxo de confirmação por exceção (`/decisoes/<uuid>/`, `apps/core/views.py:135-187`).
8. **`Compra` como fato econômico** (rascunho → confirmada) e o `MovimentacaoEstoque` como razão do estoque.
9. **`manutencao_origem` informativo na Compra** e ausência de efeito contábil na Manutenção — decisão explícita e correta.
10. **Portaria e Balanços**: fora do escopo desta tarefa e não devem ser tocados.

---

## F. PLANO DE REESTRUTURAÇÃO

### F.1 Correções de UX (sem mudança de domínio)

1. **Listagens: busca única visível + filtros sob demanda.** Demandas (11 filtros) e Manutenções (11 filtros) expõem o modelo interno. Manter a busca e os 2–3 indicadores acionáveis; mover o resto para um painel "Filtros".
2. **Uma ação primária por contexto.** Na listagem de Demandas, o botão principal deve ser "Registrar" (voz/texto primeiro); as demais ações vivem no detalhe.
3. **Rótulos com semântica.** Substituir "Mais"/"Ver" por verbos do domínio ("Aguardar", "Concluir", "Adicionar evidência", "Abrir manutenção").
4. **Manutenções sem voz.** Adicionar o microfone transversal aos campos livres (`descricao_problema`, `descricao_servico`) — nenhum motor novo, apenas o mesmo `data-eithra-ditar`.
5. **Rótulo de menu "Rotinas" → "Atividades programadas"** (ver F.2.1), se aprovado.
6. **Fornecedor: cadastro já usa progressive disclosure** (`fornecedores.html:150-217`) — manter; não transformar em ERP.

### F.2 Ajustes de domínio (somente quando indispensáveis)

1. **Rotinas × Preventivas.** Recomendação: **não excluir nada**. Manter `RotinaRecorrente` como a regra geral de recorrência (limpeza, inspeção, ronda, segurança — tipos já suportados em `apps/rotinas/constants.py:25-34`) e tratar o `PlanoPreventivo` como a variante que **gera Manutenção**. Duas direções possíveis, a decidir com o humano:
   - **Curto prazo (só apresentação):** expor o `PlanoPreventivo` sob o mesmo rótulo de "Atividades programadas" e unificar a linguagem da tela; nada muda no banco.
   - **Médio prazo (só se comprovada a redundância):** estudar convergência de campos (`periodicidade_tipo/intervalo/dia_semana...` × `frequencia_unidade/frequencia_intervalo`) — exige análise de migração e **não deve ser feita sem autorização**.
2. **Ponte Demanda → Compra.** Hoje não existe. É o maior vazio da jornada ("preciso comprar dois carrinhos"). Desenho mínimo, sem tabela nova: permitir que a Compra (ou o `ItemCompra`) referencie a Demanda de origem, do mesmo modo que `Manutencao.demanda_origem` já faz. Requer decisão humana por ser alteração de modelo/migration.
3. **Ponte Demanda → Manutenção automática (sugestão).** Hoje o link é manual. Um caminho possível é a Demanda sugerir "abrir manutenção" já com os dados herdados, exigindo confirmação — reutilizando `_iniciais_da_demanda`.
4. **Retorno Manutenção → Demanda.** Avaliar se concluir a manutenção deveria registrar um evento no histórico da Demanda (append-only). Hoje não acontece.

### F.3 Limpeza

1. **Unificar `Confianca`/`Estado`**: manter as do núcleo (`apps/core/interpretacao.py`) e fazer `apps/demandas/derivacao.py` consumi-las. (Risco: Demandas está fechado; requer autorização.)
2. **Unificar normalização de texto**: `apps/demandas/derivacao.py:476-494` deveria usar `apps/core/texto.py`.
3. **Consolidar endpoints de transcrição**: uma rota compartilhada servindo os dois módulos, mantendo o `apps/core/audio.py` como único serviço.
4. **Consulta consolidada "quem atende X"**: hoje a resposta está em 2 caminhos não unificados (especialidade e contrato). Uma função de leitura em `apps/governanca/consultas.py` (onde já vive `vigencia_dos_vinculos`) resolveria sem novo modelo.
5. **Ligar `EspecialidadeFornecedor` a `TipoEquipamento`** (ou documentar que são vocabulários distintos) para responder "quem atende elevador?" com precisão.

### F.4 Futuro (interpretação multimodal e automação)

1. **Multimodal real.** `apps/core/imagem.py` hoje só **valida** formato (JPG/PNG/WEBP); não há OCR/visão. Um print com "Pode liberar João da ClimaTech amanhã para o 902" ainda não vira dado estruturado. Caminho: estender o contrato de interpretação do núcleo, sempre com confirmação — **sem segundo motor**.
2. **Cadeia intenção → Demanda → próxima ação.** Já existem as peças (leitura determinística + LLM por exceção + `Intencao.acoes`), mas nenhuma ligação automática Demanda→Manutenção/Compra. Implementar sobre o contrato existente.
3. **Automação por origem.** O fluxo de e-mail (`gmail.py` → `email_interpretacao.py` → `/decisoes/<uuid>/` → `email_acoes.py`, com `CRIAR_NOVA_DEMANDA`) é o modelo a generalizar para WhatsApp no futuro — **não nesta tarefa**.

---

## Respostas diretas às perguntas da auditoria

### 1. Demandas
1. **Sim** — `Demanda` já é a entidade principal de necessidade: 5 status, 4 prioridades, e campos de acompanhamento (`proxima_acao`, `aguardando_tipo`, `previsao`) que dispensam status novos.
2. Decisões expostas que poderiam ser derivadas: prioridade (já é derivada em manutenção via `prioridade_do_relato`, `apps/manutencoes/forms.py:269-271`), prazo e a escolha entre Manutenção/Compra.
3. **Ações duplicadas: não há** — cada operação tem view própria (`apps/demandas/urls.py:15-31`).
4. A interface obriga a entender o modelo interno na **listagem** (11 filtros, 7 indicadores) e ao escolher manualmente entre "Criar manutenção" e "Gerar comunicado".
5. Ação contextual principal sugerida: **uma** — avançar o estado da Demanda ("Aguardar", "Concluir", "Registrar atualização"), com Manutenção/Compra aparecendo como consequência confirmável.

### 2. Compras
1. `Compra` representa **compra efetivada** (fato econômico) em rascunho até `CONFIRMADA` — exige `data_compra`, aceita `fornecedor`/`numero_documento`, e não tem cotação/aprovação.
2. Uma Demanda **deveria** poder sugerir Compra; hoje **não existe** esse caminho.
3. **Sim, cedo demais na forma**: o fluxo obriga criar a compra (com data) antes de descrever o item.
4. Deriváveis: `condominio` (contexto), `fornecedor` (por especialidade/histórico), `destino` (estoque por padrão).
5. Perguntas realmente necessárias: **o que** e **quanto**; o resto é consequência.

### 3. Estoque
1. O cadastro manual **não é longo**: só `nome` é obrigatório na prática (1 campo visível); o resto está em "Mais detalhes".
2. Deriváveis: `condominio`; parcialmente `unidade` e `categoria_texto` (de compras anteriores).
3. **Não há** suporte a cadastro por documento/importação — `apps/estoque` não tem importação.
4. O estoque funciona melhor como **contexto de operação** (retirar material para manutenção) do que como módulo a explorar; a entrada de menu já é justificada pela operação.

### 4. Manutenções
1. Herdados automaticamente hoje: `titulo`, `descricao_problema`, `responsavel`, `data_prevista` (do prazo), `local`, `equipamento` (`apps/manutencoes/views.py:1381-1396`).
2. Perguntados sem necessidade frequente: `descricao_servico`, `fornecedor`, `custo_previsto` (deveriam aparecer só quando aplicável).
3. Voz/texto: **não há** `data-eithra-ditar` em nenhum template de manutenções — lacuna real.
4. "BSA amanhã às 9": o campo `data_prevista` existe; falta o preenchimento por linguagem natural (o motor de normalização de números falados já existe na Portaria — `apps/portaria/numero_falado.py`).
5. A Manutenção deveria aparecer como **consequência contextual confirmável** da Demanda, não como decisão de módulo.

### 5. Rotinas × Preventivas
1. **Sim, são diferentes**: `RotinaRecorrente` cobre qualquer recorrência (operacional, inspeção, limpeza, preventiva, administrativa, segurança, outra); `PlanoPreventivo` gera **somente** Manutenção preventiva.
2. **Há sobreposição**: campos de contexto e data idênticos, duas bibliotecas de checklist, duas entidades de ocorrência, dois caminhos de geração.
3. **Sim**, a Rotina serve para atividades não ligadas a manutenção — suportado pelos tipos e por `local`/`equipamento`/`fornecedor` opcionais e `gera_manutencao=False` por padrão.
4. Exemplos suportados: limpeza semanal, teste mensal de iluminação de emergência, inspeção trimestral, ronda de segurança, atividades administrativas (`apps/rotinas/models.py:4-5` e `constants.py:25-34`).
5. **Sim, há redundância arquitetural** — detalhada em B.1.
6. O usuário **não precisa** enxergar "Rotinas" como módulo separado; o rótulo "Atividades programadas" (ou absorver visualmente em Preventivas) reduz carga cognitiva.
7. Recomendação: manter internamente e apresentar unificado. **Não excluir nada.**

### 6. Fornecedores
1. **Parcialmente contextualizado** — tem especialidades e vínculos por contrato, mas nenhum vínculo direto a equipamento/local.
2. "Quem atende elevador?" **é respondível** por especialidade (`FornecedorEspecialidade`) ou por contrato (`vigencia_dos_vinculos`, `apps/governanca/consultas.py:20-102`); **não há consulta consolidada**.
3. O cadastro manual **não exige demais**: só `nome_razao_social` é obrigatório; 4 campos visíveis + collapses.
4. Poderiam vir de contratos/documentos: CNPJ, contato, telefone, e-mail, especialidade — hoje são digitados no cadastro e não importados.

### 7. Campo de intenções
1. **O domínio já suporta** boa parte: contrato de interpretação no núcleo, leitura determinística + LLM por exceção, fluxo de confirmação com execução (e-mail cria Demanda/Autorização).
2. **Lacunas**: (a) Demanda não gera automaticamente a próxima ação nem Manutenção/Compra; (b) manutenções/rotinas sem voz; (c) sem leitura multimodal real.
3. **UX**: mostrar a interpretação e conduzir a confirmação; **Domínio**: a ponte Demanda→Compra/Manutenção e o multimodal.
4. **Risco de duplicar inteligência**: os pares `Confianca`/`Estado` (núcleo × demandas), a normalização de texto (núcleo × demandas) e os dois endpoints de transcrição.

### 8. UX
- Excesso de filtros: Demandas (11) e Manutenções (11).
- Formulários longos: Fornecedor (24 campos no modelo) — mitigado por collapses.
- Ações duplicadas: **não identificadas** em Demandas; nas listagens a linha inteira é clicável e há "Mais ações" (Editar / Mudar situação).
- Botões pouco claros: "Mais", "Mais ações", "Ver" (nas listagens).
- Decisões deriváveis: criação de manutenção/compra a partir da Demanda; prioridade.
- Direção desejada já parcialmente presente (progressive disclosure em cadastros, "Mais detalhes" em estoque/compras/fornecedor) — falta aplicar o mesmo padrão às listagens operacionais.

---

## Restrições respeitadas

- Nenhum código alterado, nenhuma migration criada, nenhum teste alterado.
- Nenhum commit ou push.
- Branding, Portaria, Balanços e Contratos não foram tocados.
- Nenhuma recomendação foi implementada.
