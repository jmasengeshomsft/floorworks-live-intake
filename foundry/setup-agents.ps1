<#
.SYNOPSIS
  Prepares a Foundry project for the FloorWorks intake gateway: Dataverse access, connection, toolbox, note-taker agent and voice agent.

.DESCRIPTION
  Give it a Foundry project (pick one, pass -ProjectEndpoint, or pass -FoundryAccount and -FoundryProject) and your Dataverse URL.
  It then:
    1. Preflight: login, project, managed identity, model deployments (changes nothing; -Plan stops here)
    2. Grants the project's managed identity access to Dataverse (application user, role, allowed MCP client)
    3. Creates the project connection to the Dataverse MCP server (managed identity, works unattended)
    4. Publishes the toolbox, the note-taker prompt agent and the voice agent (with the transfer_to_rep function tool)
    5. Verifies by reading everything back and prints the values for the gateway settings file
  Safe to re-run: the connection is reused, agents and the toolbox get a new version each run.

.EXAMPLE
  az login
  .\foundry\setup-agents.ps1 -OrgUrl https://myorg.crm.dynamics.com
  .\foundry\setup-agents.ps1 -ProjectEndpoint https://acct.services.ai.azure.com/api/projects/proj -OrgUrl https://myorg.crm.dynamics.com -VoiceAgent my-voice -NotetakerAgent my-notetaker
  .\foundry\setup-agents.ps1 -OrgUrl ... -Plan            # preflight only
  .\foundry\setup-agents.ps1 -OrgUrl ... -ParamsFile .\infra\gateway.my-gateway.json   # also writes agent names and endpoint into the settings file
