<#
 Teams-side readiness check for a FloorWorks phone line. READ-ONLY: changes nothing.
 Run by a Teams admin in the customer tenant (needs Teams Administrator or
 Teams Telephony Administrator; Graph User.Read.All only if -CheckLicense is used).

 Verifies, for one line:
   1. Tenant context
   2. Resource account exists and its ApplicationId == the bot's Microsoft App ID
   3. The phone number is assigned to that resource account, status is healthy,
      and number type / capability are sane
   4. The resource account is licensed (optional, -CheckLicense)
   5. The Object ID to place in the gateway/Foundry config (28:orgid:<ObjectId>)

 Prerequisites (once):
   Install-Module MicrosoftTeams -Scope CurrentUser
   Install-Module Microsoft.Graph.Users -Scope CurrentUser   # only for -CheckLicense

 Usage:
   .\verify-teams-side.ps1 -ResourceAccountUpn tpeintake2@contoso.onmicrosoft.com `
       -PhoneNumber +16307933525 -ExpectedBotAppId <bot-microsoft-app-id>

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
  1 = @{ Upn = 'tpeintake@inyarwanda.com';  Phone = '+16307351758'; BotAppId = 'bb45aa6d-fb10-488e-b057-fd1d7fb38167'; AcsResourceId = 'b80e9c7f-bbf3-409a-a023-5b0769c97a29' }
  # 2 = @{ Upn = 'tpeintake2@inyarwanda.com'; Phone = '+16307933525'; BotAppId = 'dd0d94ff-32f0-430d-babc-d64ab88e9490'; AcsResourceId = '593ac208-e933-4af1-973e-8542a8a7377f' }
}
$DefaultTenantId = '2a6b0179-1024-4bb4-b600-02610eaaed75'
# -------------------------------------------------------------------------------------------------
if (-not $ResourceAccountUpn) { $ResourceAccountUpn = $Lines[$Line].Upn }
if (-not $PhoneNumber)        { $PhoneNumber        = $Lines[$Line].Phone }
if (-not $ExpectedBotAppId)   { $ExpectedBotAppId   = $Lines[$Line].BotAppId }
if (-not $ExpectedAcsResourceId) { $ExpectedAcsResourceId = $Lines[$Line].AcsResourceId }
if (-not $TenantId)           { $TenantId           = $DefaultTenantId }

$ErrorActionPreference = 'Stop'
$results = New-Object System.Collections.Generic.List[object]
$fail = 0; $warn = 0

function Add-Result($status, $name, $detail = '') {
  $results.Add([pscustomobject]@{ Status = $status; Check = $name; Detail = $detail })
  $color = @{ PASS = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; INFO = 'Cyan' }[$status]
  Write-Host ("[{0}] {1} {2}" -f $status, $name, $detail) -ForegroundColor $color
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
    Add-Result FAIL 'ApplicationId equals bot Microsoft App ID' "(account=$($ra.ApplicationId) expected=$ExpectedBotAppId)"
  }

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
    Add-Result FAIL 'Number is assigned to a target' '(AssignedPstnTargetId is empty = unassigned)'
  } elseif ($ra -and $target -ne $ra.ObjectId) {
    Add-Result FAIL 'Number is assigned to THIS resource account' "(target=$target resourceAccount=$($ra.ObjectId))"
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
}

# 3. License (optional)
if ($CheckLicense) {
  try {
    Import-Module Microsoft.Graph.Users -ErrorAction Stop
    Connect-MgGraph -Scopes 'User.Read.All' -NoWelcome | Out-Null
    $lic = Get-MgUserLicenseDetail -UserId $ResourceAccountUpn
    $skus = $lic.SkuPartNumber
    Add-Result INFO 'Licenses' ($skus -join ', ')
    if ($skus -match 'PHONESYSTEM_VIRTUALUSER|MCOEV_VIRTUALUSER') {
      Add-Result PASS 'Teams Phone Resource Account license present'
    } else {
      Add-Result FAIL 'Teams Phone Resource Account license present' '(add the Microsoft Teams Phone Resource Account license)'
    }
  } catch {
    Add-Result WARN 'License check skipped' "($($_.Exception.Message))"
  }
}

# 4. Value for the FloorWorks config
if ($ra) {
  Add-Result INFO 'Config value' "Teams resource account id to use in routing = 28:orgid:$($ra.ObjectId)"
}

Write-Host ''
Write-Host "Summary: $fail fail, $warn warn" -ForegroundColor $(if ($fail) { 'Red' } elseif ($warn) { 'Yellow' } else { 'Green' })
if (-not $fail) {
  Write-Host 'Teams-side wiring looks correct. If calls still fail, test a Teams-to-number call and check carrier/port status.' -ForegroundColor Green
}

[pscustomobject]@{
  GeneratedUtc = (Get-Date).ToUniversalTime().ToString('o')
  Tenant       = $tenant.TenantId
  PhoneNumber  = $phone
  Results      = $results
} | ConvertTo-Json -Depth 5 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "Report written to $ReportPath"

if ($fail) { exit 1 }
