<#
 READ-ONLY preflight for the telephony runbook (docs\15). Run it BEFORE CreateApp/Rebind/Assign/Subscribe to prove the
 Teams-side prerequisites are in place: licenses, phone number, resource account, reps. Changes nothing.

 Reads defaults from teams\.env (TenantId, AcsImmutableId, AcsHost, ResourceAccountUpn, PhoneNumber, RepObjectIds,
 optional BotAppId). Signs in once (device code) as a Teams admin; also needs `az login` in the same tenant.

 Each check prints PASS / WARN / FAIL with a fix hint. Exit code 1 if any FAIL, so it can gate the runbook.
   FAIL = the runbook will break.  WARN = fixable by the runbook itself, or worth a look.
 -AcsName/-ResourceGroup are optional; when given, the ACS immutable id and host are verified against Azure.
#>
[CmdletBinding()]
param(
  [string]$AcsName,
  [string]$ResourceGroup,
  [string]$BotAppId
)
$ErrorActionPreference = 'Stop'
$cfg = @{}
Get-Content (Join-Path $PSScriptRoot '.env') | Where-Object { $_ -match '^\w+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v.Trim() }
if (-not $BotAppId) { $BotAppId = $cfg.BotAppId }
$reps = @($cfg.RepObjectIds -split '[,;\s]+' | Where-Object { $_ -match '^[0-9a-fA-F-]{36}$' })
$script:results = @()
function Check($area, $name, $status, $detail = '', $fix = '') {
  $script:results += [pscustomobject]@{ Area = $area; Check = $name; Status = $status; Detail = $detail; Fix = $fix }
  $c = @{ PASS = 'Green'; WARN = 'Yellow'; FAIL = 'Red' }[$status]
  Write-Host ("[{0}] {1} / {2}" -f $status, $area, $name) -ForegroundColor $c -NoNewline
  if ($detail) { Write-Host " - $detail" -NoNewline }
  Write-Host ''
  if ($fix -and $status -ne 'PASS') { Write-Host "       fix: $fix" -ForegroundColor DarkGray }
}
function Graph($path) { az rest --method get --url "https://graph.microsoft.com/v1.0/$path" -o json 2>$null | ConvertFrom-Json }

# --- 0. Config + tooling ---
foreach ($k in 'TenantId', 'AcsImmutableId', 'AcsHost', 'ResourceAccountUpn', 'PhoneNumber') {
  if ($cfg[$k] -and $cfg[$k] -notmatch '[<>]') { Check 'Config' $k 'PASS' } else { Check 'Config' $k 'FAIL' 'missing or placeholder in teams\.env' "Set $k in teams\.env (where to find it: docs\15-telephony-binding-runbook.md, 'What you need and where to find it')" }
}
if ($cfg.PhoneNumber -and $cfg.PhoneNumber -notmatch '^\+\d{8,15}$') { Check 'Config' 'PhoneNumber format' 'FAIL' $cfg.PhoneNumber 'Use E.164, e.g. +16307351758' }
if (-not $reps.Count) { Check 'Config' 'RepObjectIds' 'WARN' 'none listed' 'List at least one rep Entra object id' }

$mod = Get-Module -ListAvailable MicrosoftTeams | Sort-Object Version -Descending | Select-Object -First 1
if (-not $mod) { Check 'Tooling' 'MicrosoftTeams module' 'FAIL' 'not installed' 'Install-Module MicrosoftTeams -Scope CurrentUser' }
elseif ($mod.Version -lt [version]'6.0.0') { Check 'Tooling' 'MicrosoftTeams module' 'WARN' "v$($mod.Version)" 'Update-Module MicrosoftTeams (need a version with -AcsResourceId)' }
else { Check 'Tooling' 'MicrosoftTeams module' 'PASS' "v$($mod.Version)" }

$azTenant = az account show --query tenantId -o tsv 2>$null
if (-not $azTenant) { Check 'Tooling' 'az login' 'FAIL' 'not signed in' "az login --tenant $($cfg.TenantId)" }
elseif ($azTenant -ne $cfg.TenantId) { Check 'Tooling' 'az tenant' 'FAIL' "az is in $azTenant" "az login --tenant $($cfg.TenantId)" }
else { Check 'Tooling' 'az tenant' 'PASS' }
if (($script:results | Where-Object Status -eq 'FAIL' | Where-Object Area -in 'Tooling', 'Config') ) {
  Write-Host "`nFix the above before continuing." -ForegroundColor Red; exit 1
}

# --- 1. Tenant licenses ---
$skus = (Graph 'subscribedSkus').value
$va = $skus | Where-Object skuPartNumber -eq 'PHONESYSTEM_VIRTUALUSER'
if (-not $va) { Check 'License' 'Teams Phone Resource Account (PHONESYSTEM_VIRTUALUSER)' 'FAIL' 'SKU not in tenant' 'Get the free Teams Phone Resource Account license in the M365 admin center > Marketplace' }
else {
  $free = $va.prepaidUnits.enabled - $va.consumedUnits
  if ($free -ge 1) { Check 'License' 'Resource Account SKU' 'PASS' "$free free of $($va.prepaidUnits.enabled)" }
  else { Check 'License' 'Resource Account SKU' 'WARN' 'no free units (fine if the resource account already holds one)' 'Add units, or reuse the existing resource account' }
}
$ev = $skus | Where-Object { $_.servicePlans.servicePlanName -contains 'MCOEV' -and $_.capabilityStatus -eq 'Enabled' }
if ($ev) { Check 'License' 'Teams Phone (MCOEV) in tenant' 'PASS' (($ev.skuPartNumber) -join ', ') }
else { Check 'License' 'Teams Phone (MCOEV) in tenant' 'FAIL' 'no SKU with the MCOEV service plan' 'Buy Teams Phone Standard (or an E5 / add-on SKU that includes it) for the reps' }

# --- 2. Teams session ---
Import-Module MicrosoftTeams
Connect-MicrosoftTeams -UseDeviceAuthentication -TenantId $cfg.TenantId | Out-Null
try { $null = Get-CsTenant; Check 'Teams' 'Admin access' 'PASS' 'Get-CsTenant ok' }
catch { Check 'Teams' 'Admin access' 'FAIL' $_.Exception.Message 'Sign in as Teams Administrator (or Global Admin)' }

# --- 3. Phone number ---
$n = $null
try { $n = Get-CsPhoneNumberAssignment -TelephoneNumber $cfg.PhoneNumber } catch { }
$acct = Get-CsOnlineApplicationInstance | Where-Object UserPrincipalName -eq $cfg.ResourceAccountUpn
$oid = $acct.ObjectId
if (-not $n) { Check 'Number' 'Exists in tenant' 'FAIL' $cfg.PhoneNumber 'Acquire/port the number first (Teams admin center > Voice > Phone numbers), or fix PhoneNumber in teams\.env' }
else {
  Check 'Number' 'Exists in tenant' 'PASS' "$($n.TelephoneNumber) type=$($n.NumberType)"
  if ($n.NumberType -notin 'CallingPlan', 'OperatorConnect', 'DirectRouting') { Check 'Number' 'Type' 'FAIL' $n.NumberType 'Use a Calling Plan, Operator Connect or Direct Routing number' }
  $cap = @($n.Capability)
  if ($cap -and ($cap -notcontains 'VoiceApplicationAssignment')) { Check 'Number' 'Supports voice applications' 'FAIL' ("capability: " + ($cap -join ',')) 'Use a Service (toll/toll-free) number, or a user number reassigned as a service number; user-only numbers cannot go on a resource account' }
  elseif ($cap) { Check 'Number' 'Supports voice applications' 'PASS' }
  else { Check 'Number' 'Supports voice applications' 'WARN' 'capability not reported' 'Confirm the number is a service number in the Teams admin center' }
  if ($n.ActivationState -and $n.ActivationState -ne 'Activated') { Check 'Number' 'Activation' 'FAIL' $n.ActivationState 'Wait for activation / complete the porting order' }
  if ($n.PstnAssignmentStatus -eq 'Unassigned' -or -not $n.AssignedPstnTargetId) { Check 'Number' 'Assignment' 'PASS' 'unassigned (Step 4 will assign it)' }
  elseif ($oid -and $n.AssignedPstnTargetId -eq $oid) { Check 'Number' 'Assignment' 'PASS' 'already on the resource account' }
  else {
    $who = (Graph "users/$($n.AssignedPstnTargetId)?`$select=userPrincipalName").userPrincipalName
    Check 'Number' 'Assignment' 'WARN' "assigned to $who" 'setup-teams.ps1 frees it automatically; otherwise Remove-CsPhoneNumberAssignment first'
  }
}

# --- 4. Resource account ---
if (-not $acct) { Check 'ResourceAccount' 'Exists' 'WARN' "$($cfg.ResourceAccountUpn) not found" 'Runbook Step 2 creates it' }
else {
  Check 'ResourceAccount' 'Exists' 'PASS' $oid
  if ($BotAppId -and $acct.ApplicationId -ne $BotAppId) { Check 'ResourceAccount' 'ApplicationId' 'WARN' "is $($acct.ApplicationId), expected $BotAppId" 'Step 3 (Rebind) points it at the new app' }
  elseif ($acct.ApplicationId) { Check 'ResourceAccount' 'ApplicationId' 'PASS' $acct.ApplicationId }
  if ($acct.AcsResourceId -eq $cfg.AcsImmutableId) { Check 'ResourceAccount' 'AcsResourceId' 'PASS' }
  else { Check 'ResourceAccount' 'AcsResourceId' 'WARN' "is '$($acct.AcsResourceId)'" 'Step 3 (Rebind) sets it' }
  $u = Graph "users/$oid`?`$select=usageLocation,accountEnabled"
  if ($u.usageLocation) { Check 'ResourceAccount' 'Usage location' 'PASS' $u.usageLocation } else { Check 'ResourceAccount' 'Usage location' 'WARN' 'not set' 'Set before licensing (setup-teams.ps1 does this)' }
  $lic = (Graph "users/$oid/licenseDetails").value.skuPartNumber
  if ($lic -contains 'PHONESYSTEM_VIRTUALUSER') { Check 'ResourceAccount' 'License' 'PASS' } else { Check 'ResourceAccount' 'License' 'FAIL' 'no PHONESYSTEM_VIRTUALUSER' 'Assign the Teams Phone Resource Account license (number assignment fails without it)' }
}

# --- 5. Reps ---
foreach ($r in $reps) {
  $u = Graph "users/$r`?`$select=userPrincipalName,usageLocation,accountEnabled"
  if (-not $u) { Check 'Rep' $r 'FAIL' 'not found in Entra' 'Check the object id (not the app/client id)'; continue }
  $nm = $u.userPrincipalName
  if (-not $u.accountEnabled) { Check 'Rep' $nm 'FAIL' 'account disabled' }
  $plans = (Graph "users/$r/licenseDetails").value.servicePlans.servicePlanName
  if ($plans -contains 'MCOEV') { Check 'Rep' "$nm license" 'PASS' 'Teams Phone (MCOEV)' } else { Check 'Rep' "$nm license" 'FAIL' 'no Teams Phone (MCOEV)' 'Assign Teams Phone Standard; EV cannot be enabled without it' }
  try {
    $cu = Get-CsOnlineUser -Identity $r
    if ($cu.EnterpriseVoiceEnabled) { Check 'Rep' "$nm Enterprise Voice" 'PASS' } else { Check 'Rep' "$nm Enterprise Voice" 'WARN' 'off' 'setup-teams.ps1 enables it once the license is in place' }
  } catch { Check 'Rep' "$nm Teams user" 'WARN' 'not provisioned in Teams yet (new license can take up to ~1 h)' 'Wait, then rerun' }
}

# --- 6. ACS (optional) ---
if ($AcsName -and $ResourceGroup) {
  $sub = az account show --query id -o tsv
  $a = az rest --method get --url "https://management.azure.com/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.Communication/communicationServices/${AcsName}?api-version=2023-04-01" -o json 2>$null | ConvertFrom-Json
  if (-not $a) { Check 'ACS' 'Resource' 'FAIL' "$AcsName not found in $ResourceGroup" 'Check -AcsName/-ResourceGroup' }
  else {
    if ($a.properties.immutableResourceId -eq $cfg.AcsImmutableId) { Check 'ACS' 'ImmutableId matches .env' 'PASS' } else { Check 'ACS' 'ImmutableId matches .env' 'FAIL' "Azure says $($a.properties.immutableResourceId)" 'Update AcsImmutableId in teams\.env' }
    if ($a.properties.hostName -eq $cfg.AcsHost) { Check 'ACS' 'Host matches .env' 'PASS' } else { Check 'ACS' 'Host matches .env' 'FAIL' "Azure says $($a.properties.hostName)" 'Update AcsHost in teams\.env' }
  }
}
$sp = az ad sp show --id 1fd5118e-2576-4263-8130-9503064c837a --query id -o tsv 2>$null
if ($sp) { Check 'ACS' 'First-party app in tenant' 'PASS' } else { Check 'ACS' 'First-party app in tenant' 'WARN' 'missing' 'az ad sp create --id 1fd5118e-2576-4263-8130-9503064c837a (Step 5 prerequisite)' }

# --- Summary ---
$f = @($results | Where-Object Status -eq 'FAIL').Count; $w = @($results | Where-Object Status -eq 'WARN').Count
Write-Host ("`n{0} checks: {1} PASS, {2} WARN, {3} FAIL" -f $results.Count, ($results.Count - $f - $w), $w, $f)
if ($f) { Write-Host 'NOT READY: fix the FAIL items, then rerun.' -ForegroundColor Red; exit 1 }
Write-Host 'READY: safe to start the runbook (WARN items are handled by the runbook steps).' -ForegroundColor Green
