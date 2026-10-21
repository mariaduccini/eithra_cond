# Eithra V3 — Fase 2F · Unidades, Pessoas, Vínculos e Veículos

> Documento de modelagem **pós-implementação e homologado**. A implementação está
> concluída, a suíte completa passa e a migração `0001_initial` foi **gerada** e
> **aplicada** no PostgreSQL local e no Neon DEV.
>
> A seção 21 registra o que ficou implementado, a seção 22 o que falta decidir e a
> seção 24 o fechamento técnico da fase.

A Fase 2F cria o domínio humano e patrimonial do condomínio: quem mora onde, quem
responde por quê, desde quando e até quando, e o que circula pelas vagas. É a
base das fases seguintes — Portaria (2G), Sinalizações (2H), Projetos/Obras (2I)
e Hoje/Radar (2K) — e por isso **não antecipa nenhuma delas**: não há visitante,
não há autorização de entrada, não há leitura de placa, não há biometria.

---

## 1. Objetivo

Responder, de forma confiável e humana, a perguntas como:

- Quais unidades existem neste condomínio, e quais continuam ativas?
- Quem está ligado à unidade 101 **hoje**, e com qual papel?
- Quem já esteve ligado a ela, e quando?
- Quais veículos estão associados a esta pessoa, e a quais unidades?
- Com quais unidades esta pessoa já esteve relacionada?

O que sustenta essas respostas é o **vínculo** — um fato com início e, quando
chega ao fim, uma data de encerramento. Unidade, pessoa e veículo são cadastros;
é o vínculo que diz o que eles significam juntos, e por quanto tempo.

**Entra nesta fase:** unidades; pessoas físicas e jurídicas; contatos; vínculos
Pessoa ↔ Unidade; temporalidade e histórico; ligação opcional Pessoa ↔ conta do
Eithra; veículos; vínculo Veículo ↔ Pessoa; vínculo Veículo ↔ Unidade; ativação e
inativação; normalização; busca; prevenção de duplicidade; permissões;
privacidade; auditoria mínima; preparação para importação, para 2G e para 2K.

**Não entra nesta fase:** visitantes, autorização de entrada, chegada e saída,
portaria, encomendas, biometria, reconhecimento facial, câmeras, leitura
automática de placa, QR code, tags, inadimplência, cobrança, reservas,
ocorrências, votação, feed, chat e rede social.

---

## 2. Pré-check — estado inicial

Antes de qualquer linha de código, foram revisados: `apps.accounts`,
`apps.condominios`, `apps.cadastros`, `apps.governanca`, `apps.core`, as
migrations existentes, os services e permissões existentes, e os templates/menu.

| Verificação | Resultado |
|---|---|
| `Unidade`, `Pessoa` ou `Veiculo` já existiam? | Não. Nenhum dos três existia no repositório |
| Tipo de pessoa (PF/PJ) já existia? | Sim — `TipoPessoa` em `apps/cadastros/constants.py` (`PF`, `PJ`, `NAO_INFORMADO`) |
| Quem decide acesso a um condomínio? | `apps/condominios/acesso.py` (`condominios_do_usuario`) + `AcessoUsuarioCondominio` |
| Papéis disponíveis | `Papel` em `apps/condominios/constants.py` (ADMIN, SINDICA, SUPERVISORA, OPERACIONAL, PORTEIRO, MORADOR) |
| Onde vive a regra de módulo? | `apps/<app>/permissoes.py`, consultando `condominios_do_usuario` |
| Como o menu decide visibilidade? | `NAVIGATION` em `apps/core/context_processors.py`, com `visivel_para` chamando o domínio |
| `manage.py check` | `System check identified no issues (0 silenced).` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` |

Nada foi reaproveitado por engano e nada foi duplicado: o tipo de pessoa veio de
`cadastros`, o acesso veio de `condominios`, e o app novo só entrou depois de
confirmar que não havia modelo equivalente.

### 2.1 Decisões tomadas no pré-check

| Ponto | Decisão |
|---|---|
| App do domínio | **`apps/comunidade`** (novo, dedicado) |
| Conflito com a decisão **B1** | B1 foi **substituída**: `Unidade` existe; `Bloco`, `Torre` e `Pavimento` não |
| Vaga de garagem | Relevante → **proposta 2F.1 apresentada**, aguardando a proprietária (§12) |
| Registro da decisão | `docs/v3_decisoes_arquiteturais.md`, seção *Fase 2F — Unidades, pessoas, vínculos e veículos*, com **G1–G10** aprovadas |

---

## 3. Modelo conceitual

```
                       CONDOMÍNIO
                            |
        +-------------------+-------------------+
        |                   |                   |
    UNIDADE              PESSOA              VEÍCULO
        |                   |                   |
        |   +---------------+                   |
        |   |               |                   |
        +---+-- VÍNCULO ----+-------------------+
                (tempo)
