# EITHRA V3 — Padrão de Componentes Sneat para Portaria e Demandas

> Documento de padronização para execução futura. Não implementar componentes
> globais nesta rodada.

## 1. Diretriz base

As telas fornecidas pelo Design para **Portaria** e **Demandas** são o **alvo
visual aprovado**. O papel deste documento é traduzir esse alvo para uma
linguagem de componentes reutilizáveis compatível com:

- Sneat;
- Bootstrap 5;
- Boxicons;
- arquitetura atual do EITHRA.

O objetivo não é criar um design system paralelo. O objetivo é **reproduzir o
desenho aprovado com o menor código customizado possível**.

## 2. Princípios de composição

1. O componente nasce do padrão recorrente entre Portaria e Demandas.
2. O componente não altera regra de negócio.
3. O componente não inventa dado para "fechar layout".
4. O componente deve aceitar estados vazios e ausência real de informação.
5. O componente deve preservar rastreabilidade e linguagem humana.

## 3. Mapa de componentes compartilháveis

| Componente | Papel | Uso inicial |
| --- | --- | --- |
| `page-summary-indicators` | indicadores compactos no topo da página | Portaria, Demandas |
| `filter-bar` | busca + filtros + ação de limpar | Portaria, Demandas |
| `operational-table` | grid operacional principal | Portaria, Demandas |
| `record-actions` | ações por linha e `⋮` | Portaria, Demandas |
| `step-wizard` | cabeçalho de etapas em modal | Portaria, Demandas |
| `review-summary` | bloco de revisão/resumo final | Portaria, Demandas |
| `searchable-select` | busca assistida com entrada controlada | Portaria, Demandas |
| `status` | selo, texto ou combinação ícone + texto | Portaria, Demandas |
| `modal-footer-actions` | rodapé com ação principal persistente | Portaria, Demandas |

## 4. `page-summary-indicators`

### Objetivo

Reproduzir os indicadores compactos do Design:

- superfície clara;
- ícone discreto;
- número em destaque;
- legenda curta;
- baixa altura vertical.

### Estrutura esperada

- container horizontal responsivo;
- item com ícone;
- valor principal;
- legenda secundária;
- opcionalmente tom semântico.

### Regras

- não virar card de dashboard;
- não carregar texto longo;
- não misturar ação principal dentro do indicador;
- admitir ausência de cálculo seguro sem inventar regra.

## 5. `filter-bar`

### Objetivo

Padronizar a faixa de busca e filtros logo abaixo do cabeçalho/indicadores.

### Estrutura esperada

- busca principal;
- botão ou agrupamento de filtros;
- filtros alinhados abaixo ou na mesma faixa, conforme densidade do Design;
- ação discreta de limpar filtros.

### Regras

- foco em leitura rápida;
- alinhamento horizontal no desktop;
- adaptação vertical sem poluição no mobile;
- sem fileira confusa de botões concorrentes.

## 6. `operational-table`

### Objetivo

Reproduzir a tabela operacional aprovada no Design, com densidade alta e leitura
imediata.

### Estrutura esperada

- cabeçalho de colunas;
- linhas compactas;
- coluna principal com título e contexto curto;
- colunas laterais comparáveis;
- ações à direita.

### Regras

- não colapsar todas as informações em uma coluna de texto;
- largura das colunas deve ser intencional;
- linha clicável quando fizer sentido, sem competir com ações locais;
- hover, foco e active devem ser previsíveis;
- no mobile, adaptar preservando prioridade de leitura.

## 7. `record-actions`

### Objetivo

Padronizar o comportamento de ações em linhas e cabeçalhos operacionais.

### Regra oficial

- 1 ação: ícone sem borda com tooltip;
- 2 ações: dois ícones sem borda;
- 3 ou mais: menu `⋮`;
- ação crítica: botão textual explícito.

### Uso esperado

- Portaria: verificar, chegou, saiu, mais ações;
- Demandas: ver, editar, concluir quando aplicável, mais ações.

## 8. `step-wizard`

### Objetivo

