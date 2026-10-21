<#
.SYNOPSIS
  Step 1 of 2. Discovers your Azure resources, lets you pick from real choices (never type an ID),
  validates every value, and writes the settings file that deploy-gateway.ps1 uses.

.EXAMPLE
  .\init-gateway-params.ps1                       # interactive, picks from lists
  .\init-gateway-params.ps1 -AcsResourceName my-acs -NonInteractive
  .\deploy-gateway.ps1 -ParametersFile infra\gateway.<name>.json
#>
param(
  [string]$Name = 'mycompany',
  [string]$Subscription,
  [string]$AcsResourceName,
  [string]$AppName = 'floorworks-intake-gateway',
  [string]$ResourceAccountObjectId,
  [string]$FoundryProject,
  [string]$VoiceAgent,
  [switch]$NonInteractive,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
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
  try {
    $r = Invoke-RestMethod "$endpoint/agents?api-version=v1" -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 30
    $names = @($r.data | ForEach-Object { $_.name })
  } catch { Warn "Could not list agents ($($_.Exception.Message))." }
}
if ($names.Count) {
  $voiceCand = @($names | Where-Object { $_ -match 'voice' })
  if ($VoiceAgent) { $voiceCand = @($names | Where-Object { $_ -eq $VoiceAgent }) }
  $voice = Pick 'voice agent' $(if ($voiceCand.Count) { $voiceCand } else { $names }) { param($n) $n }
  $exCand = @($names | Where-Object { $_ -match 'notetaker|extract' })
  if ($exCand.Count) { $extractor = Pick 'note-taker / extractor agent' $exCand { param($n) $n } }
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

Step "Speech (live notes)"
$speechKey = ''; $speechRegion = $fa.loc
$k = Az-Quiet cognitiveservices account keys list -g $fa.rg -n $fa.name --query key1 -o tsv
if ($k) { $speechKey = $k; Ok "using the Foundry account's key for Speech in $speechRegion (kept out of git; the file is gitignored)" }
else { Warn "Could not read a Speech key; live notes will be off unless you add speechKey." }

$params = [ordered]@{
  subscriptionId = $Subscription; resourceGroup = $envRg; appName = $AppName
  acrName = $acr.name; environmentName = $envName; acsResourceName = $acs.name
  foundryProjectEndpoint = $endpoint; voiceAgentName = $voice; extractorAgentName = $extractor
  resourceAccountObjectId = "$oid"; lineNumbers = ''; transferTeamsObjectIds = ''; corsOrigins = ''
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

