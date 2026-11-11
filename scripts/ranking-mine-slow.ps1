# Slow, continuous ranking-pool mining on the owner PC (owner 2026-11-07: "work slow but consistently so it won't stop,
# just slowly, not too much resources" + "if my pc is always running something heavy that thing may not run").
#   * NOT idle priority (that starves under constant load): normal priority inside a Windows job with a HARD CPU cap
#     (-CpuPercent, default 10 % of the machine = ~1.6 of 16 cores) - python / yt-dlp / ffmpeg all inherit the job, so
#     it always progresses and never takes more than that slice
#   * 2 threads, one YouTube request per minute, 2 downloads per cycle, 2 shows per cycle (rotating through the plan)
#   * a pause between cycles; RAM: the models load once -MinRamGB (3.5) is available (the 5.2 GB rule would stall it
#     on a busy PC; the models need ~2.5-3 GB)
#   * waits while output/ranking-mine/PAUSE exists (a Claude session doing heavy local work creates it), stops when
#     output/ranking-mine/STOP exists; waits when C: has < 25 GB free
#   * results go to the local staging (output/edit-pools/_staged/ranking): reviewed + imported in a Claude session
# Start (hidden, outside the editor's / terminal's process tree so closing them never kills it):
#   Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass
#     -WindowStyle Hidden -File C:\Users\Home\Desktop\ChinaWeatherReader\scripts\ranking-mine-slow.ps1';
#     CurrentDirectory='C:\Users\Home\Desktop\ChinaWeatherReader'}
param(
  [int]$Threads = 2,
  [int]$PaceS = 60,
  [int]$MaxDl = 2,
  [int]$ShowsPerCycle = 2,
  [int]$CycleBudgetMin = 60,
  [int]$PauseMin = 15,
  [double]$MinFreeDiskGB = 25,
  [int]$CpuPercent = 10,
  [double]$MinRamGB = 3.5,
  [int]$Focus = 8,           # > 0: plan-shows gives the owner's mining order (ranking_pool.mining_order: focus shows, action / strong, romance + couples, the rest)
  [string]$DlRate = '1M'     # download speed cap (yt-dlp --limit-rate: 1M = 1 MB/s ~ 8 Mbit/s; '' = no cap)
)
$ErrorActionPreference = 'Continue'
# a job object with a hard CPU-rate cap; every process this script starts afterwards is in the job too
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class CwrSlowJob {
  [StructLayout(LayoutKind.Sequential)] struct CpuRate { public uint Flags; public uint Rate; }
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string n);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr j, int c, ref CpuRate i, uint l);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  public static IntPtr Job;
  public static string Cap(int percent) {
    Job = CreateJobObject(IntPtr.Zero, null);
    if (Job == IntPtr.Zero) return "CreateJobObject failed " + Marshal.GetLastWin32Error();
    CpuRate r = new CpuRate(); r.Flags = 0x1 | 0x4; r.Rate = (uint)(percent * 100);   // ENABLE | HARD_CAP, 1/100 %
    if (!SetInformationJobObject(Job, 15, ref r, (uint)Marshal.SizeOf(r))) return "SetInformationJobObject failed " + Marshal.GetLastWin32Error();
    if (!AssignProcessToJobObject(Job, GetCurrentProcess())) return "AssignProcessToJobObject failed " + Marshal.GetLastWin32Error();
    return "ok";
  }
}
"@
$capResult = [CwrSlowJob]::Cap($CpuPercent)
if ($capResult -ne 'ok') { (Get-Process -Id $PID).PriorityClass = 'BelowNormal' }   # no cap possible: at least yield
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
$env:RANKING_MIN_FREE_GB = "$MinRamGB"
$env:RANKING_DL_RATE = $DlRate
# ranking_compilation.idle() would drop the miner to Idle (starves on a busy PC); the job's CPU cap keeps it small instead
$env:MINE_PRIORITY = if ($capResult -eq 'ok') { 'normal' } else { 'below' }

Say "slow loop started (pid $PID, CPU cap $CpuPercent %: $capResult, RAM >= $MinRamGB GB, threads $Threads, pace $PaceS s, $MaxDl downloads x $ShowsPerCycle shows per cycle, pause $PauseMin min, download cap $(if ($DlRate) { "$DlRate/s" } else { 'none' }))"
$k = 0
if (Test-Path $StateF) { try { $k = [int](Get-Content $StateF -TotalCount 1) } catch { $k = 0 } }
while ($true) {
  if (Test-Path (Join-Path $Work 'STOP')) { Say 'STOP file: exiting'; break }
  if (Test-Path (Join-Path $Work 'PAUSE')) { Start-Sleep -Seconds 120; continue }
  $free = (Get-PSDrive C).Free / 1GB
  if ($free -lt $MinFreeDiskGB) { Say ("C: has {0:N1} GB free (< $MinFreeDiskGB): waiting 1 h" -f $free); Start-Sleep -Seconds 3600; continue }
  if (-not (Test-Path $Py)) { Say "python venv missing ($Py): exiting"; break }

  # the plan (shows that unlock the most lists first), rotated: every show gets its turn
  $plan = @(& $Py core/edit/ranking_pool.py plan-shows --focus $Focus 2>$null | ForEach-Object {
      if ($_ -match '^\s*[\d.]+\s+(.+?)\s{2,}cast') { $Matches[1].Trim().ToLower() } })
  if ($plan.Count -eq 0) { Say 'no plan (offline?): waiting 30 min'; Start-Sleep -Seconds 1800; continue }
  # the rotation advances ONE show per cycle (2026-11-10: a cycle's time budget only ever reached its first show, so with
  # k * ShowsPerCycle every second show of the plan - eminence in shadow, dragon ball super, overlord ... - was never mined);
  # the next show rides along in case the first one finishes early
  $shows = @(0..($ShowsPerCycle - 1) | ForEach-Object { $plan[($k + $_) % $plan.Count] }) | Select-Object -Unique
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
  # through cmd.exe: PowerShell 5's own redirection writes UTF-16 (the log became unreadable mixed text); cmd passes the
  # miner's UTF-8 bytes through. cmd and python stay in the CPU-capped job.
  $mineLog = Join-Path $Work 'slow-mine.log'
  $cmdline = "`"$Py`" core/edit/ranking_pool.py mine --budget-min $CycleBudgetMin --work output/ranking-mine --max-dl $MaxDl " +
             "--max-entries $ShowsPerCycle --shows `"$($shows -join ',')`" 1> `"$out`" 2>> `"$mineLog`""
  cmd /c $cmdline
  $code = $LASTEXITCODE
  Remove-Item $cookie -Force -ErrorAction SilentlyContinue
  try {
    # stdout = the miner's RAM-wait lines, then the JSON summary (it starts at the first line that is "{")
    $lines = @(Get-Content $out -Encoding UTF8)
    $j0 = 0
    while ($j0 -lt $lines.Count -and $lines[$j0] -notmatch '^\{') { $j0++ }
    $r = ($lines[$j0..($lines.Count - 1)] -join "`n") | ConvertFrom-Json
    Say ("cycle {0} done (exit {1}): downloaded {2}, staged {3}, {4} min" -f $k, $code, $r.downloaded, $r.staged, $r.minutes)
  } catch { Say "cycle $k done (exit $code): no summary" }
  Start-Sleep -Seconds ($PauseMin * 60)
}
