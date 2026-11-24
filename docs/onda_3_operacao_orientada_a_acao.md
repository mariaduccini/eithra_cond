# ONDA 3 — OPERAÇÃO ORIENTADA A AÇÃO

Registro das FASES 23 e 24. O princípio que rege a onda é o mesmo da
constituição do produto
(`docs/constituicao_produto_ux_arquitetura_eithra.md`): a Home deixa de ser
navegação por módulos e passa a responder à pergunta *o que precisa de mim
agora?*, projetando os objetos que já existem — sem copiar nada para uma
tabela nova.

---

## FASE 23 — HOME DA SUPERVISÃO

### Status

**CONCLUÍDA.**

### Objetivo

Substituir a navegação por módulos pela pergunta `O que precisa de mim agora?`,
projetando na Home os objetos já existentes.

### Já existia (investigação antes de implementar)

A Home (`apps/core/views.py::home` e `templates/pages/home.html`) já respondia
parcialmente à pergunta, com duas origens que não se misturam:

- **manutenções** — lidas por `apps.manutencoes.atencao.obter_atencoes_de_manutencoes`;
- **mensagens que pedem decisão** — lidas por `apps.core.email_acoes.pendencias_para`.

Faltava projetar as **demandas em aberto**, que já tinham leitura pronta no
próprio domínio:

- `apps.demandas.models.DemandaQuerySet` — `abertas()`, `vencidas()`,
  `vencendo_hoje()`, `proximas()`, `sem_prazo()`, `urgentes()`, `minhas()`,
  `na_ordem_de_atenção()`;
- `apps.demandas.permissoes.condominios_para_ver(usuario)` — o escopo que a
  pessoa enxerga, pela mesma regra de vínculo ativo usada em todo o sistema.

Compromissos, retornos e autorizações **não existem** como objetos de primeira
classe (ver FASE 24). Não foram inventados.

### Alterado

- `apps/core/views.py` — `home()` passou a projetar as demandas em aberto no
  escopo visível, já na ordem de atenção do domínio, mostrando o topo
  (`LIMITE_DE_DEMANDAS_NA_HOME = 5`) e um item a mais apenas para saber se há
  excedente. Novo helper `_linha_de_demanda()` (apresentação apenas).
- `templates/pages/home.html` — bloco "Demandas" entre "Hoje" e "Mensagens que
  pedem uma decisão". Lista (não tabela), item inteiro clicável para o detalhe,
  "Ver todas" para `demandas:listagem` quando há excedente.

### Não alterado

- Nada foi copiado para tabela nova; nenhum model criado; nenhuma migration.
- A fonte única da atenção de manutenções (`apps.manutencoes.atencao`) segue
  intacta; a Home não recalcula atraso, conferência nem prioridade.
- O domínio de demandas não foi tocado — a Home só consome as leituras que já
  existiam.

### Arquivos

| Arquivo | Motivo |
| --- | --- |
| `apps/core/views.py` | Projetar demandas em aberto na Home |
| `templates/pages/home.html` | Bloco "Demandas" |
| `apps/core/tests/test_home_demandas.py` | Testes focados (novo) |

### Models / migrations / dados

Nenhum model, nenhuma migration, nenhuma escrita de dados. A Home é projeção.

### Testes executados

| Comando | Resultado |
| --- | --- |
| `.\manage-local.ps1 check` | System check identified no issues (0 silenced) |
| `.\manage-local.ps1 makemigrations --check --dry-run` | No changes detected |
| `.\test-local.ps1 apps.core.tests.test_home_demandas apps.core.tests.test_home_atencao` | 31 testes OK |
| `.\test-local.ps1 apps.core.tests` | 424 testes OK |

Cobertura nova: demanda em aberto aparece; concluída/cancelada/desativada não
aparecem; sem demanda o bloco não é desenhado; item aponta para o detalhe;
contexto traz condomínio, local e prazo; urgente e vencida vêm antes da comum;
isolamento por condomínio; limite de cinco com "Ver todas"; consultas não
crescem com o número de demandas.

### Pendências humanas

Nenhuma nesta fase.

### Riscos e limitações

- A Home mostra o **topo** da ordem de atenção; a lista completa continua a um
  clique. É deliberado: a Home não vira painel.
- O prazo aparece como a data registrada, **sem juízo de atraso** — a Home não
  recria a classificação "vencida / vence hoje / próxima" que a listagem de
  demandas já faz. Se a Home precisar desse destaque, a regra deve ser
  reutilizada do domínio, não reescrita aqui.

---

## FASE 24 — HOME DA SÍNDICA

### Status

**CONCLUÍDA — versão baseada nas exceções atualmente disponíveis.**

### Objetivo

Responder, para quem responde pelo condomínio, `O que precisa da decisão ou
atenção da síndica agora?` — mostrando **somente exceções**, sem repetir toda a
operação da supervisão (que já tem a Home da FASE 23).

### O que a investigação encontrou

