<#
 Teams-side readiness check for a FloorWorks phone line. READ-ONLY: changes nothing.
 Run by a Teams admin in the customer tenant (needs Teams Administrator or
 Teams Telephony Administrator; Graph User.Read.All only if -CheckLicense is used).

 Verifies, for one line:
   1. Tenant context
   2. Resource account exists, ApplicationId == the bot's Microsoft App ID,
      and it is linked to the expected ACS resource (AcsResourceId)
   3. The phone number is assigned to that resource account, status is healthy,
      and number type / capability are sane (Direct Routing SBC/policy if used)
   4. No other resource account shares the bot App ID
   5. The resource account is licensed (skip with -SkipLicense)
   6. The Object ID to place in the gateway/Foundry config (28:orgid:<ObjectId>)
 Not checkable from the Teams side: server consent on the ACS resource (reported as INFO).

 Prerequisites (once):
   Install-Module MicrosoftTeams -Scope CurrentUser
   Install-Module Microsoft.Graph.Users -Scope CurrentUser   # licence check

 Usage:
   Fill in the placeholders in the $Lines table below (or pass parameters), then:
   .\verify-teams-side.ps1 -Line 1 -UseDeviceAuthentication
   .\verify-teams-side.ps1 -ResourceAccountUpn ra@contoso.onmicrosoft.com `
       -PhoneNumber +15551234567 -ExpectedBotAppId <bot-microsoft-app-id>

 Output: PASS/WARN/FAIL lines, a summary, and a JSON report (-ReportPath) that can
 be sent back to the FloorWorks team. The report holds IDs and phone numbers, no secrets.
#>
param(
  [ValidateSet(1, 2)][int]$Line = 2,
  [string]$ResourceAccountUpn,
  [string]$PhoneNumber,
  [string]$ExpectedBotAppId,
  [string]$ExpectedAcsResourceId,
  [string]$TenantId,
  [switch]$SkipLicense,
  [switch]$UseDeviceAuthentication,
  [string]$ReportPath = ".\teams-side-report-$(Get-Date -Format yyyyMMdd-HHmmss).json"
)

# ---- EDIT THESE: defaults used when a parameter is not passed. Pick the line with -Line 1|2 ----
$Lines = @{
  1 = @{ Upn = '<resource-account-upn-line-1>'; Phone = '<+1XXXXXXXXXX>'; BotAppId = '<bot-microsoft-app-id-line-1>'; AcsResourceId = '<acs-immutable-resource-id-line-1>' }
  2 = @{ Upn = '<resource-account-upn-line-2>'; Phone = '<+1XXXXXXXXXX>'; BotAppId = '<bot-microsoft-app-id-line-2>'; AcsResourceId = '<acs-immutable-resource-id-line-2>' }
}
$DefaultTenantId = '<your-tenant-id>'
# -------------------------------------------------------------------------------------------------
if (-not $ResourceAccountUpn) { $ResourceAccountUpn = $Lines[$Line].Upn }
if (-not $PhoneNumber)        { $PhoneNumber        = $Lines[$Line].Phone }
if (-not $ExpectedBotAppId)   { $ExpectedBotAppId   = $Lines[$Line].BotAppId }
if (-not $ExpectedAcsResourceId) { $ExpectedAcsResourceId = $Lines[$Line].AcsResourceId }
if (-not $TenantId)           { $TenantId           = $DefaultTenantId }

$ErrorActionPreference = 'Stop'
$results = New-Object System.Collections.Generic.List[object]
$fail = 0; $warn = 0

function Add-Result($status, $name, $detail = '', $fix = '') {
  $results.Add([pscustomobject]@{ Status = $status; Check = $name; Detail = $detail; Fix = $fix })
  $color = @{ PASS = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; INFO = 'Cyan' }[$status]
  Write-Host ("[{0}] {1} {2}" -f $status, $name, $detail) -ForegroundColor $color
  if ($fix -and $status -in @('FAIL', 'WARN')) { Write-Host "       Fix: $fix" -ForegroundColor DarkYellow }
  if ($status -eq 'FAIL') { $script:fail++ }
  if ($status -eq 'WARN') { $script:warn++ }
}

function Get-Prop($obj, $name) {
  if ($null -ne $obj -and $obj.PSObject.Properties[$name]) { return $obj.$name }
  return $null
}

# Normalize to E.164 with a leading +
$phone = ($PhoneNumber -replace '[^\d+]', '')
if (-not $phone.StartsWith('+')) { $phone = "+$phone" }

Write-Host 'Loading MicrosoftTeams module...' -ForegroundColor Gray
Import-Module MicrosoftTeams -ErrorAction Stop
$tenant = $null
try { $tenant = Get-CsTenant -ErrorAction Stop; Write-Host 'Reusing existing Teams session.' -ForegroundColor Gray } catch { }
if (-not $tenant) {
  $connectArgs = @{}
  if ($TenantId) { $connectArgs.TenantId = $TenantId }
  if ($UseDeviceAuthentication) {
    $connectArgs.UseDeviceAuthentication = $true
    Write-Host 'Connecting with device code: follow the URL and code printed below...' -ForegroundColor Yellow
  } else {
    Write-Host 'Connecting to Teams (a sign-in window may open; check behind other windows)...' -ForegroundColor Yellow
  }
  try {
    Connect-MicrosoftTeams @connectArgs | Out-Null
  } catch {
    if ($UseDeviceAuthentication) { throw }
    Write-Host "Interactive sign-in failed ($($_.Exception.Message)). Retrying with device code..." -ForegroundColor Yellow
    $connectArgs.UseDeviceAuthentication = $true
    Connect-MicrosoftTeams @connectArgs | Out-Null
  }
  $tenant = Get-CsTenant
}
Write-Host 'Connected. Running checks...' -ForegroundColor Gray
Add-Result INFO 'Tenant' "$($tenant.DisplayName) ($($tenant.TenantId))"
if ($TenantId -and $tenant.TenantId -ne $TenantId) {
  Add-Result FAIL 'Connected tenant matches -TenantId' "(got $($tenant.TenantId))"
}

# 1. Resource account
$ra = $null
try { $ra = Get-CsOnlineApplicationInstance -Identity $ResourceAccountUpn } catch { }
if (-not $ra) {
  Add-Result FAIL 'Resource account exists' "($ResourceAccountUpn not found)"
} else {
  Add-Result PASS 'Resource account exists' "($($ra.DisplayName))"
  Add-Result INFO 'Resource account ObjectId' $ra.ObjectId
  Add-Result INFO 'Resource account ApplicationId' $ra.ApplicationId

  if ($ra.ApplicationId -eq $ExpectedBotAppId) {
    Add-Result PASS 'ApplicationId equals bot Microsoft App ID'
  } else {
    Add-Result FAIL 'ApplicationId equals bot Microsoft App ID' "(account=$($ra.ApplicationId) expected=$ExpectedBotAppId)" "Set-CsOnlineApplicationInstance -Identity $ResourceAccountUpn -ApplicationId $ExpectedBotAppId"
  }

  # ACS link: the property name differs by module version, so match any property containing 'Acs'
  $acsProps = @($ra.PSObject.Properties | Where-Object { $_.Name -match 'Acs' })
  $acsVal = $null
  foreach ($p in $acsProps) { if ($p.Value) { $acsVal = "$($p.Value)"; break } }
  if (-not $acsVal) {
    Add-Result FAIL 'Resource account is linked to an ACS resource' ($(if ($acsProps) { "($($acsProps.Name -join ',') is empty)" } else { '(no AcsResourceId property returned; update the MicrosoftTeams module and retry)' })) "Set-CsOnlineApplicationInstance -Identity $ResourceAccountUpn -ApplicationId $ExpectedBotAppId -AcsResourceId $ExpectedAcsResourceId; then Sync-CsOnlineApplicationInstance -ObjectId $($ra.ObjectId) -ApplicationId $ExpectedBotAppId"
  } elseif ($ExpectedAcsResourceId -and $acsVal -notmatch [regex]::Escape($ExpectedAcsResourceId)) {
    Add-Result FAIL 'ACS resource ID matches the expected resource' "(account=$acsVal expected=$ExpectedAcsResourceId)" "Set-CsOnlineApplicationInstance -Identity $ResourceAccountUpn -ApplicationId $ExpectedBotAppId -AcsResourceId $ExpectedAcsResourceId"
  } else {
    Add-Result PASS 'Resource account is linked to the expected ACS resource' "($acsVal)"
  }

  # Provisioning/sync state, if the module exposes one
  $syncProp = $ra.PSObject.Properties | Where-Object { $_.Name -match 'Provision|SyncState|Status' } | Select-Object -First 1
  if ($syncProp -and $syncProp.Value) { Add-Result INFO "Resource account $($syncProp.Name)" "$($syncProp.Value)" }

  $raPhone = Get-Prop $ra 'PhoneNumber'
  if ($raPhone) {
    $raPhoneNorm = ($raPhone -replace '^tel:', '' -replace '[^\d+]', '')
    if ($raPhoneNorm -eq $phone) { Add-Result PASS 'Resource account PhoneNumber matches' "($raPhone)" }
    else { Add-Result FAIL 'Resource account PhoneNumber matches' "(account=$raPhone expected=$phone)" }
  } else {
    Add-Result WARN 'Resource account PhoneNumber field is empty' '(assignment may still show in the number record below)'
  }
}

# 2. Phone number record
$num = $null
try { $num = Get-CsPhoneNumberAssignment -TelephoneNumber $phone } catch { }
if (-not $num) {
  Add-Result FAIL 'Phone number exists in this tenant' "($phone not found; wrong tenant, not acquired, or not ported yet)"
} else {
  $numType  = Get-Prop $num 'NumberType'
  $status   = Get-Prop $num 'PstnAssignmentStatus'
  $target   = Get-Prop $num 'AssignedPstnTargetId'
  $category = Get-Prop $num 'AssignmentCategory'
  $caps     = Get-Prop $num 'Capability'
  $activate = Get-Prop $num 'ActivationState'
  Add-Result INFO 'Number record' "type=$numType status=$status category=$category activation=$activate capability=$($caps -join ',')"

  if ($status -and $status -ne 'UserAssigned') {
    Add-Result WARN 'Number assignment status' "(got '$status'; usually 'UserAssigned' once assigned to a resource account)"
  }
  if (-not $target) {
    Add-Result FAIL 'Number is assigned to a target' '(AssignedPstnTargetId is empty = unassigned)' "Set-CsPhoneNumberAssignment -Identity $ResourceAccountUpn -PhoneNumber $phone -PhoneNumberType $(if ($numType) { $numType } else { 'CallingPlan' })"
  } elseif ($ra -and $target -ne $ra.ObjectId) {
    Add-Result FAIL 'Number is assigned to THIS resource account' "(target=$target resourceAccount=$($ra.ObjectId))" 'Remove-CsPhoneNumberAssignment from the other target, then assign to the resource account'
  } else {
    Add-Result PASS 'Number is assigned to this resource account'
  }
  if ($activate -and $activate -notin @('Activated', 'Succeeded')) {
    Add-Result WARN 'Number activation state' "(got '$activate'; a Pending or in-flight state can fail inbound calls)"
  }
  if ($caps -and ($caps -notcontains 'VoiceApplicationAssignment')) {
    Add-Result WARN 'Number supports voice application assignment' "(capabilities: $($caps -join ','))"
  }
  if ($numType -and $numType -notin @('CallingPlan', 'OperatorConnect', 'DirectRouting')) {
    Add-Result WARN 'Number type' "(unexpected: $numType)"
  }
  if ($numType -eq 'CallingPlan' -and $category -and $category -notmatch 'Service|ApplicationInstance|Voice') {
    Add-Result WARN 'Calling Plan number is a Service-type number' "(category=$category; resource accounts need a Service number, not a User number)" 'Acquire a Service (toll or toll-free) number in Teams admin center > Voice > Phone numbers'
  }
  if ($numType -eq 'DirectRouting') {
    try {
      $gw = @(Get-CsOnlinePstnGateway -ErrorAction Stop)
      $pol = @(Get-CsOnlineVoiceRoutingPolicy -ErrorAction Stop)
      if ($gw.Count -and $pol.Count) { Add-Result PASS 'Direct Routing SBC and voice routing policy exist' "($($gw.Count) SBC, $($pol.Count) policies)" }
      else { Add-Result FAIL 'Direct Routing SBC and voice routing policy exist' "(SBC=$($gw.Count) policies=$($pol.Count))" 'Configure an SBC and voice routing policy' }
    } catch { Add-Result WARN 'Direct Routing check skipped' "($($_.Exception.Message))" }
  }
}

# 2b. Other accounts must not share this line's bot app or ACS resource
try {
  $others = @(Get-CsOnlineApplicationInstance -ErrorAction Stop | Where-Object { $ra -and $_.ObjectId -ne $ra.ObjectId -and $_.ApplicationId -eq $ExpectedBotAppId })
  if ($others.Count) {
    Add-Result WARN 'Another resource account uses the same bot App ID' "($($others.UserPrincipalName -join ', '))" 'Each line should use its own bot App ID and resource account'
  } else {
    Add-Result PASS 'No other resource account shares this bot App ID'
  }
} catch { Add-Result INFO 'Duplicate bot App ID check skipped' "($($_.Exception.Message))" }

# 3. License (default on; -SkipLicense to skip)
if (-not $SkipLicense) {
  try {
    Import-Module Microsoft.Graph.Users -ErrorAction Stop
    Connect-MgGraph -Scopes 'User.Read.All' -NoWelcome | Out-Null
    $lic = Get-MgUserLicenseDetail -UserId $ResourceAccountUpn
    $skus = $lic.SkuPartNumber
    Add-Result INFO 'Licenses' ($skus -join ', ')
    if ($skus -match 'PHONESYSTEM_VIRTUALUSER|MCOEV_VIRTUALUSER') {
      Add-Result PASS 'Teams Phone Resource Account license present'
    } else {
      Add-Result FAIL 'Teams Phone Resource Account license present' '(add the Microsoft Teams Phone Resource Account license)' 'Microsoft 365 admin center > Users > resource account > Licenses'
    }
  } catch {
    Add-Result WARN 'License check skipped' "($($_.Exception.Message))" 'Check the license manually, or install Microsoft.Graph.Users and allow User.Read.All'
  }
}

# 3b. Server consent cannot be read from the Teams side
Add-Result INFO 'Server consent (not checked here)' "The ACS resource owner must have authorised resource account $($ra.ObjectId) via the Teams Extension access-assignments API (principalType teamsResourceAccount). Without it ACS never receives the call."

# 4. Value for the FloorWorks config
if ($ra) {
  Add-Result INFO 'Config value' "Teams resource account id to use in routing = 28:orgid:$($ra.ObjectId)"
}

Write-Host ''
Write-Host "Summary: $fail fail, $warn warn" -ForegroundColor $(if ($fail) { 'Red' } elseif ($warn) { 'Yellow' } else { 'Green' })
if (-not $fail) {
  Write-Host 'Teams-side checks passed. A pass does not prove calls arrive: confirm server consent on the ACS resource, then check Event Grid and the bot. If calls still fail, test a Teams-to-number call and check carrier/port status.' -ForegroundColor Green
}

[pscustomobject]@{
  GeneratedUtc = (Get-Date).ToUniversalTime().ToString('o')
  Tenant       = $tenant.TenantId
  PhoneNumber  = $phone
  Results      = $results
} | ConvertTo-Json -Depth 5 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "Report written to $ReportPath"

if ($fail) { exit 1 }
