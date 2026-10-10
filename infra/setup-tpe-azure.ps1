<#
 Azure-side Teams Phone Extensibility (TPE) prerequisites:
  - Entra app (bot identity) for the Teams resource account
  - Azure Bot with the Teams channel + calling enabled (without it Teams rejects calls with SIP 480 / code 10140)
  - ACS service principal in the tenant
  - Event Grid subscription routing IncomingCall events to the call controller
 Run BEFORE teams/setup-teams.ps1. Prints the values that script needs.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$ResourceGroup,
  [Parameter(Mandatory)][string]$AcsName,
  [Parameter(Mandatory)][string]$CallControllerUrl,     # https://<call-controller-fqdn>
  [string]$BotName = 'floorworks-intake-bot',
  [string]$EventSubscriptionName = 'floorworks-intake-incoming'
)
$ErrorActionPreference = 'Stop'
$CallControllerUrl = $CallControllerUrl.TrimEnd('/')
$tenant = az account show --query tenantId -o tsv

$appId = az ad app list --display-name $BotName --query '[0].appId' -o tsv
if (-not $appId) {
  $appId = az ad app create --display-name $BotName --sign-in-audience AzureADMyOrg --query appId -o tsv
  az ad sp create --id $appId -o none
}
# Single-tenant bots need a client secret only if the bot itself must authenticate; TPE routing does not.
az bot create -g $ResourceGroup -n $BotName --appid $appId --app-type SingleTenant --tenant-id $tenant --sku F0 --location global -o none 2>$null
az bot msteams create -g $ResourceGroup -n $BotName --enable-calling true --calling-web-hook "$CallControllerUrl/api/calling" -o none

# Microsoft's Azure Communication Services first-party app must exist in the tenant.
$acsSp = az ad sp show --id 1fd5118e-2576-4263-8130-9503064c837a --query id -o tsv 2>$null
if (-not $acsSp) { az ad sp create --id 1fd5118e-2576-4263-8130-9503064c837a -o none }

$acsId = az communication show -n $AcsName -g $ResourceGroup --query id -o tsv
$sysTopic = az eventgrid system-topic list -g $ResourceGroup --query "[?source=='$acsId'].name | [0]" -o tsv
if (-not $sysTopic) {
  $sysTopic = "$AcsName-topic"
  az eventgrid system-topic create -g $ResourceGroup -n $sysTopic --source $acsId --topic-type Microsoft.Communication.CommunicationServices --location global -o none
}
az eventgrid system-topic event-subscription create -g $ResourceGroup --system-topic-name $sysTopic -n $EventSubscriptionName `
  --endpoint "$CallControllerUrl/api/incoming" --endpoint-type webhook --included-event-types Microsoft.Communication.IncomingCall -o none

$acsImmutable = az communication show -n $AcsName -g $ResourceGroup --query immutableResourceId -o tsv
Write-Host ''
Write-Host "Tenant id:            $tenant"
Write-Host "Bot app id:           $appId"
Write-Host "ACS immutable id:     $acsImmutable"
Write-Host "ACS endpoint host:    $(az communication show -n $AcsName -g $ResourceGroup --query hostName -o tsv)"
Write-Host 'Next: run teams/setup-teams.ps1 with these values.'