A fase foi entregue como **projeção** das exceções que o domínio já sabe
derivar, e não como um fork de Home por cargo. Cada exceção tem lastro em dado
ou relação existente:

| Exceção mostrada | Deriva de |
| --- | --- |
| Urgente | `prioridade == URGENTE` |
| Risco identificado | `risco` em {segurança, incêndio, elétrico, estrutural, saúde} |
| Aguardando aprovação | `aguardando_tipo == APROVACAO` (conceito já existente no domínio) |
| Fora do prazo | `prazo` anterior a hoje, com os dias de atraso |
| Sem próxima ação | em andamento ou aguardando, e `proxima_acao` vazio |

Uma demanda gera no máximo **uma** exceção, escolhida pela precedência — a
pessoa lê um motivo, não quatro. A leitura é inteiramente derivada: **nada é
gravado**, e nenhuma tabela de decisão foi criada.

### Fontes futuras ainda não incorporadas

Registradas para quando existirem de verdade — sem que a fase fique bloqueada
por isso:

- **Escalonamento** — não há noção de escalonamento em `apps/demandas`;
- **Decisão financeira** — `apps/balancos` trata importação/conferência, não
  decisão; `apps/estoque` (`StatusCompra`) não tem estado de aprovação;
- **Comunicação pendente** — `StatusComunicacao` tem só `RASCUNHO`/`PRONTA`;
- **Reincidência relevante** — o campo `recorrencia` foi **removido** de
  `Demanda` (migration 0005); reconstruí-la do histórico seria outra conta, não
  esta leitura;
- **Pendências explicitamente atribuídas à síndica** — não existem como objeto.

Quando qualquer uma virar objeto de primeira classe, entra aqui como mais uma
fonte, pela mesma leitura derivada — por acréscimo, nunca por reescrita.

### Ações

Só aparece o que o domínio já oferece: cada exceção leva ao **contexto da
demanda** (`demandas:detalhe`), pela ação "Ver o contexto". Aprovar, encaminhar,
comunicar e resolver pendência **não** viraram botões porque ainda não há
conceito real de aprovação, encaminhamento ou pendência no domínio — botão
fictício é pior que ausência de botão.

### Arquitetura reutilizada

`apps/demandas/excecoes.py` espelha o desenho de `apps/manutencoes/atencao.py`:
leitura derivada, dataclass imutável, `TextChoices` + tabela de precedência, e
reuso das permissões do domínio (`condominios_para_ver`). Nenhuma tabela nova,
nenhum model novo, nenhuma migration. O escopo é o de **gestão**
(ADMINISTRADORA/SÍNDICA), via `pode_gerenciar_em_algum_condominio` — quem opera
ou supervisiona não recebe o bloco, porque não é a ele que a decisão cabe.

### Arquivos

| Arquivo | Motivo |
| --- | --- |
| `apps/demandas/excecoes.py` | Leitura derivada das exceções (novo) |
| `apps/demandas/permissoes.py` | `condominios_para_gerenciar` / `pode_gerenciar_em_algum_condominio` |
| `apps/core/views.py` | `home()` projeta as exceções no topo, só para quem gerencia |
| `templates/pages/home.html` | Bloco "Precisa da sua decisão" |
| `apps/core/tests/test_home_excecoes.py` | Testes focados (novo) |

### Models / migrations / dados

Nenhum model, nenhuma migration, nenhuma escrita de dados. A Home é projeção.

### Testes executados

| Comando | Resultado |
| --- | --- |
| `.\manage-local.ps1 check` | System check identifies no issues (0 silenced) |
| `.\manage-local.ps1 makemigrations --check --dry-run` | No changes detected |
| `.\test-local.ps1 apps.core.tests.test_home_excecoes` | 21 testes OK |

Cobertura: cada tipo de exceção derivado do campo correto; recém-aberta e
encerrada não geram exceção; precedência (urgente > risco > prazo; risco >
prazo); uma demanda = uma exceção; maior atraso primeiro; o bloco aparece só
para quem gerencia (OPERACIONAL e SUPERVISORA não recebem); isolamento por
condomínio; sem exceção o bloco não é desenhado; o rótulo é humano, sem código
técnico; máximo de cinco com "Ver todas".

### Pendências humanas

Nenhuma que bloqueie a fase. As fontes futuras acima ficam como evolução.

### Riscos e limitações

- A definição de "exceção" usa **só o que existe hoje**. Quando surgirem
  aprovação, escalonamento e decisão financeira de verdade, a lista desta tela
  cresce por acréscimo.
- **Reincidência não entra** porque o dado não existe mais no modelo. Registrar
  isso é parte da entrega: não foi improvisada uma conta de recorrência.
- A tela não tem, por ora, nenhum dos tipos de decisão persistidos no domínio.
  Quando existirem, a ação sugerida (`_ACAO_SUGERIDA`) já aponta o conceito;
  falta apenas o botão real, que só nasce com o conceito.
