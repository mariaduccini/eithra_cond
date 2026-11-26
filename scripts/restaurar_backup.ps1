#Requires -Version 5.1
<#
.SYNOPSIS
    Restaura um backup do Eithra em um banco NOVO: eithra_restaurado.

.DESCRIPTION
    Recebe o .zip CRIPTOGRAFADO gerado por "scripts\backup.ps1" e:
      1. descompacta com a senha EITHRA_BACKUP_SENHA (AES-256, via 7-Zip);
      2. restaura o banco em "eithra_restaurado" - NUNCA em "eithra_dev";
      3. extrai os arquivos enviados para uma pasta separada, fora do projeto;
      4. confere o que foi restaurado (condominios) e mostra no console.

    A senha do banco e a senha de criptografia vem de ".env.local" - elas nunca
    vivem neste script. Se a senha de criptografia nao estiver no arquivo, ela e
    pedida no console. Se o banco "eithra_restaurado" ja existir, ele e recriado
    do zero: o alvo da restauracao e sempre descartavel, e o "eithra_dev" jamais
    e tocado.

.EXAMPLE
    .\scripts\restaurar_backup.ps1 D:\backups_eithra\eithra_dev_2026-11-26_02-00-00.zip
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ArquivoZip
)

$ErrorActionPreference = 'Stop'

$raizProjeto = Split-Path -Parent $PSScriptRoot
$arquivoAmbiente = Join-Path $raizProjeto '.env.local'
$pastaDestino = 'D:\backups_eithra'
$NOME_BANCO_ALVO = 'eithra_restaurado'
$NOME_BANCO_PROTEGIDO = 'eithra_dev'


function Achar-Executavel {
    param([string]$Nome)
    $noPath = Get-Command $Nome -ErrorAction SilentlyContinue
    if ($noPath) { return $noPath.Source }
    $achado = Get-ChildItem -Path "C:\Program Files\PostgreSQL\*\bin\$Nome.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($achado) { return $achado.FullName }
    throw "Nao encontrei $Nome. Ajuste o PATH ou instale o cliente do PostgreSQL."
}


function Achar-SeteZip {
    $noPath = Get-Command '7z' -ErrorAction SilentlyContinue
    if ($noPath) { return $noPath.Source }
    $caminhos = @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\7-Zip\7z.exe')
    )
    foreach ($c in $caminhos) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    throw 'Nao encontrei o 7-Zip (7z.exe). Ele e necessario para abrir o backup criptografado.'
}


function Ler-VariaveisDoAmbiente {
    if (-not (Test-Path -LiteralPath $arquivoAmbiente)) {
        throw "Nao encontrei $arquivoAmbiente. Ele guarda a conexao do banco local."
    }
    $linhas = Get-Content -LiteralPath $arquivoAmbiente

    $linhaUrl = $linhas | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
    if (-not $linhaUrl) { throw 'DATABASE_URL nao esta definida em .env.local.' }
    $valorUrl = ($linhaUrl -split '=', 2)[1].Trim().Trim('"')
    $uri = [System.Uri]$valorUrl
    $partes = $uri.UserInfo -split ':', 2

    $linhaSenha = $linhas | Where-Object { $_ -match '^\s*EITHRA_BACKUP_SENHA\s*=' } | Select-Object -First 1
    $senhaBackup = ''
    if ($linhaSenha) { $senhaBackup = ($linhaSenha -split '=', 2)[1].Trim().Trim('"') }

    [pscustomobject]@{
        Usuario = [System.Uri]::UnescapeDataString($partes[0])
        Senha   = if ($partes.Count -gt 1) { [System.Uri]::UnescapeDataString($partes[1]) } else { '' }
        Host    = $uri.Host
        Porta   = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
        Banco   = $uri.AbsolutePath.TrimStart('/')
        SenhaBackup = $senhaBackup
    }
}


