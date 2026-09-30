# Optional portable installation; normal installed Ollama also works.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $projectRoot '.runtime\ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'Portable Ollama is missing. Install native Ollama or extract its official Windows archive into .runtime/ollama.'
}
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $projectRoot '.runtime\models'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
try {
    $version = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
    Write-Output "Ollama already running: $($version.version)"
} catch {
    Start-Process -FilePath $executable -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $projectRoot `
        -RedirectStandardOutput (Join-Path $projectRoot '.runtime\server.stdout.log') `
        -RedirectStandardError (Join-Path $projectRoot '.runtime\server.stderr.log') | Out-Null
    Write-Output 'Ollama starting on 127.0.0.1:11434. Use recognition status to check readiness.'
}
