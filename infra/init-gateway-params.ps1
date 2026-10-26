<#
.SYNOPSIS
  Step 1 of 2. Discovers your Azure resources, lets you pick from real choices (never type an ID),
  validates every value, and writes the settings file that deploy-gateway.ps1 uses.

.EXAMPLE
  .\init-gateway-params.ps1                       # interactive, picks from lists
  .\init-gateway-params.ps1 -Name my-gw -AcsResourceName my-acs -NonInteractive
  .\deploy-gateway.ps1 -ParametersFile infra\gateway.<name>.json
#>
param(
  [string]$Name,
  [string]$Subscription,
  [string]$AcsResourceName,
  [string]$AppName,
  [string]$ResourceAccountObjectId,
  [string]$PhoneNumber,
  [string]$TransferTeamsObjectIds,
  [string]$FoundryAccount,
  [string]$FoundryProject,
  [string]$VoiceAgent,
  [string]$ExtractorAgent,
[string]$CorsOrigins,
  [switch]$NonInteractive,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'; $env:PYTHONWARNINGS = 'ignore'; $env:AZURE_CORE_ONLY_SHOW_ERRORS = 'true'; $env:AZURE_CORE_COLLECT_TELEMETRY = 'false'
function az {
  $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $o = & az.cmd @args 2>&1
    $code = $LASTEXITCODE
    foreach ($l in $o) {
      if ($l -is [System.Management.Automation.ErrorRecord]) {
        $m = "$l"
        if ($m.Trim() -and $m -notmatch 'SyntaxWarning|^System\.Management\.Automation\.RemoteException|^\s*pattern=|invalid escape|DeprecationWarning|^\s*[/|\\-]?\s*Running \.\.|behavior of this command has been altered') { [Console]::Error.WriteLine($m) }
      } else { $l }
    }
  } finally { $ErrorActionPreference = $ea }
  $global:LASTEXITCODE = $code
}
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function J($t) { $s = ($t | Out-String).Trim(); if ($s) { $s | ConvertFrom-Json } }
function Az-Quiet { $r = & az @args 2>$null; if ($LASTEXITCODE -ne 0) { return $null }; $r }

function Pick($label, $items, $display) {
  $items = @($items)
  if ($items.Count -eq 0) { throw "No $label found. Create one first, then re-run." }
  if ($items.Count -eq 1) { Ok "$label : $(& $display $items[0])"; return $items[0] }
  if ($NonInteractive) { throw "Several ${label}s found; pass a parameter to choose: $((($items | ForEach-Object { & $display $_ }) -join ', '))" }
  Write-Host "  Choose $label :"
  for ($i = 0; $i -lt $items.Count; $i++) { Write-Host ("    [{0}] {1}" -f ($i + 1), (& $display $items[$i])) }
  while ($true) {
    $a = Read-Host "  Number"
    $n = 0
    if ([int]::TryParse($a, [ref]$n) -and $n -ge 1 -and $n -le $items.Count) { return $items[$n - 1] }
    Write-Host "  Enter a number from the list." -ForegroundColor Yellow
  }
}

$nameRx = '^[a-z][a-z0-9-]{1,30}[a-z0-9]$'
$appExplicit = [bool]$AppName
if (-not $Name) {
  if ($NonInteractive) { throw "Pass -Name <deployment-name>. It becomes both the settings file name and the Container App name." }
  $Name = Read-Host "  Deployment name (3-32 lowercase letters, digits, hyphens; used for the file gateway.<name>.json AND the Container App name)"
}
while ($Name -notmatch $nameRx) {
  if ($NonInteractive) { throw "Name '$Name' must be 3-32 lowercase letters, digits or hyphens, starting with a letter." }
  $Name = Read-Host "  '$Name' is not valid. Use 3-32 lowercase letters, digits or hyphens, starting with a letter"
}
$outFile = Join-Path $PSScriptRoot "gateway.$Name.json"
if ((Test-Path $outFile) -and -not $Force) {
  if ($NonInteractive) { throw "$outFile exists. Use -Force to overwrite or -Name <other>." }
  Write-Host "  $outFile already exists." -ForegroundColor Yellow
  $a = Read-Host "  Overwrite it? (y/N, or type a new name to keep both)"
  if ($a -match '^(y|yes)$') { $Force = [switch]$true }
  elseif ($a -match $nameRx) { $Name = $a }
  else { throw "Stopped. Re-run with -Name <other> or -Force." }
}
if (-not $appExplicit) { $AppName = $Name }
if ($AppName -notmatch $nameRx) { throw "App name '$AppName' must be 3-32 lowercase letters, digits or hyphens, starting with a letter." }
Write-Host "  Container App name: $AppName   settings file: gateway.$Name.json" -ForegroundColor DarkGray
Step "Sign in and subscription"
if (-not (Az-Quiet account show -o tsv)) { throw "Not signed in. Run: az login" }
if (-not $Subscription) {
  $cur = J (az account show --query "{id:id,name:name}" -o json)
  $use = $true
  if (-not $NonInteractive) { $a = Read-Host "  Use current subscription '$($cur.name)'? (Y/n)"; $use = ($a -notmatch '^[nN]') }
  if ($use) { $Subscription = $cur.id }
  else {
    $subs = J (az account list --query "[?state=='Enabled'].{id:id,name:name}" -o json)
    $Subscription = (Pick 'subscription' $subs { param($s) "$($s.name) ($($s.id))" }).id
  }
}
az account set --subscription $Subscription
if ($LASTEXITCODE -ne 0) { throw "Cannot select subscription $Subscription." }
$tenant = az account show --query tenantId -o tsv
Ok "subscription $Subscription"