```

O eixo é o tempo. Um vínculo é uma linha explícita com `data_inicio` e `data_fim`
opcional; não é um campo escondido dentro de outro modelo e não se infere de
outro vínculo. Três perguntas distintas, três vínculos distintos:

- **Pessoa ↔ Unidade** — quem mora, quem é proprietário, quem é locatário;
- **Veículo ↔ Pessoa** — quem utiliza o veículo;
- **Veículo ↔ Unidade** — a qual unidade o veículo está associado.

O veículo estar ligado a uma pessoa **não** diz com qual unidade ele está: o
carro de uma empresa pode estar guardado em uma unidade sem que a empresa o
dirija, e o contrário também. Por isso não existe caminho derivado de um vínculo
para o outro.

Uma quarta pergunta **não** é respondida aqui: *"esta pessoa pode entrar?"*.
Relação civil/operacional e autorização de acesso físico são coisas diferentes —
o vínculo diz a relação, e a autorização de portaria pertence à Fase 2G (§18).

---

## 4. Entidades

Seis modelos, todos com PK `UUIDField`, `criado_por` (FK a `User`, `SET_NULL`,
opcional), `criado_em` / `atualizado_em`, e todos com `on_delete=PROTECT` nos
alvos do fato (condomínio, pessoa, unidade, veículo).

`criado_por` é `SET_NULL`: quem registrou não é um alvo do fato. O vínculo
continua legível — pessoa, unidade e período permanecem — e a remoção de uma
conta de acesso não fica travada por ter criado cadastros operacionais.

### `Unidade`

| Campo | Observação |
|---|---|
| `condominio` | FK `PROTECT` |
| `identificador` | como o condomínio a chama: "101", "A-101", "Casa 03", "Loja 02" |
| `bloco_torre`, `andar`, `complemento` | **campos opcionais** — não são models |
| `observacao_interna` | anotação da gestão, não mostrada ao morador |
| `origem` | `OrigemInformacao` |
| `ativa` | `True` por padrão; desativar não apaga |

Constraint: `UniqueConstraint(Lower("identificador"), "condominio")`
(`unidade_unica_por_condominio`) — "a-101" e "A-101" são a mesma unidade.
Índice: `["condominio", "ativa"]`.

### `Pessoa`

| Campo | Observação |
|---|---|
| `condominio` | FK `PROTECT` |
| `tipo_pessoa` | `TipoPessoa` (PF / PJ / não informado), vindo de `cadastros` |
| `nome`, `nome_social` | razão social para PJ; nome social tem precedência na exibição |
| `telefone`, `whatsapp` | guardados **só com os dígitos** |
| `email` | normalizado para minúsculas |
| `documento` | **opcional**, só dígitos; CPF exige 11, CNPJ exige 14 |
| `observacao_interna` | anotação da gestão, não mostrada ao morador |
| `usuario` | **opcional**, FK a `User` com `SET_NULL`; **não cria acesso nem senha** |
| `origem` | `OrigemInformacao` |
| `ativa` | `True` por padrão |

Índices: `["condominio", "ativa"]`, `["condominio", "nome"]`.

### `Veiculo`

| Campo | Observação |
|---|---|
| `condominio` | FK `PROTECT` |
| `placa` | **obrigatória**, maiúsculas sem separadores, **única por condomínio** |
| `marca`, `modelo`, `cor` | descritivos, opcionais |
| `observacao_interna`, `origem`, `ativo` | iguais em espírito aos demais |

Constraint: `UniqueConstraint(Lower("placa"), "condominio")`
(`veiculo_placa_unica_por_condominio`).

### `VinculoPessoaUnidade`

`condominio`, `pessoa`, `unidade`, `tipo_vinculo` (`TipoVinculoPessoaUnidade`),
`data_inicio`, `data_fim` (nulo = em vigor), `observacao_interna`, `criado_por`.

- `UniqueConstraint(pessoa, unidade, tipo_vinculo, data_inicio)` — registrar duas
  vezes o mesmo vínculo começando no mesmo dia é engano, não dois moradores;
- `CheckConstraint(data_fim IS NULL OR data_fim >= data_inicio)`.

**A mesma pessoa pode ter mais de um tipo de vínculo com a mesma unidade ao
mesmo tempo.** Uma pessoa pode ser `FAMILIAR` e `FUNCIONARIO_UNIDADE` da unidade
101 ao mesmo tempo: são naturezas diferentes de relação, e as duas cabem. Isso
**não é duplicidade**. Papel nenhum se infere de outro, e nenhum exclui outro —
`PROPRIETARIO`, `MORADOR`, `LOCATARIO`, `FAMILIAR`, `FUNCIONARIO_UNIDADE` e
`OUTRO` coexistem quando fizer sentido.

**Duplicidade é outra coisa:** a sobreposição temporal da **mesma combinação
exata** — mesma pessoa + mesma unidade + mesmo tipo de vínculo. Dois períodos da
mesma tríade que se cruzam são o mesmo anotado duas vezes, e não dois vínculos.

A conferência é feita no **service** (`criar_vinculo_pessoa_unidade`), e não em
constraint: a regra é interseção de intervalos, com `data_fim` **inclusiva** dos
dois lados. Um vínculo que termina em 31/03 e outro que começa em 31/03 disputam
o mesmo dia e são recusados; um que começa em 01/04 é outro período.

Não são bloqueados, porque são fatos distintos:

- mesma pessoa + mesma unidade + **tipo diferente** (§4, acima);
- mesma pessoa + **unidades diferentes**;
- **pessoas diferentes** + mesma unidade.

### `VinculoVeiculoPessoa` e `VinculoVeiculoUnidade`

Mesma anatomia temporal, com `veiculo` no lugar de um dos lados, e as mesmas duas
constraints. `VinculoVeiculoUnidade` usa `observacao` (não `observacao_interna`).

A mesma regra de sobreposição vale onde há identidade de relação:

- `VinculoVeiculoPessoa` — impede dois períodos que se cruzem para
  (**veículo + pessoa**). O mesmo veículo pode estar ligado a pessoas diferentes
  ao mesmo tempo: o carro da casa é dirigido por quem mora nela;
- `VinculoVeiculoUnidade` — impede dois períodos que se cruzem para
  (**veículo + unidade**). O mesmo veículo pode estar relacionado a unidades
  diferentes ao mesmo tempo.

Nenhuma das duas infere exclusividade de uso: quem usa o veículo e a que unidade
ele se relaciona continuam sendo perguntas independentes.

### `TipoVinculoPessoaUnidade`

`PROPRIETARIO`, `MORADOR`, `LOCATARIO`, `FAMILIAR`, `FUNCIONARIO_UNIDADE`,
`OUTRO`. "Funcionário da unidade" é dito por inteiro de propósito: é quem
trabalha **para a unidade** (a diarista da casa), não funcionário do condomínio.
O papel é do vínculo, não da pessoa — a mesma pessoa pode ser proprietária de uma
unidade e moradora de outra, e isso muda com o tempo.

**O tipo de vínculo descreve a relação da pessoa com a unidade — e só isso.**
Ele responde *"qual é a relação desta pessoa com esta unidade?"*. Ele **não**
responde se a pessoa pode entrar, se pode autorizar visitante, se pode entrar sem
confirmação, se pode acessar fora de horário ou se pode autorizar terceiros.
Papel **não** é autorização, e autorização de portaria pertence à Fase 2G.

- ser `FAMILIAR` **não** significa estar autorizado a entrar;
- ser `FUNCIONARIO_UNIDADE` **não** significa estar autorizado a entrar;
- não existe — nem existirá nesta fase — `SECRETARIA_AUTORIZADA` ou qualquer tipo
  que misture a relação da pessoa com uma regra de acesso físico. Distinguir
  funções (secretária, cuidador, motorista) é outra pergunta, a ser analisada
  separadamente, quando houver necessidade real.

### `OrigemInformacao`

`CADASTRO`, `IMPORTACAO`, `DECLARACAO`, `INTEGRACAO`. O Eithra não infere origem:
a lista é curta e fechada para que a pergunta "de onde veio isto?" tenha sempre a
mesma resposta possível.

### Vínculo Pessoa ↔ conta do Eithra

`Pessoa.usuario` é opcional e explícito. Ligar uma pessoa a uma conta **não**
concede acesso a nada: quem governa o acesso continua sendo
`AcessoUsuarioCondominio`. O `clean()` recusa uma conta que não alcança o
condomínio da pessoa — uma pessoa do condomínio A não aponta para quem só
responde pelo condomínio B.

---

## 5. Temporalidade

Vigência **inclusiva no fim**, decidida em um só lugar e usada por todos:

```python
# vigente hoje
data_inicio <= referencia  E  (data_fim IS NULL  ou  data_fim >= referencia)

