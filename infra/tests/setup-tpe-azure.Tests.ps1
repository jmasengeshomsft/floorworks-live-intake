$ErrorActionPreference = 'Stop'
$setup = Join-Path $PSScriptRoot '..\setup-tpe-azure.ps1'

function Assert($Condition, $Message) {
  if (-not $Condition) { throw $Message }
}

function az {
  $script:commands.Add(@($args))
  $global:LASTEXITCODE = 0
  $command = $args -join ' '
  switch -Regex ($command) {
    '^account show ' { return 'tenant-test' }
    '^ad app list ' { return 'bot-app-test' }
    '^ad sp show ' { return 'acs-principal-test' }
    '^bot (create|msteams create) ' { return }
    '^communication show .*--query id ' { return '/subscriptions/test/resourceGroups/test/providers/Microsoft.Communication/communicationServices/acs-test' }
    '^communication show .*--query immutableResourceId ' { return 'immutable-acs-test' }
    '^communication show .*--query hostName ' { return 'acs-test.communication.azure.com' }
    '^eventgrid system-topic list ' { if ($script:topicExists) { return 'existing-topic' }; return }
    '^eventgrid system-topic create ' { return }
    '^eventgrid system-topic event-subscription create ' {
      if ($script:subscriptionFails) { $global:LASTEXITCODE = 1 }
      return
    }
    default { throw "Unexpected Azure CLI command in offline test: $($args[0..1] -join ' ')" }
  }
}

function Invoke-Setup([string]$Secret) {
  $script:commands = [System.Collections.Generic.List[object]]::new()
  . $setup -ResourceGroup test -AcsName acs-test -AppUrl 'https://gateway.test/' -EventGridSecret $Secret
}

$script:topicExists = $true
$script:subscriptionFails = $false
foreach ($absent in '', ' ') {
  Invoke-Setup $absent
  Assert (-not @($script:commands | Where-Object { $_[0] -eq 'eventgrid' }).Count) 'Bootstrap without a secret must not create an unauthenticated Event Grid endpoint.'
}

$secret = 'test+secret/with?reserved&characters'
foreach ($exists in $true, $false) {
  $script:topicExists = $exists
  Invoke-Setup $secret
  $subscriptions = @($script:commands | Where-Object { $_[0] -eq 'eventgrid' -and $_[2] -eq 'event-subscription' })
  Assert ($subscriptions.Count -eq 1) 'Setup must create exactly one incoming-call subscription.'
  $subscription = $subscriptions[0]
  $endpoint = $subscription[[array]::IndexOf($subscription, '--endpoint') + 1]
  Assert ($endpoint -eq "https://gateway.test/api/incoming?s=$([uri]::EscapeDataString($secret))") 'Event Grid must receive the exact URL-encoded gateway secret.'
  $creates = @($script:commands | Where-Object { $_[0] -eq 'eventgrid' -and $_[2] -eq 'create' })
  Assert ($creates.Count -eq [int](-not $exists)) 'Setup must create a system topic only when missing.'
}

$script:subscriptionFails = $true
$caught = $null
try { Invoke-Setup $secret } catch { $caught = $_.Exception.Message }
Assert ($caught -match 'Authenticated Event Grid subscription creation failed') 'CLI subscription failure must stop setup with an actionable error.'

function Invoke-Rebind([string]$Secret) {
  $script:commands = [System.Collections.Generic.List[object]]::new()
  function Get-Content {
    'TenantId=tenant-test'
    'AcsHost=acs-test.communication.azure.com'
  }
  . (Join-Path $PSScriptRoot '..\rebind-tpe-azure.ps1') -Action Subscribe -ResourceGroup test `
    -GatewayUrl 'https://gateway.test/' -EventGridSecret $Secret
}

$script:subscriptionFails = $false
foreach ($absent in '', ' ') {
  $caught = $null
  try { Invoke-Rebind $absent } catch { $caught = $_.Exception.Message }
  Assert ($caught -match 'EventGridSecret must match') 'Rebinding without a secret must fail before Azure operations.'
  Assert ($script:commands.Count -eq 0) 'Missing-secret validation must precede all Azure operations.'
}
Invoke-Rebind $secret
$subscription = $script:commands[0]
$endpoint = $subscription[[array]::IndexOf($subscription, '--endpoint') + 1]
Assert ($endpoint -eq "https://gateway.test/api/incoming?s=$([uri]::EscapeDataString($secret))") 'Rebinding must use the authenticated gateway endpoint.'
$script:subscriptionFails = $true
$caught = $null
try { Invoke-Rebind $secret } catch { $caught = $_.Exception.Message }
Assert ($caught -match 'Authenticated Event Grid subscription creation failed') 'Rebinding must surface subscription failures.'
$global:LASTEXITCODE = 0
Write-Host 'Telephony setup/rebinding authentication regressions passed.'