function Executar-Psql {
    param([string]$Banco, [string]$Sql)
    $saida = & $script:psql --host=$script:hostBanco --port=$script:porta --username=$script:usuario `
        --dbname=$Banco --tuples-only --no-align --command=$Sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql falhou: $saida" }
    return ($saida | Where-Object { $_ -notmatch '^\s*$' })
}


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
$caminhoZip = (Resolve-Path -LiteralPath $ArquivoZip).Path
$itemZip = Get-Item -LiteralPath $caminhoZip
if ($itemZip.PSIsContainer -or $itemZip.Extension -ne '.zip') {
    throw "Informe o arquivo .zip gerado pelo backup."
}

$banco = Ler-VariaveisDoAmbiente
if ($banco.Banco -eq $NOME_BANCO_ALVO) {
    throw "O alvo da restauracao nao pode ser o proprio banco apontado por .env.local."
}

# A senha de criptografia vem de .env.local; se faltar, ela e pedida no console.
$senhaCripto = $banco.SenhaBackup
if ([string]::IsNullOrWhiteSpace($senhaCripto)) {
    $segura = Read-Host -Prompt 'Senha do backup (EITHRA_BACKUP_SENHA)' -AsSecureString
    $senhaCripto = [System.Net.NetworkCredential]::new('', $segura).Password
}
if ([string]::IsNullOrWhiteSpace($senhaCripto)) {
    throw 'Sem a senha do backup nao e possivel abrir o arquivo criptografado.'
}

$seteZip = Achar-SeteZip
$pgRestore = Achar-Executavel 'pg_restore'
$psql = Achar-Executavel 'psql'
$createdb = Achar-Executavel 'createdb'
$dropdb = Achar-Executavel 'dropdb'

$script:psql = $psql
$script:hostBanco = $banco.Host
$script:porta = $banco.Porta
$script:usuario = $banco.Usuario
$env:PGPASSWORD = $banco.Senha

$carimbo = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
$pastaTrabalho = Join-Path $env:TEMP ("eithra_restore_" + [guid]::NewGuid().ToString('N'))
$pastaArquivos = Join-Path (Join-Path $pastaDestino 'restaurado') $carimbo

try {
    Write-Host ''
    Write-Host 'Restauracao do Eithra' -ForegroundColor Cyan
    Write-Host "Arquivo: $caminhoZip"
    Write-Host "Banco alvo: $NOME_BANCO_ALVO   (protegido: $NOME_BANCO_PROTEGIDO)"
    Write-Host ''

    # 1. Descompacta o backup criptografado em uma pasta de trabalho temporaria.
    New-Item -ItemType Directory -Path $pastaTrabalho -Force | Out-Null
    $senhaArg = '-p' + $senhaCripto
    & $seteZip x -y $senhaArg ("-o" + $pastaTrabalho) $caminhoZip | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Nao consegui abrir o backup (7-Zip codigo $LASTEXITCODE). Senha correta?" }

    $dump = Get-ChildItem -LiteralPath $pastaTrabalho -Filter '*.dump' -File -Recurse | Select-Object -First 1
    if (-not $dump) { throw 'Nao encontrei o arquivo .dump dentro do backup.' }

    # 2. Cria o banco alvo do zero - nunca o eithra_dev.
    $existe = Executar-Psql -Banco 'postgres' -Sql "SELECT 1 FROM pg_database WHERE datname = '$NOME_BANCO_ALVO'"
    if ($existe) {
        Write-Host "Banco '$NOME_BANCO_ALVO' ja existia; sera recriado." -ForegroundColor Yellow
        & $dropdb --host=$($banco.Host) --port=$($banco.Porta) --username=$($banco.Usuario) --if-exists $NOME_BANCO_ALVO
        if ($LASTEXITCODE -ne 0) { throw 'Nao consegui remover o banco de restauracao anterior.' }
    }
    & $createdb --host=$($banco.Host) --port=$($banco.Porta) --username=$($banco.Usuario) $NOME_BANCO_ALVO
    if ($LASTEXITCODE -ne 0) { throw 'Nao consegui criar o banco de restauracao.' }
    Write-Host "Banco '$NOME_BANCO_ALVO' criado." -ForegroundColor Green

    # 3. Restaura o conteudo do dump.
    & $pgRestore --host=$($banco.Host) --port=$($banco.Porta) --username=$($banco.Usuario) `
        --dbname=$NOME_BANCO_ALVO --no-owner --no-privileges --exit-on-error $dump.FullName
    if ($LASTEXITCODE -ne 0) { throw "pg_restore falhou (codigo $LASTEXITCODE)." }
    Write-Host 'Banco restaurado.' -ForegroundColor Green

    # 4. Extrai os arquivos enviados para uma pasta separada.
    $mediaNoBackup = Get-ChildItem -LiteralPath $pastaTrabalho -Directory -Recurse |
        Where-Object { $_.Name -eq 'media' } | Select-Object -First 1
    if ($mediaNoBackup) {
        New-Item -ItemType Directory -Path $pastaArquivos -Force | Out-Null
        Copy-Item -LiteralPath $mediaNoBackup.FullName -Destination (Join-Path $pastaArquivos 'media') -Recurse -Force
        Write-Host "Arquivos enviados restaurados em: $pastaArquivos\media" -ForegroundColor Green
    } else {
        Write-Host 'O backup nao tinha pasta de arquivos enviados.' -ForegroundColor Yellow
    }

    # 5. Confere o resultado.
    Write-Host ''
    Write-Host 'Conferencia do banco restaurado' -ForegroundColor Cyan
    $total = Executar-Psql -Banco $NOME_BANCO_ALVO -Sql 'SELECT count(*) FROM condominios_condominio'
    Write-Host ("Condominios no banco restaurado: {0}" -f (($total | Select-Object -First 1).Trim()))

    $alvo = Executar-Psql -Banco $NOME_BANCO_ALVO -Sql "SELECT nome FROM condominios_condominio WHERE nome ILIKE '%Rio Vermelho Exclusive%'"
    if ($alvo) {
        Write-Host ''
        Write-Host 'Encontrado no banco restaurado:' -ForegroundColor Green
        foreach ($nome in $alvo) { Write-Host ("  - " + $nome.Trim()) -ForegroundColor Green }
        Write-Host ''
        Write-Host 'RESTAURACAO CONCLUIDA E CONFERIDA COM SUCESSO.' -ForegroundColor Green
    } else {
        Write-Host 'Nao encontrei "Rio Vermelho Exclusive" no banco restaurado.' -ForegroundColor Red
        exit 1
    }
}
catch {
    Write-Host ''
    Write-Host ("ERRO na restauracao: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $pastaTrabalho) {
        Remove-Item -LiteralPath $pastaTrabalho -Recurse -Force -ErrorAction SilentlyContinue
    }
}