# encerrado
data_fim < referencia
```

`data_fim == hoje` ainda é **vigente**: o vínculo vale no último dia. O dia
seguinte é que o encerra.

Leituras derivadas, nunca persistidas:

| QuerySet | Propósito |
|---|---|
| `vigentes(hoje=None)` | vínculos que valem na referência (padrão: hoje) |
| `encerrados(hoje=None)` | vínculos que já terminaram |
| `da_unidade(unidade)` | vínculos de uma unidade |
| `da_pessoa(pessoa)` | vínculos de uma pessoa |
| `do_veiculo(veiculo)` | vínculos de um veículo |

Propriedades `vigente` e `encerrado` respondem o mesmo para um registro isolado.
Nenhum status é gravado: **não existe** campo `vigente`, `encerrado`, `atual` ou
`ocupada`. Quem mora onde agora é resposta de leitura.

Derivações de tela, todas calculadas na hora:

- `Unidade.vinculos_vigentes()`, `.pessoas_atuais()`, `.moradores_atuais()`,
  `.proprietarios_atuais()`, `.veiculos_relacionados()`, `.rotulo`;
- `Pessoa.vinculos_vigentes()`, `.unidades_atuais()`, `.veiculos_atuais()`;
- `Veiculo.pessoas_atuais()`, `.unidades_atuais()`.

Há um só relógio (`timezone.localdate()`), exposto como parâmetro `hoje=` em toda
leitura derivada — é o que permite a uma fase futura perguntar "e ontem?" sem
reescrever a regra.

---

## 6. Pessoa física e pessoa jurídica

`Pessoa` representa as duas. `tipo_pessoa` usa `TipoPessoa`, já existente em
`cadastros` — nenhum enum novo foi criado. `nome` é "nome ou razão social".

A conferência de documento é **estrutural e acompanha o tipo**: CPF com 11
dígitos, CNPJ com 14. O Eithra não consulta cadastro externo e não calcula dígito
verificador — quem digita o documento responde por ele. Documento é opcional:
uma pessoa existe sem documento.

---

## 7. Privacidade

- **O módulo inteiro é fechado ao morador.** Não há "campo escondido": o perfil
  MORADOR recebe 404 na tela, e por isso não alcança `observacao_interna`,
  documento, telefone ou e-mail de ninguém.
- **Observação interna nunca é mostrada a morador** — está dita no próprio
  `help_text` de cada campo.
- **Documento não aparece na lista**, apenas na ficha de quem opera.
- **Nada atravessa condomínios.** Todo QuerySet parte de `condominio`; os
  `clean()` recusam vínculo entre condomínios diferentes; a tela resolve o
  condomínio atual contra a lista autorizada.
- **Nenhum arquivo** nesta fase — portanto nenhum bucket, nenhuma URL assinada,
  nenhuma exposição por link direto.
- **A conta ligada à pessoa não é acesso.** Ligar `Pessoa.usuario` não expõe
  nada a essa conta: o acesso continua sendo uma decisão separada.

---

## 8. Proveniência

`origem` (`OrigemInformacao`) existe em `Unidade`, `Pessoa` e `Veiculo`, com
padrão `CADASTRO`. É o lugar onde uma importação futura vai declarar de onde o
registro veio, sem que isso vire fato presumido. Nenhuma origem é inferida.

---

## 9. Auditoria

Cada modelo e cada vínculo registra:

- `criado_por` — quem cadastrou/registrou, preservado com `PROTECT`;
- `criado_em` / `atualizado_em`.

O **vínculo é o próprio histórico**: encerrar não apaga, apenas grava `data_fim`;
`on_delete=PROTECT` impede que apagar uma unidade apague a história de quem morou
nela. Não há tabela de auditoria paralela nem log de alteração campo a campo —
essa é uma limitação declarada (§20).

---

## 10. Regras de inativação

- Inativar não apaga: `Unidade.ativa`, `Pessoa.ativa`, `Veiculo.ativo`.
- **Inativar exige tratar os vínculos vigentes.** Se ainda houver vínculo em
  vigor, o domínio recusa a operação e pede que os vínculos sejam encerrados
  primeiro: *"Esta unidade tem vínculos em vigor. Encerre-os antes de
  desativá-la."* (a mensagem se repete para pessoa e veículo).
- Reativar é imediato e não exige vínculo (§10.1).
- Desativar em silêncio deixaria o histórico contando uma história que não
  aconteceu — por isso a recusa, e não o efeito colateral.

---

## 11. Prevenção de duplicidade

Determinística, sem IA e sem busca semântica:

`PessoaQuerySet.possiveis_duplicidades(*, email, telefone, whatsapp, documento,
excluir_pk=None)` compara **valores normalizados** (e-mail em minúsculas;
telefone, WhatsApp e documento só com dígitos) e devolve os candidatos.

O Eithra **avisa, mostra o candidato e nunca funde cadastros sozinho**. Dois
cadastros com o mesmo telefone podem ser a mesma pessoa — ou mãe e filha que
dividem o número. Quem decide é a gestão. O merge automático fica fora desta
fase.

Não confundir com a **duplicidade de vínculo** (§4): aquela não é sobre dois
cadastros de pessoa, e sim sobre dois períodos sobrepostos da mesma combinação
pessoa + unidade + tipo de vínculo — recusada pelo service, sem aviso e sem
escolha.

`PessoaQuerySet.buscar(termo)` procura por nome, nome social, e-mail e — quando o
termo tem dígitos — telefone, WhatsApp e documento.

---

## 12. Veículo

- **Placa obrigatória** e **única por condomínio**, guardada em maiúsculas sem
  separadores.
- Aceita o **padrão antigo (ABC1234)** e o **Mercosul (ABC1D23)**. A validação é
  de formato (`[A-Z]{3}[0-9][0-9A-Z][0-9]{2}`), **sem consulta a serviço externo**.
- **Bicicleta e veículo sem placa não entram em `Veiculo`** — não há "placa
  vazia" nem registro parcial.
- Duas perguntas separadas: quem **utiliza** (Veículo ↔ Pessoa) e a qual unidade
  está **associado** (Veículo ↔ Unidade).

---

## 13. Decisão sobre garagem — fora da 2F

A avaliação do pré-check concluiu que o controle estrutural de vagas é relevante
para o domínio. Conforme a regra do ponto de decisão, **não foi implementado**:

- **Proposta 2F.1 apresentada** com modelo, relações, impacto, telas e testes;
- **Aguardando decisão da proprietária do produto.**

A proposta não mistura vaga com unidade: `VagaGaragem` seria um cadastro próprio
(`condominio`, identificador, localização opcional, ativa) com dois vínculos
temporais — Vaga ↔ Unidade e Vaga ↔ Veículo —, seguindo a mesma anatomia de
vigência desta fase. Até a decisão, **a vaga fica explicitamente fora da 2F**, e
nada de garagem foi criado.

---

## 14. Permissões

Regra de módulo em `apps/comunidade/permissoes.py`, apoiada em
`apps/condominios/acesso.py` — **uma só resposta** para "esta pessoa acessa este
condomínio?".

```python
PAPEIS_QUE_OPERAM = {ADMIN, SINDICA, SUPERVISORA}
PAPEIS_QUE_VEEM   = PAPEIS_QUE_OPERAM     # nesta fase, quem vê também opera
```

| Perfil | Resultado |
|---|---|
| ADMIN | vê, cadastra, corrige, vincula e encerra |
| SÍNDICA | vê, cadastra, corrige, vincula e encerra |
| SUPERVISORA | vê, cadastra, corrige, vincula e encerra |
| OPERACIONAL | **404** — não descobre que a tela existe |
| PORTEIRO | **404** |
| MORADOR | **404** |

- **Falha fechada:** sem condomínio resolvido, a resposta é negar.
- **O menu não autoriza.** `NAVIGATION` chama `pode_ver_comunidade_em_algum` para
  decidir se mostra o item; a **view** decide se responde. Esconder o item não é
  controle de acesso, e o teste de permissões verifica isso pelos seis papéis.
- O condomínio em vista vem do endereço, sempre conferido contra a lista
  autorizada. Não há condomínio guardado em sessão, cookie ou preferência.

---

## 15. Escopo explicitamente fora

Visitantes · autorização de entrada · chegada e saída · portaria · encomendas ·
biometria · reconhecimento facial · câmeras · leitura automática de placa · QR
code · tags · inadimplência · cobrança · reservas · ocorrências · votação · feed ·
chat · rede social · importação de planilhas · merge automático de cadastros ·
vagas de garagem (§13).

**Nenhum campo de acesso físico existe nesta fase.** Não há — e não será criado —
`pode_entrar`, `autorizado`, `autorizado_portaria`, `pode_autorizar_visitante`,
`acesso_livre`, `horario_autorizado`, `validade_autorizacao` ou
`pode_liberar_terceiro`. Não há enum de autorização, não há regra de acesso físico
e não há tabela de autorização. O cadastro que a portaria virá a usar é este; a
decisão de entrada é da Fase 2G.

---

## 16. Migrations

| Item | Estado |
|---|---|
| Arquivo | `apps/comunidade/migrations/0001_initial.py` |
| Conteúdo | 6 `CreateModel` (Pessoa, Unidade, Veiculo, VinculoPessoaUnidade, VinculoVeiculoPessoa, VinculoVeiculoUnidade) + índices e constraints |
| `criado_por` | `SET_NULL` nos seis modelos — corrigido **na própria `0001_initial`**, antes da primeira aplicação |
| Aplicada? | **Sim, no PostgreSQL local** (`localhost` · `eithra_dev`), no fechamento da 2F. **Não** no Neon DEV |
| `makemigrations --check --dry-run` | `No changes detected` |
| Aplicação no Neon | **Somente com autorização explícita** |

O ajuste de `criado_por` foi incorporado à migração inicial em vez de gerar uma
corretiva: como `0001_initial` ainda não havia sido aplicada em banco algum
quando o ajuste foi feito, não existia estado a corrigir — apenas o arquivo a
manter correto.

No fechamento, o `migrate` contra o banco local não encontrou apenas
`comunidade.0001_initial`: o `eithra_dev` estava vazio, sem `django_migrations`,
e recebeu o grafo inteiro do projeto. `showmigrations comunidade` confirmou
`[X] 0001_initial`.

---

## 17. Testes

`apps/comunidade/tests/` — **152 testes**, todos verdes:

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_models.py` | 38 | normalização, unicidade, `clean()`, constraints, derivações temporais |
| `test_views.py` | 63 | telas, formulários, isolamento entre condomínios, chave inválida, seletor de condomínio, alteração de unidade |
| `test_services.py` | 40 | cadastro, edição, inativação/reativação, vínculos, sobreposição, transferência, recusas |
| `test_permissoes.py` | 11 | os seis papéis, fail closed, menu × view |

