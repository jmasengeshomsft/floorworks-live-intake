<#
Adds the transfer_to_rep function tool (and the instruction line) to a Foundry voice agent and publishes a new version.
Usage:
  az login
  .\foundry\add-transfer-tool.ps1 -Endpoint https://<account>.services.ai.azure.com/api/projects/<project> -Agent <voice agent name>
  .\foundry\add-transfer-tool.ps1 -Endpoint ... -Agent ... -WhatIf     # show the result without publishing
#>
param(
  [Parameter(Mandatory)][string]$Endpoint,
  [Parameter(Mandatory)][string]$Agent,
  [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'
$Endpoint = $Endpoint.TrimEnd('/')
$line = 'If the caller asks for a person, call transfer_to_rep. Never search for it.'

$t = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
if (-not $t) { throw 'Not logged in. Run: az login' }
$h = @('-H', "Authorization: Bearer $t", '-H', 'Content-Type: application/json', '-H', 'Foundry-Features: VoiceAgents=V1Preview')

$resp = curl.exe -s "$Endpoint/agents/$Agent`?api-version=v1" @h | ConvertFrom-Json
$cur = $resp.versions.latest
if (-not $cur) { throw "Agent '$Agent' not found at $Endpoint. Response: $($resp | ConvertTo-Json -Compress -Depth 4)" }
Write-Host "Current version: $($cur.version)"
$def = $cur.definition

$tool = [ordered]@{
  type = 'function'; name = 'transfer_to_rep'; response_scheduling = 'when_idle'
  description = 'Connect the live caller to a human service representative. Call only when the caller explicitly asks for a person.'
  parameters = [ordered]@{ type = 'object'; required = @(); properties = @{} }
}
$def.tools = @(@($def.tools | Where-Object { $_.name -ne 'transfer_to_rep' }) + $tool)
if ($def.instructions -notmatch 'transfer_to_rep') { $def.instructions = ("$($def.instructions)".TrimEnd() + "`n`n" + $line) }

if ($WhatIf) { $def.tools | ConvertTo-Json -Depth 8; return }

$f = New-TemporaryFile
@{ description = $cur.description; definition = $def } | ConvertTo-Json -Depth 20 | Set-Content $f -Encoding utf8
$r = curl.exe -s -X POST "$Endpoint/agents/$Agent/versions?api-version=v1" @h --data "@$f" | ConvertFrom-Json
Remove-Item $f
if (-not $r.version) { throw "Publish failed: $($r | ConvertTo-Json -Compress -Depth 4)" }
Write-Host "Published $Agent v$($r.version)" -ForegroundColor Green

$chk = (curl.exe -s "$Endpoint/agents/$Agent`?api-version=v1" @h | ConvertFrom-Json).versions.latest.definition.tools
if ($chk.name -contains 'transfer_to_rep') { Write-Host 'Verified: transfer_to_rep is on the latest version.' -ForegroundColor Green }
else { Write-Host 'WARNING: transfer_to_rep not found on the latest version.' -ForegroundColor Yellow }
