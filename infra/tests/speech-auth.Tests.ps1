$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\speech-auth.ps1')

function Assert($Condition, $Message) {
  if (-not $Condition) { throw $Message }
}
function Assert-Throws($Action, $Pattern) {
  $caught = $null
  try { & $Action | Out-Null } catch { $caught = $_.Exception.Message }
  Assert ($caught -and $caught -match $Pattern) "Expected failure matching '$Pattern'; got '$caught'."
}

$id = '/subscriptions/10000000-0000-0000-0000-000000000001/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/speech-test'
$resource = [pscustomobject]@{
  id = $id; kind = 'AIServices'; location = 'eastus'
  properties = [pscustomobject]@{ customSubDomainName = 'speech-test' }
}
$settings = Resolve-GatewaySpeechSettings ([pscustomobject]@{}) $resource
Assert ($settings.Auth -eq 'aad' -and $settings.ResourceId -eq $id -and $settings.Region -eq 'eastus') 'New settings must default to keyless Speech on the selected account.'
$aad = [pscustomobject]@{ speechAuth = 'aad'; speechKey = 'unused-test-value'; speechResourceId = $id }
$settings = Resolve-GatewaySpeechSettings $aad $resource
$environment = @(Get-GatewaySpeechEnvironment $settings $true)
Assert ($environment -contains 'SPEECH_KEY=' -and $environment -notcontains 'SPEECH_KEY=secretref:speech-key') 'AAD migration must clear an existing key reference.'
Assert ($environment -contains "SPEECH_RESOURCE_ID=$id") 'AAD must identify the account used for the authorization token.'
Assert (-not ($environment -match 'unused-test-value')) 'Environment generation must never expose an input key.'

$legacy = [pscustomobject]@{ speechKey = 'test-value'; speechRegion = 'westus' }
$settings = Resolve-GatewaySpeechSettings $legacy $null
Assert ($settings.Auth -eq 'key' -and $settings.Region -eq 'westus') 'Existing key-based configurations must keep their selected mode and region.'
Assert (@(Get-GatewaySpeechEnvironment $settings $true) -contains 'SPEECH_KEY=secretref:speech-key') 'Key mode must retain the secret reference.'
Assert-Throws { Get-GatewaySpeechEnvironment $settings $false } 'requires speechKey'
Assert ((Get-GatewaySpeechAuth ([pscustomobject]@{ speechResourceId = $id; speechKey = 'old-test-value' })) -eq 'aad') 'An explicit resource ID must select keyless auth over an old key.'

Assert-Throws { Resolve-GatewaySpeechSettings ([pscustomobject]@{ speechAuth = 'other' }) $resource } 'speechAuth'
Assert-Throws { Resolve-GatewaySpeechSettings ([pscustomobject]@{ liveNotes = 'maybe' }) $resource } 'liveNotes'
Assert-Throws { Resolve-GatewaySpeechSettings $aad $null } 'accessible resource'
Assert-Throws { Resolve-GatewaySpeechSettings ([pscustomobject]@{ speechRegion = 'westus' }) $resource } 'does not match'
Assert-Throws { Resolve-GatewaySpeechSettings ([pscustomobject]@{ speechResourceId = "$id-other" }) $resource } 'does not match'

foreach ($change in @(
  @{ Field = 'id'; Value = 'not-a-resource-id'; Error = 'full Azure' },
  @{ Field = 'kind'; Value = 'OpenAI'; Error = 'does not support Speech' },
  @{ Field = 'location'; Value = ''; Error = 'location could not be resolved' },
  @{ Field = 'properties'; Value = [pscustomobject]@{}; Error = 'custom subdomain' }
)) {
  $badResource = $resource | Select-Object *
  $badResource.($change.Field) = $change.Value
  Assert-Throws { Resolve-GatewaySpeechSettings ([pscustomobject]@{ speechAuth = 'aad' }) $badResource } $change.Error
}
$disabled = Resolve-GatewaySpeechSettings ([pscustomobject]@{ liveNotes = 'off' }) $null
Assert (-not $disabled.Enabled) 'Disabling live notes must not require a Speech resource or key.'
Assert (@(Get-GatewaySpeechEnvironment $disabled $false) -contains 'SPEECH_KEY=') 'Disabled live notes must not retain an old key reference.'

foreach ($script in 'speech-auth.ps1', 'deploy-gateway.ps1', 'init-gateway-params.ps1', 'verify-gateway.ps1') {
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot "..\$script"), [ref]$tokens, [ref]$errors) | Out-Null
  Assert (-not $errors) "$script has PowerShell parse errors: $errors"
}

function Test-RequiredVoiceParameters {
  function az.cmd { throw 'Unexpected Azure CLI call before required-parameter validation.' }
  $file = [IO.Path]::GetTempFileName()
  try {
    foreach ($missing in 'extractorAgentName', 'dataverseUrl') {
      $parameters = [ordered]@{
        subscriptionId = 'test'; resourceGroup = 'test'; appName = 'test'; acrName = 'test'
        environmentName = 'test'; acsResourceName = 'test'
        foundryProjectEndpoint = 'https://test.services.ai.azure.com/api/projects/test'
        voiceAgentName = 'test'; extractorAgentName = 'test'; dataverseUrl = 'https://test.crm.dynamics.com'
      }
      $parameters.Remove($missing)
      [IO.File]::WriteAllText($file, ($parameters | ConvertTo-Json))
      Assert-Throws { & (Join-Path $PSScriptRoot '..\deploy-gateway.ps1') -ParametersFile $file -Plan } "Parameter '$missing' is missing"
    }
  } finally { Remove-Item -LiteralPath $file }
}
Test-RequiredVoiceParameters
Write-Host 'Speech deployment configuration regressions passed.'
