"""Kaggle CPU probe (2026-11-27): can a Kaggle machine download from YouTube? GitHub runners now fail on every video
(no_info / 'Failed to extract any player response'). Tries 3 ids that failed on GitHub, no cookies; prints one line each."""
import glob, os, shutil, subprocess, sys

subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp[default]', 'deno'], check=False)
deno = shutil.which('deno') or next(iter(glob.glob('/usr/local/lib/python3*/*-packages/deno/bin/deno') +
                                         glob.glob(os.path.expanduser('~/.local/lib/python3*/site-packages/deno/bin/deno'))), None)
print('yt-dlp', subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True).stdout.strip(), '| deno', deno, flush=True)
IDS = [('ranking edit (Mila list)', 'NNDS_6ah-KY'), ('slow-mo candidate', '5RaBX6oVkCg'), ('official song', 'TRpSC_hWkGE')]
os.makedirs('/kaggle/working/dl', exist_ok=True)
for what, vid in IDS:
    cmd = ['yt-dlp', '-f', 'bv*[height<=720]+ba/b[height<=720]/b', '--no-playlist', '-o', f'/kaggle/working/dl/{vid}.%(ext)s',
           f'https://www.youtube.com/watch?v={vid}']
    if deno:
        cmd[1:1] = ['--js-runtimes', f'deno:{deno}']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    got = glob.glob(f'/kaggle/working/dl/{vid}.*')
    size = sum(os.path.getsize(f) for f in got) / 1e6
    err = [l for l in (r.stderr or '').splitlines() if 'ERROR' in l][:1]
    print(f'RESULT {what} {vid}: {"OK %.1f MB" % size if got and r.returncode == 0 else "FAIL " + (err[0][:200] if err else str(r.returncode))}', flush=True)
shutil.rmtree('/kaggle/working/dl', ignore_errors=True)
