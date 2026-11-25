# Relatório da noite — fila de 24 para 25/11/2026

## Resumo final

_(preenchido ao encerrar a fila, conforme a regra 11)_

## Registro por tarefa

### T2. Obrigações e vencimentos (governança)

- **Tarefa:** T2 — tela única de vencimentos (contratos, garantias, documentos).
- **Commit:** ver `git log noite-25-11` para o hash desta entrega (o hash do
  commit corrente não pode constar dentro do próprio arquivo).
- **Testes rodados e resultado:**
  - `.\test-local.ps1 --noinput --keepdb apps.governanca apps.core.tests.test_home_financeiro`
    → 147 testes, OK.
  - `.\test-local.ps1 --noinput --keepdb apps.cadastros.tests.test_views.MenuTests apps.cadastros.tests.test_views.AgrupadoresDeNavegacaoTests`
    → 8 testes, OK.
- **Decisões tomadas:**
  - A faixa de cada vencimento é lida da data diante de hoje, com os cortes
    fixos que a especificação pediu (vencido / até 30 dias / até 90 dias / em
    dia). As leituras reaproveitam `ativos()` e `do_condominio()` de cada
    queryset; os buckets "vencido" equivalem exatamente a `vencidos()` /
    `vencidas()` dos models.
  - A leitura foi posta em `apps/governanca/vencimentos.py` para ser reusada
    pela tela e pelo painel, sem duplicar regra e sem novo modelo/migração.
  - O bloco do painel aparece só para quem responde pelo condomínio
    (`condominios_para_gerenciar` = ADMINISTRADORA e SINDICA); a supervisora
    entra na tela, mas não recebe o bloco.
  - O item de menu "Vencimentos" entrou como filho no topo do grupo de
    Contratos/Garantias/Documentos. O ícone `bx-calendar-exclamation` foi
    declarado, ainda que os filhos do grupo não renderizem ícone hoje.
- **Pendências:** nenhuma dentro do escopo. O arquivo-bandeja da fila está fora
  do repositório e não foi alterado (regra 8 prevalece sobre a regra 10 quanto a
  arquivos fora do repositório); o estado de cada tarefa fica registrado aqui.

### T3. "Meu dia" para colaboradores

- **Tarefa:** T3 — tela única `/meu-dia/` com o que a pessoa logada precisa fazer
  hoje (rotinas, manutenções e demandas), mais o bloco na home e o item de menu.
- **Commit:** ver `git log noite-25-11` para o hash desta entrega (o hash do
  commit corrente não pode constar dentro do próprio arquivo).
- **Testes rodados e resultado:**
  - `.\test-local.ps1 --noinput --keepdb apps.core.tests.test_meu_dia`
    → 16 testes, OK.
  - Regressão direcionada (navegação + home, sem rodar a suíte completa de um
    app): `apps.core.tests.test_shell`, `...test_home_supervisao`,
    `...test_home_atencao`, `...test_home_demandas`, `...test_home_excecoes`,
    `...test_home_financeiro` e `apps.condominios.tests.test_acesso_global`
    → 140 testes, OK.
- **Decisões tomadas:**
  - A leitura do "meu dia" foi posta em `apps/core/meu_dia.py` e é reusada pela
    tela e pelo bloco da home, sem modelo novo nem migração. Cada origem usa as
    permissões de leitura já existentes do módulo (`condominios_para_ver` de
    rotinas, manutenções e demandas) e os querysets `minhas()`/`abertas()`/
    `do_condominio()`/`pendentes()` de cada domínio — nenhuma regra de situação
    nasce aqui.
  - Visibilidade do menu/home pelo vínculo ativo com os papéis da especificação
    (`condominios_com_papel` = SUPERVISORA, OPERACIONAL, PORTEIRO). Como as
    permissões de leitura dos módulos não incluem PORTEIRO, o porteiro enxerga o
    item e a tela, mas sem itens — exatamente o que a especificação pede ("use as
    permissões de leitura já existentes de cada módulo para filtrar").
  - Manutenção sem data prevista é lida como de hoje (é minha e está em aberto);
    a demanda sem prazo vencido fica de fora. A data de referência da demanda é a
    mais urgente entre `prazo_proxima_acao` e `prazo`.
  - O bloco da home mostra só os 3 primeiros itens (`LIMITE_DO_MEU_DIA_NA_HOME`),
    com "Ver tudo"; o condomínio em vista é resolvido pelo mesmo helper da home
    (`_condominio_da_home`), conferido contra a lista autorizada.
- **Pendências:** nenhuma dentro do escopo. O arquivo-bandeja da fila está fora
  do repositório e não foi alterado (regra 8 prevalece sobre a regra 10 quanto a
  arquivos fora do repositório), mantendo o precedente da T2; o estado de cada
  tarefa fica registrado aqui.
