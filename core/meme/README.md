# Meme shorts

## Acapella audio engine (`acapella.py`)

Turns a song into the layered "<song> acapella" soundtrack: the song's real lead
vocal (separated with Demucs), plus a hummed bass, a mouth beatbox and ooh/aah
harmony. We synthesise those three layers ourselves in numpy, using formant
synthesis and filtered noise, with no samples. Each section adds one layer:

| section | layers |
|---|---|
| 1 | vocal |
| 2 | vocal + bass |
| 3 | vocal + bass + beatbox |
| 4 | vocal + bass + beatbox + harmony |

After the last bar there is a one-beat "button": a kick, a "doom" and an "aah" on the next downbeat.

```
python core/meme/acapella.py --audio song.mp3 --out-dir work/ [--start SEC] [--length 20] [--heatmap heatmap.json]
```

- The window is about `--length` seconds long and always a whole number of 4/4 bars (16-22 s by default). It starts on a downbeat. There are three ways to pick it:
  - `--start` is used as given (snapped to the nearest downbeat).
  - Otherwise, the window is centred on the most-replayed region from a yt-dlp heatmap (`yt-dlp -J URL`, field `heatmap`; passing the whole `-J` dump works too).
  - Otherwise, it picks the loudest, most vocal and most repeated (chorus) stretch.
- Section 1 is the vocal alone, so it has to contain singing. Without `--start`, the window slides by whole bars (up to L/4 earlier or L/3 later) until the separated vocal is present in bar 1 and in most of section 1.
- Demucs runs only on the window plus that sliding room, which keeps it fast on CPU.
- The vocal is not changed apart from a 70 Hz rumble filter and fades at the cut points. There is no pitch or speed change. Do not add any.

Outputs in `--out-dir`:

- `acapella.wav`: the final stereo mix, 44.1 kHz, about -14 LUFS, true peak below -1 dBTP.
- `vocal.wav`, `bass.wav`, `beatbox.wav`, `harmony.wav`: dry stems at their level in the mix. The reverb is only in the mix.
- `timeline.json`: all times in seconds of the output file.
  - `bpm`, `beats`, `downbeats`
  - `bars[]`, each with its `chord`, and `chords_per_beat`
  - `sections[]` with the `layers` active in each
  - `end_hit`
  - `source.window_start` / `source.offset`: the source-song times of the first downbeat and of output t=0. The output can start up to a beat early so a pickup word isn't cut off.
  - `layers.*`: `vocal.onsets`, `bass.notes` (t, dur, midi, syllable), `beatbox.hits` (t, kind = kick / snare / k / hat / open_hat), `harmony.notes`
  - `onsets.{vocal,bass,beatbox,harmony}`: flat lists for cutting clips "on the hit".
- The last stdout line is `{"ok": true, "mix", "stems", "timeline", "bpm", "duration", ...}`. On failure it is `{"ok": false, "reason"}` with exit code 1.

Install on CI (ubuntu-latest, CPU):

```
pip install --break-system-packages torch --index-url https://download.pytorch.org/whl/cpu
pip install --break-system-packages demucs librosa
```

The first run downloads the htdemucs weights (about 80 MB) into `~/.cache/huggingface`. Cache that directory between workflow runs.

A 20 s window takes about 35 s on a 16-core desktop and about 45 s when limited to 2 threads. Demucs accounts for about 30 s of that, on a region of about 37 s: the window plus room to slide it onto the singing. The first call in a fresh environment also spends about 20 s compiling librosa's numba code.

Assumptions: 4/4 time and a steady, click-tracked tempo, which covers pop, K-pop and anime songs. The grid is one straight tempo fitted to the separated drums. `analysis.grid_to_attacks_median_ms` in the timeline shows how well the grid fits.
