# Keep the platform logins alive by exercising them on a schedule.
#
# Sessions expire server-side - noon's dies after about a week idle - and no
# local setting can lengthen them. What does work is using them: a visited
# session gets its cookies rotated and extended. This runs the refresh
# (which opens each saved profile headless and re-exports its cookies) and
# then pushes the fresh exports to the deployed service, so the Railway copy
# never drifts far behind the laptop's.
#
# Registered as a scheduled task by scripts/register_keepalive.ps1 - every
# 2 days, catching up after sleep. Remove it with:
#   Unregister-ScheduledTask -TaskName "TrustIn session keepalive" -Confirm:$false
#
# Output lands in artifacts/keepalive.log (git-ignored). A platform that has
# been logged out makes the run exit non-zero and is named in the log with the
# login command to run - the cookies on the volume are left alone in that case.

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$log = Join-Path $repo "artifacts\keepalive.log"

New-Item -ItemType Directory -Force (Split-Path -Parent $log) | Out-Null
"`n===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') keepalive =====" | Add-Content $log

& $python (Join-Path $repo "scripts\refresh_storage_state.py") 2>&1 | Add-Content $log
$refreshOk = ($LASTEXITCODE -eq 0)

# Push even after a partial failure: the refresh never exports a logged-out
# session's cookies, so the dead platform's file on the volume is left as it
# was while the healthy ones still get their fresh rotation. One expired login
# must not starve the other three.
# SERVICE_URL and WEBHOOK_SECRET come from .env; unset means local-only.
$envFile = Join-Path $repo ".env"
$hasService = (Test-Path $envFile) -and
    (Select-String -Path $envFile -Pattern "^\s*SERVICE_URL\s*=\s*\S" -Quiet)
if ($hasService) {
    & $python (Join-Path $repo "scripts\push_sessions.py") 2>&1 | Add-Content $log
    if ($LASTEXITCODE -ne 0) { "push failed (exit $LASTEXITCODE)" | Add-Content $log }
} else {
    "no SERVICE_URL in .env - refreshed locally, nothing pushed" | Add-Content $log
}

"done (all sessions healthy: $refreshOk)" | Add-Content $log
if (-not $refreshOk) { exit 1 }
