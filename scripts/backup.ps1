#Requires -Version 5.1
<#
.SYNOPSIS
    Backup automatico do Eithra: banco e arquivos enviados, criptografado.

.DESCRIPTION
    Faz, em uma rodada:
      (a) pg_dump -F c do banco eithra_dev;
      (b) compacta junto a pasta de arquivos enviados (MEDIA_ROOT = media\);
      (c) grava o .zip CRIPTOGRAFADO (AES-256, via 7-Zip) em
          D:\backups_eithra\<data-hora>.zip;
      (d) mantem os ultimos 7 diarios e o ultimo de cada uma das 4 ultimas
          semanas, apagando os mais antigos;
      (e) copia o .zip mais recente para a pasta do OneDrive ou do Google Drive
          do usuario, quando existir, mantendo la apenas os 7 mais recentes;
      (f) grava sucesso ou erro em D:\backups_eithra\log.txt.

    A senha do banco e a senha de criptografia vem de ".env.local" - elas nunca
    vivem neste script. Sem EITHRA_BACKUP_SENHA o backup FALHA e nada e gravado
    sem protecao. O script recusa rodar contra qualquer banco que nao seja
    "eithra_dev".

.EXAMPLE
    .\scripts\backup.ps1
#>
$ErrorActionPreference = 'Stop'

$raizProjeto = Split-Path -Parent $PSScriptRoot
$arquivoAmbiente = Join-Path $raizProjeto '.env.local'
$pastaDestino = 'D:\backups_eithra'
$arquivoLog = Join-Path $pastaDestino 'log.txt'
$pastaMedia = Join-Path $raizProjeto 'media'

# Quantos backups ficam guardados.
$QTD_DIARIOS = 7
$QTD_SEMANAS = 4
# Quantos backups ficam guardados na nuvem.
$QTD_NUVEM = 7


function Gravar-Log {
    param([string]$Mensagem, [string]$Nivel = 'INFO')
    if (-not (Test-Path -LiteralPath $pastaDestino)) {
        New-Item -ItemType Directory -Path $pastaDestino -Force | Out-Null
    }
    $linha = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Nivel, $Mensagem
    Add-Content -LiteralPath $arquivoLog -Value $linha -Encoding UTF8
    Write-Host $linha
}


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
    throw 'Nao encontrei o 7-Zip (7z.exe). Instale-o para gravar o backup criptografado.'
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


function Get-SemanaIsoKey {
    # Chave "ano-semana" no calendario ISO, valida tambem no Windows PowerShell 5.1.
    param([datetime]$Data)
    $dia = [int]$Data.DayOfWeek
    if ($dia -eq 0) { $dia = 7 }            # domingo conta como 7
    $quinta = $Data.Date.AddDays(4 - $dia)  # quinta-feira da mesma semana ISO
    $primeiroDia = New-Object datetime($quinta.Year, 1, 1)
    $semana = [int][math]::Floor(($quinta - $primeiroDia).TotalDays / 7) + 1
    return ('{0}-{1:d2}' -f $quinta.Year, $semana)
}


function Aplicar-RetencaoLocal {
    $zips = Get-ChildItem -LiteralPath $pastaDestino -Filter '*.zip' -File |
        Sort-Object LastWriteTime -Descending
    if (-not $zips) { return }

    $manter = @{}
    # (d) os N diarios mais recentes.
    foreach ($z in ($zips | Select-Object -First $QTD_DIARIOS)) { $manter[$z.FullName] = $true }
    # (d) o ultimo de cada uma das N semanas mais recentes que tem backup.
    $semanasVistas = @{}
    foreach ($z in $zips) {
        $chave = Get-SemanaIsoKey -Data $z.LastWriteTime
        if (-not $semanasVistas.ContainsKey($chave)) {
            if ($semanasVistas.Count -lt $QTD_SEMANAS) {
                $semanasVistas[$chave] = $true
                $manter[$z.FullName] = $true
            }
        }
    }

    foreach ($z in $zips) {
        if (-not $manter.ContainsKey($z.FullName)) {
            Remove-Item -LiteralPath $z.FullName -Force
            Gravar-Log "Backup antigo removido pela retencao local: $($z.Name)"
        }
    }
}


function Achar-PastaNuvem {
    $candidatos = @()
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'OneDrive*') -Directory -ErrorAction SilentlyContinue
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'Google Drive*') -Directory -ErrorAction SilentlyContinue
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'GoogleDrive*') -Directory -ErrorAction SilentlyContinue
    return ($candidatos | Select-Object -First 1)
}


