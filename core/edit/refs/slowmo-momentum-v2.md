# Slow-mo momentum model v2 (groove songs) - per-cut tables (2026-10-23)

Measured with judge_slowmo's own analysis (wave_events + the percussive beat grid of `_jw_groove`) on each edit's own audio,
in seconds of the judged window. Study only: no footage kept in the repo, no lyrics (words are never written here).
"strong sung attack - cut" = the nearest strong sung attack in [-0.15, +0.40] s after the cut (+ = the cut leads the voice).
"cut - beat" = against the librosa beat grid of the window (blue: 0.604 s; the grid phase can sit on the off-beat eighths).
14 kinds: tight = attack - cut in [-0.06, +0.07] s; beat = cut - beat in [-0.07, +0.03] s; other = no sung attack it
announces (a gap / release switch); edge = the first / last second; LOOSE = the rest.

Summary (judge v2 on the rendered audio of each edit; 11 = momentum, 13 = hold tail, 14 = groove):

| edit | cuts | 14 loose / judged | 11 bad | 11 missed (v1 -> v2) | 13 tail cuts | verdict |
|---|---|---|---|---|---|---|
| blue ref 6LM8-q3PnVs (AMV, 1.5 M) | 22 / 36 s | 1 / 16 (6 %) | 1 | 15 -> 0 | 1 | groove timing PASS (one mid-phrase + one tail cut break the owner's hold rule) |
| blue ref Ulco4K08-u4 (Waguri beauty edit) | 9 / 23 s | 2 / 8 (25 %) | 0 | 12 -> 2 (both in the first 1.6 s of the window) | 0 | groove PASS |
| blue ref EEdpjFviGJg (dance edit) | 9 / 12 s | 2 / 5 (40 %, < 3 loose) | 0 | 0 | 0 | groove PASS (8.5) |
| blue ref 1CFWede1erg (fast AMV mix, 1.1 M) | 49 / 41 s | 18 / 34 (53 %) | 8 | 4 -> 0 | 0 | FAIL - a 0.8 s-per-shot AMV, not a slow-mo edit |
| blue ref MS3S7SaPJN0 (MV, 666 k) | 17 / 37 s | (pulse 0.43, ballad rule) | 3 | 21 | 0 | FAIL - every cut a constant ~0.16 s before the voice (upload A/V offset), not used |
| **ours blue round 6 (owner: "timing off")** | 24 / 38 s | **8 / 23 (35 %)** | 0 | 0 | 0 | **NOT PASS (14)** |
| ours blue v2 plan (plan-only) | 23 body | 3 / 22 (14 %) | 0 | 0 | 0 | PASS (12b one off start) |
| 3g (Golden Hour slowed, approved) | 18 | pulse 0.29: ballad rule | 0 | 0 | 0 | PASS, unchanged |
| 3j (Golden Hour regular, approved) | 17 | pulse 0.34: ballad rule | 0 | 0 | 0 | PASS, unchanged |
| UIF ref 5jgBLnTHKR8 (Shikimori, 3.7 M) | 8 / 14 s | pulse 0.23: ballad rule | 2 | 9 | 0 | fails the owner's every-wave ballad rule |
| UIF ref DfwpBMfE6aw (AMV, 967 k) | 24 / 54 s | pulse 0.43: ballad rule | 8 | 24 | 0 | fails the owner's every-wave ballad rule |

### blue ref 6LM8-q3PnVs (AMV, 1.5 M views)

| cut (s) | strong sung attack - cut (ms) | cut - beat (ms) | 14 kind | 11 (momentum) |
|---|---|---|---|---|
| 1.51 | +58 | +252 | tight | ok |
| 3.47 | - | -243 | other | ok |
| 4.31 | - | -36 | beat | ok |
| 5.21 | -4 | +260 | tight | ok |
| 7.84 | -2 | -195 | tight | ok |
| 8.91 | - | +268 | other | **mid-phrase (0.37 s into it, next attack ** |
| 15.24 | +248 | -179 | loose **LOOSE** | ok |
| 16.27 | +31 | +250 | tight | ok |
| 18.11 | +33 | +227 | tight | ok |
| 19.04 | +1 | -48 | tight | ok |
| 20.01 | - | +269 | other | ok |
| 21.51 | +33 | -65 | tight | ok |
| 23.41 | +3 | -23 | tight | ok |
| 24.57 | +61 | -64 | tight | ok |
| 25.51 | +62 | +243 | tight | ok |
| 26.47 | +269 | -22 | beat | ok |
| 26.74 | +2 | +245 | tight | ok |
| 27.14 | - | +41 | other | ok |
| 28.31 | -1 | -22 | tight | ok |
| 31.07 | +55 | -43 | tight | ok |
| 33.84 | - | +263 | other | ok |
| 34.84 | - | +79 | other | ok |

loose 1 / 16 judged (0.062), 11 bad [8.906], missed [], 13 tail cuts 1

### blue ref Ulco4K08-u4 (Waguri beauty edit)

| cut (s) | strong sung attack - cut (ms) | cut - beat (ms) | 14 kind | 11 (momentum) |
|---|---|---|---|---|
| 2.50 | - | -13 | beat | ok |
| 3.36 | - | +251 | other | ok |
| 7.89 | -41 | -139 | tight | ok |
| 9.79 | +48 | -97 | tight | ok |
| 11.43 | +267 | -251 | loose **LOOSE** | ok |
| 11.66 | +34 | -18 | tight | ok |
| 11.86 | +140 | +182 | loose **LOOSE** | ok |
| 13.56 | +293 | -22 | beat | ok |
| 19.06 | -22 | -25 | tight | ok |

loose 2 / 8 judged (0.25), 11 bad [], missed [1.275, 1.564], 13 tail cuts 0

### blue ref EEdpjFviGJg (dance edit)

| cut (s) | strong sung attack - cut (ms) | cut - beat (ms) | 14 kind | 11 (momentum) |
|---|---|---|---|---|
| 1.54 | - | +265 | other | ok |
| 3.44 | - | -273 | other | ok |
| 5.17 | +95 | +206 | loose **LOOSE** | ok |
| 6.08 | -6 | -102 | tight | ok |
| 6.97 | - | +195 | other | ok |
| 7.97 | -121 | -59 | beat | ok |
| 8.88 | +101 | +237 | loose **LOOSE** | ok |
| 9.84 | +1 | -26 | tight | ok |
| 11.71 | +294 | +28 | edge | ok |

loose 2 / 5 judged (0.4), 11 bad [], missed [], 13 tail cuts 0

### OURS blue round 6 (owner: "timing off")

| cut (s) | strong sung attack - cut (ms) | cut - beat (ms) | 14 kind | 11 (momentum) |
|---|---|---|---|---|
| 7.20 | +53 | -91 | tight | ok |
| 8.57 | +84 | -117 | loose **LOOSE** | ok |
| 10.10 | +81 | +46 | loose **LOOSE** | ok |
| 10.97 | +82 | -86 | loose **LOOSE** | ok |
| 12.37 | +42 | -56 | tight | ok |
| 13.40 | +162 | -68 | beat | ok |
| 14.40 | +68 | -66 | tight | ok |
| 15.43 | +36 | -55 | tight | ok |
| 16.57 | +17 | +34 | tight | ok |
| 18.33 | +347 | +82 | loose **LOOSE** | ok |
| 19.53 | +35 | -65 | tight | ok |
| 20.57 | +21 | -76 | tight | ok |
| 22.60 | +28 | -63 | tight | ok |
| 23.27 | +97 | -115 | loose **LOOSE** | ok |
| 25.33 | +52 | -93 | tight | ok |
| 27.40 | +82 | -92 | loose **LOOSE** | ok |
| 28.77 | +37 | -72 | tight | ok |
| 29.80 | +81 | -84 | loose **LOOSE** | ok |
| 31.10 | +65 | -131 | tight | ok |
| 31.87 | +299 | -60 | beat | ok |
| 32.90 | +3 | -26 | tight | ok |
| 34.93 | -9 | -59 | tight | ok |
| 35.93 | +200 | -81 | loose **LOOSE** | ok |

loose 8 / 23 judged (0.348), 11 bad [], missed [], 13 tail cuts 0

### OURS blue v2 plan (plan-only, song window, judge v2 on the song audio)

| cut (s) | strong sung attack - cut (ms) | cut - beat (ms) | 14 kind | 11 (momentum) |
|---|---|---|---|---|
| 7.29 | -39 | - | tight | ok |
| 10.24 | -58 | - | tight | ok |
| 11.09 | -40 | - | tight | ok |
| 12.47 | -59 | - | tight | ok |
| 13.53 | -1 | - | tight | ok |
| 14.53 | -21 | - | tight | ok |
| 15.50 | -30 | - | tight | ok |
| 16.55 | +73 | - | beat | ok |
| 18.66 | +20 | - | tight | ok |
| 19.63 | -58 | - | tight | ok |
| 21.70 | -68 | - | loose **LOOSE** | ok |
| 22.67 | -39 | - | tight | ok |
| 25.46 | -59 | - | tight | ok |
| 26.79 | - | - | beat | ok |
| 27.53 | -49 | - | tight | ok |
| 28.85 | -49 | - | tight | ok |
| 29.93 | +199 | - | loose **LOOSE** | ok |
| 31.22 | -40 | - | tight | ok |
| 31.96 | +206 | - | loose **LOOSE** | ok |
| 32.95 | -49 | - | tight | ok |
| 33.71 | - | - | other | ok |
| 34.99 | -68 | - | beat | ok |
| 36.12 | -59 | - | tight | ok |

loose 3 / 22 judged (0.136), 11 bad [], missed [], 13 tail cuts 0
