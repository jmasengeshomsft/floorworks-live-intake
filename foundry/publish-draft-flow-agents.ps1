# Publishes new versions of the note-taker and the voice agent for the draft-then-submit flow.
$ErrorActionPreference = 'Stop'
$ep = $env:FOUNDRY_PROJECT_ENDPOINT # https://<account>.services.ai.azure.com/api/projects/<project>
if (-not $ep) { throw 'Set FOUNDRY_PROJECT_ENDPOINT' }
$t = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$h = @('-H', "Authorization: Bearer $t", '-H', 'Content-Type: application/json', '-H', 'Foundry-Features: VoiceAgents=V1Preview')

function Publish($name, $mutate) {
  $cur = (curl.exe -s "$ep/agents/$name`?api-version=v1" @h | ConvertFrom-Json).versions.latest
  $def = $cur.definition
  & $mutate $def
  $f = New-TemporaryFile
  @{ description = $cur.description; definition = $def } | ConvertTo-Json -Depth 20 | Set-Content $f -Encoding utf8
  $r = curl.exe -s -X POST "$ep/agents/$name/versions?api-version=v1" @h --data "@$f" | ConvertFrom-Json
  Remove-Item $f
  "$name -> v$($r.version)"
}

if ($args[0] -ne 'voice-only') { Publish 'floorworks-notetaker' { param($d)
  $ins = Get-Content (Join-Path $PSScriptRoot 'prompts\notetaker-instructions.md') -Raw
  $ins = [regex]::Replace($ins, '(?s)<!-- ENRICHMENT:BEGIN.*?-->\r?\n(.*?)<!-- ENRICHMENT:END -->\r?\n?', $(if ($env:ENRICHMENT -eq 'on') { '$1' } else { '' }))
  if ($env:ENRICHMENT -eq 'on') {
    # Foundry IQ: note-taker only. The voice agent is never given the KB tool.
    if (-not $env:SEARCH_SERVICE) { throw 'ENRICHMENT=on requires SEARCH_SERVICE (azd output)' }
    $kb = @{ type = 'mcp'; server_label = 'floorworks-kb'
      server_url = "https://$($env:SEARCH_SERVICE).search.windows.net/knowledgebases/floorworks-kb/mcp?api-version=2026-05-01-preview"
      project_connection_id = 'floorworks-kb'; allowed_tools = @{ tool_names = @('knowledge_base_retrieve') }; require_approval = 'never' }
    $d.tools = @(@($d.tools | Where-Object { $_.server_label -ne 'floorworks-kb' }) + $kb)
  }
  $d.instructions = $ins } }
Publish 'floorworks-voice-agent-app' {
  param($d)
  $d.instructions = $d.instructions.Replace(
    "You can create or update records only after the caller clearly approves the exact change.",
    "You never create or update records: a human service representative reviews the call notes and submits the service ticket.")
  $i = $d.instructions.IndexOf('### Call context')
  $d.instructions = $d.instructions.Substring(0, $i) + @"
### Call context
- A system message at the start gives the Call ID and caller phone. A separate note-taker captures the call and prepares a draft service request, and a human representative submits it. You must NEVER create, update or delete any record, and never tell the caller a ticket has been created. If the caller wants to report a problem, collect their name, the equipment (look it up by name or model with the tools instead of asking for an asset number), and what is wrong, then offer to connect them to a service representative with transfer_to_rep.
"@
}
