<#
.SYNOPSIS
  One-command, idempotent deploy of the FloorWorks intake gateway (Option A) into an Azure subscription.
  Builds the image, creates or updates the Container App, wires secrets and environment variables,
  grants the app's identity the access it needs, connects Event Grid (IncomingCall) with the right
  shared secret, then smoke-tests the result. Safe to re-run.

  Assumes already done: Teams number + resource account, ACS resource (with the Teams extension
  consent), Foundry project with the voice agent, a Container Apps environment, an ACR.

.PARAMETER ParametersFile
  JSON copy of gateway.parameters.example.json. Keep it out of git.

.PARAMETER Plan
  Run preflight checks only; change nothing.

.EXAMPLE
  Copy-Item infra\gateway.parameters.example.json infra\gateway.mycustomer.json   # edit it
  .\deploy-gateway.ps1 -ParametersFile infra\gateway.mycustomer.json -Plan        # check first
  .\deploy-gateway.ps1 -ParametersFile infra\gateway.mycustomer.json
#>
param(
  [Parameter(Mandatory)][string]$ParametersFile,
  [string]$ImageTag = (Get-Date -Format 'yyyyMMdd-HHmm'),
  [switch]$SkipBuild,
  [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$script:problems = @()
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Info($m) { Write-Host "  $m" }
function Problem($m, $fix) { Write-Host "  [FAIL] $m" -ForegroundColor Red; if ($fix) { Write-Host "         Fix: $fix" -ForegroundColor Yellow }; $script:problems += $m }
function J($t) { $s = ($t | Out-String).Trim(); if ($s) { $s | ConvertFrom-Json } }
function Az-Quiet { $r = & az @args 2>$null; if ($LASTEXITCODE -ne 0) { return $null }; $r }

function New-Secret {
  $b = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  -join ($b | ForEach-Object { '{0:x2}' -f $_ })
}

# ---------------------------------------------------------------- parameters
if (-not (Test-Path $ParametersFile)) { throw "Parameters file not found: $ParametersFile" }
$p = Get-Content $ParametersFile -Raw | ConvertFrom-Json
foreach ($k in 'subscriptionId','resourceGroup','appName','acrName','environmentName','acsResourceName','foundryProjectEndpoint','voiceAgentName') {
  if (-not $p.$k -or "$($p.$k)" -match '^<') { throw "Parameter '$k' is missing or still a placeholder in $ParametersFile" }
}
$rg = $p.resourceGroup; $app = $p.appName

Step "Preflight"
if (-not (Az-Quiet account show -o tsv)) { throw "Not signed in. Run: az login" }
az account set --subscription $p.subscriptionId
if ($LASTEXITCODE -ne 0) { throw "Cannot select subscription $($p.subscriptionId). Check access." }
Ok "subscription $($p.subscriptionId)"
if (-not (Az-Quiet extension show --name containerapp --query name -o tsv)) { Info "installing the containerapp CLI extension"; az extension add --name containerapp --only-show-errors | Out-Null }
foreach ($ns in 'Microsoft.App','Microsoft.ContainerRegistry','Microsoft.EventGrid','Microsoft.Communication') {
  $state = Az-Quiet provider show -n $ns --query registrationState -o tsv
  if ($state -ne 'Registered') { Info "registering provider $ns"; if (-not $Plan) { az provider register -n $ns --wait 2>$null | Out-Null } }
}
if (-not (Az-Quiet group show -n $rg -o tsv)) { Problem "Resource group '$rg' not found." "Create it or fix resourceGroup." } else { Ok "resource group $rg" }
$acr = J (Az-Quiet acr show -n $p.acrName -o json)
if (-not $acr) { Problem "ACR '$($p.acrName)' not found." "Fix acrName." } else { Ok "ACR $($p.acrName)" }
$envId = Az-Quiet containerapp env show -g $rg -n $p.environmentName --query id -o tsv
if (-not $envId) { Problem "Container Apps environment '$($p.environmentName)' not found in '$rg'." "Fix environmentName." } else { Ok "environment $($p.environmentName)" }
$acs = J (Az-Quiet communication show -g $rg -n $p.acsResourceName -o json)
if (-not $acs) { Problem "ACS resource '$($p.acsResourceName)' not found in '$rg'." "Event Grid topic and ACS must share this resource group." } else { Ok "ACS $($p.acsResourceName) (immutable id $($acs.immutableResourceId))" }

$fHost = ([uri]$p.foundryProjectEndpoint).Host.Split('.')[0]
$foundry = J (Az-Quiet cognitiveservices account list --query "[?name=='$fHost'] | [0]" -o json)
if (-not $foundry) { Info "Foundry account '$fHost' not found in this subscription: the identity role assignment will be skipped (assign 'Azure AI User' yourself)." } else { Ok "Foundry account $fHost" }

if ($script:problems.Count) { Write-Host "`nPreflight failed; nothing was changed." -ForegroundColor Red; exit 1 }
if ($Plan) { Write-Host "`nPreflight passed. Re-run without -Plan to deploy." -ForegroundColor Green; exit 0 }

# ---------------------------------------------------------------- secrets
Step "Secrets"
$existing = J (Az-Quiet containerapp show -g $rg -n $app -o json)
$sec = @{}
if ($existing) {
  foreach ($s in (J (az containerapp secret list -g $rg -n $app --show-values -o json))) { $sec[$s.name] = $s.value }
  Ok "reusing secrets from running app (Event Grid URL stays valid)"
}
foreach ($n in 'gateway-secret','eventgrid-secret') { if (-not $sec[$n]) { $sec[$n] = New-Secret; Info "generated $n" } }
if ($p.apiKeys) { $sec['api-keys'] = $p.apiKeys } elseif (-not $sec['api-keys']) { $sec['api-keys'] = New-Secret; Info "generated api-keys" }
$keys = J (az communication list-key -g $rg -n $p.acsResourceName -o json)
$sec['acs-connection-string'] = $keys.primaryConnectionString
if ($p.speechKey) { $sec['speech-key'] = $p.speechKey }
$secretArgs = @($sec.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })

# ---------------------------------------------------------------- image
$image = "$($p.acrName).azurecr.io/intake-gateway:$ImageTag"
if (-not $SkipBuild) {
  Step "Build image $image"
  $ctx = Join-Path (Split-Path $PSScriptRoot -Parent) 'src\intake-gateway'
  az acr build -r $p.acrName -t "intake-gateway:$ImageTag" $ctx --no-logs --only-show-errors | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Image build failed. Run: az acr build -r $($p.acrName) -t intake-gateway:$ImageTag $ctx" }
  Ok "built"
}

# ---------------------------------------------------------------- app
Step "Container App"
$lines = @()
if ($p.lineNumbers) { $lines += ($p.lineNumbers -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
if ($p.resourceAccountObjectId) {
  $id = "28:orgid:$($p.resourceAccountObjectId)"
  if ($lines -notcontains $id) { $lines += $id }
}
$plain = [ordered]@{
  FOUNDRY_PROJECT_ENDPOINT  = $p.foundryProjectEndpoint
  VOICE_AGENT_NAME          = $p.voiceAgentName
  EXTRACTOR_AGENT_NAME      = $p.extractorAgentName
  ACS_RESOURCE_ID           = $acs.immutableResourceId
  ACS_JWT_MODE              = 'enforce'
  LIVE_NOTES                = $(if ($p.liveNotes) { $p.liveNotes } else { 'on' })
  ENRICHMENT                = $(if ($p.enrichment) { $p.enrichment } else { 'off' })
  CORS_ORIGINS              = $p.corsOrigins
  LINE_NUMBERS              = ($lines -join ',')
  TRANSFER_TEAMS_OBJECT_IDS = $p.transferTeamsObjectIds
  SPEECH_REGION             = $p.speechRegion
}
$envVars = @()
foreach ($k in $plain.Keys) { if ("$($plain[$k])".Trim()) { $envVars += "$k=$($plain[$k])" } }
$envVars += 'ACS_CONNECTION_STRING=secretref:acs-connection-string', 'GATEWAY_SECRET=secretref:gateway-secret',
            'EVENTGRID_SECRET=secretref:eventgrid-secret', 'API_KEYS=secretref:api-keys'
if ($sec['speech-key']) { $envVars += 'SPEECH_KEY=secretref:speech-key' }

if (-not $existing) {
  Info "creating $app"
  az containerapp create -g $rg -n $app --environment $p.environmentName --image "mcr.microsoft.com/k8se/quickstart:latest" `
    --ingress external --target-port 80 --min-replicas 1 --max-replicas 3 --system-assigned --only-show-errors | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "containerapp create failed" }
}
$principal = az containerapp show -g $rg -n $app --query identity.principalId -o tsv
Info "granting AcrPull to the app identity on $($p.acrName)"
az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role AcrPull --scope $acr.id --only-show-errors 2>$null | Out-Null
az containerapp registry set -g $rg -n $app --server "$($p.acrName).azurecr.io" --identity system --only-show-errors | Out-Null
if ($foundry) {
  Info "granting 'Azure AI User' on Foundry account $fHost"
  az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Azure AI User" --scope $foundry.id --only-show-errors 2>$null | Out-Null
}
az containerapp secret set -g $rg -n $app --secrets @secretArgs --only-show-errors | Out-Null
$fqdn = az containerapp show -g $rg -n $app --query properties.configuration.ingress.fqdn -o tsv
az containerapp ingress update -g $rg -n $app --target-port 8080 --type external --only-show-errors | Out-Null
Info "deploying $image (PUBLIC_BASE_URL=https://$fqdn)"
az containerapp update -g $rg -n $app --image $image --min-replicas 1 --set-env-vars @envVars "PUBLIC_BASE_URL=https://$fqdn" --only-show-errors | Out-Null
if ($LASTEXITCODE -ne 0) { throw "containerapp update failed" }

Step "Wait for a healthy app (role assignments can take a minute to apply)"
$ok = $false; $h = $null
foreach ($i in 1..40) {
  try { $h = Invoke-RestMethod "https://$fqdn/healthz" -TimeoutSec 10; if ($h.status -eq 'ok') { $ok = $true; break } } catch { $h = $null }
  Start-Sleep 6
}
if (-not $ok) {
  if ($h) { Write-Host "  healthz: $($h | ConvertTo-Json -Compress)" }
  throw "https://$fqdn/healthz never became ok. Run: az containerapp logs show -g $rg -n $app --tail 100"
}
Ok "healthz ok"

# ---------------------------------------------------------------- Event Grid
Step "Event Grid"
$topic = Az-Quiet eventgrid system-topic list -g $rg --query "[?contains(to_string(source), '$($p.acsResourceName)')].name | [0]" -o tsv
if (-not $topic) {
  $topic = "$($p.acsResourceName)-topic"
  az eventgrid system-topic create -g $rg -n $topic --location global --topic-type Microsoft.Communication.CommunicationServices --source $acs.id --only-show-errors 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not create system topic for ACS '$($p.acsResourceName)'." }
}
$others = J (Az-Quiet eventgrid system-topic event-subscription list -g $rg --system-topic-name $topic -o json)
foreach ($o in $others) {
  if ($o.name -ne 'gateway-incoming' -and $o.filter.includedEventTypes -contains 'Microsoft.Communication.IncomingCall') {
    Write-Host "  [WARN] subscription '$($o.name)' on this topic also receives IncomingCall. Two consumers will both try to answer; delete one." -ForegroundColor Yellow
  }
}
$endpoint = "https://$fqdn/api/incoming?s=$($sec['eventgrid-secret'])"
az eventgrid system-topic event-subscription create -g $rg --system-topic-name $topic -n gateway-incoming `
  --endpoint $endpoint --endpoint-type webhook --included-event-types Microsoft.Communication.IncomingCall --only-show-errors 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Event Grid subscription failed (endpoint validation). Check: az containerapp logs show -g $rg -n $app --tail 50" }
Ok "gateway-incoming on $topic -> https://$fqdn/api/incoming"

# ---------------------------------------------------------------- smoke tests
Step "Smoke tests"
try { Invoke-WebRequest "https://$fqdn/api/incoming?s=WRONG" -Method Post -Body '[]' -ContentType 'application/json' -UseBasicParsing | Out-Null; $code = 200 }
catch { $code = [int]$_.Exception.Response.StatusCode }
if ($code -eq 401) { Ok "wrong secret rejected (401)" } else { Problem "wrong secret returned $code instead of 401" "Gateway is not enforcing EVENTGRID_SECRET." }
$vb = '[{"id":"smoke","topic":"t","subject":"s","eventType":"Microsoft.EventGrid.SubscriptionValidationEvent","eventTime":"2026-01-01T00:00:00Z","data":{"validationCode":"abc123"},"dataVersion":"2","metadataVersion":"1"}]'
try {
  $r = Invoke-RestMethod "https://$fqdn/api/incoming?s=$($sec['eventgrid-secret'])" -Method Post -Body $vb -ContentType 'application/json'
  if ($r.validationResponse -eq 'abc123') { Ok "right secret accepted and validation handshake works" } else { Problem "validation handshake returned: $($r | ConvertTo-Json -Compress)" }
} catch { Problem "right secret was rejected: $($_.Exception.Message)" "The app secret and Event Grid URL differ." }

& (Join-Path $PSScriptRoot 'verify-gateway.ps1') -ResourceGroup $rg -AppName $app -SkipLogChecks
if ($LASTEXITCODE -ne 0) { $script:problems += 'verifier reported failures' }

Write-Host "`nDeployed: https://$fqdn   image: $image" -ForegroundColor Green
Write-Host "Place a test call to the Teams number, then: az containerapp logs show -g $rg -n $app --tail 100 --format text"
if ($script:problems.Count) { exit 1 }
