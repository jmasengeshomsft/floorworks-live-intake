<#
 Renders a prompt template ({{PLACEHOLDERS}}) from the gateway's Dataverse config and prints it.
 Uses the same DATAVERSE_SCHEMA / DATAVERSE_SCHEMA_FILE / BRAND_NAME settings as the gateway (default: dataverse\config.json),
 so prompts and gateway always agree. Usage: $text = & .\render-prompt.ps1 prompts\voice-instructions.md -Brand Acme
#>
param([Parameter(Mandatory)][string]$Template, [string]$Brand = $env:BRAND_NAME)
$gw = Join-Path $PSScriptRoot '..\src\intake-gateway'
if (-not $env:DATAVERSE_SCHEMA -and -not $env:DATAVERSE_SCHEMA_FILE) { $env:DATAVERSE_SCHEMA_FILE = (Resolve-Path (Join-Path $PSScriptRoot '..\dataverse\config.json')).Path }
$py = Join-Path $gw '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$tpl = if ([IO.Path]::IsPathRooted($Template)) { $Template } else { Join-Path $PSScriptRoot $Template }
Push-Location $gw
try {
  $args2 = @('-m', 'app.render_prompts', $tpl)
  if ($Brand) { $args2 += @('--brand', $Brand) }
  $out = & $py @args2
  if ($LASTEXITCODE -ne 0) { throw "render_prompts failed for $Template" }
  ($out -join "`n")
} finally { Pop-Location }
