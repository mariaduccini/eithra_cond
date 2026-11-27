#Requires -Version 5.1
<#
.SYNOPSIS
    Sobe o Eithra para acesso remoto SEGURO pelo tunel da Cloudflare.

.DESCRIPTION
    Reune os tres passos do acesso de fora de casa, SEM modo de desenvolvimento:
    coleta os arquivos estaticos, sobe o Eithra com o waitress (servidor de
    producao) em 127.0.0.1:8000 e abre o Quick Tunnel da Cloudflare apontando
    para ele. O Eithra roda com config.settings.remoto: DEBUG sempre falso, o
    unico host aceito e o *.trycloudflare.com e nenhuma pagina mostra segredo.

    O servidor escuta apenas em 127.0.0.1 - nunca em 0.0.0.0. Quem chega de fora
    passa pelo tunel; ninguem alcanca o servidor direto pela rede local.

    O endereco https aparece em destaque nesta janela e MUDA a cada reinicio.
    Ao fechar a janela (Ctrl+C), o tunel e o servidor sao encerrados.

    Nenhuma senha vive neste arquivo. A credencial fica apenas em ".env.local",
    que nao e versionado; a conferencia le somente a PRESENCA das variaveis,
    nunca o valor.

    Dica: num acesso exposto a internet, troque o caminho "admin/" previsivel
    por um dificil de adivinhar, em .env.local:
        EITHRA_ADMIN_URL=gestao-eithra-rve/

.EXAMPLE
    .\acesso-remoto-seguro.ps1
#>
$ErrorActionPreference = 'Stop'

$raiz = $PSScriptRoot
$arquivoDeAmbiente = '.env.local'
$caminhoDoAmbiente = Join-Path $raiz $arquivoDeAmbiente
$python = Join-Path $raiz '.venv\Scripts\python.exe'
$cloudflaredPadrao = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$enderecoLocal = 'http://127.0.0.1:8000'
$porta = 8000

Write-Host ''
Write-Host 'Eithra - acesso remoto seguro (tunel da Cloudflare)' -ForegroundColor Cyan
Write-Host 'Servidor: 127.0.0.1:8000 (apenas local)   Ambiente: config.settings.remoto'
Write-Host ''

# --- 1. Python da venv ------------------------------------------------------
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Nao encontrei o Python da venv em: $python" -ForegroundColor Yellow
    Write-Host 'Crie a venv e instale as dependencias: pip install -r requirements.txt'
    exit 1
}

# --- 2. .env.local com as duas variaveis obrigatorias ------------------------
# Le a PRESENCA das variaveis; nunca imprime valor nenhum.
if (-not (Test-Path -LiteralPath $caminhoDoAmbiente)) {
    Write-Host "Nao encontrei o arquivo $arquivoDeAmbiente." -ForegroundColor Yellow
    Write-Host 'Copie .env.local.example para .env.local e preencha DATABASE_URL.'
    exit 1
}

$conteudo = Get-Content -LiteralPath $caminhoDoAmbiente -Raw
$temBanco = $conteudo -match '(?m)^\s*DATABASE_URL\s*=\s*\S'
$temChave = $conteudo -match '(?m)^\s*DJANGO_SECRET_KEY\s*=\s*\S'
if (-not $temBanco -or -not $temChave) {
    Write-Host 'Falta configuracao obrigatoria em .env.local:' -ForegroundColor Yellow
    if (-not $temBanco) {
        Write-Host '  - DATABASE_URL (conexao com o PostgreSQL)'
    }
    if (-not $temChave) {
        Write-Host '  - DJANGO_SECRET_KEY (o ambiente remoto nao usa a chave de desenvolvimento)'
        Write-Host '    Gere uma, por exemplo:' -ForegroundColor Gray
        Write-Host '      -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 50 | % {[char]$_})' -ForegroundColor Gray
    }
    Write-Host 'Nenhum valor foi impresso. Edite .env.local e rode de novo.'
    exit 1
}

