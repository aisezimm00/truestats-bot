Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*python*' -and $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUNBUFFERED='1'
Start-Process -FilePath python -ArgumentList 'bot.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\bot.out.log" -RedirectStandardError "$PSScriptRoot\data\bot.log"
"restarted"
