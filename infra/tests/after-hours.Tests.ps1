$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\after-hours.ps1')

function Assert($Condition, $Message) {
  if (-not $Condition) { throw $Message }
}
function Assert-Throws($Action, $Pattern) {
  $caught = $null
  try { & $Action | Out-Null } catch { $caught = $_.Exception.Message }
  Assert ($caught -and $caught -match $Pattern) "Expected '$Pattern'; got '$caught'."
}

$schedule = [ordered]@{
  afterHours = 'auto'; serviceTz = 'America/Chicago'; serviceDays = 'Mon-Fri'; serviceHours = '07:00-18:00'
}
$settings = Resolve-GatewayAfterHoursSettings ([pscustomobject]$schedule)
$environment = @(Get-GatewayAfterHoursEnvironment $settings)
Assert ($environment.Count -eq 4 -and $environment -contains 'AFTER_HOURS=auto' -and
  $environment -contains 'SERVICE_TZ=America/Chicago' -and $environment -contains 'SERVICE_DAYS=Mon-Fri' -and
  $environment -contains 'SERVICE_HOURS=07:00-18:00') 'Automatic mode must emit the full canonical schedule.'
Assert (@(Get-GatewayAfterHoursEnvironment (Resolve-GatewayAfterHoursSettings ([pscustomobject]@{}))).Count -eq 0) 'Legacy parameters must not overwrite live settings.'
foreach ($mode in 'on', 'off') {
  $envVars = @(Get-GatewayAfterHoursEnvironment (Resolve-GatewayAfterHoursSettings ([pscustomobject]@{ afterHours = $mode })))
  Assert ($envVars.Count -eq 1 -and $envVars[0] -eq "AFTER_HOURS=$mode") 'Mode-only overrides must leave an existing schedule unchanged.'
}
foreach ($change in @(
  @{ Field = 'afterHours'; Value = 'maybe'; Error = 'afterHours' },
  @{ Field = 'serviceTz'; Value = 'Central Standard Time'; Error = 'IANA' },
  @{ Field = 'serviceTz'; Value = 'america/chicago'; Error = 'IANA' },
  @{ Field = 'serviceDays'; Value = 'Nonday'; Error = 'serviceDays' },
  @{ Field = 'serviceDays'; Value = 'Mon,,Fri'; Error = 'serviceDays' },
  @{ Field = 'serviceHours'; Value = '18:00-07:00'; Error = 'same-day' },
  @{ Field = 'serviceHours'; Value = '07:00-07:00'; Error = 'same-day' },
  @{ Field = 'serviceHours'; Value = '25:00-26:00'; Error = 'same-day' }
)) {
  $bad = [pscustomobject]$schedule
  $bad.($change.Field) = $change.Value
  Assert-Throws { Resolve-GatewayAfterHoursSettings $bad } $change.Error
}
foreach ($field in 'serviceTz', 'serviceDays', 'serviceHours') {
  $bad = [pscustomobject]$schedule
  $bad.PSObject.Properties.Remove($field)
  Assert-Throws { Resolve-GatewayAfterHoursSettings $bad } 'requires'
}
Assert-Throws { Resolve-GatewayAfterHoursSettings ([pscustomobject]@{ afterHours = 'off'; serviceTz = 'UTC' }) } 'requires'
Assert-Throws { Resolve-GatewayAfterHoursSettings ([pscustomobject]@{ afterHour = 'on' }) } 'final S'

function Test-Initializer($Overrides = @{}) {
  $name = 'office-test-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
  $file = Join-Path $PSScriptRoot "..\gateway.$name.json"
  $resourceId = '/subscriptions/10000000-0000-0000-0000-000000000001/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/foundry'
  function az.cmd {
    $global:LASTEXITCODE = 0
    $command = $args -join ' '
    switch -Regex ($command) {
      '^account show -o tsv' { return 'signed-in' }
      '^account set ' { return }
      '^account show --query tenantId' { return 'tenant' }
      '^account get-access-token ' { return 'test-token' }
      '^communication list ' { return '[{"name":"acs","rg":"test","id":"acs-id"}]' }
      '^communication show ' { return '{"hostName":"acs.example"}' }
      '^containerapp env list ' { return '[{"name":"env"}]' }
      '^acr list ' { return '[{"name":"acr","rg":"test"}]' }
      '^cognitiveservices account list ' { return (@(@{ name = 'foundry'; id = $resourceId; rg = 'test' }) | ConvertTo-Json -AsArray -Compress) }
      '^rest --method get ' { return '{"value":[{"name":"project"}]}' }
      '^resource show ' { return (@{ id = $resourceId; kind = 'AIServices'; location = 'eastus'; properties = @{ customSubDomainName = 'foundry' } } | ConvertTo-Json -Compress) }
      default { throw "Unexpected initializer Azure call: $command" }
    }
  }
  function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec)
    if ($Uri -notlike '*/agents?api-version=v1') { throw "Unexpected request: $Uri" }
    @{ data = @(
      @{ name = 'voice'; versions = @{ latest = @{ definition = @{ kind = 'voice' } } } },
      @{ name = 'notes'; versions = @{ latest = @{ definition = @{ kind = 'prompt' } } } }
    ) }
  }
  try {
    & (Join-Path $PSScriptRoot '..\init-gateway-params.ps1') -Name $name -Subscription test -NonInteractive -CorsOrigins '' -BrandName Intake -DataverseUrl https://test.crm.dynamics.com @Overrides | Out-Null
    $generated = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
    Resolve-GatewayAfterHoursSettings $generated | Out-Null
    $expectedFields = @{}
    foreach ($key in $schedule.Keys) { $expectedFields[$key] = $schedule[$key] }
    foreach ($key in $Overrides.Keys) { $expectedFields[$key] = $Overrides[$key] }
    foreach ($field in $expectedFields.Keys) {
      Assert ($generated.$field -ceq $expectedFields[$field]) "Initializer did not persist $field=$($expectedFields[$field])."
    }
  } finally { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file } }
}
Test-Initializer
Test-Initializer @{ AfterHours = 'auto'; ServiceTz = 'Europe/Paris'; ServiceDays = 'Tue-Sat'; ServiceHours = '09:00-17:30' }