A sobreposição de vínculos é coberta por `SobreposicaoDeVinculosTests`, caso a
caso:

| Caso | Situação | Esperado |
|---|---|---|
| A | mesmo papel, períodos que se cruzam | **recusado** |
| B | mesmo papel, período que começa no dia seguinte ao fim do anterior | permitido |
| C | papéis diferentes na mesma unidade, ao mesmo tempo | permitido |
| D | unidades diferentes, papéis diferentes | permitido |
| E | pessoas diferentes na mesma unidade | permitido |
| F | vínculo futuro que se cruza com outro | **recusado** |
| G | fim 31/03 e novo início 31/03 (limite inclusivo) | **recusado** |
| G | fim 31/03 e novo início 01/04 | permitido |

Nos vínculos de veículo, os mesmos quatro cenários: mesmo uso (veículo + pessoa)
sobreposto **recusado**, veículo com pessoas diferentes permitido, mesma relação
(veículo + unidade) sobreposta **recusada**, veículo em unidades diferentes
permitido.

Resultados executados — **homologação final local** (fechamento da 2F):

```
.\test-local.ps1 apps.comunidade --keepdb
  Found 152 test(s). Ran 152 tests in 100.474s  OK

.\test-local.ps1 apps.cadastros apps.core --keepdb
  Found 143 test(s). Ran 143 tests in 122.797s  OK

.\test-local.ps1 --keepdb        (suíte completa, PostgreSQL local)
  Found 1283 test(s). Ran 1283 tests in 1817.217s  OK
```

