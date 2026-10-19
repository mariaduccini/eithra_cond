# Fase 2C — Estoque e Compras Operacionais: Modelagem Proposta

> **Status**: proposta de modelagem, para autorização. **Nenhuma migration foi
> gerada** (`makemigrations` não foi executado), **nada foi aplicado** ao banco
> e **nenhum código de implementação foi escrito**.
>
> **O que não entrou aqui**: OCR, IA, leitura automática de nota ou cupom,
> integração fiscal (NF-e, XML, SEFAZ), conciliação automática com balanço,
> cotação, aprovação de compra, pedido formal, contrato, multi-almoxarifado,
> lote complexo, inventário por código de barras, painel "Hoje", transparência,
> Fase 1I, WhatsApp, Gmail e notificações. Nada disso foi iniciado.

## 1. Princípio que governa a fase

**"O saldo não é digitado. O saldo é consequência das movimentações."**

Não existe campo de saldo em lugar nenhum. O que existe é o registro do que
entrou e do que saiu; o saldo é a leitura desse conjunto. Isso mantém a
rastreabilidade: qualquer número na tela pode ser explicado pelas movimentações
que o produziram.

## 2. Onde o código mora

**Recomendação: um único app `apps/estoque`** (verbose name *"Estoque e
Compras"*), com os quatro models. Motivo: `MovimentacaoEstoque` aponta para
`ItemCompra`, e `ItemCompra` aponta para `ItemEstoque`. Manter o grafo de FKs
dentro de um app evita dependência circular entre apps e mantém uma migration
inicial coerente. O menu continua com **dois itens** (Estoque e Compras),
servidos pelo mesmo app — como as telas de manutenção fazem hoje.

```
apps/estoque/
  constants.py      # enums e conjuntos entrada/saída
  models.py         # ItemEstoque, MovimentacaoEstoque, Compra, ItemCompra
  services.py       # criar_item, registrar_movimento, confirmar_compra, cancelar_compra
  permissoes.py     # a autorização, centralizada
  forms.py          # item, filtros, compra, itens da compra, movimentos
  views.py  urls.py  admin.py
  migrations/0001_initial.py   # a gerar somente após autorização
  tests/
templates/pages/estoque/
  itens.html  item_detalhe.html  compras.html  compra_detalhe.html
```

## 3. Enums

Ficam em `apps/estoque/constants.py` — o vocabulário só diz respeito a este
domínio (não é vocabulário compartilhado como `Prioridade`, que vive em
`apps.core.constants`).

### UnidadeMedida
`unidade` · `caixa` · `pacote` · `metro` · `litro` · `quilo` · `rolo` · `par` ·
`jogo` · `outra`

Catálogo curto de propósito. Quantidades são `Decimal`, nunca inteiro: 2,5
litros e 3,2 metros precisam caber.

### DestinoDoItem
`uso_imediato` · `estoque` · `misto`

### StatusCompra
`rascunho` · `confirmada` · `cancelada`

### TipoMovimentacaoEstoque
`entrada_compra` · `saida_manutencao` · `saida_uso` · `ajuste_entrada` ·
`ajuste_saida` · `perda` · `vencimento`

Não existe tipo "saldo". A direção do movimento é derivada do tipo, com dois
conjuntos declarados uma única vez:

```python
TIPOS_DE_ENTRADA = frozenset({
    TipoMovimentacaoEstoque.ENTRADA_COMPRA,
    TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
})
TIPOS_DE_SAIDA = frozenset({
    TipoMovimentacaoEstoque.SAIDA_MANUTENCAO,
    TipoMovimentacaoEstoque.SAIDA_USO,
    TipoMovimentacaoEstoque.AJUSTE_SAIDA,
    TipoMovimentacaoEstoque.PERDA,
    TipoMovimentacaoEstoque.VENCIMENTO,
})
```

`quantidade` é **sempre positiva**; o sinal vem do tipo. Isso permite a
constraint `quantidade > 0` no banco sem ambiguidade.

## 4. `ItemEstoque`

Algo físico que pode entrar, sair, ser consumido, ficar armazenado, ter mínimo e
ser crítico.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | padrão do projeto |
| `condominio` | FK `Condominio` PROTECT | `related_name="itens_estoque"` |
| `nome` | CharField(150) | normalizado no `save()` |
| `descricao` | TextField(blank) | |
| `categoria_texto` | CharField(60, blank) | texto livre; não é catálogo |
| `unidade` | CharField(20) choices | `UnidadeMedida`, default `UNIDADE` |
| `local_armazenamento` | FK `LocalOperacional` PROTECT null | nullable nesta fase |
| `estoque_minimo` | Decimal(14,3) default 0 | `>= 0` |
| `item_critico` | BooleanField(default False) | sem lógica automática de compra |
| `controla_validade` | BooleanField(default False) | apenas informativo |
| `ativo` | BooleanField(default True) | retirada administrativa |
| `criado_por` | FK `User` PROTECT | rastreabilidade |
| `criado_em` / `atualizado_em` | DateTimeField | |

**Proibido persistir**: `saldo_atual`. Saldo negativo também não é persistido:
abaixo do mínimo é comparação, não flag.

**`clean()`** — nada de outro condomínio entra: `local_armazenamento` precisa ser
do mesmo condomínio do item (mensagem humana, sem revelar existência de outro
condomínio). `estoque_minimo` negativo é recusado.

**Constraints**
- `estoque_minimo >= 0`
- `UniqueConstraint(condominio, nome)` — evita o mesmo item duas vezes na mesma
  casa, seguindo a convenção já usada nos cadastros.

**Índices**: `(condominio, ativo)`, `(condominio, item_critico)`,
`(condominio, local_armazenamento)`.

**Cadastro não é movimento** (§54): criar o item **não** gera saldo. Para
cadastrar estoque já existente, registra-se `AJUSTE_ENTRADA` com justificativa
*"Inventário inicial"* — sem campo "saldo inicial" (§55).

## 5. `MovimentacaoEstoque`

O efeito físico no estoque. É também a trilha de auditoria principal (§60), por
isso não há histórico paralelo de saldo.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `condominio` | FK `Condominio` PROTECT | `related_name="movimentacoes"` |
| `item` | FK `ItemEstoque` PROTECT | `related_name="movimentacoes"` |
| `tipo` | CharField(24) choices | `TipoMovimentacaoEstoque` |
| `quantidade` | Decimal(14,3) | sempre positiva |
| `compra_item` | FK `ItemCompra` PROTECT null | linha que originou a entrada |
| `manutencao` | FK `Manutencao` PROTECT null | saída para serviço |
| `motivo` | CharField(255, blank) | exigido em saída de uso, ajuste e perda |
| `usuario` | FK `User` PROTECT | quem registrou |
| `criado_em` | DateTimeField(auto_now_add) | |

**Não criar** `dados_adicionais` JSON nesta fase: nenhuma regra atual precisa
dele, e campo sem uso é armadilha. Fica documentado como evolução se a leitura
de nota (§22) precisar guardar a origem.

**Remoção de `item`/`compra_item`/`manutencao` é bloqueada por `PROTECT`** — o
movimento é a memória do saldo e não pode ficar órfão.

**Constraints**: `quantidade > 0`. As regras de direção por tipo ficam no
domínio (constraint SQL com `IN` por conjunto é possível, mas o ganho não
justifica acoplar o banco à lista de tipos).

**`clean()` / serviço** — *fail closed*:
- `item.condominio == manutencao.condominio` quando há manutenção;
- `compra_item.compra.condominio == item.condominio` quando a entrada vem de compra;
- saída (`saida_manutencao`, `saida_uso`, `ajuste_saida`, `perda`, `vencimento`)
  exige `motivo` preenchido, exceto `vencimento` com validade clara (§33).

## 6. `Compra`

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `condominio` | FK `Condominio` PROTECT | `related_name="compras"` |
| `fornecedor` | FK `Fornecedor` PROTECT null | do mesmo condomínio |
| `data_compra` | DateField | data do fato |
| `numero_documento` | CharField(60, blank) | nota/cupom, quando houver |
| `observacao` | TextField(blank) | |
| `status` | CharField(12) choices | default `RASCUNHO` |
| `manutencao_origem` | FK `Manutencao` PROTECT null | mesmo condomínio |
| `confirmada_em` | DateTimeField null | quando passou a valer |
| `criado_por` | FK `User` PROTECT | |
| `criado_em` / `atualizado_em` | DateTimeField | |
| `ativo` | BooleanField(default True) | |

**Total da compra é derivado** — somatório de `ItemCompra.valor_total` (§25).
Não existe campo de total editável.

**`clean()`** — fornecedor e manutenção de origem pertencem ao mesmo condomínio
da compra. A manutenção de origem **não** muda de status automaticamente (§20).

**Constraints**
- `status = CONFIRMADA` ⟺ `confirmada_em` não nulo — a situação diz o que
  aconteceu, a data diz quando, e as duas nunca se contradizem (mesmo padrão já
  usado em `Manutencao`).
- `data_compra` não é futura? **Não** — compra registrada com atraso é comum;
  fica sem constraint.

**Índices**: `(condominio, status)`, `(condominio, data_compra)`,
`(fornecedor, data_compra)`. `manutencao_origem` já é indexado pela FK.

## 7. `ItemCompra`

Uma linha comprada, com destino explícito.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUIDField PK | |
| `compra` | FK `Compra` CASCADE | a linha pertence à compra |
| `item_estoque` | FK `ItemEstoque` PROTECT null | obrigatório quando há estoque |
| `descricao` | CharField(200) | permite comprar sem cadastro prévio |
| `unidade` | CharField(20) choices | |
| `quantidade` | Decimal(14,3) | `> 0` |
| `valor_unitario` | Decimal(12,2) | `>= 0` |
| `valor_total` | Decimal(12,2) | **recalculado no backend** |
| `destino` | CharField(14) choices | `USO_IMEDIATO` / `ESTOQUE` / `MISTO` |
| `quantidade_uso_imediato` | Decimal(14,3) default 0 | |
| `quantidade_estoque` | Decimal(14,3) default 0 | |
| `manutencao` | FK `Manutencao` PROTECT null | vínculo do uso imediato |
| `validade` | DateField null | só quando o item controla validade |
| `criado_em` / `atualizado_em` | DateTimeField | |

`CASCADE` em `compra` é composição (a linha não existe sem a compra); a remoção
perigosa é barrada pelo `PROTECT` que os movimentos fazem sobre `compra_item`.

**Item sem cadastro prévio** (§22): `item_estoque = null` é permitido. Quando o
destino **inclui estoque**, `item_estoque` passa a ser **obrigatório** — o item é
selecionado, nunca criado em silêncio (§23).

**Valor**: `valor_total = quantidade * valor_unitario`, sempre recalculado no
backend, nunca aceito do navegador (§24).

**Constraints de destino** (§47, §73) — a soma que precisa fechar, no banco:
- `USO_IMEDIATO`: `quantidade_estoque = 0` e `quantidade_uso_imediato = quantidade`
- `ESTOQUE`: `quantidade_uso_imediato = 0` e `quantidade_estoque = quantidade`
- `MISTO`: `quantidade_uso_imediato + quantidade_estoque = quantidade`, com
  ambos `> 0`
- quando o destino inclui estoque, `item_estoque` não nulo
- `quantidade > 0`, `valor_unitario >= 0`, quantidades de destino `>= 0`

A regra "validade só quando o item controla validade" cruza tabelas e fica no
domínio (§34).

## 8. Saldo derivado

Nasce de uma expressão única, declarada no queryset do item:

```python
def com_saldo(self):
    """O saldo é a leitura das movimentações: entradas menos saídas."""
    return self.annotate(
        saldo=Coalesce(
            Sum("movimentacoes__quantidade",
                filter=models.Q(movimentacoes__tipo__in=TIPOS_DE_ENTRADA)),
            Value(Decimal("0")), output_field=CAMPO_DE_QUANTIDADE,
        ) - Coalesce(
            Sum("movimentacoes__quantidade",
                filter=models.Q(movimentacoes__tipo__in=TIPOS_DE_SAIDA)),
            Value(Decimal("0")), output_field=CAMPO_DE_QUANTIDADE,
        )
    )
```

Sobre o saldo anotado:

- **Abaixo do mínimo**: `saldo < estoque_minimo` (com `estoque_minimo > 0`).
- **Zerado**: `saldo <= 0`.
- **Situação (§39)**: `ZERADO` tem precedência; depois `ABAIXO_DO_MINIMO`;
  depois `NORMAL`. É derivada na leitura, nunca gravada.
- **Item crítico abaixo do mínimo**: destaque **moderado** (badge de aviso),
  sem cor agressiva.

**Último custo** (§36): `ItemCompra` de compras **confirmadas** com
`item_estoque = item`, ordenado por `-compra__data_compra, -criado_em`. Derivado,
não persistido. **Custo médio não entra nesta fase** (§37) — documentado como
evolução.

## 9. Concorrência e bloqueio de saldo negativo

Nesta fase **não se permite saída maior que o saldo disponível** (§16), com
mensagem humana *"Quantidade maior que o saldo disponível."*

Estratégia (§59), dentro de `transaction.atomic()`:

1. `ItemEstoque.objects.select_for_update().get(pk=...)` — trava a linha do item;
2. **recalcular** o saldo dentro da transação, com agregação sobre os movimentos;
3. comparar e recusar antes de gravar;
4. gravar o movimento.

O saldo lido **antes** do lock não é usado para decidir nada. Duas saídas
concorrentes disputam a mesma trava, então a segunda recalcula e vê o saldo já
consumido pela primeira.

## 10. Confirmar compra (§26, §27)

`RASCUNHO → CONFIRMADA`, em `transaction.atomic()`, aplicando o destino de cada
linha:

| Destino | Efeito |
| --- | --- |
| `USO_IMEDIATO` | **não** gera entrada de estoque; registra a relação operacional (manutenção, quando houver) |
| `ESTOQUE` | gera `ENTRADA_COMPRA` de `quantidade_estoque` |
| `MISTO` | gera `ENTRADA_COMPRA` **apenas** de `quantidade_estoque`; `quantidade_uso_imediato` permanece ligada ao uso imediato/manutenção |

**Idempotência**: confirmar duas vezes **recusa** com mensagem humana
("Esta compra já está confirmada."). Não há caminho que duplique movimentos, e a
recusa é explícita em vez de silenciosa.

## 11. Cancelamento de compra (§28)

- `RASCUNHO → CANCELADA`: simples, nada foi movimentado.
- `CONFIRMADA`: **bloqueado**, com mensagem humana explicando que a compra já
  movimentou o estoque e que o estorno será tratado em fase própria.
- `CANCELADA`: não gera efeito novo; a edição não é mais oferecida.

Nada é apagado em nenhum dos casos. Nenhum movimento é removido ou invertido em
silêncio.

## 12. Custos (§35–§37, §49)

- `valor_unitario` e `valor_total` são da compra; **último custo é derivado**.
- **Custo médio**: não implementado nesta fase. Evolução documentada.
- **Não duplicar custo**: `ItemCompra` ligado a uma manutenção **não** soma
  automaticamente em `Manutencao.custo_real` nesta fase. O valor fica disponível
  como custo associado, para composição futura. Isso evita a dupla contagem
  quando a gestão também preenche `custo_real` à mão.

## 13. Validade — limitação declarada (§34)

Sem controle de lote, `validade` é **apenas informativa da entrada**. Ela não
gera baixa automática, não alerta e não produz movimento sozinha. A baixa por
vencimento é um movimento manual (`VENCIMENTO`). Limitação registrada aqui de
propósito: é melhor dizer o que o sistema não faz do que deixar supor que faz.

## 14. Autorização

Tudo decidido no backend, a partir do vínculo ativo com o condomínio, em
`apps/estoque/permissoes.py`. O menu pergunta ao domínio a mesma coisa, mas o
menu não autoriza. Quem não enxerga recebe **404**; quem enxerga e não decide
recebe **403**.

### Estoque (§50)

| Papel | Vê | Cria/edita item | Movimenta | Ajuste | Perda/vencimento |
| --- | --- | --- | --- | --- | --- |
| ADMIN / SINDICA | sim | sim | sim | sim | sim |
| SUPERVISORA | sim | não | entrada operacional e saída | sim, com justificativa | sim |
| OPERACIONAL | sim | não | saída **apenas** se responsável pela manutenção | não | não |
| PORTEIRO / MORADOR | não (404) | — | — | — | — |

### Compras (§51)

| Papel | Vê | Cria/edita | Confirma | Cancela |
| --- | --- | --- | --- | --- |
| ADMIN / SINDICA | sim | sim | sim | rascunho |
| SUPERVISORA | sim | sim | sim | não |
| OPERACIONAL | ver §24 (ponto de decisão) | não | não | não |
| PORTEIRO / MORADOR | não (404) | — | — | — |

Regras de apoio: ajuste exige **justificativa obrigatória**; perda exige
justificativa; vencimento dispensa justificativa quando a validade já explica o
fato; a operação nunca ajusta saldo administrativo nesta fase.

## 15. Menu (§52)

Duas entradas novas de primeiro nível, sem submenu:

**Painel · Demandas · Manutenções · Estoque · Compras · Cadastros · Balanços**

`Estoque` (`bx-package`) e `Compras` (`bx-cart`) usam `visivel_para` com helpers
do próprio app — `pode_ver_estoque_em_algum` e `pode_ver_compras_em_algum` —,
seguindo exatamente o padrão de Demandas e Manutenções no context processor.

## 16. Telas

**Estoque** (§38): Item · Unidade · Saldo · Estoque mínimo · Situação · Local ·
Último custo · Ações. Tabela no desktop, cards no mobile, linha clicável, ações
compactas (ícone), estado vazio. Filtros (§40): ativos · item crítico · abaixo do
mínimo · zerados · local · busca.

**Item** (§41): nome, descrição, unidade, local, estoque mínimo, saldo, crítico,
última compra, último custo e **movimentações recentes**. O histórico (§42)
mostra data, tipo, quantidade, origem, responsável e — quando o cálculo é seguro
— o saldo após o movimento. Sem JSON, sem código interno, sem nome de model.

**Compras** (§43): Data · Fornecedor · Quantidade de itens · Total · Status ·
Manutenção relacionada · Ações. Filtros: status, fornecedor e busca.

**Compra** (§44): cabeçalho (data, fornecedor, status, total, manutenção origem)
e itens (descrição, quantidade, unidade, valor unitário, total, destino, uso
imediato, estoque).

**Criação** (§45, §46): fluxo enxuto — primeiro o cabeçalho (fornecedor, data,
manutenção origem, observação); depois os itens. O formulário de destino mostra
**apenas os campos que importam**: uso imediato não mostra quantidade de estoque,
estoque não mostra uso imediato, misto mostra os dois.

## 17. Querysets (§56)

- `ItemEstoqueQuerySet`: `do_condominio` · `ativos` · `criticos` ·
  `abaixo_do_minimo` · `zerados` · `buscar`
- `CompraQuerySet`: `do_condominio` · `por_status` · `por_fornecedor` ·
  `por_manutencao` · `recentes` · `buscar`
- `MovimentacaoEstoqueQuerySet`: `do_condominio` · `do_item` · `entradas` ·
  `saidas` · `por_manutencao` · `recentes`

## 18. Índices e constraints — resumo consolidado (§72, §73)

**Não** serão criados índices redundantes em colunas de FK que o Django já
indexa (`ItemCompra.compra`, `ItemCompra.item_estoque`, `ItemCompra.manutencao`,
`Compra.manutencao_origem`, `MovimentacaoEstoque.compra_item`).

**No banco**: `estoque_minimo >= 0`; `quantidade > 0`; `valor_unitario >= 0`;
quantidades de destino `>= 0`; as três regras de destino fechando a quantidade;
destino com estoque exigindo `item_estoque`; `confirmada_em` coerente com o
status; unicidade de nome por condomínio.

**No domínio**: coerência de condomínio em todas as ligações; motivo obrigatório
por tipo de saída; validade só com item que controla validade; saldo não negativo.

## 19. Transações (§58)

`transaction.atomic()` em: confirmar compra, entrada, saída, ajuste, perda e
vencimento. Movimento e efeito operacional na **mesma** transação — nunca um sem
o outro.

## 20. Impacto em Manutenções (§76.18)

Nenhuma tela de Manutenções muda nesta fase. O que aparece é apenas o vínculo,
em dois pontos opcionais e rotulados:

- `Compra.manutencao_origem` — procedência da compra;
- `ItemCompra.manutencao` — a que serviço o uso imediato se refere;
- `MovimentacaoEstoque.manutencao` — saída de estoque para uma manutenção.

Regras preservadas: a manutenção **não** muda de status por causa de compra ou
movimento; `custo_real` **não** é alterado automaticamente; nenhum balanço ou
lançamento financeiro é tocado.

## 21. Testes previstos (§66–§70)

| Grupo | Casos |
| --- | --- |
| Item de estoque | 10 — criação, unidade, local do mesmo condomínio, local de outro condomínio negado, crítico, mínimo, saldo inicial zero, abaixo do mínimo, zerado, busca |
| Movimentações | 10 — entrada, saída, ajuste de entrada, ajuste de saída, perda, vencimento, saldo, bloqueio de saldo negativo, concorrência, vínculo com manutenção |
| Compra | 13 — rascunho, fornecedor, item, cálculo do total, uso imediato, estoque, misto, divisão correta, confirmar, idempotência, manutenção origem, cancelar rascunho, bloqueio de cancelamento após confirmação |
| Autorização | ADMIN, SINDICA, SUPERVISORA, OPERACIONAL responsável, OPERACIONAL não responsável, PORTEIRO, MORADOR, outro condomínio |
| Views | listagem de estoque, detalhe do item, filtros, listagem de compras, detalhe da compra, criação, itens, confirmação, saída por manutenção, menu, mobile |

## 22. Integrações adiadas — arquitetura futura (§61–§65, §75)

Estas capacidades **não** serão implementadas nesta fase. O que a modelagem já
garante é que elas caberão sem reescrita:

- **Foto de nota ou cupom**: `Compra` já é a unidade que reúne fornecedor, data,
  itens, quantidades, unidade, valor unitário e total. Documento original, foto,
  PDF, origem da importação, dados extraídos e dados confirmados entram depois,
  como campos e models próprios — não inventados agora sem necessidade.
- **Fluxo futuro**: nota fotografada → o Eithra extrai fornecedor, data, itens,
  quantidade, unidade, valor unitário e total → sugere destino → **o usuário
  revisa** → confirma → `Compra`/`ItemCompra` são criados → movimentos gerados.
  A IA **nunca** confirma sozinha.
- **Fornecedor automático**: se o fornecedor extraído já existir, sugere o
  vínculo; se não existir, sugere cadastro. Nunca cria fornecedor em silêncio.
- **Item automático**: se o item extraído se parecer com um `ItemEstoque`
  existente, sugere; se não existir, quem decide é o usuário. Nunca mescla
  automaticamente.
- **Preço histórico**: a estrutura já permite último preço, variação, fornecedor
  habitual e comparação de compras. Alertas não entram agora.
- **Financeiro**: nenhuma conciliação com balanço nesta fase. O vínculo com o
  financeiro será desenhado em fase própria, para não misturar custo operacional
  com registro contábil.

## 23. Migrations

Quando autorizado, a fase gera **uma** migration inicial de `estoque`
(`0001_initial`), contendo os quatro models, os enums, as constraints e os
índices deste documento. Ela será **gerada** e apresentada; a **aplicação** em
DEV depende de autorização separada. Produção não é tocada.

## 24. Pontos que precisam de decisão

1. **App único `apps/estoque`** (recomendado) ou dois apps separados
   (`estoque` + `compras`)?
2. **OPERACIONAL em Compras**: recomendação — **sem acesso** nesta fase (404),
   mantendo a superfície pequena; ele vê estoque e registra saída para as
   manutenções dele. Alternativa: acesso somente leitura às compras.
3. **Unicidade de nome de item por condomínio** (recomendado, seguindo os
   cadastros) ou nomes repetidos permitidos?
4. **`dados_adicionais` JSON** em `MovimentacaoEstoque`: recomendação —
   **não criar** agora.
5. **`confirmada_em`** em `Compra` (recomendado, com constraint de coerência com
   o status) ou apenas `status` + `criado_em`/`atualizado_em`?

## 25. Confirmação

OCR, IA, leitura automática de nota ou cupom, integração fiscal, conciliação
automática com balanço, cotação, aprovação, contrato, controle de lote,
multi-almoxarifado e inventário por código de barras **não foram iniciados**.

Nenhuma migration foi gerada. Nada foi aplicado. Aguardando autorização.