function Test-Deployment($ScheduleFields, [bool]$Invalid = $false) {
  $capture = [pscustomobject]@{ Update = @(); Calls = 0 }
  $parameters = [ordered]@{
    subscriptionId = 'test'; resourceGroup = 'test'; appName = 'office-test'; acrName = 'acr'
    environmentName = 'env'; acsResourceName = 'acs'; foundryProjectEndpoint = 'https://foundry.services.ai.azure.com/api/projects/project'
    voiceAgentName = 'voice'; extractorAgentName = 'notes'; dataverseUrl = 'https://test.crm.dynamics.com'; liveNotes = 'off'
  }
  foreach ($key in $ScheduleFields.Keys) { $parameters[$key] = $ScheduleFields[$key] }
  function az.cmd {
    $capture.Calls++
    $global:LASTEXITCODE = 0
    $command = $args -join ' '
    switch -Regex ($command) {
      '^account show ' { return 'signed-in' }
      '^account set ' { return }
      '^account get-access-token ' { return 'test-token' }
      '^extension show ' { return 'containerapp' }
      '^provider show ' { return 'Registered' }
      '^group show ' { return 'test' }
      '^acr show ' { return '{"id":"acr-id"}' }
      '^containerapp env show ' { return 'environment-id' }
      '^communication show ' { return '{"immutableResourceId":"acs-id"}' }
      '^cognitiveservices account list ' { return 'null' }
      '^containerapp list ' { return '["office-test"]' }
      '^containerapp secret list ' { return '[]' }
      '^communication list-key ' { return '{"primaryConnectionString":"test-connection"}' }
      '^acr repository show-tags ' { return '[]' }
      '^containerapp show .*identity.principalId' { return 'principal' }
      '^containerapp show .*ingress.fqdn' { return 'gateway.example' }
      '^ad sp show ' { return 'application' }
      '^(role assignment create|containerapp registry set|containerapp secret set|containerapp ingress update) ' { return }
      '^containerapp update ' { $capture.Update = @($args); $global:LASTEXITCODE = 1; return }
      default { throw "Unexpected deployment Azure call: $command" }
    }
  }
  function Invoke-RestMethod {
    param($Uri, $Headers, $Method, $Body)
    if ($Method -eq 'Post') { return }
    if ($Uri -like '*/systemusers?*') { return @{ value = @(@{ systemuserid = 'user' }) } }
    if ($Uri -like '*/roles?*') { return @{ value = @(@{ roleid = 'role' }) } }
    throw "Unexpected deployment request: $Uri"
  }
  $file = [IO.Path]::GetTempFileName()
  try {
    [IO.File]::WriteAllText($file, ($parameters | ConvertTo-Json))
    $pattern = if ($Invalid) { 'requires' } else { 'containerapp update failed' }
    Assert-Throws { & (Join-Path $PSScriptRoot '..\deploy-gateway.ps1') -ParametersFile $file -SkipBuild } $pattern
    if ($Invalid) {
      Assert ($capture.Calls -eq 0) 'Invalid schedules must fail before Azure is called.'
    } elseif ($ScheduleFields.Count) {
      foreach ($entry in @(Get-GatewayAfterHoursEnvironment (Resolve-GatewayAfterHoursSettings ([pscustomobject]$ScheduleFields)))) {
        Assert ($capture.Update -contains $entry) "Deployment omitted $entry."
      }
    } else {
      Assert ($capture.Update.Count -gt 0 -and -not ($capture.Update -match '^(AFTER_HOURS|SERVICE_TZ|SERVICE_DAYS|SERVICE_HOURS)=')) 'Legacy deployment must not override office hours.'
    }
  } finally { Remove-Item -LiteralPath $file; $global:LASTEXITCODE = 0 }
}
Test-Deployment $schedule
Test-Deployment @{}
Test-Deployment @{ afterHours = 'on' }
Test-Deployment @{ afterHours = 'auto' } $true