Step "Phone resource (ACS)"
$acsList = J (az communication list --query "[].{name:name,rg:resourceGroup,id:id,loc:dataLocation}" -o json)
if ($AcsResourceName) { $acsList = @($acsList | Where-Object { $_.name -eq $AcsResourceName }) }
$acs = Pick 'ACS resource' $acsList { param($a) "$($a.name) (resource group $($a.rg))" }
$rg = $acs.rg
$acsFull = J (az communication show -g $rg -n $acs.name -o json)
Ok "resource group $rg (taken from the ACS resource so Event Grid and the app share it)"

Step "Container Apps environment"
$envs = J (az containerapp env list -g $rg --query "[].{name:name}" -o json)
if (-not $envs) { $envs = J (az containerapp env list --query "[].{name:name,rg:resourceGroup}" -o json) }
$ce = Pick 'Container Apps environment' $envs { param($e) $e.name }
$envName = $ce.name
$envRg = if ($ce.rg) { $ce.rg } else { $rg }
if ($envRg -ne $rg) { Warn "Environment is in '$envRg', the app will be created there." }

Step "Container registry"
$acrs = J (az acr list --query "[].{name:name,rg:resourceGroup}" -o json)
$acr = Pick 'container registry' $acrs { param($a) "$($a.name) ($($a.rg))" }

Step "Foundry project and agents"
$accts = J (az cognitiveservices account list --query "[?kind=='AIServices'].{name:name,id:id,rg:resourceGroup,loc:location}" -o json)
if ($FoundryAccount) { $accts = @($accts | Where-Object { $_.name -eq $FoundryAccount }) }
$fa = Pick 'Foundry account' $accts { param($a) "$($a.name) ($($a.rg))" }
$projects = J (Az-Quiet rest --method get --url "https://management.azure.com$($fa.id)/projects?api-version=2025-06-01" -o json)
$projNames = @($projects.value | ForEach-Object { ($_.name -split '/')[-1] })
if ($FoundryProject) { $projNames = @($projNames | Where-Object { $_ -eq $FoundryProject }) }
$proj = Pick 'Foundry project' $projNames { param($n) $n }
$endpoint = "https://$($fa.name).services.ai.azure.com/api/projects/$proj"
Ok "endpoint $endpoint"

$voice = ''; $extractor = ''
$tok = Az-Quiet account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$names = @()
if ($tok) {
  for ($try = 1; $try -le 4 -and -not $names.Count; $try++) {
    try {
      $r = Invoke-RestMethod "$endpoint/agents?api-version=v1" -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 90
      $names = @($r.data | ForEach-Object { $_.name })
      if (-not $names.Count) { break }
    } catch {
      $msg = $_.Exception.Message
      if ($try -lt 4) { Write-Host "  agent list attempt $try failed ($msg); retrying..." -ForegroundColor DarkGray; Start-Sleep -Seconds (3 * $try) }
      else { Warn "Could not list agents after $try attempts ($msg). If this keeps happening, check your network/VPN reaches $($endpoint -replace '/api/projects/.*','')." }
    }
  }
}
if ($names.Count) {
  $voiceCand = @($names | Where-Object { $_ -match 'voice' })
  if ($VoiceAgent) { $voiceCand = @($names | Where-Object { $_ -eq $VoiceAgent }) }
  $voice = Pick 'voice agent' $(if ($voiceCand.Count) { $voiceCand } else { $names }) { param($n) $n }
  $exCand = @($names | Where-Object { $_ -match 'notetaker|extract' })
  if ($ExtractorAgent) {
    if ($names -notcontains $ExtractorAgent) { throw "Note-taker agent '$ExtractorAgent' not found in this project. Agents: $($names -join ', ')" }
    $extractor = $ExtractorAgent; Ok "note-taker / extractor agent : $extractor"
  } elseif ($NonInteractive) {
    if ($exCand.Count -eq 1) { $extractor = $exCand[0]; Ok "note-taker / extractor agent : $extractor" } else { Warn "No single note-taker agent found; live notes extraction is off. Pass -ExtractorAgent <name> to set it." }
  } else {
    Write-Host "  Choose note-taker / extractor agent (the agent that turns the call into the service ticket) :"
    for ($i = 0; $i -lt $names.Count; $i++) { Write-Host ("    [{0}] {1}{2}" -f ($i + 1), $names[$i], $(if ($exCand -contains $names[$i]) { '   (suggested)' } else { '' })) }
    Write-Host "    [0] none"
    $def = if ($exCand.Count) { [array]::IndexOf($names, $exCand[0]) + 1 } else { 0 }
    while ($true) {
      $a = Read-Host "  Number (Enter for $def)"
      if (-not $a) { $a = "$def" }
      $n = -1
      if ([int]::TryParse($a, [ref]$n) -and $n -ge 0 -and $n -le $names.Count) { if ($n -gt 0) { $extractor = $names[$n - 1] }; break }
      Write-Host "  Enter a number from the list." -ForegroundColor Yellow
    }
    if ($extractor) { Ok "note-taker / extractor agent : $extractor" } else { Warn "No note-taker selected; the ticket will not be extracted from the call." }
  }
}
if (-not $voice) {
  if ($NonInteractive) { throw "Could not discover the voice agent. Create it in Foundry, then re-run." }
  $voice = Read-Host "  Voice agent name (exactly as shown in Foundry)"
}

