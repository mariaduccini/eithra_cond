# Segurança e backup do Eithra

Este documento reúne as decisões de proteção do Eithra: o desligamento da
integração com o Gmail, o backup automático, a restauração em caso de pane, o
bloqueio de tentativas de senha e os ajustes de produção. Ele é a referência
para quem precisar agir rápido — sobretudo na hora de restaurar.

---

## 1. Gmail desligado (`EITHRA_GMAIL_ATIVO`)

A supervisão passou a registrar tudo manualmente. A leitura automática de
mensagens do Gmail está desligada por padrão.

- `EITHRA_GMAIL_ATIVO` (padrão `False`) controla a integração inteira.
- Com `False`:
  - o comando `sincronizar_gmail` não faz nada e avisa no log;
  - os blocos da integração (ex.: “Mensagens que pedem uma decisão”) ficam
    ocultos nas telas;
  - nenhuma chamada de IA de e-mail acontece.
- Para religar temporariamente, defina `EITHRA_GMAIL_ATIVO=True` no ambiente.
  O código da integração continua existindo; ele apenas não é acionado.

### Credenciais OAuth do Gmail

- Nenhum arquivo de token/credencial OAuth do Gmail foi encontrado fora do
  repositório (busca por `token.json`, `credentials.json`, `client_secret*` e
  similares em `%USERPROFILE%`).
- Para revogar qualquer acesso remanescente do aplicativo na conta Google:
  **https://myaccount.google.com/permissions** → selecione o app na lista →
  “Remover acesso”.

---

## 2. Backup automático — `scripts\backup.ps1`

Uma rodada faz, em ordem:

