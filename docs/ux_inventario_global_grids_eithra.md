# EITHRA V3 — Inventário Global de UX, Grids e Fidelidade Visual

> Documento de auditoria. Não autoriza implementação global, mudança de regra de
> negócio, alteração de model, migration, commit ou push.

## 1. Diretriz corrigida

As telas fornecidas pelo Design para **Portaria** e **Demandas** passam a ser
tratadas como o **alvo visual aprovado de implementação do EITHRA**.

Isto significa que, nas próximas rodadas de execução:

- a composição visual não deve ser reinterpretada livremente;
- a estrutura deve seguir a referência;
- o Sneat deve ser usado para reproduzir esse desenho com o menor código
  customizado possível;
- quando houver dúvida visual, a referência do Design prevalece.

Esta diretriz afeta diretamente:

- estrutura de página;
- grids e tabelas;
- filtros e indicadores;
- modais e wizard de etapas;
- hierarquia visual;
- posicionamento de ações;
- densidade, espaçamento e proporção.

## 2. Escopo deste inventário

Este documento registra:

- o alvo visual aprovado;
- o que Portaria e Demandas precisam reproduzir;
- os desvios relevantes entre o estado auditado e o alvo;
- os pontos que podem ser implementados sem distorcer o domínio;
- os componentes compartilháveis que deverão orientar a execução futura.

## 3. Portaria — alvo visual aprovado

### 3.1 Estrutura obrigatória

A tela de Portaria deve convergir para a composição do Design:

1. `Sidebar` enxuta com identidade EITHRA no topo, item ativo destacado e saída
   na parte inferior.
2. Cabeçalho com `Portaria`, texto auxiliar e contexto do usuário à direita.
3. Barra operacional com:
   - busca ampla à esquerda;
   - `Registrar chegada`;
   - `Registrar saída`.
4. Três indicadores compactos.
5. Tabela de `Movimentação atual` com densidade operacional.
6. Ações contextuais por linha, com `⋮` quando necessário.

### 3.2 Regra de fidelidade

Portaria **não** deve voltar para cards empilhados como estrutura principal da
visão desktop. A imagem aprovada estabelece:

- tabela como forma principal da movimentação atual;
- indicadores pequenos, não cards de dashboard;
- barra de operação horizontal, não blocos soltos;
- modal em wizard de três etapas, não formulário longo.

### 3.3 Regras funcionais que continuam obrigatórias

Mesmo com a reprodução visual da referência, o domínio não pode ser distorcido:

- autorização não é entrada;
- registrar entrada cria presença física;
- registrar saída encerra presença;
- o EITHRA não autoriza;
- o porteiro registra autorização recebida externamente;
- `Sem resposta` não equivale a `Não autorizado`;
- troca de turno não encerra presença.

## 4. Portaria — leitura do estado auditado

Com base na auditoria corrente, Portaria já tem decisões corretas de domínio e
alguns acertos de linguagem operacional, mas ainda não está alinhada ao alvo
visual final porque:

- a organização recente privilegiou listas abertas e seções operacionais;
- o mockup aprovado pede tabela compacta como estrutura principal da área
  central;
- o modal precisa reproduzir explicitamente o wizard visual de três etapas;
- a barra superior operacional ainda deve ser tratada como peça principal da
  página, não apenas como conjunto de ações.

Conclusão de auditoria: **o domínio atual é reaproveitável, mas a composição
visual precisa convergir diretamente para o Design.**

## 5. Demandas — alvo visual aprovado

### 5.1 Estrutura obrigatória

A tela de Demandas deve convergir para a composição do Design:

1. Título e descrição curta.
2. Botão `Registrar demanda`.
3. Quatro indicadores compactos.
4. Barra de busca.
5. Botão de filtros.
6. Faixa de filtros organizada abaixo.
7. Tabela operacional.
8. Paginação.

### 5.2 Wizard obrigatório para registrar demanda

O fluxo visual aprovado passa a ser:

1. **Descrição** — `Descreva o que aconteceu`.
2. **Informações** — `Informações sugeridas pelo EITHRA`.
3. **Revisão** — resumo claro antes do registro.

Isso substitui qualquer interpretação minimalista que elimine:

- stepper;
- card-resumo;
- área de dicas;
- bloco de evidências/origem;
- organização visual por etapas.

