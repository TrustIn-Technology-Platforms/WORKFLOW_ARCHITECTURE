# Register (or re-register) the session keep-alive as a Windows scheduled task.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_keepalive.ps1
#
# Every 2 days at 09:30, catching up as soon as the machine is next awake if it
# was off. Runs only while this user is logged on - the browser profiles are
# theirs. Remove with:
#   Unregister-ScheduledTask -TaskName "TrustIn session keepalive" -Confirm:$false

$name = "TrustIn session keepalive"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "scripts\keepalive.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval 2 -At 09:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Description "Exercises the saved platform logins so they do not expire idle, and pushes fresh cookies to the deployed service. Log: artifacts\keepalive.log" `
    -Force | Out-Null

Write-Host "Registered '$name' - every 2 days at 09:30 (runs on next wake if missed)."
Write-Host "Log: $repo\artifacts\keepalive.log"
