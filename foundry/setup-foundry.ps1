<#
 Wires Foundry to Dataverse.
  1. Grants the Foundry PROJECT managed identity access to Dataverse (application user + role + allowed MCP client).
  2. Creates a project connection that uses the project managed identity (no per-user sign-in, so it works unattended).
  3. Publishes a new version of the prompt agent pointing at that connection.
 Needs `az login` as someone who can administer the Dataverse environment and the Foundry project.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$SubscriptionId,
  [Parameter(Mandatory)][string]$ResourceGroup,
  [Parameter(Mandatory)][string]$FoundryAccount,       # e.g. my-foundry
  [Parameter(Mandatory)][string]$FoundryProject,       # e.g. firstProject
  [Parameter(Mandatory)][string]$OrgUrl,               # e.g. https://myorg.crm.dynamics.com
  [string]$ConnectionName = 'dataverse-mfg-mi',
  # Broad on purpose for a demo; replace with a least-privilege custom role for production.
  [string]$DataverseRole = 'System Administrator'
)
$ErrorActionPreference = 'Stop'
$OrgUrl = $OrgUrl.TrimEnd('/')
$projectArm = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccount/projects/$FoundryProject"
$endpoint = "https://$FoundryAccount.services.ai.azure.com/api/projects/$FoundryProject"

function TmpJson($obj) { $f = [IO.Path]::GetTempFileName(); [IO.File]::WriteAllText($f, ($obj | ConvertTo-Json -Depth 12 -Compress)); $f }

# 1. Project managed identity -> Dataverse
$proj = az rest --method get --url "https://management.azure.com$projectArm`?api-version=2025-04-01-preview" | ConvertFrom-Json
$miObjectId = $proj.identity.principalId
if (-not $miObjectId) { throw 'Project has no system-assigned identity. Enable it first.' }
$miAppId = az ad sp show --id $miObjectId --query appId -o tsv
Write-Host "Project MI: objectId=$miObjectId appId=$miAppId"

$h = @{ Authorization = "Bearer $(az account get-access-token --resource $OrgUrl --query accessToken -o tsv)"; Accept = 'application/json'; 'Content-Type' = 'application/json' }
$api = "$OrgUrl/api/data/v9.2"

$existing = (Invoke-RestMethod "$api/systemusers?`$filter=applicationid eq $miAppId&`$select=systemuserid" -Headers $h).value
if ($existing) { $userId = $existing[0].systemuserid; Write-Host 'Application user exists.' }
else {
  $bu = (Invoke-RestMethod "$api/businessunits?`$filter=parentbusinessunitid eq null&`$select=businessunitid" -Headers $h).value[0].businessunitid
  $body = @{ applicationid = $miAppId; 'businessunitid@odata.bind' = "/businessunits($bu)" } | ConvertTo-Json
  $r = Invoke-WebRequest "$api/systemusers" -Method Post -Headers $h -Body $body
  $userId = ($r.Headers['OData-EntityId'] -replace '.*\(|\).*', '')
  Write-Host 'Application user created.'
}
$roleId = (Invoke-RestMethod "$api/roles?`$filter=name eq '$DataverseRole'&`$select=roleid" -Headers $h).value[0].roleid
try {
  Invoke-RestMethod "$api/systemusers($userId)/systemuserroles_association/`$ref" -Method Post -Headers $h `
    -Body (@{ '@odata.id' = "$api/roles($roleId)" } | ConvertTo-Json) | Out-Null
  Write-Host "Role '$DataverseRole' assigned."
} catch { Write-Host "Role assignment: $($_.ErrorDetails.Message)" }

# The Dataverse MCP server rejects clients that are not on the allow list.
$allowed = (Invoke-RestMethod "$api/allowedmcpclients?`$filter=applicationid eq $miAppId&`$select=allowedmcpclientid" -Headers $h).value
if (-not $allowed) {
  $b = @{ name = 'Foundry Project MI'; uniquename = 'fw_foundryprojectmi'; applicationid = $miAppId; isenabled = $true } | ConvertTo-Json
  Invoke-RestMethod "$api/allowedmcpclients" -Method Post -Headers $h -Body $b | Out-Null
  Write-Host 'Allowed MCP client added.'
}

# 2. Project connection using the project managed identity
$conn = @{ properties = @{
    category = 'RemoteTool'; target = "$OrgUrl/api/mcp"; authType = 'ProjectManagedIdentity'
    audience = $OrgUrl; isSharedToAll = $true; metadata = @{ type = 'custom_MCP' } } }
$f = TmpJson $conn
az rest --method put --url "https://management.azure.com$projectArm/connections/$ConnectionName`?api-version=2025-04-01-preview" --body "@$f" -o none
Remove-Item $f
Write-Host "Connection '$ConnectionName' created."
