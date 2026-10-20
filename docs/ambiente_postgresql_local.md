# Eithra — ambiente PostgreSQL local

Este documento existe por um motivo simples: o Eithra tem **dois** bancos de
desenvolvimento, e confundir os dois é fácil. Um é o **local**, que roda na
própria máquina; o outro é o **remoto** (Neon DEV), que é compartilhado. O
local é para o trabalho do dia a dia; o remoto é para homologação e para o
fluxo de fase. Nenhum dos dois é produção.

O que este documento descreve é como escolher o ambiente de forma explícita —
e como o sistema impede que a escolha aconteça por descuido.

## 1. Qual é a diferença

| | Local | Remoto (Neon DEV) |
|---|---|---|
| Onde roda | `localhost:5432`, na sua máquina | Neon, na AWS (`aws-us-east-2`) |
| Quem usa | só você | todos os envolvidos no DEV |
| Arquivo de ambiente | `.env.local` | `.env` |
| Velocidade | a do disco local, sem rede | depende da rede e do PgBouncer |
| Indicado para | desenvolvimento diário, testes | homologação, fechamento de fase |
| É produção? | não | não |

O banco oficial é **PostgreSQL** nos dois casos. Não existe fallback para
SQLite: se a conexão não estiver configurada, o ambiente **não sobe**. Isso é
deliberado — subir contra um banco diferente do real cria problemas que só
aparecem depois.

## 2. Como o ambiente é escolhido

O Django lê **um** arquivo de `.env`, e qual arquivo é decidido pela variável
`EITHRA_ENV_FILE`:

- sem a variável → lê `.env` (o remoto; é o comportamento de sempre);
- com `EITHRA_ENV_FILE=.env.local` → lê `.env.local` (o local).

Duas travas acompanham essa escolha:

1. **O arquivo tem de estar dentro do projeto.** Um caminho absoluto
   (`C:\...`) ou com `..` é recusado com erro explícito. O motivo é que um
   arquivo de ambiente carrega segredos, e a variável de ambiente é uma entrada
   externa.
2. **Um arquivo escolhido que não existe é erro**, e não silêncio. Seguir em
   frente faria o ambiente subir contra o banco errado sem avisar.

Nada disso fica no controle de versão: `.env` e `.env.local` são ignorados pelo
`.gitignore`. Só os modelos — `.env.example` e `.env.local.example` — são
versionados, e nenhum deles contém segredo.

## 3. Como criar o `.env.local`

O arquivo não existe no repositório, e é você quem o cria. Na raiz do projeto:

```powershell
Copy-Item .env.local.example .env.local
```

Depois abra `.env.local` e preencha a `DATABASE_URL` com a credencial do seu
PostgreSQL local:

```
DATABASE_URL=postgresql://eithra_dev_user:SUA_SENHA@localhost:5432/eithra_dev
```

Três observações que costumam custar tempo:

- **A senha precisa ser codificada para URL.** Caracteres como `@`, `:`, `/`,
  `#` e espaço precisam ser escapados. Uma senha com `@` vira `%40`, por
  exemplo: `senha@123` → `senha%40123`.
- **O banco e o usuário já existem** (`eithra_dev` / `eithra_dev_user`). Este
  documento não ensina a criá-los: eles são parte da sua instalação local do
  PostgreSQL.
- **Nunca coloque a credencial em outro arquivo.** Nada de senha em script, em
  código, em documentação ou no Git. Ela vive só no `.env.local`.

## 4. Como trabalhar: os dois scripts

Os dois scripts ficam na raiz, selecionam o `.env.local` por você e **conferem o
alvo antes de executar**.

### Subir o servidor

```powershell
.\run-local.ps1
```

Aceita os argumentos do `runserver`:

```powershell
.\run-local.ps1 0.0.0.0:8000
```

### Rodar testes

```powershell
.\test-local.ps1                      # suíte completa
.\test-local.ps1 apps.cadastros       # um módulo
.\test-local.ps1 apps.condominios apps.governanca   # vários módulos
```

Os scripts usam o Python da venv (`.venv\Scripts\python.exe`), nunca o do PATH.

## 5. A proteção contra o alvo errado

Antes de qualquer coisa, os dois scripts perguntam ao próprio Django qual é o
banco configurado e **imprimem apenas HOST, NAME e USER** — nunca a senha,
nunca a URL inteira. A saída é assim:

```
Eithra - ambiente LOCAL
Banco esperado: localhost / eithra_dev
Banco lido:     localhost / eithra_dev   (usuario: eithra_dev_user)
```

