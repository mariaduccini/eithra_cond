#Requires -Version 5.1
<#
.SYNOPSIS
    Restaura um backup do Eithra em um banco NOVO: eithra_restaurado.

.DESCRIPTION
    Recebe o .zip gerado por "scripts\backup.ps1" e:
      1. restaura o banco em "eithra_restaurado" - NUNCA em "eithra_dev";
      2. extrai os arquivos enviados para uma pasta separada, fora do projeto;
      3. confere o que foi restaurado (condominios) e mostra no console.

    A senha do banco vem de ".env.local" - ela nunca vive neste script. Se o
    banco "eithra_restaurado" ja existir, ele e recriado do zero: o alvo da
    restauracao e sempre descartavel, e o "eithra_dev" jamais e tocado.

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


function Ler-BancoDoAmbiente {
    if (-not (Test-Path -LiteralPath $arquivoAmbiente)) {
        throw "Nao encontrei $arquivoAmbiente. Ele guarda a conexao do banco local."
    }
    $linha = Get-Content -LiteralPath $arquivoAmbiente |
        Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
    if (-not $linha) { throw 'DATABASE_URL nao esta definida em .env.local.' }
    $valor = ($linha -split '=', 2)[1].Trim().Trim('"')
    $uri = [System.Uri]$valor
    $partes = $uri.UserInfo -split ':', 2
    [pscustomobject]@{
        Usuario = [System.Uri]::UnescapeDataString($partes[0])
        Senha   = if ($partes.Count -gt 1) { [System.Uri]::UnescapeDataString($partes[1]) } else { '' }
        Host    = $uri.Host
        Porta   = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
        Banco   = $uri.AbsolutePath.TrimStart('/')
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

$banco = Ler-BancoDoAmbiente
if ($banco.Banco -eq $NOME_BANCO_ALVO) {
    throw "O alvo da restauracao nao pode ser o proprio banco apontado por .env.local."
}

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

    # 1. Extrai o backup em uma pasta de trabalho temporaria.
    New-Item -ItemType Directory -Path $pastaTrabalho -Force | Out-Null
    Expand-Archive -LiteralPath $caminhoZip -DestinationPath $pastaTrabalho -Force

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
    $mediaNoBackup = Join-Path $pastaTrabalho 'media'
    if (Test-Path -LiteralPath $mediaNoBackup) {
        New-Item -ItemType Directory -Path $pastaArquivos -Force | Out-Null
        Copy-Item -LiteralPath $mediaNoBackup -Destination $pastaArquivos -Recurse -Force
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
