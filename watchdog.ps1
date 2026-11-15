# Запускает bot.py, если он не запущен. Вызывается Планировщиком при входе и каждые 5 минут.
$running = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*python*' -and $_.CommandLine -like '*bot.py*' }
if ($running) { exit 0 }
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUNBUFFERED='1'
$log = "$PSScriptRoot\data\bot.log"
if (Test-Path $log) { Add-Content -Path "$PSScriptRoot\data\bot.history.log" -Value (Get-Content $log -Tail 50) }
Start-Process -FilePath python -ArgumentList 'bot.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot\data\bot.out.log" -RedirectStandardError $log
