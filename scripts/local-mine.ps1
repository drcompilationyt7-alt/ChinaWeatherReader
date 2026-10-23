# Local idle-time pool miner (scripts/local-mine.py): start / stop / status. Nothing persistent (no scheduled task).
#   powershell -ExecutionPolicy Bypass -File scripts\local-mine.ps1 start|stop|status
param([ValidateSet('start', 'stop', 'status')][string]$Action = 'status')
$py = Join-Path $env:TEMP 'acapvenv\Scripts\python.exe'          # the PC python with cv2 / torch / yt-dlp[default]
$script = Join-Path $PSScriptRoot 'local-mine.py'
$home_ = Join-Path $env:USERPROFILE 'local-miner'
New-Item -ItemType Directory -Force $home_ | Out-Null
if ($Action -eq 'start') {
    $p = Start-Process -FilePath $py -ArgumentList @("`"$script`"", 'run') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $home_ 'stdout.txt') -RedirectStandardError (Join-Path $home_ 'stderr.txt')
    try { $p.PriorityClass = 'Idle' } catch {}
    "local miner started (pid $($p.Id)); log: $home_\local-mine.log"
} elseif ($Action -eq 'stop') {
    & $py $script stop
} else {
    & $py $script status
}