`manage.py check` → `System check identified no issues (0 silenced).`
`manage.py makemigrations --check --dry-run` → `No changes detected`.

Bancos: **PostgreSQL local** (`localhost` · `eithra_dev` · usuário
`eithra_dev_user`), sem SQLite e sem tocar no Neon. A sonda de `test-local.ps1`
confirmou o alvo antes de qualquer teste. Sem falhas e sem skips.

O lote `apps.cadastros apps.core` foi reexecutado como **regressão de contexto**:
o menu e a lista de apps mudaram, e nada mais podia ter se movido.

### 17.1 Homologação funcional local

Roteiro transacional executado contra o PostgreSQL local, com dados fictícios e
reversão garantida ao final — nada fica gravado. **48 verificações, todas
conformes**, cobrindo: unidades (criar, editar, visualizar, inativar sem
vínculo, recusar inativação com vínculo vigente), pessoas (física, jurídica, sem
conta, sem unidade, editar, visualizar, inativar, recusar inativação com
vínculo), vínculos (vincular, papéis simultâneos, recusar sobreposição,
encerrar, histórico visível, preservação do anterior), veículos (normalização de
placa, relação com pessoa e unidade, recusar sobreposição equivalente, permitir
relações distintas, encerrar, histórico), transferência de unidade, os seis
papéis e três tentativas cross-condomínio.