1. `pg_dump -F c` (formato custom) do banco **`eithra_dev`**;
2. compacta o dump **junto com a pasta de arquivos enviados** (`MEDIA_ROOT`,
   ou seja, `media\` na raiz do projeto: fotos, evidências e PDFs);
3. grava o pacote **criptografado com AES-256** (via 7-Zip) em
   **`D:\backups_eithra\<data-hora>.zip`**;
4. aplica a **retenção local**: mantém os **7 diários** mais recentes e **o
   último de cada uma das 4 semanas** mais recentes; apaga os mais antigos;
5. copia o `.zip` mais recente para a pasta do **OneDrive** ou **Google Drive**
   do usuário (procura em `%USERPROFILE%`; se não existir, registra e segue) e
   **mantém lá apenas os 7 mais recentes**, apagando os mais antigos;
6. grava **sucesso ou erro** em **`D:\backups_eithra\log.txt`**.

### Criptografia (AES-256)

- O pacote é gravado pelo **7-Zip** (`7z a -tzip -mem=AES256`) com a senha lida
  de **`EITHRA_BACKUP_SENHA`** no `.env.local`.
- **Sem a senha o backup falha** e registra o erro no log — o script **nunca
  gera um arquivo sem proteção**.
- Requer o 7-Zip instalado (`winget install --id 7zip.7zip -e`). O script
  procura `7z.exe` no `PATH` e nos diretórios usuais de instalação.

Regras de segurança do script:

- A senha do banco vem de **`.env.local`** (campo `DATABASE_URL`); a senha
  nunca vive no script.
- A senha de criptografia vem de **`EITHRA_BACKUP_SENHA`** no `.env.local`.
- O script **aborta** se o banco apontado por `.env.local` não for `eithra_dev`.
- Nenhuma credencial é escrita em log.

### Agendamento (Agendador de Tarefas do Windows)

- Tarefa: **`Eithra - Backup`**, diária às **02:00**.
- Comando: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
  "D:\github\eithra_v3\scripts\backup.ps1"`.
- **Limitação conhecida:** a tarefa foi registrada sem elevação, então ela roda
  apenas com o usuário logado.

Para rodar **mesmo sem ninguém logado**, abra o **PowerShell como
Administrador** e cole o bloco abaixo (ele recria a tarefa do zero):

```powershell
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "D:\github\eithra_v3\scripts\backup.ps1"'
$gatilho = New-ScheduledTaskTrigger -Daily -At '02:00'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Password -RunLevel Highest
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Unregister-ScheduledTask -TaskName 'Eithra - Backup' -Confirm:$false
Register-ScheduledTask -TaskName 'Eithra - Backup' -Action $acao -Trigger $gatilho `
    -Principal $principal -Settings $config
```

O `-LogonType Password` (com `-RunLevel Highest`) é o que faz a tarefa executar
sem sessão aberta. O Windows pedirá a senha da conta no momento do registro.

---

## 3. Restauração em caso de pane — `scripts\restaurar_backup.ps1`

Restaura **sem nunca tocar no `eithra_dev`**:

```powershell
.\scripts\restaurar_backup.ps1 D:\backups_eithra\eithra_dev_AAAA-MM-DD_HH-MM-SS.zip
```

O que ele faz:

1. **descompacta** o `.zip` criptografado com a senha `EITHRA_BACKUP_SENHA`
   (via 7-Zip); se a senha não estiver no `.env.local`, ela é pedida no console;
2. **recria** o banco **`eithra_restaurado`** do zero (se já existir, é
   descartado e recriado) — o alvo é sempre descartável;
3. restaura o dump com `pg_restore --no-owner --no-privileges --exit-on-error`;
4. extrai os arquivos enviados para **`D:\backups_eithra\restaurado\<carimbo>\media`**;
5. confere o resultado: total de condomínios e a presença de
   “Rio Vermelho Exclusive”.

Ao final imprime `RESTAURACAO CONCLUIDA E CONFERIDA COM SUCESSO.`

### Passo a passo para promover uma restauração (pane real)

1. Rode `scripts\restaurar_backup.ps1` apontando para o `.zip` desejado.
2. Confira no console que o banco alvo é `eithra_restaurado` e que o
   condomínio esperado aparece.
3. Ajuste a `DATABASE_URL` do ambiente de destino para apontar para o banco
   restaurado (renomeie/copie conforme a infraestrutura).
4. Restaure os arquivos enviados a partir de
   `D:\backups_eithra\restaurado\<carimbo>\media`.
5. Só depois de validar, aposente o banco antigo.

O script **nunca sobrescreve o `eithra_dev`**; a promoção é sempre um passo
manual e consciente.

---

## 4. Bloqueio de tentativas de senha (django-axes)

- Biblioteca: **django-axes** (em `requirements.txt`).
- **5 senhas erradas** seguidas bloqueiam a combinação **usuário + IP** por
  **30 minutos**.
- As tentativas ficam registradas no **admin do Django** (`AccessAttempt` /
  `AccessLog`) — a administração vê quem tentou, de onde e quando.
- Durante o bloqueio, mesmo a **senha correta** é recusada: o Axes barra antes
  de qualquer backend autenticar.
- `AXES_USERNAME_FORM_FIELD = "username"` garante que a chave usada para gravar
  e para consultar a tentativa seja a mesma (o e-mail viaja no campo
  `username` do formulário de login).
- A senha mínima é de **10 caracteres** (`AUTH_PASSWORD_VALIDATORS`).
- O backend `apps.accounts.backends.BackendDeBloqueio` tolera chamadas de
  autenticação sem `request` (usadas em testes internos), sem afetar o bloqueio
  real, que sempre passa pela tela e traz o `request`.

---

## 5. Produção (`config/settings/production.py`)

- `DEBUG = False`.
- `SECRET_KEY` obrigatória do ambiente: sem ela, a aplicação **não sobe**
  (`ImproperlyConfigured`). O mesmo vale para `DATABASE_URL`.
- `SESSION_COOKIE_SECURE = True` e `CSRF_COOKIE_SECURE = True`.
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` para o túnel.
- `SECURE_HSTS_SECONDS = 3600` (e `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`).
- `X_FRAME_OPTIONS = "DENY"`.
- A sessão expira após **8 horas sem uso** (`SESSION_COOKIE_AGE = 8*60*60` com
  `SESSION_SAVE_EVERY_REQUEST = True`: o relógio conta da última requisição, e
  não do login).
- O admin do Django fica em URL configurável por **`EITHRA_ADMIN_URL`**
  (padrão `admin/`); um caminho difícil de adivinhar reduz a varredura
  automática.

---

## 6. PostgreSQL — aceita conexões de onde?

Estado atual da instalação local:

- `postgresql.conf`: `listen_addresses = '*'` — o servidor **escuta em todas as
  interfaces**.
- `pg_hba.conf`: só aceita as linhas de **localhost** (`127.0.0.1/32` e
  `::1/128`). Não há `host all all 0.0.0.0/0 ...`, então **conexões externas
  são rejeitadas** na prática.

Conclusão: hoje o banco **não aceita conexões de fora** (o `pg_hba.conf` barra).
Resta, ainda assim, uma recomendação — **não aplicada**: fazer o servidor nem
escutar na rede, reduzindo a superfície mesmo se o `pg_hba.conf` for alterado por
engano.

Para aplicar, abra o **PowerShell como Administrador** e cole o bloco abaixo
(não foi executado por ninguém; é sua decisão rodá-lo):

```powershell
$conf = 'C:\Program Files\PostgreSQL\17\data\postgresql.conf'
$bak  = "$conf.bak-listen-localhost"

# Protege o original antes de mexer.
Copy-Item -LiteralPath $conf -Destination $bak -Force

# Fixa listen_addresses em 'localhost' (substitui a linha, ou acrescenta se faltar).
$conteudo = Get-Content -LiteralPath $conf
if ($conteudo -match "^\s*#?\s*listen_addresses\s*=") {
    $conteudo = $conteudo -replace "^\s*#?\s*listen_addresses\s*=.*$", "listen_addresses = 'localhost'"
} else {
    $conteudo += "listen_addresses = 'localhost'"
}
Set-Content -LiteralPath $conf -Value $conteudo -Encoding ASCII

# Reinicia o servico para valer.
Restart-Service -Name 'postgresql-x64-17' -Force

# Confere o valor em vigor.
& 'C:\Program Files\PostgreSQL\17\bin\psql.exe' -U postgres -c "SHOW listen_addresses;"
```

Ajuste o nome do serviço (`postgresql-x64-17`) se a instalação usar outro.
Para desfazer, restaure o backup: `Copy-Item $bak $conf -Force` e reinicie.

Nenhuma alteração foi feita nos arquivos do PostgreSQL até aqui.

---

## 7. Ambiente padrão seguro e o Neon

### Padrão de arquivo de ambiente

- `config/settings/base.py`: quando **`EITHRA_ENV_FILE` não está definida**, o
  arquivo lido é **`.env.local`** (o local) — e não mais `.env`. Subir contra a
  nuvem passou a exigir escolha explícita, nunca um acidente de rodar `manage.py`.
- Por isso, `manage.py` direto no desenvolvimento agora sobe contra o banco
  **local**.

### Neon

- O antigo `.env` (que apontava para o Neon) foi **renomeado** para
  **`.env.neon.desativado`** — nada foi apagado, e o arquivo **não é lido** por
  nenhum caminho padrão.
- `.env.local` e `.env.neon.desativado` estão no **`.gitignore`**.
- Com o novo padrão, **não há mais caminho automático para o Neon**. Só um
  `EITHRA_ENV_FILE` apontando de forma consciente alcançaria o arquivo.
- **Nada foi apagado** e a senha não é exibida em lugar algum. Regra vigente:
  **não usar o banco Neon**.

---

## 8. Resumo operacional

| Assunto | Onde | Como acionar |
| --- | --- | --- |
| Backup (criptografado AES-256) | `scripts\backup.ps1` | Agendador, tarefa “Eithra - Backup”, 02:00 |
| Senha do backup | `EITHRA_BACKUP_SENHA` em `.env.local` | guardada fora do computador |
| Log do backup | `D:\backups_eithra\log.txt` | leitura direta |
| Retenção na nuvem | `OneDrive\Backups Eithra` | automática (7 mais recentes) |
| Restauração | `scripts\restaurar_backup.ps1 <zip>` | manual, banco `eithra_restaurado` |
| Bloqueio de login | django-axes | automático (5 erros → 30 min) |
| Ambiente padrão | `EITHRA_ENV_FILE` (padrão `.env.local`) | variável de ambiente |
| Neon | `.env.neon.desativado` | desativado, não lido |
| Desligar/ligar Gmail | `EITHRA_GMAIL_ATIVO` | variável de ambiente |
| Admin | `EITHRA_ADMIN_URL` | variável de ambiente |
| Revogar Gmail | https://myaccount.google.com/permissions | manual na conta Google |
