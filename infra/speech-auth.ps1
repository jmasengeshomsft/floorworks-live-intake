function Get-GatewaySpeechAuth($Parameters) {
  $mode = ([string]$Parameters.speechAuth).Trim().ToLowerInvariant()
  if (-not $mode) {
    $mode = if ($Parameters.speechResourceId -or -not $Parameters.speechKey) { 'aad' } else { 'key' }
  }
  if ($mode -notin 'aad', 'key') { throw "speechAuth must be 'aad' or 'key'." }
  return $mode
}

function Resolve-GatewaySpeechSettings($Parameters, $Resource) {
  $mode = Get-GatewaySpeechAuth $Parameters
  $liveNotes = ([string]$Parameters.liveNotes).Trim().ToLowerInvariant()
  if ($liveNotes -and $liveNotes -notin 'on', 'off') { throw "liveNotes must be 'on' or 'off'." }
  $enabled = $liveNotes -ne 'off'
  $region = ([string]$Parameters.speechRegion).Trim().ToLowerInvariant()
  $resourceId = ''
  if ($mode -eq 'aad' -and $enabled) {
    if (-not $Resource -or -not $Resource.id) {
      throw "Speech AAD authentication needs an accessible resource. Set speechResourceId or use an accessible Foundry account."
    }
    if ($Resource.id -notmatch '^/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+$') {
      throw "speechResourceId must be a full Azure Cognitive Services account resource ID."
    }
    if ($Parameters.speechResourceId -and $Parameters.speechResourceId.TrimEnd('/') -ne $Resource.id.TrimEnd('/')) {
      throw "The resolved Speech resource does not match speechResourceId."
    }
    if ($Resource.kind -notin 'AIServices', 'SpeechServices', 'CognitiveServices') {
      throw "The selected resource kind '$($Resource.kind)' does not support Speech."
    }
    if (-not $Resource.properties.customSubDomainName) {
      throw "Speech AAD authentication requires a custom subdomain. Configure it explicitly on the Speech resource, then rerun; deployment will not make that irreversible change."
    }
    $actualRegion = ([string]$Resource.location).Trim().ToLowerInvariant()
    if (-not $actualRegion) { throw "The Speech resource location could not be resolved." }
    if ($region -and $region -ne $actualRegion) {
      throw "speechRegion '$region' does not match the Speech resource location '$actualRegion'."
    }
    $region = $actualRegion
    $resourceId = $Resource.id
  }
  if (-not $region) { $region = 'eastus' }
  [pscustomobject]@{ Auth = $mode; Enabled = $enabled; ResourceId = $resourceId; Region = $region }
}

function Get-GatewaySpeechEnvironment($Settings, [bool]$HasKey) {
  if ($Settings.Auth -eq 'key' -and $Settings.Enabled -and -not $HasKey) {
    throw "Speech key authentication requires speechKey or an existing speech-key secret. Use speechAuth=aad for managed identity."
  }
  "SPEECH_AUTH=$($Settings.Auth)"
  "SPEECH_RESOURCE_ID=$($Settings.ResourceId)"
  "SPEECH_REGION=$($Settings.Region)"
  if ($Settings.Auth -eq 'key' -and $Settings.Enabled -and $HasKey) { 'SPEECH_KEY=secretref:speech-key' }
  else { 'SPEECH_KEY=' } # Explicitly replace any key reference left by an earlier revision.
}
