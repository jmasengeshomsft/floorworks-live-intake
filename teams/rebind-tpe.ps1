<#
 Teams-side binding: resource account <-> Entra bot app <-> ACS resource.
 Teams PowerShell sessions do not persist, so each action signs in (device code) and runs in one session.

 -Action Snapshot : read-only dump of the resource account, number assignment and ACS assignment
 -Action Rebind   : point the existing resource account at -BotAppId + the ACS resource, sync, verify
 Reads defaults from teams\.env (TenantId, AcsImmutableId, AcsHost, ResourceAccountUpn, PhoneNumber).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('Snapshot', 'Rebind')][string]$Action,
  [string]$BotAppId
)
$ErrorActionPreference = 'Stop'
$cfg = @{}
Get-Content (Join-Path $PSScriptRoot '.env') | Where-Object { $_ -match '^\w+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v }

Import-Module MicrosoftTeams
Connect-MicrosoftTeams -UseDeviceAuthentication -TenantId $cfg.TenantId | Out-Null

$acct = Get-CsOnlineApplicationInstance | Where-Object UserPrincipalName -eq $cfg.ResourceAccountUpn
if (-not $acct) { throw "Resource account $($cfg.ResourceAccountUpn) not found." }
$oid = $acct.ObjectId

if ($Action -eq 'Rebind') {
  if (-not $BotAppId) { throw '-BotAppId is required for Rebind.' }
  Set-CsOnlineApplicationInstance -Identity $oid -ApplicationId $BotAppId -AcsResourceId $cfg.AcsImmutableId | Out-Null
  Sync-CsOnlineApplicationInstance -ObjectId $oid -ApplicationId $BotAppId
  Start-Sleep 5
}

"--- Resource account ---"
Get-CsOnlineApplicationInstance -Identity $oid | Select-Object ObjectId, UserPrincipalName, DisplayName, ApplicationId, AcsResourceId, PhoneNumber | Format-List
"--- Number ---"
Get-CsPhoneNumberAssignment -TelephoneNumber $cfg.PhoneNumber | Select-Object TelephoneNumber, NumberType, PstnAssignmentStatus, AssignedPstnTargetId | Format-List