Duas expectativas do próprio roteiro foram **refutadas como artefato da sonda**,
não como defeito:

- o tema Sneat aparece no HTML apenas pelos caminhos de assets
  (`vendor/sneat/...`), que são a base oficial — não há componente de
  demonstração;
- o modal "Alterar unidade" é único e serve qualquer vínculo, então a ficha em
  `GET` não estreita o campo `unidade`; a origem é excluída no cliente
  (`option.disabled`) e pelo domínio, que recusa `destino == origem`.

---

## 18. Preparação para a Fase 2G (Portaria)

O que a 2G vai precisar já existe, derivado:

- `Unidade.pessoas_atuais()` / `.moradores_atuais()` / `.proprietarios_atuais()`
  respondem "quem mora onde agora";
- `Pessoa.unidades_atuais()` responde "onde esta pessoa mora";
- as leituras aceitam `hoje=` — a 2G poderá perguntar por outra referência sem
  reescrever a regra;
- `Pessoa.usuario` liga pessoa e conta de forma **opcional**, sem criar acesso;
- `OrigemInformacao` deixa a porta aberta para registros de importação.

**O Eithra não decide entrada e saída nesta fase.** Nenhum estado de
"dentro/fora", nenhum evento de portaria e nenhuma autorização foram criados: a
2G decide o que fazer com quem já se sabe que mora ali.

