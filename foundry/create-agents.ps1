<#
 Creates the FloorWorks agents from scratch in a Foundry project (idempotent: re-running publishes new versions).
   1. Toolbox  floorworks-dataverse  (Dataverse MCP via the project-MI connection)
   2. Note-taker prompt agent (Dataverse MCP, plus the Foundry IQ KB tool when -Enrichment on)
   3. Voice agent floorworks-voice-agent-app (Voice Live, toolbox + transfer_to_rep)
 Prerequisite: foundry\setup-foundry.ps1 (creates the dataverse-mfg-mi connection) and, for enrichment, the KB connection (foundry CLI `connect`).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$FoundryAccount,
  [Parameter(Mandatory)][string]$FoundryProject,
  [Parameter(Mandatory)][string]$OrgUrl,
  [string]$SearchService,
  [ValidateSet('on', 'off')][string]$Enrichment = 'off',
  [string]$ConnectionName = 'dataverse-mfg-mi',
  [string]$NotetakerModel = 'gpt-5.6-sol'
)
$ErrorActionPreference = 'Stop'
$OrgUrl = $OrgUrl.TrimEnd('/')
$ep = "https://$FoundryAccount.services.ai.azure.com/api/projects/$FoundryProject"
$tok = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv

function Post($path, $features, $body) {
  $f = New-TemporaryFile
  [IO.File]::WriteAllText($f, ($body | ConvertTo-Json -Depth 20 -Compress))
  $r = curl.exe -s -X POST "$ep/$path`?api-version=v1" -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -H "Foundry-Features: $features" --data "@$f" | ConvertFrom-Json
  Remove-Item $f
  if (-not $r.version) { throw "POST $path failed: $($r | ConvertTo-Json -Depth 6)" }
  $r
}

# 1. Toolbox
$tb = Post 'toolboxes/floorworks-dataverse/versions' 'Toolboxes=V1Preview' @{
  description = 'Dataverse MCP via project managed identity'
  tools = @(@{ type = 'mcp'; name = 'dataverse-mfg'; server_label = 'dataverse-mfg'; server_url = "$OrgUrl/api/mcp"; project_connection_id = $ConnectionName })
}
Write-Host "Toolbox floorworks-dataverse v$($tb.version)"

# 2. Note-taker
$ins = Get-Content (Join-Path $PSScriptRoot 'notetaker-instructions.md') -Raw
$tools = @(@{ type = 'mcp'; server_label = 'dataverse-mfg'; server_url = "$OrgUrl/api/mcp"; require_approval = 'never'; project_connection_id = $ConnectionName })
if ($Enrichment -eq 'on') {
  if (-not $SearchService) { throw '-Enrichment on requires -SearchService' }
  $ins += Get-Content (Join-Path $PSScriptRoot 'notetaker-enrichment.md') -Raw
  $tools += @{ type = 'mcp'; server_label = 'floorworks-kb'
    server_url = "https://$SearchService.search.windows.net/knowledgebases/floorworks-kb/mcp?api-version=2026-05-01-preview"
    project_connection_id = 'floorworks-kb'; allowed_tools = @{ tool_names = @('knowledge_base_retrieve') }; require_approval = 'never' }
}
$nt = Post 'agents/floorworks-notetaker/versions' 'VoiceAgents=V1Preview' @{
  description = 'Call note-taker: drafts the service request'
  definition = @{ kind = 'prompt'; model = $NotetakerModel; instructions = $ins; reasoning = @{ effort = 'low' }; tools = $tools }
}
Write-Host "Agent floorworks-notetaker v$($nt.version)"

# 3. Voice agent (the voice agent never gets the KB tool)
$voice = @{
  kind = 'voice'; model_type = 'managed'; model = 'gpt-realtime-2.1'; store = $true
  instructions = (Get-Content (Join-Path $PSScriptRoot 'voice-instructions.md') -Raw)
  greeting = @{ type = 'template'; text = 'Hello, I''m FloorWorks Assistant. I can look up equipment, parts, quality alerts, and service tickets using live records. What would you like to check?' }
  audio = @{
    input = @{
      format = @{ type = 'audio/pcm'; rate = 24000 }
      noise_reduction = @{ type = 'azure_deep_noise_suppression' }
      turn_detection = @{ type = 'azure_semantic_vad'; auto_truncate = $true; profile = 'normal'; remove_filler_words = $false; create_response = $true; interrupt_response = $true }
      echo_cancellation = @{ type = 'server_echo_cancellation'; reference_source = 'client'; channels = 2 }
      transcription = @{ language = ''; model = 'azure-speech' }
    }
    output = @{ format = @{ type = 'audio/pcm'; rate = 24000 }; voice = 'en-US-Ava:DragonHDLatestNeural'; voice_type = 'azure-standard' }
  }
  output_modalities = @('audio')
  interim_response = @{ type = 'llm_interim_response'; triggers = @('latency'); latency_threshold_ms = 500; max_completion_tokens = 50 }
  tools = @(
    @{ type = 'toolbox'; server_label = 'floorworks-dataverse'; toolbox_name = 'floorworks-dataverse'; toolbox_version = "$($tb.version)"
       server_url = "$ep/toolboxes/floorworks-dataverse/versions/$($tb.version)/mcp?api-version=v1"; require_approval = 'never'; response_scheduling = 'when_idle' },
    @{ type = 'function'; name = 'transfer_to_rep'; description = 'Connect the live caller to a human service representative.'
       response_scheduling = 'when_idle'; parameters = @{ type = 'object'; required = @(); properties = @{} } })
}
$va = Post 'agents/floorworks-voice-agent-app/versions' 'VoiceAgents=V1Preview' @{ description = 'FloorWorks voice agent'; definition = $voice }
Write-Host "Agent floorworks-voice-agent-app v$($va.version)"
