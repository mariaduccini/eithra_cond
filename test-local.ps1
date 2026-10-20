#Requires -Version 5.1
<#
.SYNOPSIS
    Roda testes do Eithra contra o PostgreSQL LOCAL.

.DESCRIPTION
    Seleciona o arquivo de ambiente ".env.local" por EITHRA_ENV_FILE, confere
    que o alvo e mesmo o banco local e so entao executa os testes com o Python
    da venv. Se o alvo nao for local, o script aborta - nenhuma suite pesada
    deve cair no banco remoto por descuido.

    Sem argumentos, roda a suite completa. Com argumentos, roda apenas os
    rotulos informados.

    Nenhuma senha vive aqui. A credencial fica apenas em ".env.local", que nao
    e versionado. A conferencia le somente HOST, NAME e USER.

.EXAMPLE
    .\test-local.ps1

.EXAMPLE
    .\test-local.ps1 apps.cadastros

.EXAMPLE
    .\test-local.ps1 apps.condominios apps.governanca
#>
$ErrorActionPreference = 'Stop'

$arquivoDeAmbiente = '.env.local'
$caminhoDoAmbiente = Join-Path $PSScriptRoot $arquivoDeAmbiente
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $caminhoDoAmbiente)) {
    Write-Host ''
    Write-Host 'Eithra - testes no ambiente LOCAL' -ForegroundColor Cyan
    Write-Host ''
    Write-Host "Nao encontrei o arquivo $arquivoDeAmbiente." -ForegroundColor Yellow
    Write-Host 'Ele guarda a conexao com o seu PostgreSQL local e nao vai para o Git.'
    Write-Host ''
    Write-Host 'Crie o arquivo a partir do modelo e preencha a senha do banco local:'
    Write-Host ''
    Write-Host "    Copy-Item $arquivoDeAmbiente.example $arquivoDeAmbiente" -ForegroundColor Gray
    Write-Host ''
    exit 1
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Nao encontrei o Python da venv em: $python" -ForegroundColor Yellow
    Write-Host 'Crie a venv e instale as dependencias antes de continuar.'
    exit 1
}

$env:EITHRA_ENV_FILE = $arquivoDeAmbiente

# O alvo vem do proprio Django: e ele quem interpreta a string de conexao. A
# sonda le apenas HOST, NAME e USER - nunca a senha, nunca a URL inteira.
#
# As aspas do codigo Python sao simples de proposito: o PowerShell 5 nao escapa
# aspas duplas ao montar a linha de comando de um executavel nativo, e o codigo
# chegaria ao Python com as aspas comidas.
$sondaDoAlvo = @'
from django.conf import settings

banco = settings.DATABASES['default']
host = (banco.get('HOST') or '').strip()
nome = (banco.get('NAME') or '').strip()
usuario = (banco.get('USER') or '').strip()
print('eithra-alvo|' + host + '|' + nome + '|' + usuario)
'@

# A preferencia e afrouxada so durante a sonda: no PowerShell 5, o stderr de um
# comando nativo redirecionado vira erro e abortaria a leitura da saida.
$preferenciaAnterior = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$saida = & $python manage.py shell -c $sondaDoAlvo 2>&1
$ErrorActionPreference = $preferenciaAnterior

$linhaDoAlvo = $saida | Where-Object { $_ -like 'eithra-alvo|*' } | Select-Object -First 1

if (-not $linhaDoAlvo) {
    Write-Host ''
    Write-Host 'Nao consegui ler o banco configurado em .env.local.' -ForegroundColor Red
    Write-Host 'Confira a DATABASE_URL do arquivo e rode: .\.venv\Scripts\python.exe manage.py check'
    Write-Host ''
    exit 1
}

$partes = $linhaDoAlvo -split '\|'
$hostDoBanco = $partes[1]
$nomeDoBanco = $partes[2]
$usuarioDoBanco = $partes[3]

Write-Host ''
Write-Host 'Eithra - testes no ambiente LOCAL' -ForegroundColor Cyan
Write-Host 'Banco esperado: localhost / eithra_dev'
Write-Host "Banco lido:     $hostDoBanco / $nomeDoBanco   (usuario: $usuarioDoBanco)"
Write-Host ''

if ($hostDoBanco -notin @('localhost', '127.0.0.1', '::1')) {
    Write-Host "ABORTADO: o host configurado e '$hostDoBanco', que nao e local." -ForegroundColor Red
    Write-Host 'Este script existe para rodar testes contra o PostgreSQL local.'
    Write-Host 'Para testar no ambiente remoto, use o fluxo do remoto de forma consciente.'
    Write-Host ''
    exit 1
}

if ($args.Count -eq 0) {
    Write-Host 'Suite completa. Para focar em um modulo: .\test-local.ps1 apps.cadastros' -ForegroundColor Gray
    Write-Host ''
    & $python manage.py test
} else {
    Write-Host ("Modulos: " + ($args -join ' ')) -ForegroundColor Gray
    Write-Host ''
    & $python manage.py test @args
}