#>
[CmdletBinding()]
param(
  [string]$ProjectEndpoint,                 # https://<account>.services.ai.azure.com/api/projects/<project>
  [string]$FoundryAccount,
  [string]$FoundryProject,
  [string]$Subscription,
  [string]$OrgUrl,                          # https://<org>.crm.dynamics.com
  [string]$VoiceAgent = 'floorworks-voice-agent-app',
  [string]$NotetakerAgent = 'floorworks-notetaker',
  [string]$ToolboxName = 'floorworks-dataverse',
  [string]$ConnectionName = 'dataverse-mfg-mi',
  [string]$NotetakerModel,                  # deployment name; chosen from the project's deployments when omitted
  [string]$VoiceModel = 'gpt-realtime-2.1',
  [string]$Voice = 'en-US-Ava:DragonHDLatestNeural',
  [string]$Greeting = "Hello, I'm FloorWorks Assistant. How can I help you today?",
  [string]$VoiceInstructionsFile = (Join-Path $PSScriptRoot 'prompts\voice-instructions.md'),
  [string]$NotetakerInstructionsFile = (Join-Path $PSScriptRoot 'prompts\notetaker-instructions.md'),
  [ValidateSet('on', 'off')][string]$Enrichment = 'off',
  [string]$SearchService,                   # only for -Enrichment on
  [string[]]$AllowedTools = @('describe', 'read_query', 'search'),   # Dataverse tools the agents may use (read-only); '*' = all, including create/update/delete
  [string]$DataverseRole = 'System Administrator',   # broad on purpose for a demo; use a least-privilege role in production
  [switch]$GrantDataverseAccess,            # optional: also grant the project identity access to Dataverse (needs Dataverse admin)
  [string]$ParamsFile,                      # optional gateway settings file to update
  [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$problems = New-Object System.Collections.ArrayList
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  [ OK ] $t" -ForegroundColor Green }
function Info($t) { Write-Host "  $t" }
function Warn($t) { Write-Host "  [WARN] $t" -ForegroundColor Yellow }
function Problem($t, $fix) {
  Write-Host "  [FAIL] $t" -ForegroundColor Red
  if ($fix) { Write-Host "         Fix: $fix" -ForegroundColor Yellow }
  [void]$problems.Add($t)
}
function AzQ {
  $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $r = & az.cmd @args 2>$null } finally { $ErrorActionPreference = $ea }
  if ($LASTEXITCODE -ne 0) { return $null }
  $r
}
function Token($resource) { ("$(AzQ account get-access-token --resource $resource --query accessToken -o tsv)").Trim() }

function Rest($method, $url, $tok, $body, $features, $accept = 'application/json') {
  $out = [IO.Path]::GetTempFileName(); $hd = [IO.Path]::GetTempFileName(); $bf = $null
  $a = @('-s', '-o', $out, '-D', $hd, '-w', '%{http_code}', '-X', $method, $url, '-H', "Authorization: Bearer $tok", '-H', 'Content-Type: application/json', '-H', "Accept: $accept")
  if ($features) { $a += @('-H', "Foundry-Features: $features") }
  if ($null -ne $body) {
    $bf = [IO.Path]::GetTempFileName()
    $txt = if ($body -is [string]) { $body } else { $body | ConvertTo-Json -Depth 20 -Compress }
    [IO.File]::WriteAllText($bf, $txt)
    $a += @('--data-binary', "@$bf")
  }
  $code = & curl.exe @a
  $text = [IO.File]::ReadAllText($out); $headers = [IO.File]::ReadAllText($hd)
  Remove-Item $out, $hd -ErrorAction SilentlyContinue; if ($bf) { Remove-Item $bf -ErrorAction SilentlyContinue }
  $json = $null; try { if ($text.Trim()) { $json = $text | ConvertFrom-Json } } catch {}
  [pscustomobject]@{ Code = [int]$code; Json = $json; Text = $text; Headers = $headers }
}
function Pick($what, $items, $label) {
  if (-not $items -or @($items).Count -eq 0) { throw "No $what found." }
  $items = @($items)
  if ($items.Count -eq 1) { Ok "$what : $(& $label $items[0])"; return $items[0] }
  Write-Host "  Choose $what :"
  for ($i = 0; $i -lt $items.Count; $i++) { Write-Host "    [$($i + 1)] $(& $label $items[$i])" }
  while ($true) { $n = Read-Host '  Number'; if ($n -match '^\d+$' -and [int]$n -ge 1 -and [int]$n -le $items.Count) { return $items[[int]$n - 1] } }
}

$ArmApi = '2025-04-01-preview'
Step 'Preflight'
if (-not (AzQ account show -o tsv)) { throw 'Not signed in. Run: az login' }
if ($Subscription) { AzQ account set -s $Subscription | Out-Null }
$subId = ("$(AzQ account show --query id -o tsv)").Trim()
Ok "subscription $subId"
foreach ($f in $VoiceInstructionsFile, $NotetakerInstructionsFile) { if (-not (Test-Path $f)) { throw "Instructions file not found: $f" } }

# Foundry account and project
if ($ProjectEndpoint -match '^https://([^.]+)\.services\.ai\.azure\.com/api/projects/([^/?]+)') { $FoundryAccount = $Matches[1]; $FoundryProject = $Matches[2] }
elseif ($ProjectEndpoint) { throw "ProjectEndpoint must look like https://<account>.services.ai.azure.com/api/projects/<project>" }
$accts = @((AzQ cognitiveservices account list --query "[?kind=='AIServices'].{name:name,rg:resourceGroup}" -o json | ConvertFrom-Json))
if (-not $FoundryAccount) { $FoundryAccount = (Pick 'Foundry account' $accts { param($a) "$($a.name) ($($a.rg))" }).name }
$acct = $accts | Where-Object { $_.name -eq $FoundryAccount } | Select-Object -First 1
if (-not $acct) { throw "Foundry account '$FoundryAccount' not found in subscription $subId." }
$rg = $acct.rg
$acctArm = "/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.CognitiveServices/accounts/$FoundryAccount"
$arm = Token 'https://management.azure.com'
if (-not $FoundryProject) {
  $pl = Rest GET "https://management.azure.com$acctArm/projects?api-version=$ArmApi" $arm
  $names = @($pl.Json.value | ForEach-Object { ($_.name -split '/')[-1] })
  $FoundryProject = Pick 'Foundry project' $names { param($n) $n }
}
$projArm = "$acctArm/projects/$FoundryProject"
$ep = "https://$FoundryAccount.services.ai.azure.com/api/projects/$FoundryProject"
$pr = Rest GET "https://management.azure.com$projArm`?api-version=$ArmApi" $arm
if ($pr.Code -ne 200) { Problem "project '$FoundryProject' not readable (HTTP $($pr.Code))" "Check the name and that you have access to $FoundryAccount." }
else { Ok "project $FoundryProject on $FoundryAccount ($rg)" }
$miObjectId = if ($pr.Json.identity) { $pr.Json.identity.principalId } else { $null }
if (-not $miObjectId) { Problem 'project has no system-assigned managed identity' "Enable it: Foundry portal > project > Identity, or az resource update --ids $projArm --set identity.type=SystemAssigned" }
else { Ok "project managed identity $miObjectId" }

# Reuse or create: connection, agents, toolbox
$boundNames = @($PSBoundParameters.Keys)
function ChooseOrNew($label, $param, $existing, $default) {
  if ($boundNames -contains $param -or $Plan) { return [pscustomobject]@{ Name = $default; IsNew = ($existing -notcontains $default) } }
  Write-Host "  $label" -ForegroundColor Cyan
  $i = 1; foreach ($e in $existing) { Write-Host "    [$i] reuse '$e' (adds a new version)"; $i++ }
  Write-Host "    [N] create new"
  $d = if ($existing.Count) { '1' } else { 'N' }
  $a = (Read-Host "  Choice [$d]").Trim(); if (-not $a) { $a = $d }
  if ($a -match '^\d+$' -and [int]$a -ge 1 -and [int]$a -le $existing.Count) { return [pscustomobject]@{ Name = $existing[[int]$a - 1]; IsNew = $false } }
  $n = (Read-Host "  New name [$default]").Trim(); if (-not $n) { $n = $default }
  [pscustomobject]@{ Name = $n; IsNew = ($existing -notcontains $n) }
}
$ftChk = Token 'https://ai.azure.com'

$cl = Rest GET "https://management.azure.com$projArm/connections?api-version=$ArmApi" $arm
$dvConns = @($cl.Json.value | Where-Object { "$($_.properties.target)" -match 'dynamics\.com' })
$cx = ChooseOrNew 'Dataverse connection in this project' 'ConnectionName' @($dvConns | ForEach-Object { ($_.name -split '/')[-1] }) $ConnectionName
$ConnectionName = $cx.Name
if (-not $cx.IsNew -and -not $OrgUrl) {
  $m = $dvConns | Where-Object { ($_.name -split '/')[-1] -eq $ConnectionName } | Select-Object -First 1
  if ($m) { $OrgUrl = ("$($m.properties.target)" -replace '/api/mcp/?$', '') }
}
if ($cx.IsNew) { Info "connection '$ConnectionName' will be created" } else { Info "reusing connection '$ConnectionName'" }

$al = Rest GET "$ep/agents?api-version=v1" $ftChk $null 'VoiceAgents=V1Preview'
$allAgents = @($al.Json.data)
function AgentsOfKind($kind) { @($allAgents | Where-Object { $_.versions.latest.definition.kind -eq $kind } | ForEach-Object { $_.name }) }
$vx = ChooseOrNew 'Voice agent' 'VoiceAgent' (AgentsOfKind 'voice') $VoiceAgent
$VoiceAgent = $vx.Name
$nx = ChooseOrNew 'Note-taker agent' 'NotetakerAgent' (AgentsOfKind 'prompt') $NotetakerAgent
$NotetakerAgent = $nx.Name
foreach ($x in @(@('voice agent', $vx), @('note-taker', $nx))) { if ($x[1].IsNew) { Info "$($x[0]) '$($x[1].Name)' will be created" } else { Info "$($x[0]) '$($x[1].Name)': a new version will be added" } }
# Dataverse URL
if (-not $OrgUrl) {
  $c0 = Rest GET "https://management.azure.com$projArm/connections/$ConnectionName`?api-version=$ArmApi" $arm
  if ($c0.Code -eq 200 -and $c0.Json.properties.target) { $OrgUrl = ("$($c0.Json.properties.target)" -replace '/api/mcp/?$', ''); Info "using Dataverse URL from connection '$ConnectionName'" }
}
if (-not $OrgUrl) { $OrgUrl = Read-Host '  Dataverse URL (for example https://myorg.crm.dynamics.com)' }
$OrgUrl = $OrgUrl.Trim().TrimEnd('/')
if ($OrgUrl -notmatch '^https://[^/]+\.dynamics\.com$') { Problem "OrgUrl '$OrgUrl' does not look like https://<org>.crm.dynamics.com" 'Copy it from the Power Platform admin center.' } else { Ok "Dataverse $OrgUrl" }

# One toolbox per Dataverse endpoint in the project, independent of the agents
if (-not ($boundNames -contains 'ToolboxName')) {
  $tl = Rest GET "$ep/toolboxes?api-version=v1" $ftChk $null 'Toolboxes=V1Preview'
  $mine = @($tl.Json.data | Where-Object { @($_.versions.latest.tools | Where-Object { "$($_.server_url)".TrimEnd('/') -eq "$OrgUrl/api/mcp" }).Count })
  if ($mine.Count) { $ToolboxName = $mine[0].name; Info "reusing toolbox '$ToolboxName' (already points at $OrgUrl/api/mcp)" }
  else { $ToolboxName = (([uri]$OrgUrl).Host -split '\.')[0] + '-dataverse'; Info "toolbox '$ToolboxName' will be created for $OrgUrl/api/mcp" }
}
# Model deployments
$dep = Rest GET "https://management.azure.com$acctArm/deployments?api-version=$ArmApi" $arm
if ($dep.Code -ne 200) { Start-Sleep 3; $dep = Rest GET "https://management.azure.com$acctArm/deployments?api-version=$ArmApi" $arm }
if ($dep.Code -ne 200) { Problem "could not list model deployments (HTTP $($dep.Code))" 'Check Reader access on the Foundry account and re-run.' }
$deployed = @($dep.Json.value | ForEach-Object { [pscustomobject]@{ Name = $_.name; Model = $_.properties.model.name } })
if ($NotetakerModel) {
  if ($deployed.Name -notcontains $NotetakerModel) { Problem "note-taker model deployment '$NotetakerModel' not found in $FoundryAccount" "Deployments: $($deployed.Name -join ', ')" }
  else { Ok "note-taker model $NotetakerModel" }
}
else {
  $chat = @($deployed | Where-Object { $_.Model -match '^(gpt|o\d)' -and $_.Model -notmatch 'realtime|audio|embedding|image|transcribe|tts' })
  $pref = $null; foreach ($n in 'gpt-5.6-sol', 'gpt-4.1', 'gpt-4o', 'gpt-4.1-mini') { $pref = $chat | Where-Object { $_.Name -eq $n } | Select-Object -First 1; if ($pref) { break } }
  if ($pref) { $NotetakerModel = $pref.Name; Ok "note-taker model $NotetakerModel (use -NotetakerModel to change)" }
  elseif ($chat.Count -gt 0) { $NotetakerModel = (Pick 'note-taker model' $chat { param($d) "$($d.Name) ($($d.Model))" }).Name }
  else { Problem "no chat model deployment in $FoundryAccount" 'Deploy a chat model (for example gpt-4.1) in the Foundry portal, then re-run.' }
}
if ($Enrichment -eq 'on') {
  if (-not $SearchService) { Problem '-Enrichment on needs -SearchService' 'Pass the Azure AI Search service name.' }
  elseif ((Rest GET "https://management.azure.com$projArm/connections/floorworks-kb?api-version=$ArmApi" $arm).Code -ne 200) {
    Problem "project connection 'floorworks-kb' not found" 'Build the knowledge base first (foundry CLI: dotnet run -- all).'
  }
}
if ($problems.Count) { Write-Host "`nPreflight found $($problems.Count) problem(s). Fix them and re-run." -ForegroundColor Red; exit 1 }
if ($Plan) { Write-Host "`nPreflight passed. Re-run without -Plan to set up the agents." -ForegroundColor Green; exit 0 }

# ------------------------------------------------------------------ Dataverse access
Step 'Dataverse access for the project managed identity'
if (-not $GrantDataverseAccess) { Info 'skipped (assumes the project identity already has Dataverse access; add -GrantDataverseAccess to do it)' }
else {
  try {
    $miAppId = ("$(AzQ ad sp show --id $miObjectId --query appId -o tsv)").Trim()
    if (-not $miAppId) { throw "Could not resolve the application id of $miObjectId (az ad sp show failed)." }
    $dv = Token $OrgUrl
    if (-not $dv) { throw "Could not get a Dataverse token for $OrgUrl." }
    $api = "$OrgUrl/api/data/v9.2"
    $u = Rest GET "$api/systemusers?`$filter=applicationid%20eq%20$miAppId&`$select=systemuserid" $dv
    if ($u.Code -ne 200) { throw "Dataverse query failed (HTTP $($u.Code)). You need to be an admin of this environment. $($u.Text)" }
    if (@($u.Json.value).Count) { $userId = $u.Json.value[0].systemuserid; Ok 'application user exists' }
    else {
      $bu = (Rest GET "$api/businessunits?`$filter=parentbusinessunitid%20eq%20null&`$select=businessunitid" $dv).Json.value[0].businessunitid
      $c = Rest POST "$api/systemusers" $dv @{ applicationid = $miAppId; 'businessunitid@odata.bind' = "/businessunits($bu)" }
      if ($c.Code -notin 200, 201, 204) { throw "Creating the application user failed (HTTP $($c.Code)): $($c.Text)" }
      $userId = [regex]::Match($c.Headers, 'systemusers\(([0-9a-fA-F-]+)\)').Groups[1].Value
      Ok 'application user created'
    }
    $roleId = (Rest GET "$api/roles?`$filter=name%20eq%20'$([uri]::EscapeDataString($DataverseRole))'&`$select=roleid" $dv).Json.value[0].roleid
    if (-not $roleId) { throw "Security role '$DataverseRole' not found." }
    $ra = Rest POST "$api/systemusers($userId)/systemuserroles_association/`$ref" $dv @{ '@odata.id' = "$api/roles($roleId)" }
    if ($ra.Code -in 200, 204) { Ok "role '$DataverseRole' assigned" } elseif ($ra.Text -match 'duplicate|already') { Ok "role '$DataverseRole' already assigned" } else { Warn "role assignment: HTTP $($ra.Code) $($ra.Text)" }
    $al = Rest GET "$api/allowedmcpclients?`$filter=applicationid%20eq%20$miAppId&`$select=allowedmcpclientid" $dv
    if ($al.Code -eq 200 -and -not @($al.Json.value).Count) {
      $ac = Rest POST "$api/allowedmcpclients" $dv @{ name = 'Foundry Project MI'; uniquename = 'fw_foundryprojectmi'; applicationid = $miAppId; isenabled = $true }
      if ($ac.Code -in 200, 201, 204) { Ok 'allowed MCP client added' } else { Warn "allowed MCP client: HTTP $($ac.Code) $($ac.Text)" }
    }
    elseif ($al.Code -eq 200) { Ok 'allowed MCP client exists' }
  }
  catch {
    Warn "Dataverse access not completed: $($_.Exception.Message)"
    Warn 'Continuing. Ask a Dataverse admin to run this step (docs\12-dataverse-mcp-managed-identity-setup.md) or re-run with an admin account.'
  }
}

# ------------------------------------------------------------------ connection
Step 'Project connection'
$connUrl = "https://management.azure.com$projArm/connections/$ConnectionName`?api-version=$ArmApi"
$ex = Rest GET $connUrl $arm
if ($ex.Code -eq 200) { Ok "connection '$ConnectionName' already exists" }
else {
  $cb = @{ properties = @{ category = 'RemoteTool'; target = "$OrgUrl/api/mcp"; authType = 'ProjectManagedIdentity'; audience = $OrgUrl; isSharedToAll = $true; metadata = @{ type = 'custom_MCP' } } }
  $cr = Rest PUT $connUrl $arm $cb
  if ($cr.Code -in 200, 201) { Ok "connection '$ConnectionName' created" } else { throw "Creating connection failed (HTTP $($cr.Code)): $($cr.Text)" }
}

# ------------------------------------------------------------------ toolbox, agents
$toolFilter = if ($AllowedTools -contains '*') { @{} } else { @{ allowed_tools = @($AllowedTools) } }
$ft = Token 'https://ai.azure.com'
function Publish($path, $features, $body, $label) {
  $r = Rest POST "$ep/$path`?api-version=v1" $ft $body $features
  if (-not $r.Json -or -not $r.Json.version) { throw "$label failed (HTTP $($r.Code)): $($r.Text)" }
  Ok "$label v$($r.Json.version)"
  $r.Json
}
Step 'Toolbox (voice agent)'
$tb = Publish "toolboxes/$ToolboxName/versions" 'Toolboxes=V1Preview' @{
  description = 'Dataverse MCP via project managed identity'
  tools = @(@{ type = 'mcp'; name = $ConnectionName; server_label = $ConnectionName; server_url = "$OrgUrl/api/mcp"; require_approval = 'never'; project_connection_id = $ConnectionName } + $toolFilter)
} "toolbox $ToolboxName"

Step 'Note-taker agent'
$ins = Get-Content $NotetakerInstructionsFile -Raw
$ins = [regex]::Replace($ins, '(?s)<!-- ENRICHMENT:BEGIN.*?-->\r?\n(.*?)<!-- ENRICHMENT:END -->\r?\n?', $(if ($Enrichment -eq 'on') { '$1' } else { '' }))
$ntTools = @(@{ type = 'mcp'; server_label = $ToolboxName; server_url = "$ep/toolboxes/$ToolboxName/versions/$($tb.version)/mcp?api-version=v1"; require_approval = 'never' })
if ($Enrichment -eq 'on') {
  $ntTools += @{ type = 'mcp'; server_label = 'floorworks-kb'; require_approval = 'never'; project_connection_id = 'floorworks-kb'
    server_url = "https://$SearchService.search.windows.net/knowledgebases/floorworks-kb/mcp?api-version=2026-05-01-preview"
    allowed_tools = @{ tool_names = @('knowledge_base_retrieve') } }
}
$nt = Publish "agents/$NotetakerAgent/versions" 'VoiceAgents=V1Preview' @{
  description = 'Call note-taker: drafts the service request'
  definition = @{ kind = 'prompt'; model = $NotetakerModel; instructions = $ins; reasoning = @{ effort = 'low' }; tools = $ntTools }
} "agent $NotetakerAgent"

Step 'Voice agent'
$voiceDef = @{
  kind = 'voice'; model_type = 'managed'; model = $VoiceModel; store = $true
  instructions = (Get-Content $VoiceInstructionsFile -Raw)
  greeting = @{ type = 'template'; text = $Greeting }
  audio = @{
    input = @{
      format = @{ type = 'audio/pcm'; rate = 24000 }
      noise_reduction = @{ type = 'azure_deep_noise_suppression' }
      turn_detection = @{ type = 'azure_semantic_vad'; auto_truncate = $true; profile = 'normal'; remove_filler_words = $false; create_response = $true; interrupt_response = $true }
      echo_cancellation = @{ type = 'server_echo_cancellation'; reference_source = 'client'; channels = 2 }
      transcription = @{ language = ''; model = 'azure-speech' }
    }
    output = @{ format = @{ type = 'audio/pcm'; rate = 24000 }; voice = $Voice; voice_type = 'azure-standard' }
  }
  output_modalities = @('audio')
  interim_response = @{ type = 'llm_interim_response'; triggers = @('latency'); latency_threshold_ms = 500; max_completion_tokens = 50 }
  tools = @(
    @{ type = 'toolbox'; server_label = $ToolboxName; toolbox_name = $ToolboxName; toolbox_version = "$($tb.version)"
      server_url = "$ep/toolboxes/$ToolboxName/versions/$($tb.version)/mcp?api-version=v1"; require_approval = 'never'; response_scheduling = 'when_idle' },
    @{ type = 'function'; name = 'transfer_to_rep'; description = 'Connect the live caller to a human service representative. Call only when the caller explicitly asks for a person.'
      response_scheduling = 'when_idle'; parameters = @{ type = 'object'; required = @(); properties = @{} } })
}
$va = Publish "agents/$VoiceAgent/versions" 'VoiceAgents=V1Preview' @{ description = 'FloorWorks voice agent'; definition = $voiceDef } "agent $VoiceAgent"

# ------------------------------------------------------------------ verify
Step 'Verify (reading everything back)'
$bad = 0
$v = (Rest GET "$ep/agents/$VoiceAgent`?api-version=v1" $ft $null 'VoiceAgents=V1Preview').Json.versions.latest.definition
if (@($v.tools | Where-Object { $_.type -eq 'function' -and $_.name -eq 'transfer_to_rep' }).Count) { Ok 'voice agent has the transfer_to_rep function tool' } else { Problem 'voice agent is missing transfer_to_rep' $null; $bad++ }
if (@($v.tools | Where-Object { $_.type -eq 'toolbox' -and "$($_.toolbox_version)" -eq "$($tb.version)" }).Count) { Ok "voice agent uses toolbox $ToolboxName v$($tb.version)" } else { Problem 'voice agent does not point at the new toolbox version' $null; $bad++ }
$tbv = (Rest GET "$ep/toolboxes/$ToolboxName/versions/$($tb.version)?api-version=v1" $ft $null 'Toolboxes=V1Preview').Json
if (@($tbv.tools | Where-Object { $_.type -eq 'mcp' -and $_.require_approval -ne 'never' }).Count) { Problem 'a tool inside the toolbox still requires approval' $null; $bad++ } else { Ok 'toolbox tools never wait for approval' }
$n = (Rest GET "$ep/agents/$NotetakerAgent`?api-version=v1" $ft $null 'VoiceAgents=V1Preview').Json.versions.latest.definition
$needsApproval = @($n.tools | Where-Object { $_.require_approval -ne 'never' })
if ($needsApproval.Count) { Problem 'a note-taker tool still requires approval (the agent would stop waiting for it)' $null; $bad++ } else { Ok 'note-taker tools never wait for approval' }
$rpc = Rest POST "$ep/toolboxes/$ToolboxName/versions/$($tb.version)/mcp?api-version=v1" $ft '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 'Toolboxes=V1Preview' 'application/json, text/event-stream'
if ($rpc.Code -eq 200 -and $rpc.Text -match '"tools"') {
  $toolNames = [regex]::Matches($rpc.Text, '"name"\s*:\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
  Ok "toolbox MCP answers tools/list ($(@($toolNames).Count) names, for example $((@($toolNames) | Select-Object -First 4) -join ', '))"
}
else { Warn "toolbox tools/list did not answer (HTTP $($rpc.Code)). If Dataverse access was skipped this is expected; test a call to be sure." }
if ($rpc.Code -eq 200 -and $AllowedTools -notcontains '*') {
  $extra = @($toolNames | Where-Object { $_ -match '___' -and (($_ -split '___')[-1]) -notin $AllowedTools })
  if ($extra.Count) { Problem "toolbox still exposes tools outside the allow-list: $($extra -join ', ')" 'Check that allowed_tools is honored by the toolbox, or use the agent-level filter.'; $bad++ }
  else { Ok "toolbox exposes only: $((@($toolNames | Where-Object { $_ -match '___' }) -replace '^.*___','') -join ', ')" }
}
# Probe: call the note-taker once. An mcp_approval_request here is exactly what hangs a real call.
$probe = @{ input = 'Use your Dataverse tool once to describe the table bdm_equipment, then answer in one short sentence.'; agent_reference = @{ name = $NotetakerAgent; type = 'agent_reference' } }
$pr2 = Rest POST "$ep/openai/v1/responses" $ft $probe 'VoiceAgents=V1Preview'
if ($pr2.Code -ne 200 -or -not $pr2.Json) { Warn "probe call did not complete (HTTP $($pr2.Code)); test a real call." }
else {
  $types = @($pr2.Json.output | ForEach-Object { $_.type })
  if ($types -contains 'mcp_approval_request') { Problem 'probe: the note-taker is waiting for tool approval' 'Set require_approval to never on its MCP tools.'; $bad++ }
  elseif ($types -contains 'message') { Ok "probe: note-taker ran its tools without approval (outputs: $($types -join ', '))" }
  else { Warn "probe returned no message (outputs: $($types -join ', ')); status=$($pr2.Json.status)" }
}
if ($bad) { Write-Host "`nVerification found problems above." -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ result
Write-Host "`nDone. Gateway settings:" -ForegroundColor Green
Write-Host "  foundryProjectEndpoint : $ep"
Write-Host "  voiceAgentName         : $VoiceAgent"
Write-Host "  extractorAgent         : $NotetakerAgent"
if ($ParamsFile) {
  $p = Get-Content $ParamsFile -Raw | ConvertFrom-Json
  foreach ($kv in @{ foundryProjectEndpoint = $ep; voiceAgentName = $VoiceAgent; extractorAgent = $NotetakerAgent }.GetEnumerator()) {
    if ($p.PSObject.Properties[$kv.Key]) { $p.($kv.Key) = $kv.Value } else { $p | Add-Member -NotePropertyName $kv.Key -NotePropertyValue $kv.Value }
  }
  [IO.File]::WriteAllText((Resolve-Path $ParamsFile), ($p | ConvertTo-Json -Depth 10))
  Ok "updated $ParamsFile"
}
Write-Host "The gateway's identity also needs the 'Foundry User' role on $FoundryAccount (infra\deploy-gateway.ps1 grants it)."
