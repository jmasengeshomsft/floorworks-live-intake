<#
 Runs a script using values from the .env file next to it (KEY=VALUE per line, keys = parameter names).
 Explicit parameters still win. Usage:  .\lib\run.ps1 teams\setup-teams.ps1 [-Param value ...]
#>
param([Parameter(Mandatory, Position = 0)][string]$Script, [Parameter(ValueFromRemainingArguments)]$Rest)
$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path (Split-Path $PSScriptRoot -Parent) $Script)).Path
$envFile = Join-Path (Split-Path $path -Parent) '.env'
$cmd = Get-Command $path
$splat = @{}
if (Test-Path $envFile) {
  foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*$') { continue }
    $k = $Matches[1]; $v = $Matches[2].Trim('"', "'")
    if (-not $cmd.Parameters.ContainsKey($k)) { continue }
    $splat[$k] = if ($cmd.Parameters[$k].ParameterType -eq [string[]]) { $v -split '\s*,\s*' } else { $v }
  }
} else { Write-Warning "No .env next to $path" }
& $path @splat @Rest
