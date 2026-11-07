# Slow, continuous ranking-pool mining on the owner PC (owner 2026-11-07: "work slow but consistently so it won't stop,
# just slowly, not too much resources").
#   * Idle CPU priority (inherited by python / yt-dlp / ffmpeg): it only gets CPU nobody else wants
#   * 2 threads, one YouTube request per minute, 2 downloads per cycle, 2 shows per cycle (rotating through the plan)
#   * a pause between cycles; the RAM rule stays (ranking_compilation.wait_ram: waits for 5.2 GB available)
#   * waits while output/ranking-mine/PAUSE exists (a Claude session doing heavy local work creates it), stops when
#     output/ranking-mine/STOP exists; waits when C: has < 25 GB free
#   * results go to the local staging (output/edit-pools/_staged/ranking): reviewed + imported in a Claude session
# Start (hidden, survives closing the terminal):
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\ranking-mine-slow.ps1'
param(
  [int]$Threads = 2,
  [int]$PaceS = 60,
  [int]$MaxDl = 2,
  [int]$ShowsPerCycle = 2,
  [int]$CycleBudgetMin = 60,
  [int]$PauseMin = 15,
  [double]$MinFreeDiskGB = 25
)
$ErrorActionPreference = 'Continue'
(Get-Process -Id $PID).PriorityClass = 'Idle'          # children inherit the Idle class
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $env:LOCALAPPDATA 'Temp\acapvenv\Scripts\python.exe'
$Work = Join-Path $Root 'output\ranking-mine'
New-Item -ItemType Directory -Force $Work | Out-Null
$Log = Join-Path $Work 'slow-loop.log'
$StateF = Join-Path $Work 'slow-loop.state'
$CookieSrc = Join-Path $env:USERPROFILE 'yt-cookies.txt'
$CookieDir = Join-Path $Work 'ytc'

function Say($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $Log }

$env:PYTHONIOENCODING = 'utf-8'
$env:EDIT_POOL_DIR = 'output/edit-pools'
$env:EDIT_THREADS = "$Threads"
$env:OMP_NUM_THREADS = "$Threads"
$env:MKL_NUM_THREADS = "$Threads"
$env:RANKING_PACE_S = "$PaceS"
$env:EDIT_GPU = '0'

Say "slow loop started (pid $PID, threads $Threads, pace $PaceS s, $MaxDl downloads x $ShowsPerCycle shows per cycle, pause $PauseMin min)"
$k = 0
if (Test-Path $StateF) { try { $k = [int](Get-Content $StateF -TotalCount 1) } catch { $k = 0 } }
while ($true) {
  if (Test-Path (Join-Path $Work 'STOP')) { Say 'STOP file: exiting'; break }
  if (Test-Path (Join-Path $Work 'PAUSE')) { Start-Sleep -Seconds 120; continue }
  $free = (Get-PSDrive C).Free / 1GB
  if ($free -lt $MinFreeDiskGB) { Say ("C: has {0:N1} GB free (< $MinFreeDiskGB): waiting 1 h" -f $free); Start-Sleep -Seconds 3600; continue }
  if (-not (Test-Path $Py)) { Say "python venv missing ($Py): exiting"; break }

  # the plan (shows that unlock the most lists first), rotated: every show gets its turn
  $plan = @(& $Py core/edit/ranking_pool.py plan-shows 2>$null | ForEach-Object {
      if ($_ -match '^\s*[\d.]+\s+(.+?)\s{2,}cast') { $Matches[1].Trim().ToLower() } })
  if ($plan.Count -eq 0) { Say 'no plan (offline?): waiting 30 min'; Start-Sleep -Seconds 1800; continue }
  $shows = @(0..($ShowsPerCycle - 1) | ForEach-Object { $plan[($k * $ShowsPerCycle + $_) % $plan.Count] }) | Select-Object -Unique
  $k++
  Set-Content -Path $StateF -Value $k -Encoding ascii

  New-Item -ItemType Directory -Force $CookieDir | Out-Null
  $cookie = Join-Path $CookieDir 'cookies.txt'
  Copy-Item $CookieSrc $cookie -Force                  # a copy per cycle (yt-dlp rewrites its cookie file)
  $env:YT_SPARE_COOKIES = $cookie
  $out = Join-Path $Work 'slow-last.json'
  foreach ($lf in @((Join-Path $Work 'slow-mine.log'), $Log)) {     # keep the logs small
    if ((Test-Path $lf) -and (Get-Item $lf).Length -gt 20MB) { Move-Item $lf "$lf.1" -Force }
  }
  Say ("cycle {0}: {1}" -f $k, ($shows -join ', '))
  & $Py core/edit/ranking_pool.py mine --budget-min $CycleBudgetMin --work output/ranking-mine --max-dl $MaxDl `
      --max-entries $ShowsPerCycle --shows ($shows -join ',') 1> $out 2>> (Join-Path $Work 'slow-mine.log')
  $code = $LASTEXITCODE
  Remove-Item $cookie -Force -ErrorAction SilentlyContinue
  try {
    $r = Get-Content $out -Raw | ConvertFrom-Json
    Say ("cycle {0} done (exit {1}): downloaded {2}, staged {3}, {4} min" -f $k, $code, $r.downloaded, $r.staged, $r.minutes)
  } catch { Say "cycle $k done (exit $code): no summary" }
  Start-Sleep -Seconds ($PauseMin * 60)
}