# --- 3. Nao conviver com o tunel antigo (esse roda com DEBUG ligado) ---------
$antigo = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'acesso-remoto\.ps1' }
if ($antigo) {
    Write-Host 'AVISO: o acesso-remoto.ps1 antigo (com DEBUG ligado) parece estar rodando.' -ForegroundColor Yellow
    Write-Host 'Feche a janela dele antes de seguir: dois tuneis ao mesmo tempo confundem o teste.'
    Write-Host ''
}

# --- 4. cloudflared ---------------------------------------------------------
$exeTunel = $cloudflaredPadrao
if (-not (Test-Path -LiteralPath $exeTunel)) {
    $encontrado = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($encontrado) { $exeTunel = $encontrado.Source }
}
if (-not $exeTunel -or -not (Test-Path -LiteralPath $exeTunel)) {
    Write-Host 'Nao encontrei o cloudflared.' -ForegroundColor Yellow
    Write-Host 'Instale com: winget install --id Cloudflare.cloudflared'
    exit 1
}

# --- 5. Porta livre ---------------------------------------------------------
if (Get-NetTCPConnection -State Listen -LocalPort $porta -ErrorAction SilentlyContinue) {
    Write-Host "A porta $porta ja esta em uso." -ForegroundColor Yellow
    Write-Host 'Feche o que estiver nela (por exemplo, um runserver) e rode de novo.'
    exit 1
}

# --- 6. Ambiente do processo -----------------------------------------------
$env:EITHRA_ENV_FILE = $arquivoDeAmbiente
$env:DJANGO_SETTINGS_MODULE = 'config.settings.remoto'

# --- 7. Estaticos -----------------------------------------------------------
Write-Host 'Coletando os arquivos estaticos...' -ForegroundColor Gray
& $python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Falha ao coletar os estaticos. Nada foi iniciado.' -ForegroundColor Red
    exit 1
}

$jobTunel = $null
try {
    # --- 8. Tunel em segundo plano, so para ler o endereco que a Cloudflare da
    Write-Host 'Abrindo o tunel da Cloudflare...' -ForegroundColor Gray
    $jobTunel = Start-Job -ScriptBlock {
        param($exe, $alvo)
        & $exe tunnel --url $alvo --no-autoupdate 2>&1
    } -ArgumentList $exeTunel, $enderecoLocal

    $endereco = $null
    for ($i = 0; $i -lt 60 -and -not $endereco; $i++) {
        Start-Sleep -Milliseconds 500
        $saida = Receive-Job -Job $jobTunel -Keep
        $achado = [regex]::Match(($saida -join "`n"), 'https://[A-Za-z0-9-]+\.trycloudflare\.com')
        if ($achado.Success) { $endereco = $achado.Value }
    }

    if (-not $endereco) {
        Write-Host 'O tunel nao entregou um endereco. Ultimas linhas:' -ForegroundColor Red
        Receive-Job -Job $jobTunel -Keep | Select-Object -Last 20
        exit 1
    }

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host '  Eithra disponivel em:' -ForegroundColor Green
    Write-Host ''
    Write-Host "    $endereco" -ForegroundColor Green
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host 'Abra este endereco no celular. Ele MUDA a cada reinicio.' -ForegroundColor Yellow
    Write-Host 'Feche esta janela (Ctrl+C) para encerrar o servidor e o tunel.' -ForegroundColor Gray
    Write-Host ''

    # --- 9. Servidor (waitress) em primeiro plano ---------------------------
    # Enquanto ele vive, esta janela vive; ao Ctrl+C, o finally encerra o tunel.
    & $python -m waitress --listen=127.0.0.1:$porta config.wsgi:application
}
finally {
    if ($jobTunel) {
        Stop-Job -Job $jobTunel -ErrorAction SilentlyContinue
        Remove-Job -Job $jobTunel -Force -ErrorAction SilentlyContinue
    }
    Write-Host ''
    Write-Host 'Acesso remoto encerrado.' -ForegroundColor Cyan
}
