#Requires -Version 5.1
<#
.SYNOPSIS
    Backup automatico do Eithra: banco e arquivos enviados, com retencao.

.DESCRIPTION
    Faz, em uma rodada:
      (a) pg_dump -F c do banco eithra_dev;
      (b) compacta junto a pasta de arquivos enviados (MEDIA_ROOT = media\);
      (c) grava o .zip em D:\backups_eithra\<data-hora>.zip;
      (d) mantem os ultimos 7 diarios e o ultimo de cada uma das 4 ultimas
          semanas, apagando os mais antigos;
      (e) copia o .zip mais recente para a pasta do OneDrive ou do Google Drive
          do usuario, quando existir;
      (f) grava sucesso ou erro em D:\backups_eithra\log.txt.

    A senha do banco vem de ".env.local" - ela nunca vive neste script. O script
    recusa rodar contra qualquer banco que nao seja "eithra_dev".

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


function Aplicar-Retencao {
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
            Gravar-Log "Backup antigo removido pela retencao: $($z.Name)"
        }
    }
}


function Copiar-ParaNuvem {
    param([string]$Arquivo)
    $candidatos = @()
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'OneDrive*') -Directory -ErrorAction SilentlyContinue
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'Google Drive*') -Directory -ErrorAction SilentlyContinue
    $candidatos += Get-ChildItem -Path (Join-Path $env:USERPROFILE 'GoogleDrive*') -Directory -ErrorAction SilentlyContinue
    $nuvem = $candidatos | Select-Object -First 1
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
}


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
$arquivoDump = $null
try {
    Gravar-Log 'Iniciando backup do Eithra.'

    $pgDump = Achar-Executavel 'pg_dump'
    $banco = Ler-BancoDoAmbiente
    if ($banco.Banco -ne 'eithra_dev') {
        throw "O banco de .env.local e '$($banco.Banco)', e nao 'eithra_dev'. Abortando por seguranca."
    }

    if (-not (Test-Path -LiteralPath $pastaDestino)) {
        New-Item -ItemType Directory -Path $pastaDestino -Force | Out-Null
    }

    $carimbo = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
    $arquivoZip = Join-Path $pastaDestino ("eithra_dev_$carimbo.zip")
    $arquivoDump = Join-Path $env:TEMP ("eithra_dev_$carimbo.dump")

    # (a) Dump do banco em formato custom (comprimido).
    $env:PGPASSWORD = $banco.Senha
    & $pgDump --format=custom --no-owner --no-privileges --no-password `
        --host=$($banco.Host) --port=$($banco.Porta) --username=$($banco.Usuario) `
        --file=$arquivoDump $banco.Banco
    if ($LASTEXITCODE -ne 0) { throw "pg_dump falhou (codigo $LASTEXITCODE)." }
    Gravar-Log ("Dump do banco gerado ({0:N0} bytes)." -f (Get-Item -LiteralPath $arquivoDump).Length)

    # (b) e (c) Compacta o dump e a pasta de arquivos enviados no .zip do dia.
    Compress-Archive -LiteralPath $arquivoDump -DestinationPath $arquivoZip -Force
    if (Test-Path -LiteralPath $pastaMedia) {
        Compress-Archive -Path $pastaMedia -DestinationPath $arquivoZip -Update
        Gravar-Log 'Pasta de arquivos enviados (media) incluida no backup.'
    } else {
        Gravar-Log 'Pasta media nao encontrada; o backup ficou so com o banco.'
    }

    # (d) Retencao.
    Aplicar-Retencao

    # (e) Copia para a nuvem.
    Copiar-ParaNuvem -Arquivo $arquivoZip

    # (f) Sucesso.
    Gravar-Log ("Backup concluido com sucesso: $arquivoZip ({0:N0} bytes)." -f (Get-Item -LiteralPath $arquivoZip).Length)
}
catch {
    Gravar-Log ("ERRO no backup: {0}" -f $_.Exception.Message) 'ERRO'
    exit 1
}
finally {
    if ($arquivoDump -and (Test-Path -LiteralPath $arquivoDump)) {
        Remove-Item -LiteralPath $arquivoDump -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
