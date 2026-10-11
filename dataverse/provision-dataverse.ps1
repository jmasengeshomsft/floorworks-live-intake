<#
 Provisions the "Blue Dot Manufacturing Quality & Service" Dataverse solution:
 publisher, solution, 4 tables, lookups, and seed data.
 Idempotent: safe to re-run. Requires `az login` with Environment Maker (or better) on the org.
#>
[CmdletBinding()]param(
  [Parameter(Mandatory)][string]$OrgUrl,
  [string]$Prefix = 'bdm',
  [int]$OptionPrefix = 10573,
  [string]$Solution = 'BlueDotMfgQuality'
)
$ErrorActionPreference = 'Stop'
$api = "$OrgUrl/api/data/v9.2"
$script:tok = $null; $script:tokAt = [datetime]::MinValue

function Get-Headers($sol) {
  if (((Get-Date) - $script:tokAt).TotalMinutes -gt 40) {
    $script:tok = az account get-access-token --resource $OrgUrl --query accessToken -o tsv
    $script:tokAt = Get-Date
  }
  $h = @{ Authorization = "Bearer $script:tok"; 'OData-Version' = '4.0'; Accept = 'application/json' }
  if ($sol) { $h['MSCRM.SolutionUniqueName'] = $sol }
  $h
}
function ToBytes($o) { , [System.Text.Encoding]::UTF8.GetBytes(($o | ConvertTo-Json -Depth 20)) }
function Dv($method, $path, $body = $null, $sol = $null) {
  $p = @{ Method = $method; Uri = "$api/$path"; Headers = (Get-Headers $sol); ContentType = 'application/json; charset=utf-8' }
  if ($null -ne $body) { $p.Body = ToBytes $body }
  try { Invoke-RestMethod @p } catch { throw "$method $path -> $($_.ErrorDetails.Message)" }
}
function Exists($path) {
  try { Invoke-RestMethod -Uri "$api/$path" -Headers (Get-Headers $null) | Out-Null; $true }
  catch { if ($_.Exception.Response.StatusCode.value__ -eq 404) { $false } else { throw } }
}
function PostId($set, $body) {
  $res = Invoke-WebRequest -Method Post -Uri "$api/$set" -Headers (Get-Headers $Solution) -ContentType 'application/json; charset=utf-8' -Body (ToBytes $body)
  [regex]::Match(($res.Headers['OData-EntityId'] | Select-Object -First 1), '\(([^)]+)\)').Groups[1].Value
}
function L($t) { @{ '@odata.type' = 'Microsoft.Dynamics.CRM.Label'; LocalizedLabels = @(@{ '@odata.type' = 'Microsoft.Dynamics.CRM.LocalizedLabel'; Label = $t; LanguageCode = 1033 }) } }
function Req($lvl = 'None') { @{ Value = $lvl; CanBeChanged = $true; ManagedPropertyLogicalName = 'canmodifyrequirementlevelsettings' } }

# ---------- Publisher + solution ----------
$pub = (Dv GET "publishers?`$select=publisherid&`$filter=uniquename eq 'BlueDotMfg'").value
if (-not $pub) {
  Dv POST publishers @{ uniquename = 'BlueDotMfg'; friendlyname = 'Blue Dot Mfg'; customizationprefix = $Prefix; customizationoptionvalueprefix = $OptionPrefix } | Out-Null
  $pub = (Dv GET "publishers?`$select=publisherid&`$filter=uniquename eq 'BlueDotMfg'").value
  Write-Host 'Created publisher'
}
$pubId = $pub[0].publisherid
if (-not (Dv GET "solutions?`$select=solutionid&`$filter=uniquename eq '$Solution'").value) {
  Dv POST solutions @{ uniquename = $Solution; friendlyname = 'Blue Dot Manufacturing Quality & Service'; version = '1.0.0.0'
    'publisherid@odata.bind' = "/publishers($pubId)" } | Out-Null
  Write-Host 'Created solution'
}

