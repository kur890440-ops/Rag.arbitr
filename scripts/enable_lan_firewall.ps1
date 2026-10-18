#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$ruleName = 'rag.arbitr LAN HTTPS 8765'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$listener = Get-NetTCPConnection -State Listen -LocalPort 8765 | Select-Object -First 1
$pythonPath = (Get-Process -Id $listener.OwningProcess).Path
if (-not $pythonPath.StartsWith($repositoryRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Port 8765 is not owned by this checkout.'
}
$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existingRule) { throw 'Rule already exists. Inspect it before changing it.' }
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -LocalAddress 192.168.11.41 -RemoteAddress 192.168.11.0/24 -Profile Any -Program $pythonPath
