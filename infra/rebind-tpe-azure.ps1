<#
 Azure-side binding steps (Entra app, Azure Bot, ACS Teams-extension assignment, Event Grid subscription).
 Pair with teams\rebind-tpe.ps1 (Teams side). Values come from teams\.env.

 -Action Snapshot : read-only (app, Azure Bot, ACS assignment, Event Grid subscription)
 -Action Teardown : delete Event Grid subscription, ACS assignment, the Entra app + SP and the Azure Bot
 -Action CreateApp: create the Entra app + SP and the Azure Bot with Teams calling (prints the new AppId)
 -Action Assign   : ACS access assignment for the resource account + ACS first-party SP check
 -Action Subscribe: Event Grid subscription IncomingCall -> $GatewayUrl/api/incoming
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('Snapshot', 'Teardown', 'CreateApp', 'Assign', 'Subscribe')][string]$Action,
  [string]$ResourceGroup = 'sharepointdemo',
  [string]$AcsName = 'acs-jmdeere-demo',
  [string]$AppName = 'tpe-live-intake-bot',
  [string]$ResourceAccountObjectId = $env:TPE_RESOURCE_ACCOUNT_OID,
  [string]$SystemTopic = 'acs-jmdeere-demo-topic',
  [string]$SubscriptionName = 'audio-probe-incoming',
  [string]$GatewayUrl = 'https://floorworks-intake-gateway.mangomushroom-8c85c1e1.eastus.azurecontainerapps.io'
)
$ErrorActionPreference = 'Stop'
$cfg = @{}
Get-Content (Join-Path $PSScriptRoot '..\teams\.env') | Where-Object { $_ -match '^\w+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v }
$tenant = $cfg.TenantId
$acsUri = "https://$($cfg.AcsHost)/access/teamsExtension/tenants/$tenant/assignments/$ResourceAccountObjectId`?api-version=2025-06-30"
$acsTok = { az account get-access-token --resource https://communication.azure.com --query accessToken -o tsv }

switch ($Action) {
  'Snapshot' {
    'App:'; az ad app list --display-name $AppName --query '[].{appId:appId,objectId:id,audience:signInAudience}' -o json
    'Azure Bot (msaAppId must equal the app AppId):'
    az bot show -g $ResourceGroup -n $AppName --query '{msaAppId:properties.msaAppId,endpoint:properties.endpoint}' -o json 2>$null
    az bot msteams show -g $ResourceGroup -n $AppName --query '{calling:properties.properties.enableCalling,webhook:properties.properties.callingWebhook}' -o json 2>$null
    'ACS assignment:'
    try { Invoke-RestMethod -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | ConvertTo-Json } catch { "GET failed: $($_.Exception.Message)" }
    'Event Grid subscription:'
    az eventgrid system-topic event-subscription show -g $ResourceGroup --system-topic-name $SystemTopic -n $SubscriptionName --query '{url:destination.endpointBaseUrl,state:provisioningState,types:filter.includedEventTypes}' -o json
  }
  'Teardown' {
    az eventgrid system-topic event-subscription delete -g $ResourceGroup --system-topic-name $SystemTopic     -n $SubscriptionName --yes -o none
    Invoke-RestMethod -Method Delete -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | Out-Null
    foreach ($id in (az ad app list --display-name $AppName --query '[].id' -o tsv)) { az ad app delete --id $id }
    az bot delete -g $ResourceGroup -n $AppName 2>$null
    'Teardown done.'
  }
  'CreateApp' {
    $appId = az ad app create --display-name $AppName --sign-in-audience AzureADMyOrg --query appId -o tsv
    az ad sp create --id $appId -o none
    # REQUIRED: Azure Bot (same name) bound to the new AppId with Teams calling; without it Teams returns SIP 480.
    az bot create -g $ResourceGroup -n $AppName --appid $appId --app-type SingleTenant --tenant-id (az account show --query tenantId -o tsv) --sku F0 --location global -o none
    az bot msteams create -g $ResourceGroup -n $AppName --enable-calling true --calling-web-hook 'https://tpe-call-controller.mangomushroom-8c85c1e1.eastus.azurecontainerapps.io/api/calling' -o none
    "NEW_APP_ID=$appId"
  }
  'Assign' {
    # ACS first-party app must exist in the tenant
    if (-not (az ad sp show --id 1fd5118e-2576-4263-8130-9503064c837a --query id -o tsv 2>$null)) { az ad sp create --id 1fd5118e-2576-4263-8130-9503064c837a -o none }
    Invoke-RestMethod -Method Put -ContentType 'application/json' -Body '{"principalType":"teamsResourceAccount"}' `
      -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | ConvertTo-Json
  }
  'Subscribe' {
    az eventgrid system-topic event-subscription create -g $ResourceGroup --system-topic-name $SystemTopic -n $SubscriptionName `
      --endpoint "$GatewayUrl/api/incoming" --endpoint-type webhook --included-event-types Microsoft.Communication.IncomingCall --query provisioningState -o tsv
  }
}
