# Registers the Parlay Locks panel to run daily at 10:30 and 17:30 local (ET) as the current user (no admin needed).
$ErrorActionPreference = "Stop"
$arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\parlay-locks\run-panel.ps1"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory "C:\parlay-locks"
$t1 = New-ScheduledTaskTrigger -Daily -At "10:30"
$t2 = New-ScheduledTaskTrigger -Daily -At "17:30"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Parlay Locks Panel" -Action $action -Trigger $t1, $t2 -Settings $settings -Principal $principal `
  -Description "Paper dry run: Claude/Codex/Cursor vote on Parlay Locks candidate legs. Script: C:\parlay-locks\run-panel.ps1" -Force | Out-Null
Get-ScheduledTask -TaskName "Parlay Locks Panel" | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime | Format-List
