# Регистрирует задачу Планировщика: сторож бота при входе в систему + каждые 5 минут.
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $PSScriptRoot\watchdog.ps1"
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "truestats-bot-watchdog" -Action $action -Trigger $t1,$t2 -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName "truestats-bot-watchdog" | Select-Object TaskName, State