# ---------- Attribute builders ----------
function StrAttr($n, $label, $max = 200) { @{ '@odata.type' = 'Microsoft.Dynamics.CRM.StringAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req); MaxLength = $max; FormatName = @{ Value = 'Text' } } }
function MemoAttr($n, $label, $max = 4000) { @{ '@odata.type' = 'Microsoft.Dynamics.CRM.MemoAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req); MaxLength = $max } }
function IntAttr($n, $label) { @{ '@odata.type' = 'Microsoft.Dynamics.CRM.IntegerAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req); MinValue = 0; MaxValue = 1000000; Format = 'None' } }
function DecAttr($n, $label) { @{ '@odata.type' = 'Microsoft.Dynamics.CRM.DecimalAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req); MinValue = 0; MaxValue = 100000000; Precision = 2 } }
function DateAttr($n, $label, $dateOnly = $false) {
  $fmt = 'DateAndTime'; $beh = 'UserLocal'; if ($dateOnly) { $fmt = 'DateOnly'; $beh = 'DateOnly' }
  @{ '@odata.type' = 'Microsoft.Dynamics.CRM.DateTimeAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req); Format = $fmt; DateTimeBehavior = @{ Value = $beh } }
}
function PickAttr($n, $label, [string[]]$options) {
  $i = 0
  $opts = foreach ($o in $options) { @{ Value = ($OptionPrefix * 10000 + $i++); Label = (L $o) } }
  @{ '@odata.type' = 'Microsoft.Dynamics.CRM.PicklistAttributeMetadata'; SchemaName = "${Prefix}_$n"; DisplayName = (L $label); RequiredLevel = (Req)
     OptionSet = @{ '@odata.type' = 'Microsoft.Dynamics.CRM.OptionSetMetadata'; IsGlobal = $false; OptionSetType = 'Picklist'; Options = @($opts) } }
}

function EnsureTable($name, $display, $plural, $desc, $nameLabel, $attrs) {
  $logical = "${Prefix}_$name"
  if (-not (Exists "EntityDefinitions(LogicalName='$logical')")) {
    Dv POST EntityDefinitions @{
      '@odata.type' = 'Microsoft.Dynamics.CRM.EntityMetadata'; SchemaName = $logical
      DisplayName = (L $display); DisplayCollectionName = (L $plural); Description = (L $desc)
      OwnershipType = 'UserOwned'; HasActivities = $false; HasNotes = $true
      Attributes = @(@{ '@odata.type' = 'Microsoft.Dynamics.CRM.StringAttributeMetadata'; SchemaName = "${Prefix}_name"; IsPrimaryName = $true
        RequiredLevel = (Req 'ApplicationRequired'); MaxLength = 200; FormatName = @{ Value = 'Text' }; DisplayName = (L $nameLabel) })
    } $Solution | Out-Null
    Write-Host "Created table $logical"
  }
  foreach ($a in $attrs) {
    $al = $a.SchemaName.ToLower()
    if (-not (Exists "EntityDefinitions(LogicalName='$logical')/Attributes(LogicalName='$al')")) {
      Dv POST "EntityDefinitions(LogicalName='$logical')/Attributes" $a $Solution | Out-Null
      Write-Host "  + $al"
    }
  }
}
function EnsureLookup($from, $to, $schema, $label) {
  $rel = "${Prefix}_${to}_${from}_$($schema.ToLower())"
  if (-not (Exists "RelationshipDefinitions(SchemaName='$rel')")) {
    Dv POST RelationshipDefinitions @{
      '@odata.type' = 'Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata'; SchemaName = $rel
      ReferencedEntity = "${Prefix}_$to"; ReferencingEntity = "${Prefix}_$from"
      Lookup = @{ AttributeType = 'Lookup'; AttributeTypeName = @{ Value = 'LookupType' }; SchemaName = "${Prefix}_$schema"; DisplayName = (L $label); RequiredLevel = (Req) }
      AssociatedMenuConfiguration = @{ Behavior = 'UseCollectionName'; Group = 'Details'; Order = 10000 }
      CascadeConfiguration = @{ Assign = 'NoCascade'; Delete = 'RemoveLink'; Merge = 'NoCascade'; Reparent = 'NoCascade'; Share = 'NoCascade'; Unshare = 'NoCascade' }
    } $Solution | Out-Null
    Write-Host "  lookup $rel"
  }
}

# ---------- Tables ----------
EnsureTable 'equipment' 'Equipment' 'Equipment' 'Manufacturing equipment / assets on the plant floor' 'Equipment Name' @(
  (StrAttr 'assetnumber' 'Asset Number' 50), (StrAttr 'plant' 'Plant' 100), (StrAttr 'manufacturer' 'Manufacturer' 100),
  (PickAttr 'status' 'Status' @('Operational', 'Degraded', 'Down', 'Under Maintenance')), (DateAttr 'installdate' 'Install Date' $true))

EnsureTable 'part' 'Part' 'Parts' 'Manufactured or purchased part' 'Part Name' @(
  (StrAttr 'partnumber' 'Part Number' 50), (StrAttr 'supplier' 'Supplier' 100),
  (PickAttr 'category' 'Category' @('Hydraulic', 'Electrical', 'Mechanical', 'Fastener', 'Consumable')), (DecAttr 'unitcost' 'Unit Cost (USD)'))

EnsureTable 'qualityalert' 'Quality Alert' 'Quality Alerts' 'Quality issue raised against a part and/or equipment' 'Alert Title' @(
  (PickAttr 'severity' 'Severity' @('Low', 'Medium', 'High', 'Critical')),
  (PickAttr 'status' 'Status' @('New', 'Investigating', 'Contained', 'Closed')),
  (MemoAttr 'description' 'Description'), (IntAttr 'defectcount' 'Defect Count'), (DateAttr 'detectedon' 'Detected On'))

EnsureTable 'serviceticket' 'Service Ticket' 'Service Tickets' 'Maintenance / repair work order for equipment' 'Ticket Title' @(
  (PickAttr 'priority' 'Priority' @('Low', 'Normal', 'High', 'Urgent')),
  (PickAttr 'status' 'Status' @('Open', 'In Progress', 'Waiting for Parts', 'Resolved')),
  (MemoAttr 'description' 'Description'), (StrAttr 'assignedtechnician' 'Assigned Technician' 100), (DateAttr 'openedon' 'Opened On'))

EnsureTable 'customer' 'Customer' 'Customers' 'Customer account that calls in for service' 'Account Name' @(
  (StrAttr 'accountnumber' 'Account Number' 50), (StrAttr 'firstname' 'First Name' 100), (StrAttr 'lastname' 'Last Name' 100),
  (StrAttr 'primaryphone' 'Primary Phone' 30), (StrAttr 'email' 'Email Address' 200), (StrAttr 'street' 'Street' 200),
  (StrAttr 'city' 'City' 100), (StrAttr 'state' 'State' 50), (StrAttr 'zip' 'Zip' 20))

EnsureTable 'servicerequestdraft' 'Service Request Draft' 'Service Request Drafts' 'Live draft built by the agents during a call; the rep submits it to create the service ticket' 'Draft Title' @(
  (StrAttr 'callid' 'Call ID' 100), (StrAttr 'callerphone' 'Caller Phone' 30), (StrAttr 'customername' 'Customer Name (heard)' 200),
  (StrAttr 'equipmenttext' 'Equipment (heard)' 200), (MemoAttr 'complaint' 'Complaint' 4000),
  (PickAttr 'priority' 'Priority' @('Low', 'Normal', 'High', 'Urgent')), (MemoAttr 'summary' 'Summary' 4000),
  (MemoAttr 'transcript' 'Transcript' 100000), (MemoAttr 'coaching' 'Coaching Prompts' 2000),
  (PickAttr 'status' 'Draft Status' @('Draft', 'Submitted', 'Discarded')), (StrAttr 'submittedby' 'Submitted By' 200),
  (DateAttr 'submittedon' 'Submitted On'),
  (StrAttr 'contactphone' 'Contact Phone' 30), (StrAttr 'assetnumber' 'Asset Number' 100), (StrAttr 'location' 'Location' 200),
  (StrAttr 'preferredtime' 'Preferred Time' 200), (StrAttr 'editedfields' 'Rep-edited fields' 300))

EnsureLookup 'equipment' 'customer' 'CustomerId' 'Customer'
EnsureLookup 'servicerequestdraft' 'customer' 'CustomerId' 'Customer'
EnsureLookup 'servicerequestdraft' 'equipment' 'EquipmentId' 'Equipment'
EnsureLookup 'servicerequestdraft' 'serviceticket' 'TicketId' 'Service Ticket'
EnsureLookup 'serviceticket' 'customer' 'CustomerId' 'Customer'
EnsureLookup 'qualityalert' 'part' 'PartId' 'Part'
EnsureLookup 'qualityalert' 'equipment' 'EquipmentId' 'Equipment'
EnsureLookup 'serviceticket' 'equipment' 'EquipmentId' 'Equipment'
EnsureLookup 'serviceticket' 'qualityalert' 'QualityAlertId' 'Related Quality Alert'

# ---------- Seed data ----------
function Sets($t) { (Dv GET "EntityDefinitions(LogicalName='${Prefix}_$t')?`$select=EntitySetName").EntitySetName }
function OptVal($t, $attr, $label) {
  $r = Dv GET "EntityDefinitions(LogicalName='${Prefix}_$t')/Attributes(LogicalName='${Prefix}_$attr')/Microsoft.Dynamics.CRM.PicklistAttributeMetadata?`$select=LogicalName&`$expand=OptionSet(`$select=Options)"
  ($r.OptionSet.Options | Where-Object { $_.Label.UserLocalizedLabel.Label -eq $label }).Value
}
# Inserts rows (hashtables with full column names) unless a row with the same name exists; returns name -> id
function Seed($table, $rows) {
  $set = Sets $table; $ids = @{}
  foreach ($r in $rows) {
    $nm = $r['bdm_name']
    $ex = (Dv GET "${set}?`$select=${Prefix}_${table}id&`$filter=bdm_name eq '$($nm.Replace("'", "''"))'").value
    if ($ex) { $ids[$nm] = $ex[0]."${Prefix}_${table}id" } else { $ids[$nm] = PostId $set $r }
  }
  $ids
}

$eqSet = Sets 'equipment'; $ptSet = Sets 'part'; $qaSet = Sets 'qualityalert'; $tkSet = Sets 'serviceticket'
$eq = Seed 'equipment' @(
  @{ bdm_name = 'CNC Mill 3'; bdm_assetnumber = 'EQ-1003'; bdm_plant = 'Grand Rapids, MI'; bdm_manufacturer = 'Haas'; bdm_status = (OptVal 'equipment' 'status' 'Operational'); bdm_installdate = '2021-03-15' },
  @{ bdm_name = 'Injection Press 2'; bdm_assetnumber = 'EQ-2002'; bdm_plant = 'Monterrey, MX'; bdm_manufacturer = 'Engel'; bdm_status = (OptVal 'equipment' 'status' 'Degraded'); bdm_installdate = '2019-09-01' },
  @{ bdm_name = 'Robotic Welder R7'; bdm_assetnumber = 'EQ-3007'; bdm_plant = 'Grand Rapids, MI'; bdm_manufacturer = 'FANUC'; bdm_status = (OptVal 'equipment' 'status' 'Operational'); bdm_installdate = '2022-06-20' },
  @{ bdm_name = 'Packaging Line A'; bdm_assetnumber = 'EQ-4001'; bdm_plant = 'Brussels, BE'; bdm_manufacturer = 'Bosch Packaging'; bdm_status = (OptVal 'equipment' 'status' 'Under Maintenance'); bdm_installdate = '2020-01-10' },
  @{ bdm_name = 'Hydraulic Press H1'; bdm_assetnumber = 'EQ-5001'; bdm_plant = 'Monterrey, MX'; bdm_manufacturer = 'Schuler'; bdm_status = (OptVal 'equipment' 'status' 'Down'); bdm_installdate = '2018-11-05' },
  @{ bdm_name = 'Heat Treat Furnace F2'; bdm_assetnumber = 'EQ-6002'; bdm_plant = 'Brussels, BE'; bdm_manufacturer = 'Ipsen'; bdm_status = (OptVal 'equipment' 'status' 'Operational'); bdm_installdate = '2023-02-28' })

$pt = Seed 'part' @(
  @{ bdm_name = 'Hydraulic Valve HV-220'; bdm_partnumber = 'P-HV220'; bdm_supplier = 'Parker Hannifin'; bdm_category = (OptVal 'part' 'category' 'Hydraulic'); bdm_unitcost = 184.50 },
  @{ bdm_name = 'Servo Motor SM-90'; bdm_partnumber = 'P-SM90'; bdm_supplier = 'Siemens'; bdm_category = (OptVal 'part' 'category' 'Electrical'); bdm_unitcost = 920.00 },
  @{ bdm_name = 'Spindle Bearing SB-45'; bdm_partnumber = 'P-SB45'; bdm_supplier = 'SKF'; bdm_category = (OptVal 'part' 'category' 'Mechanical'); bdm_unitcost = 76.25 },
  @{ bdm_name = 'Heating Element HE-12'; bdm_partnumber = 'P-HE12'; bdm_supplier = 'Watlow'; bdm_category = (OptVal 'part' 'category' 'Electrical'); bdm_unitcost = 310.00 },
  @{ bdm_name = 'Hex Bolt M12 Grade 10.9'; bdm_partnumber = 'P-BLT12'; bdm_supplier = 'Fastenal'; bdm_category = (OptVal 'part' 'category' 'Fastener'); bdm_unitcost = 0.42 },
  @{ bdm_name = 'Coolant Concentrate CC-5'; bdm_partnumber = 'P-CC5'; bdm_supplier = 'Castrol'; bdm_category = (OptVal 'part' 'category' 'Consumable'); bdm_unitcost = 58.00 })

function Alert($title, $sev, $stat, $desc, $count, $detected, $part, $equip) {
  @{ bdm_name = $title; bdm_severity = (OptVal 'qualityalert' 'severity' $sev); bdm_status = (OptVal 'qualityalert' 'status' $stat); bdm_description = $desc; bdm_defectcount = $count; bdm_detectedon = $detected
     'bdm_PartId@odata.bind' = "/$ptSet($($pt[$part]))"; 'bdm_EquipmentId@odata.bind' = "/$eqSet($($eq[$equip]))" }
}
$qa = Seed 'qualityalert' @(
  (Alert 'HV-220 valves leaking at seat' 'Critical' 'Investigating' 'Lot 4417 of HV-220 failing pressure test at 3000 psi; seat seal extrusion suspected.' 38 '2026-10-01T14:20:00Z' 'Hydraulic Valve HV-220' 'Hydraulic Press H1'),
  (Alert 'Servo motor encoder drift' 'High' 'New' 'Intermittent encoder position error on robotic welder; 3 weld seams out of tolerance.' 12 '2026-10-03T09:05:00Z' 'Servo Motor SM-90' 'Robotic Welder R7'),
  (Alert 'Spindle bearing premature wear' 'Medium' 'Contained' 'Surface finish degradation on CNC Mill 3; bearing runout above 8 microns.' 25 '2026-09-27T16:40:00Z' 'Spindle Bearing SB-45' 'CNC Mill 3'),
  (Alert 'Heating element burnout trend' 'High' 'Investigating' 'Three HE-12 elements failed within 6 weeks (expected life 12 months).' 3 '2026-09-30T08:00:00Z' 'Heating Element HE-12' 'Heat Treat Furnace F2'),
  (Alert 'Bolt torque-to-yield failures' 'Medium' 'New' 'M12 bolts snapping during assembly torque; supplier lot suspected.' 60 '2026-10-05T11:15:00Z' 'Hex Bolt M12 Grade 10.9' 'Packaging Line A'),
  (Alert 'Coolant concentration out of spec' 'Low' 'Closed' 'Refractometer readings 2 percent below spec; mix ratio corrected.' 0 '2026-09-20T07:30:00Z' 'Coolant Concentrate CC-5' 'Injection Press 2'))

function Ticket($title, $prio, $stat, $desc, $tech, $opened, $equip, $alert) {
  @{ bdm_name = $title; bdm_priority = (OptVal 'serviceticket' 'priority' $prio); bdm_status = (OptVal 'serviceticket' 'status' $stat); bdm_description = $desc
     bdm_assignedtechnician = $tech; bdm_openedon = $opened
     'bdm_EquipmentId@odata.bind' = "/$eqSet($($eq[$equip]))"; 'bdm_QualityAlertId@odata.bind' = "/$qaSet($($qa[$alert]))" }
}
Seed 'serviceticket' @(
  (Ticket 'Replace leaking HV-220 valve on press H1' 'Urgent' 'Waiting for Parts' 'Press down; valve replacement pending quarantine-free stock.' 'Dana Whitfield' '2026-10-02T06:00:00Z' 'Hydraulic Press H1' 'HV-220 valves leaking at seat'),
  (Ticket 'Recalibrate welder R7 encoder' 'High' 'In Progress' 'Re-home encoder and run seam validation coupons.' 'Luis Ortega' '2026-10-04T10:30:00Z' 'Robotic Welder R7' 'Servo motor encoder drift'),
  (Ticket 'Swap spindle bearing on CNC Mill 3' 'Normal' 'Resolved' 'Bearing replaced; runout back to 2 microns.' 'Priya Nair' '2026-09-28T13:00:00Z' 'CNC Mill 3' 'Spindle bearing premature wear'),
  (Ticket 'Inspect furnace F2 heating elements' 'High' 'Open' 'Full resistance check on all element banks.' 'Marc Dubois' '2026-10-01T07:45:00Z' 'Heat Treat Furnace F2' 'Heating element burnout trend'),
  (Ticket 'Packaging line A preventive maintenance' 'Low' 'In Progress' 'Quarterly PM including torque audit.' 'Sofie Peeters' '2026-10-06T08:00:00Z' 'Packaging Line A' 'Bolt torque-to-yield failures'),
  (Ticket 'Injection press 2 hydraulic pressure drift' 'Normal' 'Open' 'Pressure drifting 5 percent during hold phase.' 'Carlos Mendez' '2026-10-07T12:10:00Z' 'Injection Press 2' 'Coolant concentration out of spec')) | Out-Null

$cuSet = Sets 'customer'
function Cust($acct, $first, $last, $phone, $email, $street, $city, $state, $zip) {
  @{ bdm_name = "$first $last".ToUpper(); bdm_accountnumber = $acct; bdm_firstname = $first; bdm_lastname = $last; bdm_primaryphone = $phone
     bdm_email = $email; bdm_street = $street; bdm_city = $city; bdm_state = $state; bdm_zip = $zip }
}
$cu = Seed 'customer' @(
  (Cust '33507' 'Abby' 'Smith' '9374699275' 'abby.smith@example.com' '2284 HWY K 20' 'East Moline' 'IL' '61244'),
  (Cust '21063' 'John' 'Brown' '9888472750' 'john.brown@example.com' '613 PINE' 'East Moline' 'IL' '61244'),
  (Cust '40112' 'Aaron' 'Minde' '9374699271' 'aaron.minde@example.com' '88 Oak Ave' 'Moline' 'IL' '61265'),
  (Cust '40113' 'Jamie' 'Fergison' '1234567890' 'jamie.fergison@example.com' '5 Mill Rd' 'Davenport' 'IA' '52801'))
$ownership = @{ 'CNC Mill 3' = 'ABBY SMITH'; 'Injection Press 2' = 'ABBY SMITH'; 'Robotic Welder R7' = 'JOHN BROWN'; 'Hydraulic Press H1' = 'JOHN BROWN';
  'Packaging Line A' = 'AARON MINDE'; 'Heat Treat Furnace F2' = 'JAMIE FERGISON' }
foreach ($e in $ownership.Keys) {
  Dv PATCH "$eqSet($($eq[$e]))" @{ 'bdm_CustomerId@odata.bind' = "/$cuSet($($cu[$ownership[$e]]))" } | Out-Null
}

Write-Host "Done. equipment=$($eq.Count) parts=$($pt.Count) alerts=$($qa.Count)"

