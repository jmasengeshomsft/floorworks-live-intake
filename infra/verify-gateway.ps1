<#
.SYNOPSIS
  Read-only check of an intake-gateway Container App and the Event Grid subscriptions that feed it.
  Answers: "Event Grid shows calls but the container is never invoked. Why?"

.EXAMPLE
  .\verify-gateway.ps1 -ResourceGroup myrg -AppName my-gateway
#>
param(
  [Parameter(Mandatory)][string]$ResourceGroup,
  [Parameter(Mandatory)][string]$AppName,
  [string]$Subscription,
  [switch]$SkipLogChecks
)
$ErrorActionPreference = 'Continue'
$WarningPreference = 'SilentlyContinue'; $env:PYTHONWARNINGS = 'ignore'; $env:AZURE_CORE_ONLY_SHOW_ERRORS = 'true'; $env:AZURE_CORE_COLLECT_TELEMETRY = 'false'
function az {
  $ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $o = & az.cmd @args 2>&1
    $code = $LASTEXITCODE
    foreach ($l in $o) {
      if ($l -is [System.Management.Automation.ErrorRecord]) {
        $m = "$l"
        if ($m.Trim() -and $m -notmatch 'SyntaxWarning|^System\.Management\.Automation\.RemoteException|^\s*pattern=|invalid escape|DeprecationWarning|^\s*[/|\\-]?\s*Running \.\.|behavior of this command has been altered') { [Console]::Error.WriteLine($m) }
      } else { $l }
    }
  } finally { $ErrorActionPreference = $ea }
  $global:LASTEXITCODE = $code
}
if ($Subscription) { az account set --subscription $Subscription | Out-Null }

$script:fail = 0; $script:warn = 0
function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red; $script:fail++ }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:warn++ }
function J($t)    { if ($t) { $t | ConvertFrom-Json } }
. (Join-Path $PSScriptRoot 'speech-auth.ps1')
. (Join-Path $PSScriptRoot 'after-hours.ps1')

Write-Host "`n== Container App" -ForegroundColor Cyan
$app = J (az containerapp show -g $ResourceGroup -n $AppName -o json 2>$null | Out-String)
if (-not $app) { Bad "App '$AppName' not found in '$ResourceGroup'."; exit 1 }
$fqdn = $app.properties.configuration.ingress.fqdn
$c    = $app.properties.template.containers[0]
Ok "revision $($app.properties.latestRevisionName), image $($c.image)"
if ($app.properties.configuration.ingress.external) { Ok "ingress external: https://$fqdn" } else { Bad "ingress is internal: Event Grid cannot reach it." }
if ($app.properties.configuration.ingress.targetPort -ne 8080) { Warn "targetPort is $($app.properties.configuration.ingress.targetPort); the image listens on 8080 (PORT)." }
$min = $app.properties.template.scale.minReplicas
$max = $app.properties.template.scale.maxReplicas
if ($min -eq 1 -and $max -eq 1) { Ok "single-replica scale: min=1, max=1" }
else { Bad "minReplicas=$min, maxReplicas=$max; the gateway requires min=1 and max=1. Scale-out splits process-local call state." }
if ($app.properties.runningStatus -and $app.properties.runningStatus -ne 'Running') { Bad "runningStatus=$($app.properties.runningStatus)" }

Write-Host "`n== Environment variables" -ForegroundColor Cyan
$secrets = @{}
foreach ($s in (J (az containerapp secret list -g $ResourceGroup -n $AppName --show-values -o json 2>$null | Out-String))) { $secrets[$s.name] = $s.value }
$env = @{}
foreach ($e in $c.env) {
  $v = if ($e.secretRef) { $secrets[$e.secretRef] } else { $e.value }
  $env[$e.name] = "$v"
}
foreach ($n in 'PUBLIC_BASE_URL','FOUNDRY_PROJECT_ENDPOINT','VOICE_AGENT_NAME','EXTRACTOR_AGENT_NAME','DATAVERSE_URL','ACS_CONNECTION_STRING','API_KEYS','GATEWAY_SECRET','EVENTGRID_SECRET','ACS_RESOURCE_ID') {
  if ([string]::IsNullOrWhiteSpace($env[$n])) { Bad "$n is missing or empty (the gateway refuses to start, or rejects every call)." } else { Ok "$n set" }
}
if (-not $env['DATAVERSE_SCHEMA'] -and -not $env['DATAVERSE_SCHEMA_FILE']) { Bad "DATAVERSE_SCHEMA or DATAVERSE_SCHEMA_FILE is required outside LOCAL_DEV." }
if ($env['PUBLIC_BASE_URL'] -and $env['PUBLIC_BASE_URL'].TrimEnd('/') -ne "https://$fqdn") {
  Bad "PUBLIC_BASE_URL=$($env['PUBLIC_BASE_URL']) but the app FQDN is https://$fqdn (callbacks and /ws go to the wrong host)."
}
$jwt = if ($env['ACS_JWT_MODE']) { $env['ACS_JWT_MODE'] } else { '(default enforce)' }
if ($env['ACS_JWT_MODE'] -and $env['ACS_JWT_MODE'] -ne 'enforce') { Bad "ACS_JWT_MODE=$jwt; it must be enforce outside LOCAL_DEV." } else { Ok "ACS_JWT_MODE=$jwt" }
if ($env['LOCAL_DEV'] -eq '1') { Bad "LOCAL_DEV=1 in a deployed app." }
if ($env['LINE_NUMBERS']) {
  Warn "LINE_NUMBERS=$($env['LINE_NUMBERS']). Calls to other identities are dropped silently (HTTP 200). Teams resource accounts arrive as 28:orgid:<oid>, not +E164."
}
if ($env['ACS_CONNECTION_STRING'] -match 'endpoint=https://([^./]+)\.') { Ok "ACS in connection string: $($Matches[1])" }

