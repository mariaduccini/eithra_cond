# Fase 21.10A — Preparação da homologação da Portaria

Checklist do que **ainda depende de olho humano no navegador**.

Esta fase não declara homologação visual. Ela automatiza o que dava para
automatizar e separa, aqui, apenas o que nenhum teste prova: aparência,
captura real de áudio/foto e comportamento de layout.

- Banco: `localhost / eithra_dev` (testes rodam em banco de teste isolado)
- Suíte: `apps.portaria` — 207 testes, OK
- `manage.py check` — 0 issues
- `makemigrations --check --dry-run` — no changes detected

---

## 1. HOMOLOGAÇÃO HUMANA PENDENTE

Só estes itens precisam de navegador. Todos os demais já têm teste.

| # | Item | Por que só no navegador | Como verificar |
|---|------|--------------------------|----------------|
| 1 | Layout da lista de destino (`andar \| unidades`) | É visual: teste não "vê" layout | Abrir Passo 1; conferir uma coluna por andar (rótulo à esquerda, unidades reais à direita) ocupando toda a largura, sem imagem nem coluna decorativa |
| 2 | Estado da unidade **902 selecionada** | Contraste/halo são percepção | Selecionar 902; confirmar que a seleção é inequívoca |
| 3 | Ordem descendente a partir do 16º | Posição visual de abertura | Abrir Passo 1; confirmar que começa no 16º e desce |
| 4 | Rolagem única no mapa (sem duas scrollbars) | Só existe na renderização | Confirmar **uma** barra de rolagem na região de unidades |
| 5 | Progressive disclosure do Passo 2 | O clique é comportamento de tela | Escolher Visitante; confirmar que motivo/objeto/instrução **não** aparecem até serem tocados |
| 6 | Contexto do Passo 3 (destino + tipo + quem chegou) | Leitura de tela | Preencher nome e tipo; confirmar que o topo do Passo 3 mostra os três |
| 7 | Observação por voz no fim do Passo 3 | Ordem/posição na tela | Confirmar que a observação fica **antes** do resumo e **depois** dos passos |
| 8 | Ditado real (voz) preenchendo o campo | `MediaRecorder` + permissão são do navegador | Ditar nome, empresa, observação e destino; confirmar o texto no campo |
| 9 | Microfone só aparece se o navegador gravar | Capacidade do navegador | Em navegador sem gravação, confirmar que o microfone não se oferece e o texto continua inteiro |
| 10 | Captura de foto (câmera/galeria) | Hardware + permissão | Anexar foto no Passo 2; confirmar pré-visualização e envio |
| 11 | Modal cabe confortavelmente em desktop | Sensação de formulário longo | Abrir o registro em 1440px; confirmar que não parece formulário longo |
| 12 | Uma ação primária por momento | Hierarquia visual | Percorrer os 3 passos; confirmar 1 CTA primário por passo |
| 13 | Sem áreas vazias / excesso de cards na tela principal | Compositivo | Abrir a tela principal; conferir densidade |

> Itens 1–7 e 11–13 são do fechamento visual (Fases 21.5–21.8).
> Itens 8–10 são de captura (voz/foto) em ambiente real.

---

## 2. AUTOMATIZADO — NÃO precisa de humano

Jornadas da Fase 21.10A já cobertas por teste, com a classe responsável.

| Jornada | Classe / teste |
|---------|----------------|
| Chegada não prevista | `PortariaFormulariosTests`, `PortariaViewsTests` |
| Chegada prevista | `PortariaEsperadosTests.test_ciclo_completo_da_visita` |
| Visitante | `PortariaFormulariosTests` (tipos + wizard) |
| Prestador | `test_prestador_registra_sem_pessoa_permanente`, `test_prestador_de_unidade_mantem_a_unidade` |
| Fornecedor | `test_fornecedor_e_tecnico_registram_sem_pessoa_permanente` **(novo)** |
| Delivery | `PortariaEntregasTests.test_delivery_guarda_a_instrucao_de_entrega` |
| Entrada com foto | `test_fotos.FotoValidaTests` |
| Entrada sem foto | `test_fotos.FotoOpcionalTests` |
| Saída | `PortariaViewsTests.test_saiu_fecha_a_presenca` |
| Voltar/avançar | `test_o_voltar_nao_reconstroi_o_que_ja_foi_escolhido`, `test_erro_reabre_o_wizard_no_passo_do_erro` |
| Nova chegada sem herdar contexto | `test_a_proxima_chegada_comeca_sem_o_destino_anterior` |
| Nome + empresa sem sobrescrita | `PortariaAudioTests.test_nome_e_empresa_do_mesmo_formulario_nao_se_sobrescrevem` |
| Destino por voz | `PortariaDestinoPorVozTests` |
| Autorizador real | `PortariaAutorizadorTests` |
| Resultado de autorização | `PortariaVerificacaoTests` **(novo)**, `PortariaEntregasTests`, `test_interpretacao` |

---

## 3. O que foi acrescentado nesta fase

Testes faltantes com cobertura real (sem duplicar o que já existia):

- `PortariaFormulariosTests.test_fornecedor_e_tecnico_registram_sem_pessoa_permanente`
  — antes, só `prestador` era enviado ponta a ponta; `fornecedor` e `tecnico`
  apareciam apenas na lista de escolhas.
- `PortariaVerificacaoTests` (6 testes) — o fluxo `verificar` (concluir uma
  chegada que ficou aguardando) não tinha **nenhum** teste. Cobre os quatro
  desfechos (`autorizado` abre a passagem; `nao_autorizado` e `sem_resposta`
  encerram sem abrir), a exigência do meio, a recusa de reabrir uma passagem já
  aberta e o isolamento por condomínio.
