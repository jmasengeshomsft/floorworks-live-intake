<#
 Teams-side TPE setup. Needs a Teams admin (device-code sign-in); everything runs in ONE session
 because Teams PowerShell sessions do not persist between invocations.

 - Creates the resource account bound to the bot app and the ACS resource
 - Registers it with ACS (access assignment)
 - Assigns the Teams Phone Resource Account license and a calling phone number
 - Enables Enterprise Voice for each rep and syncs the application instance

 Prereqs: infra/setup-tpe-azure.ps1 done; a Calling Plan / Operator Connect / Direct Routing number you own
 (a number type that supports voice applications). Reps need a Teams Phone license (MCOEV).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$TenantId,
  [Parameter(Mandatory)][string]$BotAppId,
  [Parameter(Mandatory)][string]$AcsImmutableId,
  [Parameter(Mandatory)][string]$AcsHost,               # <name>.<geo>.communication.azure.com
  [Parameter(Mandatory)][string]$ResourceAccountUpn,    # e.g. intake@contoso.com
  [Parameter(Mandatory)][string]$PhoneNumber,           # E.164, e.g. +15551234567
  [Parameter(Mandatory)][string[]]$RepObjectIds,
  [string]$DisplayName = 'FloorWorks Live Intake',
  [string]$UsageLocation = 'US'
)
$ErrorActionPreference = 'Stop'
Import-Module MicrosoftTeams
Connect-MicrosoftTeams -UseDeviceAuthentication -TenantId $TenantId | Out-Null

$acct = Get-CsOnlineApplicationInstance | Where-Object UserPrincipalName -eq $ResourceAccountUpn
if (-not $acct) {
  New-CsOnlineApplicationInstance -UserPrincipalName $ResourceAccountUpn -ApplicationId $BotAppId -DisplayName $DisplayName | Out-Null
  Start-Sleep 30
  $acct = Get-CsOnlineApplicationInstance | Where-Object UserPrincipalName -eq $ResourceAccountUpn
}
$oid = $acct.ObjectId
Set-CsOnlineApplicationInstance -Identity $oid -ApplicationId $BotAppId -AcsResourceId $AcsImmutableId | Out-Null

$tok = az account get-access-token --resource https://communication.azure.com --query accessToken -o tsv
Invoke-RestMethod -Method Put -ContentType 'application/json' -Body '{"principalType":"teamsResourceAccount"}' `
  -Headers @{ Authorization = "Bearer $tok" } `
  -Uri "https://$AcsHost/access/teamsExtension/tenants/$TenantId/assignments/$oid`?api-version=2025-06-30" | Out-Null

# License (Teams Phone Resource Account) needs a usage location first.
$sku = az rest --method get --url 'https://graph.microsoft.com/v1.0/subscribedSkus' --query "value[?skuPartNumber=='PHONESYSTEM_VIRTUALUSER'].skuId | [0]" -o tsv
if (-not $sku) { throw 'No PHONESYSTEM_VIRTUALUSER (Teams Phone Resource Account) license in the tenant.' }
$f = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($f, (@{ usageLocation = $UsageLocation } | ConvertTo-Json -Compress))
az rest --method patch --url "https://graph.microsoft.com/v1.0/users/$oid" --body "@$f" -o none
[IO.File]::WriteAllText($f, (@{ addLicenses = @(@{ skuId = $sku }); removeLicenses = @() } | ConvertTo-Json -Depth 4 -Compress))
az rest --method post --url "https://graph.microsoft.com/v1.0/users/$oid/assignLicense" --body "@$f" -o none
Remove-Item $f

# Number: read its current type, free it if assigned elsewhere, then assign it to the resource account.
$cur = Get-CsPhoneNumberAssignment -TelephoneNumber $PhoneNumber
if (-not $cur) { throw "Number $PhoneNumber not found in this tenant." }
if ($cur.AssignedPstnTargetId -and $cur.AssignedPstnTargetId -ne $oid) {
  Remove-CsPhoneNumberAssignment -Identity $cur.AssignedPstnTargetId -PhoneNumber $PhoneNumber -PhoneNumberType $cur.NumberType
  Start-Sleep 10
}
Set-CsPhoneNumberAssignment -Identity $oid -PhoneNumber $PhoneNumber -PhoneNumberType $cur.NumberType

foreach ($rep in $RepObjectIds) { Set-CsPhoneNumberAssignment -Identity $rep -EnterpriseVoiceEnabled $true }

Sync-CsOnlineApplicationInstance -ObjectId $oid -ApplicationId $BotAppId
Get-CsPhoneNumberAssignment -TelephoneNumber $PhoneNumber | Format-List
Write-Host "Done. Resource account object id: $oid"
