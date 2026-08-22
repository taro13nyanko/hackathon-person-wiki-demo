$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $projectDir '.env'

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectDir '.env.example') -Destination $envPath
}

$lines = Get-Content -LiteralPath $envPath -Encoding UTF8
$existing = $lines | Where-Object { $_ -match '^OPENAI_API_KEY=.+$' } | Select-Object -First 1
if ($existing) {
    exit 0
}

Write-Host ''
Write-Host 'OpenAI API key setup for AI Fast Forward.'
Write-Host 'Paste your API key below. The characters will stay hidden.'
$secure = Read-Host 'OpenAI API key' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}

if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Host 'No API key was entered. Starting without AI.'
    exit 0
}

$updated = foreach ($line in $lines) {
    if ($line -match '^OPENAI_API_KEY=') { "OPENAI_API_KEY=$key" } else { $line }
}
Set-Content -LiteralPath $envPath -Value $updated -Encoding UTF8
Write-Host 'The API key was saved locally in .env.'