function Test-Verifier($ExtraEnvironment, $RuntimeChanges, [string]$ExpectedError = '') {
  $deployed = [ordered]@{
    PUBLIC_BASE_URL = 'https://gateway.example'; FOUNDRY_PROJECT_ENDPOINT = 'https://foundry.example'
    VOICE_AGENT_NAME = 'voice'; EXTRACTOR_AGENT_NAME = 'notes'; DATAVERSE_URL = 'https://test.crm.dynamics.com'
    ACS_CONNECTION_STRING = 'test'; API_KEYS = 'test-key'; GATEWAY_SECRET = 'test'; EVENTGRID_SECRET = 'test-event'
    ACS_RESOURCE_ID = 'acs'; DATAVERSE_SCHEMA = '{}'; LIVE_NOTES = 'off'
  }
  foreach ($key in $ExtraEnvironment.Keys) { $deployed[$key] = $ExtraEnvironment[$key] }
  $runtime = [pscustomobject]@{
    afterHoursMode = 'auto'; afterHoursNow = $true; serviceTz = 'America/Chicago'
    serviceDays = 'Mon-Fri'; serviceHours = '07:00-18:00'; liveNotes = 'off'
  }
  foreach ($key in $RuntimeChanges.Keys) {
    if ($RuntimeChanges[$key] -eq '[missing]') { $runtime.PSObject.Properties.Remove($key) }
    else { $runtime.$key = $RuntimeChanges[$key] }
  }
  $app = @{
    properties = @{
      latestRevisionName = 'revision'
      configuration = @{ ingress = @{ fqdn = 'gateway.example'; external = $true; targetPort = 8080 } }
      template = @{
        scale = @{ minReplicas = 1; maxReplicas = 1 }
        containers = @(@{ image = 'test-image'; env = @($deployed.Keys | ForEach-Object { @{ name = $_; value = $deployed[$_] } }) })
      }
    }
  }
  $eventSubscription = @{
    name = 'sub'; provisioningState = 'Succeeded'; filter = @{ includedEventTypes = @('Microsoft.Communication.IncomingCall') }
    destination = @{ endpointUrl = 'https://gateway.example/api/incoming?s=test-event' }
  }
  function az.cmd {
    $global:LASTEXITCODE = 0
    switch -Regex ($args -join ' ') {
      '^containerapp show ' { return ($app | ConvertTo-Json -Depth 12 -Compress) }
      '^containerapp secret list ' { return '[]' }
      '^eventgrid system-topic list ' { return '[{"name":"topic"}]' }
      '^eventgrid system-topic event-subscription list ' { return (ConvertTo-Json -InputObject @($eventSubscription) -Depth 6 -Compress) }
      '^eventgrid system-topic event-subscription show ' { return ($eventSubscription | ConvertTo-Json -Depth 6 -Compress) }
      default { throw "Unexpected verifier Azure call: $($args -join ' ')" }
    }
  }
  function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec)
    if ($Uri -like '*/v1/config') { return $runtime }
    if ($Uri -like '*/healthz') { return @{ status = 'ok' } }
    throw "Unexpected verifier request: $Uri"
  }
  $output = & (Join-Path $PSScriptRoot '..\verify-gateway.ps1') -ResourceGroup test -AppName test -SkipLogChecks 6>&1 | Out-String
  if ($ExpectedError) { Assert ($LASTEXITCODE -eq 1 -and $output -match $ExpectedError) "Verifier did not diagnose '$ExpectedError': $output" }
  else { Assert ($LASTEXITCODE -eq 0) "Valid schedule failed verification: $output" }
  $global:LASTEXITCODE = 0
}
$autoEnvironment = @{ AFTER_HOURS = 'auto'; SERVICE_TZ = 'America/Chicago'; SERVICE_DAYS = 'Mon-Fri'; SERVICE_HOURS = '07:00-18:00' }
Test-Verifier $autoEnvironment @{}
Test-Verifier @{ AFTER_HOUR = 'on' } @{ afterHoursMode = 'off' } 'AFTER_HOUR is ignored'
Test-Verifier $autoEnvironment @{ afterHoursMode = 'off' } 'differs from deployed'
Test-Verifier $autoEnvironment @{ serviceHours = '[missing]' } 'does not report serviceHours'
Write-Host 'Office-hours configuration, deployment and verifier regressions passed.'