Reproduzir o cabeçalho de etapas dos modais do Design.

### Etapas previstas

**Portaria**

1. Identificação
2. Confirmação
3. Registro

**Demandas**

1. Descrição
2. Informações
3. Revisão

### Regras

- etapa atual claramente destacada;
- etapas futuras visíveis, mas não dominantes;
- progresso legível sem precisar rolar;
- não substituir por tabs genéricas.

## 9. `review-summary`

### Objetivo

Reproduzir o card de revisão final do Design.

### Conteúdo esperado

- resumo do registro;
- blocos de dados principais;
- seção de origem/evidências quando aplicável;
- leitura limpa antes da ação final.

### Regras

- não mostrar JSON;
- não expor score técnico;
- não esconder o dado principal atrás de acordeão;
- preservar a descrição original quando ela for relevante para a decisão.

## 10. `searchable-select`

### Objetivo

Padronizar campos como:

- empresa do prestador na Portaria;
- local e responsável em Demandas;
- demais escolhas pesquisáveis com entrada controlada.

### Regras

- respeitar acessibilidade por teclado;
- aceitar opção cadastrada quando existir;
- aceitar digitação livre apenas quando o domínio permitir;
- diferenciar claramente dado cadastrado de texto livre quando isso afetar o
  backend.

## 11. `status`

### Objetivo

Padronizar a expressão visual de estados operacionais.

### Regras

- usar o mesmo vocabulário visual entre selo, texto e ícone;
- não multiplicar estilos diferentes para o mesmo significado;
- estado deve ser legível sem depender só de cor;
- tons devem seguir os tokens atuais do EITHRA.

### Exemplos

- Portaria: `Esperado`, `Presente`, `Sem autorização`;
- Demandas: `Em aberto`, `Em andamento`, `Vencida`, `Concluída`.

## 12. `modal-footer-actions`

### Objetivo

Garantir rodapé consistente em modais de fluxo.

### Estrutura esperada

- ação secundária à esquerda ou recuada;
- ação principal destacada;
- permanência visível durante o uso;
- sem poluição de múltiplos botões equivalentes.

### Regras

- manter o CTA alinhado com a etapa;
- respeitar o modelo `Voltar / Continuar` e `Cancelar / Registrar`;
- não esconder a ação principal por scroll.

## 13. Aplicação prevista em Portaria

| Área | Componente principal |
| --- | --- |
| Indicadores | `page-summary-indicators` |
| Busca e ações | `filter-bar` adaptado para barra operacional |
| Movimentação atual | `operational-table` |
| Ações por linha | `record-actions` |
| Modal de chegada/saída | `step-wizard` + `searchable-select` + `modal-footer-actions` |
| Etapa final | `review-summary` |

## 14. Aplicação prevista em Demandas

| Área | Componente principal |
| --- | --- |
| Indicadores | `page-summary-indicators` |
| Busca e filtros | `filter-bar` |
| Tabela principal | `operational-table` |
| Ações por linha | `record-actions` |
| Wizard de registro | `step-wizard` + `searchable-select` + `modal-footer-actions` |
| Revisão final | `review-summary` |
| Situação/prioridade/prazo | `status` |

## 15. Critério de extração futura

Um padrão só deve virar componente compartilhado quando:

- existir em Portaria e Demandas com estrutura próxima;
- a variação for de conteúdo, não de desenho-base;
- o reaproveitamento reduzir duplicação sem forçar abstração excessiva;
- a versão extraída continuar fiel ao Design aprovado.

## 16. O que não fazer agora

Nesta auditoria, **não**:

- criar biblioteca nova;
- refatorar templates globais;
- mover código para pacote de UI;
- reescrever módulos inteiros;
- aplicar mudanças fora do escopo de Portaria e Demandas.

## 17. Conclusão

O Design aprovado pode ser implementado dentro do Sneat sem criar um novo
sistema visual. O caminho mais seguro é consolidar esses padrões como blocos
reutilizáveis, extraídos a partir das necessidades reais de Portaria e
Demandas, sempre preservando fidelidade visual e regras do domínio.