### 5.3 Detalhe obrigatório

O detalhe da demanda deve convergir para:

- topo com voltar, `Detalhe da demanda`, identificador, título, prioridade e
  situação;
- abas `Visão geral`, `Histórico`, `Anexos`, `Relacionadas`;
- bloco principal de descrição;
- bloco lateral ou secundário de informações operacionais;
- rodapé de ações com uma ação principal contextual, ação de conclusão quando
  suportada e `Mais ações`.

## 6. Demandas — leitura do estado auditado

Com base na auditoria corrente, Demandas evoluiu corretamente no sentido do
acompanhamento operacional, mas o estado mais recente ainda diverge do alvo
visual final porque:

- a listagem foi simplificada para linhas compactas, enquanto o Design aprovado
  pede tabela operacional;
- os indicadores compactos ainda não estruturam a abertura da página;
- os filtros precisam aparecer como barra organizada sob a busca;
- o registro foi simplificado em dois tempos, mas o alvo visual aprovado é um
  wizard explícito de três etapas;
- o detalhe foi reorganizado em seções úteis, porém ainda precisa convergir para
  a composição com topo rico, abas e rodapé de ações.

Conclusão de auditoria: **o raciocínio funcional atual é aproveitável, mas a
expressão visual futura deve seguir o Design de forma mais fiel.**

## 7. Implicações obrigatórias para grids

### 7.1 Portaria

Tabela com colunas conceituais:

- Nome;
- Tipo;
- Unidade / destino;
- Horário;
- Situação;
- Ação.

Cada linha deve permitir:

- identificação imediata;
- leitura rápida do estado;
- uma ação principal clara;
- ações secundárias em `⋮` quando necessário.

### 7.2 Demandas

Tabela com colunas conceituais:

- identificador amigável;
- Demanda;
- Local;
- Prioridade;
- Prazo;
- Responsável;
- Situação;
- Ações.

A coluna `Demanda` deve admitir:

- título;
- texto contextual curto abaixo.

Não concentrar todas as informações em uma coluna única de texto empilhado.

## 8. Implicações obrigatórias para modais

### 8.1 Portaria

O modal futuro deve reproduzir o wizard do Design:

- `Identificação`;
- `Confirmação`;
- `Registro`.

### 8.2 Demandas

O modal futuro deve reproduzir o wizard do Design:

- `Descrição`;
- `Informações`;
- `Revisão`.

Em ambos os casos:

- o cabeçalho deve mostrar claramente a etapa;
- o rodapé deve manter a ação principal visível;
- a largura e a densidade devem seguir a proporção da referência;
- o conteúdo não deve virar formulário administrativo extenso.

## 9. Componentes compartilhados a extrair na implementação futura

Os seguintes blocos aparecem como padrão de implementação futura e devem ser
tratados como candidatos a componentes reutilizáveis:

- `operational-table`
- `page-summary-indicators`
- `filter-bar`
- `record-actions`
- `step-wizard`
- `review-summary`
- `searchable-select`
- `status`
- `modal-footer-actions`

Nesta rodada, eles são apenas registrados como alvo de padronização.

## 10. Riscos de distorção a evitar

Durante a implementação futura, evitar:

- transformar mockup em "versão adaptada" sem necessidade;
- trocar tabela aprovada por lista só porque a lista já existe;
- simplificar wizard em formulário único só por conveniência técnica;
- usar card grande onde o Design pede indicador compacto;
- esconder ações relevantes em excesso;
- inventar dados para preencher indicador, aba ou resumo;
- priorizar a composição existente do código sobre a composição aprovada pelo
  Design.

## 11. Critério de aceite visual para as próximas execuções

Uma tela só deve ser considerada aderente quando estiver próxima da referência
em:

- estrutura geral;
- distribuição dos elementos;
- densidade;
- hierarquia;
- espaçamentos;
- largura das colunas;
- altura das linhas;
- posição das ações;
- largura e organização do modal;
- comportamento desktop e mobile.

## 12. Conclusão

Fica formalizado, para Portaria e Demandas, que o Design fornecido é o **alvo
visual aprovado**. A execução futura deve preservar o domínio real do EITHRA,
mas reproduzir a experiência visual da referência com alta fidelidade dentro da
arquitetura Sneat + Bootstrap + Boxicons.
