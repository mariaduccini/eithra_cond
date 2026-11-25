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