Write-Host "`n== Office hours" -ForegroundColor Cyan
if ($env.ContainsKey('AFTER_HOUR')) {
  $message = "AFTER_HOUR is ignored. Use AFTER_HOURS (with the final S) and remove the misspelled variable."
  if ($env.ContainsKey('AFTER_HOURS')) { Warn $message } else { Bad $message }
}
$config = $null
if ($env['API_KEYS']) {
  try {
    $key = ($env['API_KEYS'] -split ',')[0].Trim()
    $config = Invoke-RestMethod "https://$fqdn/v1/config" -Headers @{ 'x-api-key' = $key } -TimeoutSec 30
  } catch { Bad "Cannot read runtime configuration: $($_.Exception.Message)" }
}
try {
  $officeHours = Resolve-GatewayAfterHoursSettings ([pscustomobject]@{
    afterHours = $(if ($env.ContainsKey('AFTER_HOURS')) { $env['AFTER_HOURS'] } else { 'off' })
    serviceTz = $(if ($env.ContainsKey('SERVICE_TZ')) { $env['SERVICE_TZ'] } else { 'America/Chicago' })
    serviceDays = $(if ($env.ContainsKey('SERVICE_DAYS')) { $env['SERVICE_DAYS'] } else { 'Mon-Fri' })
    serviceHours = $(if ($env.ContainsKey('SERVICE_HOURS')) { $env['SERVICE_HOURS'] } else { '07:00-18:00' })
  })
  Ok "after hours=$($officeHours.Mode); office open $($officeHours.Days) $($officeHours.Hours) ($($officeHours.TimeZone))"
  if ($config) {
    $expected = @{
      afterHoursMode = $officeHours.Mode; serviceTz = $officeHours.TimeZone
      serviceDays = $officeHours.Days; serviceHours = $officeHours.Hours
    }
    foreach ($field in $expected.Keys) {
      if ($null -eq $config.PSObject.Properties[$field]) {
        Bad "Running gateway does not report $field. Rebuild/deploy the current image."
      } elseif ([string]$config.$field -cne [string]$expected[$field]) {
        Bad "Runtime $field=$($config.$field) differs from deployed $field=$($expected[$field]). Check the serving revision/traffic."
      }
    }
    Ok "running gateway afterHoursNow=$($config.afterHoursNow); existing calls keep their starting mode"
  }
} catch { Bad "Office hours: $($_.Exception.Message)" }

