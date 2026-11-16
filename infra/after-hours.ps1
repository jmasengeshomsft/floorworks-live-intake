function Resolve-GatewayAfterHoursSettings($Parameters) {
  if ($Parameters.PSObject.Properties['afterHour']) {
    throw "afterHour is not supported. Use afterHours (AFTER_HOURS, with the final S)."
  }
  $fields = @('afterHours', 'serviceTz', 'serviceDays', 'serviceHours')
  $specified = @($fields | Where-Object { $null -ne $Parameters.PSObject.Properties[$_] })
  if (-not $specified.Count) {
    return [pscustomobject]@{ Configured = $false; Mode = ''; TimeZone = ''; Days = ''; Hours = '' }
  }
  $mode = ([string]$Parameters.afterHours).Trim().ToLowerInvariant()
  if ($mode -notin 'on', 'off', 'auto') { throw "afterHours must be 'on', 'off' or 'auto'." }
  $zone = ([string]$Parameters.serviceTz).Trim()
  $days = ([string]$Parameters.serviceDays).Trim()
  $hours = ([string]$Parameters.serviceHours).Trim()
  if ($mode -eq 'auto' -or @($specified | Where-Object { $_ -ne 'afterHours' }).Count) {
    if (-not $zone -or -not $days -or -not $hours) {
      throw "An automatic or supplied schedule requires serviceTz, serviceDays and serviceHours together."
    }
    $windowsId = ''
    if (-not [TimeZoneInfo]::TryConvertIanaIdToWindowsId($zone, [ref]$windowsId)) {
      throw "serviceTz must be a case-sensitive IANA timezone, e.g. America/Chicago or UTC. Use PowerShell 7.2+ with ICU support, not a Windows-only timezone name."
    }
    foreach ($part in $days.Split(',')) {
      if ($part.Trim() -notmatch '^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(\s*-\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun))?$') {
        throw "serviceDays must contain weekday abbreviations or ranges, e.g. Mon-Fri or Mon,Wed,Fri."
      }
    }
    if ($hours -notmatch '^(?<start>(?:[01][0-9]|2[0-3]):[0-5][0-9])\s*-\s*(?<end>(?:[01][0-9]|2[0-3]):[0-5][0-9])$' -or
        [string]::CompareOrdinal($Matches.start, $Matches.end) -ge 0) {
      throw "serviceHours must be an increasing, same-day HH:MM-HH:MM range, e.g. 07:00-18:00; overnight hours are not supported."
    }
  }
  [pscustomobject]@{ Configured = $true; Mode = $mode; TimeZone = $zone; Days = $days; Hours = $hours }
}

function Get-GatewayAfterHoursEnvironment($Settings) {
  if (-not $Settings.Configured) { return }
  "AFTER_HOURS=$($Settings.Mode)"
  if ($Settings.TimeZone) {
    "SERVICE_TZ=$($Settings.TimeZone)"
    "SERVICE_DAYS=$($Settings.Days)"
    "SERVICE_HOURS=$($Settings.Hours)"
  }
}
