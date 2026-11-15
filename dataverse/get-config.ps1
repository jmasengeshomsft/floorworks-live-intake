# Resolves the shared Dataverse config (dataverse\config.json, or the DATAVERSE_* env vars) through the
# gateway's own loader, so the provisioner and seeder agree with the gateway and the agent prompts.
# Usage: $cfg = & .\get-config.ps1   (returns profile, standardTables, tables, equipmentEnabled)
$gw = Join-Path $PSScriptRoot '..\src\intake-gateway'
# Env DATAVERSE_* settings win; otherwise use dataverse\config.json
if (-not $env:DATAVERSE_SCHEMA -and -not $env:DATAVERSE_SCHEMA_FILE) { $env:DATAVERSE_SCHEMA_FILE = (Resolve-Path (Join-Path $PSScriptRoot 'config.json')).Path }
$py = Join-Path $gw '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
Push-Location $gw
try {
  $out = & $py -m app.schema_info
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the Dataverse config (python -m app.schema_info failed)' }
  $out | ConvertFrom-Json
} finally { Pop-Location }
