$ErrorActionPreference = 'Stop'
$provisioner = Join-Path $PSScriptRoot '..\provision-dataverse.ps1'

class MissingTestMetadata : System.Exception {
  [object]$Response
  MissingTestMetadata() : base('Offline metadata not found') {
    $this.Response = [pscustomobject]@{ StatusCode = [System.Net.HttpStatusCode]::NotFound }
  }
}

function Assert($Condition, $Message) {
  if (-not $Condition) { throw $Message }
}
function Assert-Throws($Action, $Pattern) {
  $message = $null
  try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
  Assert ($message -and $message -match $Pattern) "Expected '$Pattern'; got '$message'."
}
function az {
  if (($args[0..1] -join ' ') -ne 'account get-access-token') { throw 'Unexpected Azure CLI call in offline provisioning test.' }
  'offline-test-token'
}
function Start-Sleep { param($Seconds); $script:state.Sleeps++ }
function Invoke-WebRequest { throw 'Business-data writes are forbidden in schema-only provisioning.' }
function Invoke-RestMethod {
  param($Method = 'GET', [uri]$Uri, $Headers, $ContentType, $Body)
  $root = 'https://dataverse.invalid/api/data/v9.2/'
  if (-not $Uri.AbsoluteUri.StartsWith($root)) { throw "Unexpected network target: $Uri" }
  $path = $Uri.AbsoluteUri.Substring($root.Length)
  $method = $Method.ToUpperInvariant()
  $bodyObject = if ($null -ne $Body) { [Text.Encoding]::UTF8.GetString([byte[]]$Body) | ConvertFrom-Json } else { $null }
  if ($method -eq 'GET' -and $path.StartsWith('publishers?')) { return @{ value = @(@{ publisherid = 'test-publisher' }) } }
  if ($method -eq 'GET' -and $path.StartsWith('solutions?')) { return @{ value = @(@{ solutionid = 'test-solution' }) } }
  if ($method -eq 'POST' -and $path -eq 'EntityDefinitions') {
    $table = @{ Attributes = @{}; Key = $null; KeyReads = 0 }
    foreach ($attribute in $bodyObject.Attributes) { $table.Attributes[$attribute.SchemaName.ToLowerInvariant()] = $attribute }
    $script:state.Tables[$bodyObject.SchemaName] = $table
  } elseif ($path -match "^EntityDefinitions\(LogicalName='([^']+)'\)/Attributes\(LogicalName='([^']+)'\)$") {
    $table = $script:state.Tables[$Matches[1]]
    if ($method -ne 'GET') { throw "Unexpected method: $method $path" }
    if (-not $table -or -not $table.Attributes.ContainsKey($Matches[2])) { throw [MissingTestMetadata]::new() }
    return $table.Attributes[$Matches[2]]
  } elseif ($method -eq 'POST' -and $path -match "^EntityDefinitions\(LogicalName='([^']+)'\)/Attributes$") {
    $script:state.Tables[$Matches[1]].Attributes[$bodyObject.SchemaName.ToLowerInvariant()] = $bodyObject
  } elseif ($path -match "^EntityDefinitions\(LogicalName='([^']+)'\)/Keys(?:\?.*)?$") {
    $table = $script:state.Tables[$Matches[1]]
    if ($method -eq 'POST') { $table.Key = $bodyObject }
    elseif ($method -eq 'GET') {
      if (-not $table.Key) { return @{ value = @() } }
      $table.KeyReads++
      $status = if ($script:state.KeyStatus -eq 'Delayed') {
        if ($table.KeyReads -lt 3) { 'Pending' } else { 'Active' }
      } else { $script:state.KeyStatus }
      return @{ value = @(@{ KeyAttributes = @($table.Key.KeyAttributes); EntityKeyIndexStatus = $status }) }
    } else { throw "Unexpected method: $method $path" }
  } elseif ($method -eq 'GET' -and $path -match "^EntityDefinitions\(LogicalName='([^']+)'\)(?:\?.*)?$") {
    $logical = $Matches[1]
    if ($logical -eq 'incident' -and $script:state.Incident) { return @{ LogicalName = 'incident' } }
    if (-not $script:state.Tables.ContainsKey($logical)) { throw [MissingTestMetadata]::new() }
    return @{ IsOptimisticConcurrencyEnabled = ($logical -ne "$($script:state.Prefix)_calljournal" -or $script:state.JournalConcurrency) }
  } elseif ($method -eq 'GET' -and $path -match "^RelationshipDefinitions\(SchemaName='([^']+)'\)$") {
    if (-not $script:state.Relationships.Contains($Matches[1])) { throw [MissingTestMetadata]::new() }
    return @{ SchemaName = $Matches[1] }
  } elseif ($method -eq 'POST' -and $path -eq 'RelationshipDefinitions') {
    $script:state.Relationships.Add($bodyObject.SchemaName) | Out-Null
  } else { throw "Unexpected Dataverse request (no real service is called): $method $path" }
  $script:state.Writes.Add("$method $path") | Out-Null
}

