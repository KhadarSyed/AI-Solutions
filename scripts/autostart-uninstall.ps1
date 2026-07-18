$taskName = "PRIntelligenceAgent-Stack"
try {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "Scheduled task '$taskName' removed."
} catch {
    Write-Host "Task '$taskName' was not registered."
}
