<#
 Builds both services in ACR and deploys them to an existing Container Apps environment.
 Does NOT create the Event Grid subscription; see README step 5.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$ResourceGroup,
  [Parameter(Mandatory)][string]$ContainerAppsEnv,
  [Parameter(Mandatory)][string]$AcrName,
  [Parameter(Mandatory)][string]$AcsName,
  [Parameter(Mandatory)][string]$FoundryAccount,
  [Parameter(Mandatory)][string]$FoundryProject,
  [Parameter(Mandatory)][string]$RepObjectIds,       # comma-separated Entra object ids of reps who answer in Teams
  [string]$AgentName = 'floorworks-intake-extractor',
  [string]$SpeechRegion = 'eastus',
  [string]$Tag = 'v1'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$acr = "$AcrName.azurecr.io"

foreach ($svc in 'StreamProcessor', 'CallController') {
  $img = ($svc -creplace '([a-z])([A-Z])', '$1-$2').ToLower()
  az acr build -r $AcrName -t "${img}:$Tag" "$root\src\$svc" -o none
}

$speechKey = az cognitiveservices account keys list -n $FoundryAccount -g $ResourceGroup --query key1 -o tsv
$acsCs = az communication list-key --name $AcsName -g $ResourceGroup --query primaryConnectionString -o tsv

az containerapp create -n stream-processor -g $ResourceGroup --environment $ContainerAppsEnv `
  --image "$acr/stream-processor:$Tag" --registry-server $acr --registry-identity system `
  --system-assigned --target-port 8080 --ingress external --min-replicas 1 --max-replicas 1 `
  --secrets speech-key=$speechKey `
  --env-vars SPEECH_KEY=secretref:speech-key SPEECH_REGION=$SpeechRegion `
    FOUNDRY_PROJECT_ENDPOINT="https://$FoundryAccount.services.ai.azure.com/api/projects/$FoundryProject" `
    FOUNDRY_AGENT=$AgentName -o none
$spFqdn = az containerapp show -n stream-processor -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$spPid = az containerapp show -n stream-processor -g $ResourceGroup --query identity.principalId -o tsv

az containerapp create -n call-controller -g $ResourceGroup --environment $ContainerAppsEnv `
  --image "$acr/call-controller:$Tag" --registry-server $acr --registry-identity system `
  --system-assigned --target-port 8080 --ingress external --min-replicas 1 --max-replicas 1 `
  --secrets acs-cs=$acsCs `
  --env-vars ACS_CONNECTION_STRING=secretref:acs-cs STREAM_WSS_BASE="wss://$spFqdn" REP_TEAMS_OBJECT_IDS=$RepObjectIds -o none
$ccFqdn = az containerapp show -n call-controller -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
az containerapp update -n call-controller -g $ResourceGroup --set-env-vars PUBLIC_BASE_URL="https://$ccFqdn" -o none

# Lets the stream processor call the Foundry agent with its managed identity.
$fid = az cognitiveservices account show -n $FoundryAccount -g $ResourceGroup --query id -o tsv
az role assignment create --assignee-object-id $spPid --assignee-principal-type ServicePrincipal --role 'Azure AI User' --scope $fid -o none

Write-Host "stream-processor: https://$spFqdn/healthz"
Write-Host "call-controller:  https://$ccFqdn  (webhook: /api/incoming, calling endpoint: /api/calling)"