Step "Teams resource account (for the call allow-list and consent check)"
$oid = $ResourceAccountObjectId
if (-not $oid -and -not $NonInteractive) { $oid = Read-Host "  Resource account Object ID (Enter to skip; the app then answers every call that reaches this ACS)" }
if ($oid) {
  if ($oid -notmatch '^[0-9a-fA-F-]{36}$') { throw "'$oid' is not a GUID. Teams admin: (Get-CsOnlineApplicationInstance -Identity <upn>).ObjectId" }
  $uri = "https://$($acsFull.hostName)/access/teamsExtension/tenants/$tenant/assignments/$oid`?api-version=2025-06-30"
  $c = Az-Quiet rest --method get --resource https://communication.azure.com --url $uri -o json
  if ($c) { Ok "ACS consent exists for this resource account" }
  else { Warn "No ACS consent for $oid on $($acs.name). Calls will not arrive until you run infra\grant-acs-consent.ps1 (needs the Teams admin step first)." }
}

Step "Phone number and call allow-list"
$cors = $CorsOrigins
if (-not $PSBoundParameters.ContainsKey('CorsOrigins')) {
  $cors = Read-Host "  Console origin(s) allowed to call the gateway, comma-separated, or * for any [http://localhost:5173]"
  if (-not $cors) { $cors = 'http://localhost:5173' }
}
$phone = $PhoneNumber
if (-not $phone -and -not $NonInteractive) { $phone = Read-Host "  The Teams phone number people call, e.g. +15551234567 (Enter to skip)" }
if ($phone) {
  $phone = ($phone -replace '[\s()-]', '')
  if ($phone -notmatch '^\+[1-9][0-9]{6,14}$') { throw "'$phone' is not an E.164 number. Use + then country code and digits, e.g. +15551234567" }
  Ok "phone number $phone"
}
if (-not $phone -and -not $oid) { Warn "No phone number and no resource account: the app will answer every call that reaches this ACS." }

Step "Transfer targets (Teams users the agent can hand a call to)"
$transfer = $TransferTeamsObjectIds
if (-not $transfer -and -not $NonInteractive) {
  Write-Host "  Optional. Object IDs of the Teams users/queues for live transfer (Entra ID > Users > Object ID)."
  $transfer = Read-Host "  Comma-separated Object IDs (Enter to skip)"
}
$tIds = @()
if ($transfer) {
  foreach ($g in ($transfer -split '[,;\s]+' | Where-Object { $_ })) {
    if ($g -notmatch '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$') { throw "'$g' is not a GUID. Copy the Object ID from Entra ID > Users." }
    $tIds += $g.ToLower()
  }
  Ok "$($tIds.Count) transfer target(s)"
}

Step "Speech (live notes)"
$speechKey = ''; $speechRegion = $fa.loc
$k = Az-Quiet cognitiveservices account keys list -g $fa.rg -n $fa.name --query key1 -o tsv
if ($k) { $speechKey = $k; Ok "using the Foundry account's key for Speech in $speechRegion (kept out of git; the file is gitignored)" }
else { Warn "Could not read a Speech key; live notes will be off unless you add speechKey." }

$params = [ordered]@{
  subscriptionId = $Subscription; resourceGroup = $envRg; appName = $AppName
  acrName = $acr.name; environmentName = $envName; acsResourceName = $acs.name
  foundryProjectEndpoint = $endpoint; voiceAgentName = $voice; extractorAgentName = $extractor
  resourceAccountObjectId = "$oid"; lineNumbers = "$phone"; transferTeamsObjectIds = ($tIds -join ','); corsOrigins = "$cors"
  liveNotes = 'on'; enrichment = 'off'; speechRegion = $speechRegion; speechKey = $speechKey; apiKeys = ''
}
$out = Join-Path $PSScriptRoot "gateway.$Name.json"
if ((Test-Path $out) -and -not $Force) { throw "$out exists. Use -Force to overwrite or -Name <other>." }
$params | ConvertTo-Json | Set-Content $out -Encoding ascii
Step "Done"
Ok "wrote $out"
Write-Host "`nNext (checks everything, changes nothing):  .\infra\deploy-gateway.ps1 -ParametersFile $out -Plan"
Write-Host "Then deploy:                                .\infra\deploy-gateway.ps1 -ParametersFile $out"
Write-Host "This file holds a key. Never commit or share it." -ForegroundColor Yellow