Write-Host "`n== Speech (live notes)" -ForegroundColor Cyan
$speechParameters = [pscustomobject]@{
  liveNotes = $env['LIVE_NOTES']; speechAuth = $env['SPEECH_AUTH']; speechKey = $env['SPEECH_KEY']
  speechResourceId = $env['SPEECH_RESOURCE_ID']; speechRegion = $env['SPEECH_REGION']
}
try {
  $speechResource = $null
  if ((Get-GatewaySpeechAuth $speechParameters) -eq 'aad' -and $env['LIVE_NOTES'] -ne 'off') {
    if (-not $env['SPEECH_RESOURCE_ID']) { throw "SPEECH_RESOURCE_ID is missing for Speech AAD authentication." }
    $speechResource = J (az resource show --ids $env['SPEECH_RESOURCE_ID'] -o json 2>$null | Out-String)
  }
  $speech = Resolve-GatewaySpeechSettings $speechParameters $speechResource
  if (-not $speech.Enabled) { Ok "live notes disabled" }
  elseif ($speech.Auth -eq 'aad') {
    if (-not $app.identity.principalId) { throw "Speech AAD deployment has no system-assigned gateway identity." }
    if ($env['SPEECH_KEY']) { Warn "SPEECH_KEY is still present but ignored by SPEECH_AUTH=aad; redeploy to clear its environment reference." }
    $roles = J (az role assignment list --scope $speech.ResourceId --include-inherited --fill-principal-name false `
      --query "[?principalId=='$($app.identity.principalId)' && (roleDefinitionName=='Cognitive Services Speech User' || roleDefinitionName=='Cognitive Services Speech Contributor')]" -o json 2>$null | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "Could not read the gateway's Speech role assignments." }
    if (-not $roles) { throw "Gateway identity has no Cognitive Services Speech User/Contributor role on the Speech resource." }
    Ok "Speech AAD resource, region, custom subdomain and gateway role assignment verified"
  } elseif (-not $env['SPEECH_KEY']) { throw "Speech key authentication is enabled but SPEECH_KEY is missing." }
  else { Ok "Speech key authentication configured" }
  if ($speech.Enabled -and $config) {
    if ($config.liveNotes -ne 'azure-speech') { throw "Live notes requested but the running gateway reports liveNotes=$($config.liveNotes)." }
    Ok "running gateway has the Speech transcriber enabled; a real call is still required to prove transcription"
  }
} catch { Bad "Speech: $($_.Exception.Message)" }

Write-Host "`n== Health" -ForegroundColor Cyan
try {
  $h = Invoke-RestMethod "https://$fqdn/healthz" -TimeoutSec 30
  if ($h.status -eq 'ok') { Ok "/healthz ok" } else { Bad "/healthz status=$($h.status): $($h.errors -join '; ')" }
} catch { Bad "/healthz unreachable: $($_.Exception.Message)" }

Write-Host "`n== Event Grid subscriptions that target this app" -ForegroundColor Cyan
$found = 0
foreach ($t in (J (az eventgrid system-topic list -g $ResourceGroup -o json 2>$null | Out-String))) {
  foreach ($sub in (J (az eventgrid system-topic event-subscription list -g $ResourceGroup --system-topic-name $t.name -o json 2>$null | Out-String))) {
    $url = if ($sub.destination.endpointBaseUrl) { $sub.destination.endpointBaseUrl } else { $sub.destination.endpointUrl }
    if ($url -notmatch [regex]::Escape($fqdn)) { continue }
    $found++
    Write-Host "  - $($t.name) / $($sub.name)"
    if ($sub.provisioningState -eq 'Succeeded') { Ok "provisioningState Succeeded" } else { Bad "provisioningState=$($sub.provisioningState)" }
    if ($sub.filter.includedEventTypes -contains 'Microsoft.Communication.IncomingCall') { Ok "filters IncomingCall" } else { Bad "does not include Microsoft.Communication.IncomingCall" }
    if ($url -notmatch '/api/incoming') { Bad "endpoint path is not /api/incoming: $($url -replace '\?.*','')" }
    $full = (J (az eventgrid system-topic event-subscription show -g $ResourceGroup --system-topic-name $t.name -n $sub.name --include-full-endpoint-url -o json 2>$null | Out-String)).destination.endpointUrl
    if ($full -match '[?&]s=([^&]+)') {
      if ([uri]::UnescapeDataString($Matches[1]) -eq $env['EVENTGRID_SECRET']) { Ok "?s= matches EVENTGRID_SECRET" } else { Bad "?s= does NOT match the app's EVENTGRID_SECRET: every delivery gets 401." }
    } else { Bad "webhook URL has no ?s=<EVENTGRID_SECRET>: every delivery gets 401." }
    if ($sub.deadLetterDestination) { Ok "dead-letter configured" } else { Warn "no dead-letter destination: failed deliveries vanish after retries." }
  }
}
if (-not $found) { Bad "No Event Grid system-topic subscription in '$ResourceGroup' points at $fqdn. Check the ACS resource's own topic / resource group." }

if (-not $SkipLogChecks) {
Write-Host "`n== Recent gateway log signals (last 200 lines)" -ForegroundColor Cyan
$log = az containerapp logs show -g $ResourceGroup -n $AppName --tail 200 --format text 2>$null | Out-String
foreach ($p in @(
  @('incoming rejected: bad event grid secret','Event Grid reached the app with a wrong secret'),
  @('incoming call ignored','call dropped by LINE_NUMBERS'),
  @('config error','startup validation error'),
  @('Traceback','unhandled exception'))) {
  if ($log -match [regex]::Escape($p[0])) { Bad "log contains '$($p[0])': $($p[1])" }
}
if ($log -match 'event received type=Microsoft.Communication.IncomingCall') { Ok "IncomingCall events have reached the app" } else { Warn "no IncomingCall event in the last 200 lines" }
}
Write-Host ("`nResult: {0} failure(s), {1} warning(s)" -f $script:fail, $script:warn) -ForegroundColor $(if ($script:fail) { 'Red' } else { 'Green' })
exit ([int]($script:fail -gt 0))