function Reset-State($Prefix = 'bdm', $Incident = $false, $KeyStatus = 'Active') {
  $script:state = @{
    Prefix = $Prefix; Incident = $Incident; KeyStatus = $KeyStatus; JournalConcurrency = $true; Sleeps = 0
    Tables = @{}; Relationships = [System.Collections.Generic.HashSet[string]]::new()
    Writes = [System.Collections.Generic.List[string]]::new()
  }
}
function Invoke-Provision([bool]$Standard = $false) {
  . $provisioner -OrgUrl 'https://dataverse.invalid' -Prefix $script:state.Prefix -StandardTables:$Standard 6>$null
}
function Assert-JournalReady {
  $prefix = $script:state.Prefix
  $draft = $script:state.Tables["${prefix}_servicerequestdraft"]
  $journal = $script:state.Tables["${prefix}_calljournal"]
  Assert ($draft -and $journal) 'Normal provisioning must include both draft and journal tables.'
  foreach ($column in 'callid', 'checkpointjson', 'version', 'complete') {
    Assert ($journal.Attributes.ContainsKey("${prefix}_$column")) "Journal is missing ${prefix}_$column."
  }
  Assert ($draft.Attributes.ContainsKey("${prefix}_transcriptversion")) 'Existing drafts need the transcript-version column.'
  Assert ($journal.Attributes["${prefix}_checkpointjson"].MaxLength -eq 1000000) 'The journal must accept the gateway checkpoint payload size.'
  Assert ($draft.Key.KeyAttributes.Count -eq 1 -and $draft.Key.KeyAttributes[0] -eq "${prefix}_callid") 'Draft uniqueness must be scoped to the call ID.'
}

foreach ($profile in @(
  @{ Standard = $false; Incident = $false; Prefix = 'bdm' },
  @{ Standard = $true; Incident = $true; Prefix = 'bdm' },
  @{ Standard = $true; Incident = $false; Prefix = 'bdm' },
  @{ Standard = $false; Incident = $false; Prefix = 'custom' }
)) {
  Reset-State -Prefix $profile.Prefix -Incident $profile.Incident
  Invoke-Provision -Standard $profile.Standard
  Assert-JournalReady
  $script:state.Writes.Clear()
  Invoke-Provision -Standard $profile.Standard
  Assert ($script:state.Writes.Count -eq 0) 'Reprovisioning a ready environment must not write metadata again.'

  $prefix = $profile.Prefix
  $script:state.Tables.Remove("${prefix}_calljournal")
  $script:state.Tables["${prefix}_servicerequestdraft"].Attributes.Remove("${prefix}_transcriptversion")
  $script:state.Tables["${prefix}_servicerequestdraft"].Key = $null
  Invoke-Provision -Standard $profile.Standard
  Assert-JournalReady
  Assert ($script:state.Writes -contains "POST EntityDefinitions(LogicalName='${prefix}_servicerequestdraft')/Attributes") 'Upgrade must repair the missing draft column.'
}

Reset-State -KeyStatus 'Delayed'
Invoke-Provision
Assert ($script:state.Sleeps -gt 0) 'Provisioning must wait for an asynchronous uniqueness index to become Active.'
Reset-State -KeyStatus 'Failed'
Assert-Throws { Invoke-Provision } 'Reconcile duplicate draft call IDs'
Reset-State -KeyStatus 'Pending'
Assert-Throws { Invoke-Provision } 'not Active yet'
Reset-State
$script:state.JournalConcurrency = $false
Assert-Throws { Invoke-Provision } 'must support optimistic concurrency'
Write-Host 'Journal provisioning regressions passed (fresh, upgrade, rerun and failure paths; no live requests).'
