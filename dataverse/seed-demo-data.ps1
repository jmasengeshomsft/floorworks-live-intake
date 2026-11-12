<#
 Standalone demo-data seeding for the standard setup (dataverse\config.json). Seeds 7 accounts (FloorWorks, with Joseph Masengesho as contact),
 6 equipment items owned by accounts and 6 tickets linked to account and equipment. Idempotent. The shipped container contains no data.
#>
[CmdletBinding()]param([Parameter(Mandatory)][string]$OrgUrl)
& (Join-Path $PSScriptRoot 'provision-dataverse.ps1') -OrgUrl $OrgUrl -SeedDemoData