function Copiar-ParaNuvem {
    param([string]$Arquivo)
    $nuvem = Achar-PastaNuvem
    if (-not $nuvem) {
        Gravar-Log 'Nenhuma pasta de OneDrive/Google Drive encontrada; a copia para a nuvem foi ignorada.'
        return
    }
    $pasta = Join-Path $nuvem.FullName 'Backups Eithra'
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta -Force | Out-Null
    }
    $alvo = Join-Path $pasta (Split-Path -Leaf $Arquivo)
    Copy-Item -LiteralPath $Arquivo -Destination $alvo -Force
    Gravar-Log "Copia enviada para a nuvem: $alvo"

    # Retencao na nuvem: so os N mais recentes permanecem.
    $naNuvem = Get-ChildItem -LiteralPath $pasta -Filter '*.zip' -File |
        Sort-Object LastWriteTime -Descending
    foreach ($z in ($naNuvem | Select-Object -Skip $QTD_NUVEM)) {
        Remove-Item -LiteralPath $z.FullName -Force
        Gravar-Log "Backup antigo removido pela retencao na nuvem: $($z.Name)"
    }
}


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
$arquivoDump = $null
$pastaTemp = $null
try {
    Gravar-Log 'Iniciando backup do Eithra.'

    $pgDump = Achar-Executavel 'pg_dump'
    $seteZip = Achar-SeteZip
    $banco = Ler-VariaveisDoAmbiente
    if ($banco.Banco -ne 'eithra_dev') {
        throw "O banco de .env.local e '$($banco.Banco)', e nao 'eithra_dev'. Abortando por seguranca."
    }
    if ([string]::IsNullOrWhiteSpace($banco.SenhaBackup)) {
        throw 'EITHRA_BACKUP_SENHA nao esta definida em .env.local. O backup nao sera gravado sem criptografia.'
    }

    if (-not (Test-Path -LiteralPath $pastaDestino)) {
        New-Item -ItemType Directory -Path $pastaDestino -Force | Out-Null
    }

    $carimbo = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
    $arquivoZip = Join-Path $pastaDestino ("eithra_dev_$carimbo.zip")
    $pastaTemp = Join-Path $env:TEMP ("eithra_backup_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $pastaTemp -Force | Out-Null
    $arquivoDump = Join-Path $pastaTemp ("eithra_dev_$carimbo.dump")

    # (a) Dump do banco em formato custom (comprimido).
    $env:PGPASSWORD = $banco.Senha
    & $pgDump --format=custom --no-owner --no-privileges --no-password `
        --host=$($banco.Host) --port=$($banco.Porta) --username=$($banco.Usuario) `
        --file=$arquivoDump $banco.Banco
    if ($LASTEXITCODE -ne 0) { throw "pg_dump falhou (codigo $LASTEXITCODE)." }
    Gravar-Log ("Dump do banco gerado ({0:N0} bytes)." -f (Get-Item -LiteralPath $arquivoDump).Length)
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

    # (b) e (c) Compacta o dump e a pasta de arquivos enviados em um .zip
    # CRIPTOGRAFADO com AES-256. A senha vem de EITHRA_BACKUP_SENHA.
    # Cada entrada e adicionada a partir da sua pasta, para que o arquivo guarde
    # caminhos relativos simples ("eithra_dev_....dump" e "media\...").
    $senhaArg = '-p' + $banco.SenhaBackup

    Push-Location $pastaTemp
    try {
        & $seteZip a -tzip -mem=AES256 -mx=5 $senhaArg $arquivoZip $arquivoDump | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7-Zip falhou ao gravar o dump (codigo $LASTEXITCODE)." }
    } finally { Pop-Location }

    if (Test-Path -LiteralPath $pastaMedia) {
        Push-Location $raizProjeto
        try {
            & $seteZip a -tzip -mem=AES256 -mx=5 $senhaArg $arquivoZip 'media' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "7-Zip falhou ao gravar os arquivos enviados (codigo $LASTEXITCODE)." }
        } finally { Pop-Location }
        Gravar-Log 'Pasta de arquivos enviados (media) incluida no backup.'
    } else {
        Gravar-Log 'Pasta media nao encontrada; o backup ficou so com o banco.'
    }

    Gravar-Log 'Backup gravado com criptografia AES-256.'

    # (d) Retencao local.
    Aplicar-RetencaoLocal

    # (e) Copia para a nuvem, com retencao la.
    Copiar-ParaNuvem -Arquivo $arquivoZip

    # (f) Sucesso.
    Gravar-Log ("Backup criptografado concluido com sucesso: $arquivoZip ({0:N0} bytes)." -f (Get-Item -LiteralPath $arquivoZip).Length)
}
catch {
    Gravar-Log ("ERRO no backup: {0}" -f $_.Exception.Message) 'ERRO'
    exit 1
}
finally {
    if ($arquivoDump -and (Test-Path -LiteralPath $arquivoDump)) {
        Remove-Item -LiteralPath $arquivoDump -Force -ErrorAction SilentlyContinue
    }
    if ($pastaTemp -and (Test-Path -LiteralPath $pastaTemp)) {
        Remove-Item -LiteralPath $pastaTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
