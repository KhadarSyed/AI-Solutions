# Registers a Windows Scheduled Task that starts the PR Intelligence stack at logon.
# Containers use `restart: unless-stopped`, so once up they survive reboots on their own
# whenever the Docker engine starts; this task covers cold starts (engine up, stack never started).
# Stop the stack manually with:  docker compose down   (it then STAYS stopped until you start it)
# Remove autostart with:         .\autostart-uninstall.ps1
#
# NOTE: also enable "Start Rancher Desktop at login" in Rancher Desktop > Preferences > Application,
# otherwise there is no Docker engine for the task to talk to.

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$taskName = "PRIntelligenceAgent-Stack"

$script = @"
`$deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt `$deadline) {
    docker info *> `$null
    if (`$LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 15
}
Set-Location -LiteralPath '$projectDir'
docker compose up -d
"@

$scriptPath = Join-Path $projectDir "scripts\autostart-run.ps1"
Set-Content -Path $scriptPath -Value $script -Encoding utf8

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$taskName' registered — stack will start at logon."
Write-Host "Reminder: enable 'Start at login' inside Rancher Desktop preferences."
