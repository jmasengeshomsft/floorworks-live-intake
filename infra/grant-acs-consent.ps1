<#
.SYNOPSIS
    Checks (and optionally grants) ACS server consent for a Teams resource account.
.DESCRIPTION
    Calls the ACS teamsExtension assignment API:
      GET  = verify (200 with the assignment = consent exists, 404 = missing)
      PUT  = grant (idempotent; body {"principalType":"teamsResourceAccount"})
    Default is check-only. Pass -Grant to PUT when the GET returns 404.
    Requires: az login (account with rights on the ACS resource).
.EXAMPLE
    .\grant-acs-consent.ps1 -AcsEndpoint acs-xxx.unitedstates.communication.azure.com `
        -TenantId <tenant-guid> -ResourceAccountObjectId <oid>
.EXAMPLE
    .\grant-acs-consent.ps1 -AcsEndpoint ... -TenantId ... -ResourceAccountObjectId ... -Grant
#>
[CmdletBinding()]
param(
    [string]$AcsEndpoint = 'acs-floorworks-line2.unitedstates.communication.azure.com',  # host only, no https://
    [string]$TenantId = '2a6b0179-1024-4bb4-b600-02610eaaed75',
    [string]$ResourceAccountObjectId = 'd388f63c-3f7c-48da-91e8-71e0495c5c56',
    [switch]$Grant
)

$ErrorActionPreference = 'Stop'
$guid = '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
if ($TenantId -notmatch $guid)                { throw "TenantId is not a GUID: $TenantId" }
if ($ResourceAccountObjectId -notmatch $guid) { throw "ResourceAccountObjectId is not a GUID: $ResourceAccountObjectId" }
$AcsEndpoint = $AcsEndpoint -replace '^https?://', '' -replace '/.*$', ''
if ($AcsEndpoint -notmatch '\.communication\.azure\.com$') { throw "AcsEndpoint should look like <name>.<geo>.communication.azure.com: $AcsEndpoint" }

$url = "https://$AcsEndpoint/access/teamsExtension/tenants/$TenantId/assignments/$ResourceAccountObjectId" + "?api-version=2025-06-30"
$tok = az account get-access-token --resource https://communication.azure.com --query accessToken -o tsv
if (-not $tok) { throw 'Could not get a token. Run az login first.' }
$headers = @{ Authorization = "Bearer $tok" }

function Get-Consent {
    try {
        return Invoke-RestMethod -Method Get -Uri $url -Headers $headers
    } catch {
        $code = [int]$_.Exception.Response.StatusCode
        if ($code -eq 404) { return $null }
        throw "GET failed ($code): $($_.ErrorDetails.Message)"
    }
}

$existing = Get-Consent
if ($existing) {
    Write-Host "OK   consent exists: $($existing | ConvertTo-Json -Compress)" -ForegroundColor Green
    return
}

Write-Host 'FAIL no consent (404) for this resource account on this ACS resource.' -ForegroundColor Red
if (-not $Grant) {
    Write-Host 'Re-run with -Grant to create it.'
    return
}

try {
    Invoke-RestMethod -Method Put -Uri $url -Headers $headers -ContentType 'application/json' `
        -Body '{"principalType":"teamsResourceAccount"}' | Out-Null
} catch {
    throw "PUT failed ($([int]$_.Exception.Response.StatusCode)): $($_.ErrorDetails.Message)"
}
$after = Get-Consent
if ($after) { Write-Host "OK   granted: $($after | ConvertTo-Json -Compress)" -ForegroundColor Green }
else        { Write-Host 'FAIL PUT succeeded but GET still returns 404.' -ForegroundColor Red }
