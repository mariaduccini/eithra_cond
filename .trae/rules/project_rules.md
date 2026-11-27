# Regras do projeto Eithra (valem para toda conversa)

## Como trabalhar
1. Execute o prompt. As decisões de produto já foram tomadas. Não reabra decisões e não compare alternativas.
2. **Se algo ficar em aberto, decida em até 2 frases:** escolha a opção mais simples, que mexe em menos arquivos, implemente e registre em uma linha no resumo final ("Decidi: …"). Nunca delibere mais que isso. Pare para perguntar só se for impossível continuar.
3. **Um teste antigo que contradiz o pedido deve ser atualizado** para o novo comportamento, e isso é registrado no resumo. Não procure jeitos de agradar os dois.
4. **Leia só os arquivos que vai editar** e, no máximo, um teste de cada módulo mexido. Use busca (grep) em vez de abrir arquivos inteiros. Não releia o que já leu.
5. **Depois de um resumo automático da conversa:** não releia tudo. Rode `git status` e `git diff --stat` e continue do ponto em que parou.
6. **Pense e escreva em português, com frases curtas.** Nada de rascunho longo antes de editar: edite cedo e deixe os testes mostrarem os problemas.
7. **Testes:** rode só os módulos indicados, sempre com `.\test-local.ps1 --noinput --keepdb apps.<modulo>`. Nunca rode a suíte inteira. Falhas fora dos módulos indicados já existiam: não corrija.
8. **Comandos longos:** espere o resultado. Não deixe teste rodando em segundo plano enquanto faz outra coisa.
9. **Vários pedidos na mesma conversa: um de cada vez, na ordem.** Termine o pedido atual com testes, commit e push. Só então comece o próximo: rode `git status` (tem que estar limpo) e `git log -1 --oneline`, e siga sem pedir licença. Cada pedido tem o seu commit e o seu resumo; nunca junte dois pedidos num commit só.

## Git
- Antes de começar, confira com `git branch --show-current` que está em `main`. Se não estiver, pare e avise.
- Use `git add` só com caminhos explícitos. Nunca `-A` nem `.`.
- Nunca faça commit de `.env*`, backups, `*.bak*`, `.screenshots/`, `.tmp_render/`, `local_data/`, `manage-local.ps1`, `tmp_render.py`, `acesso-remoto.ps1` ou PDFs reais.
- No fim: commit com a mensagem do prompt, `git push origin main` e um resumo de até 8 linhas (arquivos, testes, resultado, decisões tomadas).

## Mapa rápido (para não explorar o projeto)
- Stack: Django 5.2 e PostgreSQL local; o ambiente é `.env.local` (padrão). Templates em `templates/pages/<app>/`. CSS em `static/css/eithra.css`.
- **Painel (home):** `apps/core/views.py` (função `home`) e `templates/pages/core/home.html`. A atenção das manutenções está em `apps/manutencoes/atencao.py`; a supervisão em `apps/core/supervisao.py`; o "Meu dia" em `apps/core/meu_dia.py`.
- **Permissões:** `apps/<app>/permissoes.py`. Os papéis ficam em `apps/condominios/constants.py` (Papel). Quem gerencia é ADMINISTRADORA e SINDICA.
- **Balanços:** os leitores são `extrator_atual.py` e `extrator_legado.py`; correções manuais em `correcoes.py`; categorias em `categorias.py`; privacidade em `privacidade.py`.
- **Governança:** contratos, garantias e documentos estão em `apps/governanca/`. Os vencimentos estão em `vencimentos.py`; para criar, use `services.criar_contrato`.
- **Voz:** `apps/core/audio.py` (transcrição). Texto e IA: `apps/core/llm.py`. OCR local: `apps/estoque/leitura_compra.py` (`ocr_disponivel`, `_motor_ocr`).
- **Testes que vigiam frases e estrutura** (ao mudar texto da home, confira só estes): `apps/core/tests/test_home_atencao.py` (FonteUnicaDaHome), `test_arquitetura.py`, `test_home_supervisao.py`, `test_home_demandas.py`.
- **Visual:** as ações são ícones boxicons sem borda, com tooltip, aria-label e 44px de área de toque. Cores: lilás `#8e7ab5`, petróleo `#36545b`, botão `#176f73`. Fonte Public Sans. Texto com no mínimo 13px.