**RELAÇÃO CIVIL/OPERACIONAL ≠ AUTORIZAÇÃO DE ACESSO FÍSICO.** É a separação que
sustenta toda esta preparação. O vínculo diz a relação (familiar,
funcionário da unidade, morador); a autorização dirá o acesso. A 2G poderá
relacionar uma política ou autorização a **pessoa + unidade + contexto temporal**
sem alterar o significado de `VinculoPessoaUnidade`:

```
Pessoa
 └── VinculoPessoaUnidade
          └── (a 2G referencia daqui para uma política/autorização de portaria)
```

Nada dessa entidade é criado agora. Um familiar na 2F continua sendo apenas
familiar: se ele poderá entrar, e em que condições, é pergunta da 2G.

---

## 19. Preparação para a Fase 2K (Hoje/Radar)

A pergunta "o que mudou desde ontem?" se responde sem tabela nova: os vínculos
têm `data_inicio`, `data_fim`, `criado_em` e `atualizado_em`, e os cadastros têm
`criado_em` / `atualizado_em` e `origem`. Um "mudou" é comparação de leitura
entre duas referências — **não** um campo persistido, pela mesma razão de §5.
Nenhum painel, nenhum alerta e nenhuma timeline foram implementados nesta fase.

---

## 20. Limitações

1. **Sem auditoria campo a campo.** Há quem criou e quando, e o vínculo preserva
   o histórico; não há registro de "quem alterou o telefone de X e quando".
2. **Sem importação.** `OrigemInformacao` prepara o terreno, mas nenhum fluxo de
   importação existe.
3. **Sem merge de cadastros.** A duplicidade é detectada e mostrada; a fusão fica
   fora da fase.
4. **Sem validação externa.** Documento e placa são conferidos por estrutura e
   formato, nunca contra base externa.
5. **Vagas de garagem fora da 2F** (§13).
6. **Prazo de vigência não é validado contra o futuro.** Um vínculo pode começar
   amanhã; ele simplesmente ainda não estará vigente hoje.

---

## 21. O que ficou implementado

| Artefato | Situação |
|---|---|
| `apps/comunidade/` (models, constants, services, forms, views, urls, admin, permissoes) | criado |
| `apps/comunidade/migrations/0001_initial.py` | gerada e **aplicada** (PostgreSQL local e Neon DEV) |
| `apps/comunidade/tests/` (4 arquivos + `apoio.py`) | 152 testes, verdes |
| `templates/pages/comunidade/` (unidades, unidade, pessoas, pessoa, veiculos, veiculo) | criado |
| `config/settings/base.py` | `apps.comunidade` em `LOCAL_APPS` |
| `config/urls.py` | `path("comunidade/", include(...))` |
| `apps/core/context_processors.py` | três filhos em "Cadastros": Unidades, Pessoas, Veículos |
| `apps/cadastros/tests/test_views.py` | `MenuTests` passa a esperar os onze cadastros |
| `docs/v3_decisoes_arquiteturais.md` | seção da Fase 2F com **G1–G10**, substituindo B1 |

Telas, com progressive disclosure (lista → ficha com abas/colapsos):