Se o HOST não for `localhost`, `127.0.0.1` ou `::1`, o script **aborta**:

```
ABORTADO: o host configurado e 'ep-...neon.tech', que nao e local.
```

O objetivo é direto: é muito fácil apontar o `.env.local` para o Neon sem
perceber, e a suíte de 1110 testes não deve cair no banco compartilhado por
acidente. Quando o alvo não é local, o script para — e não há como ele "seguir
mesmo assim".

Se você ver a mensagem de que `.env.local` não foi encontrado, é só criar o
arquivo (§3) e rodar de novo.

## 6. Quando você quiser o Neon, conscientemente

O remoto continua disponível, e a forma de usá-lo é **não** usar os scripts: eles
existem para o local.

Para trabalhar contra o Neon, use o `.env` (que já aponta para lá) e chame o
`manage.py` direto:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

E, se preferir ser explícito sobre qual arquivo está lendo:

```powershell
$env:EITHRA_ENV_FILE = ".env"
.\.venv\Scripts\python.exe manage.py check
```

Lembre-se de limpar a variável quando terminar. Note também que os scripts
deixam `EITHRA_ENV_FILE` definido no terminal onde você os rodou — outro motivo
para preferir o terminal já "limpo" ao voltar para o remoto.

## 7. Conferir o alvo sem rodar nada

Um comando só, que imprime HOST, NAME e USER e nada mais:

```powershell
$env:EITHRA_ENV_FILE = ".env.local"
.\.venv\Scripts\python.exe manage.py check
```

Ou, para ver os três valores que os scripts usam na conferência:

```powershell
$env:EITHRA_ENV_FILE = ".env.local"
.\.venv\Scripts\python.exe manage.py shell -c "from django.conf import settings; b = settings.DATABASES['default']; print(b['HOST'], b['NAME'], b['USER'])"
```

## 8. O fluxo oficial

**Desenvolvimento diário**

1. PostgreSQL local;
2. `.\run-local.ps1`;
3. `.\test-local.ps1 <módulo afetado>`.

**Fechamento de fase**

1. testes locais focados no que mudou;
2. suíte local completa quando a fase exigir;
3. homologação no **Neon DEV**;
4. smoke test remoto.

**Produção**

Ambiente próprio, com as variáveis dele. **Nunca** usar a configuração local, e
nunca apontar o local para o banco de produção.

## 10. Os settings de teste também usam PostgreSQL

`config/settings/test.py` existe para execução de testes automatizados, mas
**não tem banco próprio**: ele lê o mesmo `DATABASE_URL` e roda sobre
PostgreSQL, como todos os outros settings do Eithra.

```
Nenhum settings oficial do Eithra usa SQLite.
```

O arquivo originalmente forçava SQLite em memória. Isso foi removido: um banco
diferente do real esconde justamente as diferenças que mais aparecem em
produção — tipos, constraints, ordenação, comportamento transacional. Hoje ele
falha com erro explícito se `DATABASE_URL` não estiver definida, e não cai para
nenhum banco alternativo.

Ele mantém os ajustes que são só de teste: `DEBUG=False`, `SECRET_KEY`
exclusiva, `ALLOWED_HOSTS` para `testserver`, hashers de senha rápidos,
`EMAIL_BACKEND` em memória, `WHITENOISE_AUTOREFRESH` e `SECURE_SSL_REDIRECT`
desligado.

**Onde fica a proteção do "somente local".** O `test.py` **não** fixa
`localhost`: ele aceita o `DATABASE_URL` que lhe for dado, e isso é
proposital — um CI com PostgreSQL próprio precisa poder apontar para o banco
dele. A regra de "só roda se o alvo for local" pertence ao
[test-local.ps1](../test-local.ps1), que é o caminho do desenvolvimento: ele
confere HOST, NAME e USER e aborta se o alvo não for local (§5).

Ou seja: segurança no script, flexibilidade nos settings.

## 11. Regra de segurança

**Teste pesado não roda no remoto por acidente.** A suíte completa tem mais de
mil testes e ocupa por muito tempo o banco compartilhado, que é pequeno e passa
por PgBouncer. Rodar isso no Neon é lento para quem roda e atrapalha quem mais
estiver usando. O caminho é sempre:

- **local** para volume — suíte completa, módulos grandes, `--keepdb`;
- **remoto** para o que precisa do remoto — homologação, smoke test.

Os scripts §4 são o que garante isso na prática: eles só executam quando o alvo
é local.
