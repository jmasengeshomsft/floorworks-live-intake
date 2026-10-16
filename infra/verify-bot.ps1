<#
 Verifies the Azure Bot wiring for a Teams phone line (read-only).
 Checks: bot exists, SingleTenant + correct tenant, Teams channel enabled,
 Entra app exists with the bot's msaAppId, and (optionally) that the Teams
 resource account ApplicationId matches (invariant: ApplicationId == AppId == msaAppId).

 Usage:
   .\infra\verify-bot.ps1 -BotName tpe-live-intake-bot
   .\infra\verify-bot.ps1 -BotName tpe-live-intake-bot -ResourceAccountUpn ra1@contoso.onmicrosoft.com
#>
param(
  [string]$BotName = '<azure-bot-resource-name>',
  [string]$ResourceGroup = '<resource-group>',
  [string]$TenantId = '<your-tenant-id>',
  [string]$ResourceAccountUpn
)

$fail = 0
function Check($ok, $msg, $detail = '') {
  if ($ok) { Write-Host "[PASS] $msg" -ForegroundColor Green }
  else { Write-Host "[FAIL] $msg $detail" -ForegroundColor Red; $script:fail++ }
}

$bot = az bot show -g $ResourceGroup -n $BotName -o json 2>$null | ConvertFrom-Json
Check ($null -ne $bot) "Bot '$BotName' exists in $ResourceGroup"
if (-not $bot) { exit 1 }

$appId = $bot.properties.msaAppId
Write-Host "       msaAppId = $appId"
Check ($bot.properties.msaAppType -eq 'SingleTenant') 'App type is SingleTenant' "(got $($bot.properties.msaAppType))"
Check ($bot.properties.msaAppTenantId -eq $TenantId) 'Bot tenant matches' "(got $($bot.properties.msaAppTenantId))"

$teams = az bot msteams show -g $ResourceGroup -n $BotName --query "{isEnabled:properties.properties.isEnabled, enableCalling:properties.properties.enableCalling}" -o json 2>$null | ConvertFrom-Json
Check ($teams -and $teams.isEnabled) 'Teams channel enabled'
if ($teams) {
  Check ($teams.enableCalling) 'Teams calling enabled' '(run: az bot msteams update --enable-calling true ...)'
}

$app = az ad app list --filter "appId eq '$appId'" -o json 2>$null | ConvertFrom-Json
Check ($app.Count -eq 1) 'Entra app registration exists for msaAppId' "(found $($app.Count))"

if ($ResourceAccountUpn) {
  $ra = pwsh -NoProfile -Command "Import-Module MicrosoftTeams; if(-not (Get-CsTenant -ErrorAction SilentlyContinue)){Connect-MicrosoftTeams|Out-Null}; (Get-CsOnlineApplicationInstance -Identity '$ResourceAccountUpn').ApplicationId"
  $ra = ($ra | Select-Object -Last 1).Trim()
  Write-Host "       resource account ApplicationId = $ra"
  Check ($ra -eq $appId) 'Resource account ApplicationId == bot msaAppId'
} else {
  Write-Host '[SKIP] Pass -ResourceAccountUpn to verify the Teams-side ApplicationId match' -ForegroundColor Yellow
}

if ($fail) { Write-Host "$fail check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host 'All checks passed' -ForegroundColor Green