- **Unidades** — identificador, bloco, resumo de vínculos vigentes ("101 · 3
  pessoas atualmente vinculadas"), ativa/inativa; na ficha: Dados, Pessoas
  atuais, Histórico, Veículos relacionados.
- **Pessoas** — busca por nome, telefone, WhatsApp e e-mail; na ficha: Dados,
  Unidades atuais, Histórico de unidades, Veículos; fluxo *Adicionar pessoa →
  Vincular à unidade*. Cada vínculo em vigor oferece **Alterar unidade** ao lado
  de **Encerrar vínculo**.
- **Veículos** — placa, marca/modelo, pessoas atuais, unidades atuais, situação.

### Alterar a unidade de uma pessoa

A operação vive **no contexto da Pessoa** — ficha da Pessoa → Unidades → ação
contextual no vínculo. Não há item de menu novo, e não há tela própria de
transferência.

A ação parte do **vínculo**, e não da pessoa: quem tem mais de um papel na mesma
unidade altera um deles, e os outros ficam onde estão. O que a janela pede é só
o destino — o papel e a ponta de origem vêm do próprio vínculo, e pedir o papel
de novo daria margem a trocá-lo sem querer no meio de uma mudança de endereço. A
unidade de origem não aparece como destino possível.

Por dentro, `services.transferir_pessoa_unidade()` roda em `transaction.atomic`:
encerra o vínculo de origem (data de fim, sem apagar nem reescrever) e cria o
novo, com o mesmo papel, na mesma data. Nada é editado destrutivamente, e a
mudança continua legível no histórico.

Responsividade: desktop com `table-responsive d-none d-lg-block`, mobile com
`list-group list-group-flush d-lg-none`. Sem Bootstrap demo do Sneat.

Linguagem de interface: *Unidades, Pessoas, Veículos, Vincular à unidade,
Encerrar vínculo, Histórico* — nunca "Entidade Pessoa", "VinculoPessoaUnidade",
"Gerenciamento de Veículos" ou "Desativar relacionamento".

### Ação de interface

Um ícone sem borda com tooltip quando há uma ação; dois ícones quando há duas;
menu de três pontos a partir de três; ação crítica em botão textual. Listagem com
linha clicável, status em badge discreto e filtros compactos. Alternância de
ativação por POST direto, sem confirmação — é ação reversível.

---

## 22. O que falta decidir

1. **Vaga de garagem:** aguarda a decisão da proprietária sobre a proposta 2F.1.
2. **Fase 2G (Portaria Inteligente)** não iniciada, por instrução da fase — e
   nenhuma autorização de acesso físico foi antecipada para cá.

---

## 23. Constituição de produto, UX e arquitetura

Nenhum princípio genuinamente novo surgiu nesta fase. A 2F aplicou o que já
estava escrito: vínculo é fato temporal; papel não é permissão; estado derivado
não se persiste; histórico não se destrói; inferência não vira fato; privacidade
por padrão; a tela responde perguntas humanas, não expõe o banco. Por isso **a
constituição não foi alterada**. A decisão arquitetural que mudou — B1 por G1 — foi
registrada em `docs/v3_decisoes_arquiteturais.md`, que é o lugar próprio para ela.

---

## 24. Fechamento técnico da fase

| Etapa | Resultado |
|---|---|
| PostgreSQL local | homologado (ambiente de desenvolvimento e teste) |
| Suíte completa | **1283 testes OK** (sem regressão) |
| `apps.comunidade` | 152 testes OK |
| `apps.cadastros` + `apps.core` | 143 testes OK |
| `manage.py check` | sem problemas |
| `makemigrations --check --dry-run` | `No changes detected` |
| Migration `0001_initial` | aplicada no PostgreSQL local e no **Neon DEV** |
| Smoke funcional no Neon DEV | aprovado (34 verificações, dados fictícios, sem persistência) |
| Homologação responsiva | estrutural aprovada (`table-responsive d-lg-none` / `list-group d-lg-none`) |
| Revisão visual humana em navegador | **não executada nesta etapa** |

O smoke remoto cobriu os fluxos da fase — unidade, pessoa (física e jurídica),
vínculo (coexistência de papéis, recusa de sobreposição, encerramento),
transferência de unidade, veículo (normalização de placa e vedações), isolamento
entre condomínios e permissões por papel (ADMIN, SINDICA e SUPERVISORA operam;
OPERACIONAL, PORTEIRO e MORADOR não). Nenhum dado real foi criado, alterado ou
removido: o smoke rodou em transação revertida ao final.

A revisão visual humana em navegador fica registrada como pendência de
homologação — ela **não** bloqueia o fechamento técnico da 2F e não autoriza
redesenho de telas nesta etapa.

Commit final da fase: `feat: add units people relationships and vehicles`.
