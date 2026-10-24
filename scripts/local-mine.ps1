# Local idle-time pool miner (scripts/local-mine.py): start / stop / status. Nothing persistent (no scheduled task).
#   powershell -ExecutionPolicy Bypass -File scripts\local-mine.ps1 start|stop|status
param([ValidateSet('start', 'stop', 'status')][string]$Action = 'status')
$py = Join-Path $env:TEMP 'acapvenv\Scripts\python.exe'          # the PC python with cv2 / torch / yt-dlp[default]
$script = Join-Path $PSScriptRoot 'local-mine.py'
$home_ = Join-Path $env:USERPROFILE 'local-miner'
New-Item -ItemType Directory -Force $home_ | Out-Null
if ($Action -eq 'start') {
    # 2026-10-24: started through WMI (Win32_Process.Create), NOT Start-Process: a child of the calling shell dies with it (the
    # 10-23 miner stopped silently at 01:16 UTC when its launching session closed). IDLE priority class (64) from the start.
    $out = Join-Path $home_ 'stdout.txt'; $err = Join-Path $home_ 'stderr.txt'
    $cmd = "cmd.exe /c `"`"$py`" `"$script`" run 1>>`"$out`" 2>>`"$err`"`""
    $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ PriorityClass = [uint32]64; ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $home_; ProcessStartupInformation = $si }
    "local miner started (rc $($r.ReturnValue), pid $($r.ProcessId)); log: $home_\local-mine.log"
} elseif ($Action -eq 'stop') {
    & $py $script stop
} else {
    & $py $script status
}
