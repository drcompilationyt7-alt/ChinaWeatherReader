#!/usr/bin/env python3
"""
Named preset pipelines: "this pipeline for these clips, that pipeline for those clips".

    python core/edit/presets.py --list
    python core/edit/presets.py --show blur_flow
    python core/edit/presets.py --choose song --analysis music.json --shots shots.json

Every preset in PRESETS is one row of core/edit/refs/presets-catalog.json, which clusters all 37 hand-picked
references (+ the fx reference) into named styles. A preset is:

    selection  which songs and which clips it suits (`signals`, matched by choose_preset)
    pipeline   exactly ONE planner template (core/edit/planners.py) and ONE style preset (core/edit/styles.py)
    effects    the measured effect list with its probabilities and parameters, traced to the reference ids
    text / audio / grade / never   the rules that make it read as that style rather than another

presets.py adds SELECTION and the measured pools on top of what already exists; it is not a second rendering
path. Callers:

    core/edit/planners.py --preset NAME|auto   (feeds template + style + look)
    pipeline/edit-pipeline.js                  (logs the chosen preset as an experiment factor)

choose_preset(kind, song_analysis, shots, rng=None) returns (preset_id, why): it scores every preset whose
`kind` matches against the song analysis (core/edit/music.py: bpm, drops, sections, per-beat energy, drum
share, onsets) and the shot pool (core/edit/library.py shots.json: count, distinct sources, take lengths,
stills, one-character flag, sharpness), then samples among the survivors by posting weight so the channel
does not post the same style every day.

`weight` is the posting share, renormalised over the presets that map to a template the planner can build.
"""
import argparse
import json
import os
import sys

import numpy as np

# ---------------------------------------------------------------------------------------------- catalogue
# GENERATED from core/edit/refs/presets-catalog.json (+ core/edit/refs/fx.json for fx_typography).
PRESETS = \
{'blur_flow': {'id': 'blur_flow',
             'name': 'Blur Flow',
             'group': 'general',
             'weight': 0.14397,
             'one_line': 'One cut per felt beat, every cut a blur bridge. The default song edit.',
             'refs': ['ZkWQloS4jtM', 'cj12R_cW2ac', 'S2rzMsl0N7k'],
             'kind': ['song'],
             'signals': {'bpm': [78, 112], 'needs_drop': True, 'min_shots': 20, 'drums_share_min': 0.25},
             'clips': 'many crisp key-animation moments from one or several shows; 55% close-ups, 30% medium, 15% wide; no '
                      'subtitles, no talking heads, no source text',
             'song': 'steady 80-110 BPM pop with a clear drum grid and a felt drop; drum stem present; no drumless ballads',
             'length': '15-45 s Short (the long form goes to Cinematic Long)',
             'template': 'flow_blur',
             'style': 'flow_blur',
             'look': 'warm_glow',
             'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full or letterbox_blur'},
             'pacing': {'interval': '1 felt beat (82% loud / 72% mid / 43% quiet), 1/2-beat doubles only on loud beats, 2-3 '
                                   'beat holds when quiet',
                       'lead_ms': -80,
                       'hero_every': '4 bars, max 1 per 4 bars',
                       'drop': 'flash cut on the downbeat then 4-5 cuts per bar'},
             'speed': {'mode': 'velocity u_shape',
                      'params': 'lo 0.55, hi_in 1.6, hi_out 1.0, tau_in 0.12, tau_out 0.10; slow-mo holds 0.35-0.6x on '
                                'calm verses'},
             'camera': {'moves': 'push_in .35 / pull_out .35 / drift .30, |ln s| 0.04-0.10, alternating',
                       'handheld': '0.003 W at 3 Hz, roll 0.18 deg at 2.5 Hz, zoom 1% at 1.5 Hz',
                       'bounce': 0},
             'effects': [['blur_bridge',
                        0.62,
                        'sigma 18 e^(-t/0.09) px @1080, gain 1+0.5 e^(-t/0.1), dir 100 e^(-t/0.05) px @1920, out 3 -> 8 px '
                        'over 2 frames @30'],
                        ['blur_bridge_flash',
                        0.25,
                        'the same with 1-2 white frames on the cut (fires when energy >= 0.55)'],
                        ['zoom_through',
                        0.06,
                        'out 1 -> 1.13 ease-in over 100 ms + zoom blur 0.15; in 1.10 -> 1.0 over 170 ms'],
                        ['whip_slide', 0.03, '8-10% W per frame @30, blur 100-250 px @1920'],
                        ['dissolve_blur', 0.04, '2-4 frames, both defocused 4-8 px'],
                        ['flash_white', 'hero .30', '1-2 frames (3-4 on drops), the LAST white frame on the beat'],
                        ['radial_zoom_burst', 'hero .15', 'out 0.2 -> 0.4, in 0.4 -> 0 + a 0.6 flash'],
                        ['white_bar_wipe', 'hero .10', 'bars at x 0.35 / 0.62, 5% W, 4 frames'],
                        ['card_tilt_in', 'hero .10', '10 frames @30, 0.45 W -> full, rot_y 30 / rot_x 10 / roll -4'],
                        ['photo_collage', 'hero .08', 'pull-out 1.0 -> 0.45 over 12 frames onto #EDEDED paper'],
                        ['dip_black_strobe', 'hero .07', '1-2 black then 1 white'],
                        ['shape_wipe', 'hero .07', 'heart / star 0.6 -> 2.6 over 4 frames'],
                        ['invert_strobe', 'hero .05', 'invert, white, 1-2 black on the beat'],
                        ['split_screen',
                        'hero .08',
                        'seam at 0.5 W with a 6 px gap, 1-2 beats - CURRENTLY DEGRADES TO A HARD CUT'],
                        ['punch_settle', 'inside blur_bridge .25', 's = 1 + A e^(-t/0.06), A p50 0.136'],
                        ['handheld_camera', 'always', 'see camera']],
             'grade': {'look': 'warm_glow (ZkWQ) or show_palette (cj12, S2rz)',
                      'numbers': 'warm: luma 100-145, hue 5-20 deg, highlights b +8..+15. show_palette: p2 <= 18, std '
                                 '60-68, sat 95-130, cool shadows. Bloom on; vignette corner/centre 0.33-0.68 (median '
                                 '0.5); grain light; letterbox 7-11% on whole sections'},
             'text': '1-3 title words on hero downbeats only; NO lyrics unless word timings exist; no dialogue captions; '
                     'small watermark always',
             'audio': 'music only; no whoosh or impact SFX (high-band energy before cuts 0.9-1.05x the mean); -14 LUFS, TP '
                      '< -1 dBTP',
             'never': [],
             'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/zkw_strip_a.jpg',
                         'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/cj12_strip_a.jpg',
                         'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/ZkWQloS4jtM_cuts_1.jpg',
                         'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/zkw_collage.jpg'],
             'gaps': ['split_screen']},
'hype_typography': {'id': 'hype_typography',
                   'name': 'Hype Typography',
                   'group': 'general',
                   'weight': 0.07198,
                   'one_line': '4-5 frame bridges, flashes on most cuts, hero cards / splits / bar wipes and big words.',
                   'refs': ['i4fSruIkkxk', 'm3lv7H6cu_Y', 'zawBCooJwLw'],
                   'kind': ['song'],
                   'signals': {'bpm': [100, 132], 'needs_drop': True, 'energy_p90_min': 0.75, 'min_shots': 20},
                   'clips': 'action and high-contrast key animation, several shows allowed; a song title worth showing',
                   'song': 'loud 100-130 BPM with a big drop and a loud chorus; heavy drums',
                   'length': '15-40 s Short',
                   'template': 'hype_typography',
                   'style': 'hype_flash',
                   'look': 'show_palette',
                   'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full'},
                   'pacing': {'interval': '1 beat plus hero cards, splits and wipes on phrase starts',
                             'lead_ms': -147,
                             'hero_every': 'phrase (4 bars), up to 3 per short',
                             'drop': 'flash on the downbeat, 4-5 cuts per bar'},
                   'speed': {'mode': 'velocity u_shape with a faster entry',
                            'params': 'hi_in up to 1.8 into hero cuts; ramps impact 0.55 / dip 0.30'},
                   'camera': {'moves': 'push_in / pull_out, |ln s| 0.05-0.12', 'handheld': '0.003 W at 3 Hz', 'bounce': 0},
                   'effects': [['blur_bridge_hype',
                              0.62,
                              'pre 4 frames @30, sigma0 30 px, gain 0.7, zoom blur 0.25, flash on 60%'],
                              ['flash_white', '0.40-0.64', 'as blur_flow but longer on drops (3-4 frames)'],
                              ['radial_zoom_burst', 'hero .20', 'i4f 5.6 s'],
                              ['card_tilt_in', 'hero .20', 'i4f 24.8 / 32.8 s, m3lv 13.9 s'],
                              ['split_screen', 'hero .15', 'i4f 24.5 / 32.5 s - CURRENTLY DEGRADES TO A HARD CUT'],
                              ['white_bar_wipe', 'hero .15', 'zawB 10.13 / 14.67 s'],
                              ['shape_wipe', 'hero .10', ''],
                              ['invert_strobe', '0.38 in zawB', 'invert, white, black, reveal'],
                              ['music_card_intro',
                              'intro',
                              'album card 32% H, white 2% border, title + artist in wide thin caps, lyric line typed, '
                              'waveform line; 1.5-3.5 s; exit with radial_zoom_burst'],
                              ['bw_selective_outro',
                              'outro p .30 long',
                              'sat 0.15 except +-20 deg around the dominant warm hue, contrast +10%, 1 s fade - NOT '
                              'SCHEDULED TODAY']],
                   'grade': {'look': 'show_palette / warm_glow / high_key_pastel (zawB)',
                            'numbers': 'bloom on, strong vignette, letterbox sections'},
                   'text': 'big title words with letters_3d / slam animation on hero downbeats; Anton / Bebas / Poppins '
                           'Black; no emoji',
                   'audio': 'music only, -14 LUFS',
                   'never': [],
                   'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/i4f_cards.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/zaw_strip.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/i4f_strip_a.jpg'],
                   'gaps': ['split_screen',
                           'endings (bw_selective)',
                           'title_text (whole-frame zoom blur before the assembly)']},
'cinematic_slowmo': {'id': 'cinematic_slowmo',
                    'name': 'Cinematic Slow-Mo',
                    'group': 'general',
                    'weight': 0.08658,
                    'one_line': "The owner's favourite: crisp held shots, hard cut plus bloom bump every ~1 s, smooth slow "
                                'motion, words on black.',
                    'refs': ['_sX2EUexbW8', 'L2c39drDVuk'],
                    'kind': ['song'],
                    'signals': {'bpm': [76, 112], 'needs_drop': False, 'allow_no_drums': True, 'sharpness_p50_min': 600},
                    'clips': 'the crispest available: near-still hero frames and smooth slow-motion moments (hair '
                             'drifting, a turn, wind); one character per cut is fine, several shows allowed',
                    'song': 'ballad or 6/8 feel, 80-110 BPM; drumless is fine (this is the only preset that does not need '
                            'a drum grid); one clear emotional lift',
                    'length': '12-20 s Short',
                    'template': 'cinematic_slowmo',
                    'style': 'velocity_slowmo',
                    'look': 'warm_glow',
                    'format': {'aspect': '9:16', 'fps': 60, 'frame': 'rot90 (rotated 16:9) or letterbox_blur'},
                    'pacing': {'interval': 'one pulse of 0.9-1.1 s (a half bar of 6/8), steady',
                              'lead_ms': -67,
                              'hero_every': 'rare',
                              'drop': 'no drop device; the lift is a brighter bump'},
                    'speed': {'mode': 'slowmo / ramp',
                             'params': '0.35-0.6x on the moving shots, native on the held ones; hero holds 0.4-0.5x'},
                    'camera': {'moves': 'hold / slow push, |ln s| <= 0.05',
                              'handheld': 'NONE (_sX2 has none)',
                              'bounce': 0},
                    'effects': [['exposure_bump_hard',
                               0.85,
                               'bloom x(1 + 1.5 e^(-t/0.08)) and gain 1 + 0.25 e^(-t/0.08); incoming defocus 4 -> 0 px '
                               'over 100 ms; switch 1 frame early'],
                               ['blur_bridge_soft', 0.15, 'sigma0 10 px, gain 0.25, no directional blur, no entry'],
                               ['flash_white', 'hero .50', ''],
                               ['dissolve_blur', 'hero .50', ''],
                               ['text_on_black',
                               'intro',
                               'one lyric / title word at a time on black, neon glow, 0.35-0.8 s per word; enter with a '
                               'vertical stretch 3 -> 1 with motion blur over 2 frames, exit fade 4 frames'],
                               ['first_shot_entry',
                               'after the intro',
                               'defocus 20 -> 0 px and scale 1.10 -> 1.0 over 5 frames @30'],
                               ['fade_out', 'outro', 'linear 1.2 s picture fade, 1.5 s audio fade']],
                    'grade': {'look': 'warm_glow / cinematic',
                             'numbers': 'luma 100-145, hue 5-20 deg, highlight b +8..+15, bloom on every incoming frame'},
                    'text': 'lyric / title words on black for the intro only; nothing over the picture afterwards; Poppins '
                            'Black caps',
                    'audio': 'music only; 1.5 s audio fade at the end',
                    'never': [],
                    'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/_sX2EUexbW8_frames_339_389.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/L2c39drDVuk_cuts_0.jpg'],
                    'gaps': []},
'band_parade': {'id': 'band_parade',
               'name': 'Band Parade',
               'group': 'general',
               'weight': 0.03599,
               'one_line': 'A 16:9 band inside the vertical frame, one character per beat, the next one previewed as a '
                           'dark blurred slide.',
               'refs': ['GHBmP7aUoUc', 'JQHKYzYDneE'],
               'kind': ['song'],
               'signals': {'bpm': [105, 140], 'min_distinct_sources': 12, 'needs_drop': False},
               'clips': 'one clean shot per character from many different shows (a collection / parade), each visually '
                        'distinct',
               'song': 'bright 110-135 BPM with a strong steady pulse',
               'length': '10-25 s Short',
               'template': 'beat_collection_band',
               'style': 'beat_band',
               'look': 'source',
               'format': {'aspect': '9:16', 'fps': 60, 'frame': 'band16x9'},
               'pacing': {'interval': '1 beat, one character per beat',
                         'lead_ms': '+33 (LATE - the preview runs before the hard cut)',
                         'hero_every': 'none'},
               'speed': {'mode': 'normal', 'params': 'native speed; the clip itself carries the motion'},
               'camera': {'moves': 'static', 'handheld': 'NONE', 'bounce': 0},
               'effects': [['darken_slide_preview',
                          0.92,
                          '3 frames @30, x offsets [0.45, 0.25, 0.10] W, blur 25 px @1080, min/max blend mixed 0.9, no '
                          'camera on either shot'],
                          ['colour_cycle',
                          'JQH p .05',
                          'tint every half beat (red / green / blue gels, hue rotate 120 deg, 40% overlay) with a small '
                          'shake - NOT SCHEDULED TODAY'],
                          ['beat_light_pulse', 'title card', 'radial white light +60% luma per beat, decay 0.2 s']],
               'grade': {'look': 'source (minimal)', 'numbers': 'the band is on black; no bloom'},
               'text': 'character name per beat is allowed; watermark',
               'audio': 'music only, -14 LUFS',
               'never': [],
               'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/ghb_strip.jpg',
                           'C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/JQHKYzYDneE_cuts_0.jpg'],
               'gaps': ['colour_cycle']},
'cinematic_long': {'id': 'cinematic_long',
                  'name': 'Cinematic Long',
                  'group': 'general',
                  'weight': 0.0428,
                  'one_line': 'The full song at 16:9, section by section: card intro, letterboxed verses, accelerating '
                              'pre-chorus, flash drop, slow bridge, fade.',
                  'refs': ['(extrapolated from the 6 AE edits: ZkWQloS4jtM, cj12R_cW2ac, i4fSruIkkxk, S2rzMsl0N7k, '
                          'm3lv7H6cu_Y, zawBCooJwLw)'],
                  'kind': ['long'],
                  'signals': {'bpm': [78, 132], 'min_shots': 90, 'duration_s': [120, 240]},
                  'clips': 'a deep pool (100+ shots) from one show or one mood',
                  'song': 'a full song with clear sections; 80-130 BPM',
                  'length': '2-4 min 16:9',
                  'template': 'long_song',
                  'style': 'cinematic_warm',
                  'look': 'warm_glow',
                  'format': {'aspect': '16:9', 'fps': 30, 'frame': 'full with section letterbox'},
                  'pacing': {'interval': 'intro card or held shot 1-7 s; verse 1-2 beats with letterbox bars and no flash; '
                                        'pre-chorus 2 -> 1 -> 1/2 beat; drop flash cut on the downbeat then 4-5 cuts per '
                                        'bar; bridge 2-4 beats with slow motion; outro fade or B&W with a colour accent',
                            'lead_ms': -80,
                            'hero_every': '4 bars'},
                  'speed': {'mode': 'velocity u_shape; bridge slowmo 0.4-0.5x', 'params': 'as blur_flow'},
                  'camera': {'moves': 'as blur_flow', 'handheld': '0.003 W at 3 Hz', 'bounce': 0},
                  'effects': [['blur_bridge_soft', 'house', ''],
                             ['flash_white', 'hero .40', ''],
                             ['radial_zoom_burst', 'hero .20', ''],
                             ['card_tilt_in', 'hero .20', ''],
                             ['dip_black_strobe', 'hero .20', ''],
                             ['letterbox_sections', 'verses', 'bars 7-11% H animated in over 6 frames on a downbeat'],
                             ['music_card_intro', 'intro', ''],
                             ['bw_selective_outro', 'p .30', 'NOT SCHEDULED TODAY']],
                  'grade': {'look': 'warm_glow', 'numbers': 'as blur_flow'},
                  'text': 'song title on the intro card; nothing else unless word timings exist',
                  'audio': 'music only, -14 LUFS; render budget < 90 min on a 2-core runner',
                  'never': [],
                  'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/general/analysis/general_measure.json'],
                  'gaps': ['endings (bw_selective)', 'repeat_block (no cache: the 90 min budget is unproven)']},
'char_quick_velocity': {'id': 'char_quick_velocity',
                       'name': 'Character Quick Velocity',
                       'group': 'character',
                       'weight': 0.07879,
                       'one_line': 'One character, one house cut, one cut per beat, a chorus block replayed with new '
                                   'words.',
                       'refs': ['yUuiSey5kmE', 'KSjpckUTItU', 'TG_hmVvoWC4', 'YH_GH1nk1c0', 'mGz9Fm14UfU'],
                       'kind': ['character'],
                       'signals': {'bpm': [85, 140], 'needs_drop': True, 'one_character': True, 'min_shots': 16},
                       'clips': 'ONE character: 43% face close-ups, the rest waist-up then scenery; eye contact, smile, '
                                'hair in the wind, look-back, reaching hand, wink; no subtitles; nsfw / age gate applies',
                       'song': '85-140 BPM with drums on the beat and a clear +10..+15 dB drop at 22-57% of the length',
                       'length': '14-38 s Short',
                       'template': 'quick_velocity',
                       'style': 'character_aura',
                       'look': 'dark_contrast',
                       'format': {'aspect': '9:16',
                                 'fps': 60,
                                 'frame': 'window2x3 (p .55) / window3x4 (p .30) / full (p .15) with blurred bars'},
                       'pacing': {'interval': 'intro 0.5-1.0 cuts per bar; build 1.0-2.75; chorus 2.6-5.5. Pooled chorus: 1 '
                                             'beat 41%, 1/2 beat 14%, 2 beats 11%, 3+ 4%',
                                 'lead_ms': 'per transition: flash_zoom -90, mb_cut -85, whip_h -90, color_field -10, hard '
                                            '-33, spin -85 (default -65)',
                                 'hero_every': 'block start, max 2 devices per short',
                                 'structure': 'hook intro (1 hero shot, slow push, native speed) -> drop entry device -> 8 '
                                              'or 16 beat block -> replay with 0-30% swapped (p 0.6) -> one 3-5 beat hero '
                                              'hold at 0.4-0.5x -> end card 3-6 s'},
                       'speed': {'mode': 'character ramps',
                                'params': 'base U(0.5, 0.8) with impact (p .55) / dip (p .30) / flat (p .15); intros '
                                          'native; hero holds 0.4-0.5x'},
                       'camera': {'moves': 'per-shot continuous zoom |ln s| 0.07-0.51 easing out, alternating in / out; net '
                                          'roll 1-5 deg (p90 10)',
                                 'handheld': 'high-freq shake p90 0.016-0.046 frame widths',
                                 'bounce': 'NONE - every beat is a cut, the punch is the entry settle'},
                       'effects': [['flash_zoom',
                                  0.26,
                                  'gain 1 + 1.8 e^(-t/0.030) + 0.17 e^(-t/0.20), white 0.8 e^(-t/0.035) through a soft '
                                  'vignette, ln s A U(0.18,0.35) over 270 ms, roll +-3 deg'],
                                  ['mb_cut',
                                  0.22,
                                  'out ln s 0.105 -> 0.182 over 66 ms, in 0.132 settling tau 22 ms, 4 blurred frames @60'],
                                  ['whip_horizontal',
                                  0.1,
                                  'out 3/8/25% W over 3 frames, in 20/10/5/2/0% W - a smear CUT, never side by side'],
                                  ['zoom_through_color_field',
                                  0.08,
                                  '2-4 flat-colour frames, incoming at 1.46x with gain 0.72 settling by 133 ms'],
                                  ['impact_zoom_1frame', 0.07, 'ln s 0.15 e^(-t/0.055), roll 3 -> 0 tau 25 ms'],
                                  ['spin_blur',
                                  0.06,
                                  'incoming rolled 12-20 deg unwinding over 300 ms with rotational blur'],
                                  ['whip_vertical', 0.05, '15% then 45% H smear'],
                                  ['hard', 0.05, ''],
                                  ['mirror_tile_scale_in',
                                  0.04,
                                  'scale 0.40 -> 1.0 over 4 frames with mirror tiling and a white flash'],
                                  ['tint_flash', 0.04, 'palette colour at 0.75 / 0.45 / 0.2 over a blurred frame'],
                                  ['shape_wipe', 0.03, 'chevron / iris / wave / blob, block starts only'],
                                  ['card_tilt_in', 'block hero .30', ''],
                                  ['iris_wipe', 'block hero .15', ''],
                                  ['repeat_block', 'p 0.60', '8 or 16 beats replayed with 0-30% of shots swapped'],
                                  ['bar_open_reveal', 'opening .30', 'slit 8-10% H opening over 0.2-0.8 s, curved edge']],
                       'grade': {'look': 'dark_contrast .35 / teal_mono .25 / golden_bloom .20 / faded_film .20',
                                'numbers': 'teal_mono luma 115, p2 16, std 73, sat 151, hue 195 deg, vignette 0.43, Lab a '
                                           '-6.8 b -10.2. faded_film luma 128, std 50, sat 73, grain 0.71, vignette 0.69. '
                                           'golden_bloom luma 158, highlight b +20. dark_contrast luma 110-119, p2 0, std '
                                           '60-73, letterbox 6.1-6.7% each side. One look per video (per-shot std '
                                           '4.7-5.8)'},
                       'text': 'one sung word per beat (thin uppercase, cap 17% H, 85% white, letter type-on over 4-6 '
                               'frames with a blur trail) OR chunky 3D hook words at cap 25-35% H; NO dialogue captions; '
                               'watermark',
                       'audio': 'music only once the song is in; no whoosh or impact SFX; -14 LUFS',
                       'never': [],
                       'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_flash_zoom.jpg',
                                   'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_mb_cut.jpg',
                                   'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_zoom_through_color_field.jpg',
                                   'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_sync_trig.png'],
                       'gaps': ['lyric_word_per_beat (faster_whisper missing)',
                               'text_behind_subject (no matte model)',
                               'postcard_shot_layout (mGz9 cards unreachable)',
                               'card_tilt_in (the YH held card)',
                               'repeat_block (no cache)']},
'char_soft_romance': {'id': 'char_soft_romance',
                     'name': 'Character Soft Romance',
                     'group': 'character',
                     'weight': 0.03599,
                     'one_line': 'Half the cut rate, white-outs and vertical smears, pastel glow, hearts and tiny '
                                 'lowercase words. Adult characters only.',
                     'refs': ['eEPZ7fUpoE0', 'rfsjEohxVaw'],
                     'kind': ['character'],
                     'signals': {'bpm': [95, 132], 'allow_no_drums': True, 'age_min_18': True, 'framing': 'attractive'},
                     'clips': 'ONE adult character or a couple (the partner gets 35-45%); eye contact, smile, hair, '
                              'walking, reaching hand; attractiveness framing allowed only when the minimum listed age is '
                              '>= 18',
                     'song': 'soft 100-130 BPM, drums optional (rfsj has none - cuts go on phrases); warm or dreamy',
                     'length': '25-40 s Short',
                     'template': 'soft_romance',
                     'style': 'character_soft',
                     'look': 'pastel_pink',
                     'format': {'aspect': '9:16', 'fps': 60, 'frame': 'window3x4 / window2x3'},
                     'pacing': {'interval': '1 cut per 2 beats (p .70), 1 beat (p .15), 4 beats (p .15); phrase-level when '
                                           'the song has no drums',
                               'lead_ms': 'white_out -75, whip_vertical -60, impact_zoom -60, defocus_dissolve -120, '
                                          'dip_black -100',
                               'hero_every': 'block start'},
                     'speed': {'mode': 'character ramps',
                              'params': 'base 0.5-0.8x, 40% of chorus time on smooth every-frame motion; hero holds '
                                        '0.4-0.5x'},
                     'camera': {'moves': 'pull_out 74% of the time (eEPZ), |ln s| 0.11-0.17',
                               'handheld': '0.004 frame widths (much calmer)',
                               'bounce': 'NONE'},
                     'effects': [['defocus_dissolve',
                                0.25,
                                '5-8 frames @30, defocus peak 0.8% W at a=0.5, +8% exposure at the midpoint'],
                                ['white_out',
                                0.22,
                                'out 230 ms sigma 0.07 -> 0.4% W and gain -> 1.23; 4-5 white frames; in white 1 -> 0 over '
                                '300 ms, ln s 0.25 pulling out over 400 ms'],
                                ['impact_zoom_1frame', 0.15, ''],
                                ['whip_vertical', 0.12, 'out 15% then 45% H with gain 1.23'],
                                ['hard', 0.12, ''],
                                ['dip_black', 0.08, 'phrase ends only'],
                                ['glow_cut', 0.06, 'bloom 0 -> 0.8 -> 0.3 over 150 ms'],
                                ['shape_wipe', 'hero .25', ''],
                                ['iris_wipe', 'hero .25', ''],
                                ['heart_mask',
                                'section device',
                                '2-3 hearts 35% W with 2% W white stroke and 1% W glow on black with gold dust, drifting '
                                '5-10% W/s, rotating +-8 deg, ending with one heart scaling to fill into a white_out - '
                                'UNREACHABLE TODAY'],
                                ['light_streak_text',
                                'section device',
                                'gold #F5D58A 3 px core + 18 px glow along a Bezier over 0.4 s, the tiny word fading in '
                                'where it passes, ending on a heart outline - NOT SCHEDULED TODAY'],
                                ['bar_open_reveal', 'opening', 'curved bars over 0.8 s']],
                     'grade': {'look': 'pastel_pink .60 / soft_glow .40',
                              'numbers': 'pastel_pink luma 156, p2 27, p98 235, std 70, sat 50, hue 330 deg, vignette '
                                         '0.95, Lab a +7.7, crisp. soft_glow luma 99, p98 194, sat 74, highlights roll '
                                         'off, diffused (lapvar 109)'},
                     'text': 'tiny lowercase lines at 2.5% of H with a light-streak write-on; hearts and small doodles; no '
                             'dialogue captions',
                     'audio': 'music only; -14 LUFS',
                     'never': [],
                     'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_white_out_dissolve.jpg',
                                 'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_vertical_whip.jpg',
                                 'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_heart_mask.jpg',
                                 'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_light_streak_text.jpg'],
                     'gaps': ['heart_mask', 'light_streak_text']},
'char_raw_crisp': {'id': 'char_raw_crisp',
                  'name': 'Character Raw Crisp',
                  'group': 'character',
                  'weight': 0.02918,
                  'one_line': 'Native 60 fps, hard cuts and white flashes on the kicks, vivid and very sharp, no text at '
                              'all.',
                  'refs': ['JDguApf568Q'],
                  'kind': ['character'],
                  'signals': {'bpm': [118, 142], 'sharpness_p50_min': 1200, 'one_character': True},
                  'clips': 'the sharpest available (4K sources); ONE character; strong poses and action',
                  'song': 'hard 120-140 BPM with loud kicks',
                  'length': '10-18 s Short',
                  'template': 'raw_crisp',
                  'style': 'character_raw',
                  'look': 'crisp_vivid',
                  'format': {'aspect': '9:16', 'fps': 60, 'frame': 'window2x3'},
                  'pacing': {'interval': '1 beat',
                            'lead_ms': 'hard -33; white_flash_cut 0 (the white starts ON the kick and the shot appears '
                                       '100-120 ms later)',
                            'hero_every': 'none'},
                  'speed': {'mode': 'normal (native frames)', 'params': 'no interpolation, no slow motion'},
                  'camera': {'moves': 'minimal, |ln s| <= 0.07', 'handheld': 'none', 'bounce': 'NONE'},
                  'effects': [['hard', 0.45, ''],
                             ['white_flash_cut',
                             0.45,
                             '2 white frames @60 on the kick, then gain 1.6 -> 1.0 with tau 35 ms; no blur, no camera'],
                             ['dip_black', 0.05, ''],
                             ['mb_cut', 0.05, ''],
                             ['white_out', 'once', 'JDgu 4.3-5.6 s']],
                  'grade': {'look': 'crisp_vivid',
                           'numbers': 'luma 138, p2 18, p98 252, std 70, sat 139, no vignette, sharpness 2610, grain 0.27'},
                  'text': 'NONE except the watermark',
                  'audio': 'music only, -14 LUFS',
                  'never': [],
                  'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/JDguApf568Q_cuts_0.jpg',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/character/analysis/char_fx_white_out_dissolve.jpg'],
                  'gaps': []},
'dance_burst_parade': {'id': 'dance_burst_parade',
                      'name': 'Dance Burst Parade',
                      'group': 'dance',
                      'weight': 0.06518,
                      'one_line': "The owner's dance example: velocity slow on the kick with a 3x burst just before it, "
                                  'one punch per beat, a dancer per bar.',
                      'refs': ['Uf_4iSx6r3g'],
                      'kind': ['dance'],
                      'signals': {'bpm': [85, 125], 'four_on_floor': True, 'min_take_s': 4, 'needs_periodic_motion': True},
                      'clips': 'creditless ED / OP dance sequences and in-episode dance scenes; ONE dancer per shot, full '
                               'body or waist-up facing the camera, bright clean background, static camera, periodic '
                               'motion (period 0.4-1.0 s, autocorrelation >= 0.2); no dialogue, no subtitles, no credits',
                      'song': 'groovy four-on-the-floor remix at 85-125 BPM (the references use Aaron Smith "Dancin" Krono '
                              'Remix at native, x1.04 and x0.85)',
                      'length': '10-15 s Short, 4-8 bars, loopable',
                      'template': 'short_cute_parade',
                      'style': 'dance_sync',
                      'look': 'cute',
                      'format': {'aspect': '9:16',
                                'fps': 60,
                                'frame': 'full (a full-height 9:16 crop, 31.6% of the source width, centred on the dancer; '
                                         'NEVER a letterbox band)'},
                      'pacing': {'interval': 'one dancer per bar for bars 1-3, then a hero take for bars 4-7, bar 8 at 0.5x',
                                'lead_ms': -60,
                                'dancer_switch': '-114 ms (about 0.3 beat before the downbeat)',
                                'hero_every': 'each dancer switch'},
                      'speed': {'mode': 'velocity burst_land',
                               'params': 'speed(phi) = 0.5 + 2.5 exp(-0.5((phi - 0.85)/0.12)^2); base 0.43-0.57x, per-beat '
                                         'max 3.19x at phase 0.79 (60-100 ms before the kick), FWHM 0.28 beat, 1.19 beats '
                                         'of source dance per beat; the pose change happens inside the burst and the new '
                                         'pose is held on the kick'},
                      'camera': {'moves': 'static crop (drifts < 1 px over 8 s)',
                                'handheld': 'NONE',
                                'bounce': 'NONE - the accent is a discrete punch, not a bounce'},
                      'effects': [['zoom_in_punch',
                                 0.45,
                                 'x1.41-1.61 (median 1.44) centred on the SUBJECT, log scale halving each frame (1.44 -> '
                                 '1.23 -> 1.11 -> 1.05 -> 1.01), gone in 5 frames (83 ms), heavy motion blur on the first '
                                 '2'],
                                 ['vslide_wrap',
                                 0.2,
                                 'dy_k = 0.45 H x 0.45^k over 4 frames, wrap-around, alternating up / down'],
                                 ['zoom_out_punch', 0.15, 'x0.91-0.93 over 2-4 frames'],
                                 ['rotate_punch',
                                 0.07,
                                 '26 -> 13 -> 6.3 -> 2.4 -> 0.4 deg per frame plus a 0.22 fw vertical offset'],
                                 ['blur_pulse', 0.08, 'defocus [1.0, 0.6, 0.3, 0.1], luma +8% on frame 0'],
                                 ['flash_pulse', 0.05, 'img x(1 + 0.35 x 0.45^k) + 20 x 0.45^k over 3 frames'],
                                 ['hard', 'cut 0.28', 'plus a 1.18^(0.55^k) settle on 30% of them - NOT SCHEDULED TODAY'],
                                 ['defocus_in',
                                 'cut 0.20',
                                 'out 4 frames x1 -> 1.35 with defocus 0 -> 0.6; in [1, .9, .6, .3, .1] at 2% W'],
                                 ['zoom_blur_in',
                                 'cut 0.14',
                                 'out zoom blur + x1.12 over 2 frames; in 1.22^(0.75^k) with zoom blur 0.25 x 0.5^k'],
                                 ['flash_white',
                                 'cut 0.10',
                                 'glow wipe from a corner 3 frames before, 3 pure white frames on the kick, then x1.8 -> '
                                 'x1.0'],
                                 ['flash_in', 'cut 0.08', ''],
                                 ['cross_dissolve', 'cut 0.08', ''],
                                 ['whip_diag',
                                 'cut 0.04',
                                 'tiled copies at 0.3 / 0.4 / 0.2 W along 45 deg with 25% W blur, then a 1-frame '
                                 'crossfade'],
                                 ['pattern_flash',
                                 'cut 0.03',
                                 '2 frames of lavender then pink argyle, then defocus [0.8, 0.6, 0.35, 0.15] with a x1.07 '
                                 'settle'],
                                 ['dark_blur_in', 'cut 0.03', ''],
                                 ['lens_zoomthrough', 'cut 0.02', ''],
                                 ['slowmo_ending', 'bar 8', '0.5x for the last bar']],
                      'grade': {'look': 'cute',
                               'numbers': 'tone curve unchanged (quantiles within 3 levels), saturation x1.33 (chroma '
                                          'x1.31), cas 0.6, NO vignette, NO grain, NO gradient overlay'},
                      'text': 'NONE except the watermark (no lyrics, no captions, no emoji)',
                      'audio': 'music only: no clip sound and no SFX in any reference; -14 LUFS, TP < -1 dBTP',
                      'never': [],
                      'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/dance/analysis/Uf_4iSx6r3g_chika_remap.png',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/uf_zoom_punch_f488.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/uf_rotate_punch_f276.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/uf_vslide_shake_f519.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/uf_pattern_flash_cut_f306.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/uf_velocity_beats_f480.jpg'],
                      'gaps': ['dance_punches (punches warp about the frame centre, not the subject; the safety rule needs '
                              'a face centre)',
                              'entry_settle (hard_settle is in no pool)']},
'dance_smooth_velocity': {'id': 'dance_smooth_velocity',
                         'name': 'Dance Smooth Velocity',
                         'group': 'dance',
                         'weight': 0.0428,
                         'one_line': 'One long hero take on a smooth cosine velocity with a follow-pan, blur entrances, '
                                     'breathing intro, freeze ending.',
                         'refs': ['ONlH6QaPl-8', '91tpBcDvHwQ'],
                         'kind': ['dance'],
                         'signals': {'bpm': [95, 130], 'min_take_s': 8, 'needs_periodic_motion': True},
                         'clips': 'ONE continuous dance take of at least 6-8 s (16 bars is better), one dancer centred, '
                                  'static source camera',
                         'song': 'the same groovy remix family, 100-125 BPM; works when the song has a long unbroken hook',
                         'length': '15-45 s Short (the breathing intro only for >= 30 s)',
                         'template': 'short_smooth_velocity',
                         'style': 'dance_smooth',
                         'look': 'velocity_dark',
                         'format': {'aspect': '9:16 or 1:1', 'fps': 60, 'frame': 'full'},
                         'pacing': {'interval': 'blur entrances every 1-2 beats after the hero take',
                                   'lead_ms': -60,
                                   'hero_every': 'section'},
                         'speed': {'mode': 'velocity smooth_wave',
                                  'params': 'speed(phi) = 1.2 + 0.5 cos(2 pi (phi + 0.1)); 1.6-1.7x (p75 2.0) for the half '
                                            'beat after the kick and 0.57-0.86x for the half before, mean 1.18; intro at '
                                            '0.51-0.65x; 91tp variant is a flat 1.35x'},
                         'camera': {'moves': 'follow-pan: track the dancer at 2 Hz, eased 2-3 s moves, clamped to <= 12% of '
                                            'the source width, never during a punch',
                                   'handheld': 'NONE',
                                   'bounce': 'NONE - ONlH has no camera punches at all'},
                         'effects': [['breath_fade',
                                    'intro',
                                    'per 2 bars: fade-in 1.0-1.6 s (luma x0 -> x1, defocus 1 -> 0, ease-io), hold ~1 s, '
                                    'fade-out 0.5-1.2 s, black 0.3-1.0 s'],
                                    ['defocus_in', 'house', 'blur entrances into close-ups every 1-2 beats'],
                                    ['dark_blur_in',
                                    'section change',
                                    'one near-black frame (luma 22) with faint specks, then exposure [.32 .57 .75 .89 .98] '
                                    'while defocused 1.0 -> 0.3'],
                                    ['flash_white', 'hero', ''],
                                    ['freeze_end', 'outro', 'hold one frame of a wide shot for 1.5-2.0 s']],
                         'grade': {'look': 'velocity_dark',
                                  'numbers': "curves all='0/0 0.1/0.01 0.25/0.05 0.55/0.4 0.9/1 1/1', saturation x1.8-2.1, "
                                             'vignette -13.5% at the edges, colorbalance bs=0.03 (cool shadows), cas 0.8'},
                         'text': 'watermark only (ONlH: bold white with an orange glow, bottom centre)',
                         'audio': 'music only, -14 LUFS',
                         'never': [],
                         'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/dance/analysis/ONlH6QaPl-8_chika_remap.png',
                                     'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/onlh_intro_breath_fades.jpg',
                                     'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/onlh_hair_closeups_blur_f2160.jpg',
                                     'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/91tp_dark_blur_in_f328.jpg'],
                         'gaps': ['follow_crop ("never move during a punch" is not enforced)']},
'dance_raw': {'id': 'dance_raw',
             'name': 'Dance Raw',
             'group': 'dance',
             'weight': 0.0214,
             'one_line': 'Four shots, three hard cuts, no effects at all. The dance and the song carry it.',
             'refs': ['Melqn2cOdlY'],
             'kind': ['dance'],
             'signals': {'bpm': [80, 105], 'min_take_s': 10, 'quality_first': True},
             'clips': 'ONE outstanding dance take (a practice video or a clean ED), 4 usable angles / sections',
             'song': '85-100 BPM with a strong groove; use it only when the dance itself fits the song',
             'length': '10-14 s Short',
             'template': 'short_raw',
             'style': 'dance_sync',
             'look': 'source',
             'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full'},
             'pacing': {'interval': 'one bar per shot', 'lead_ms': -181, 'hero_every': 'none'},
             'speed': {'mode': 'normal', 'params': 'source speed, no ramps, no interpolation'},
             'camera': {'moves': 'static', 'handheld': 'NONE', 'bounce': 'NONE'},
             'effects': [['hard', 1.0, 'nothing else']],
             'grade': {'look': 'source', 'numbers': 'the source look is kept'},
             'text': 'watermark only',
             'audio': 'music only, -14 LUFS',
             'never': [],
             'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/melqn_cut_f84.jpg',
                         'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/melqn_cut_f166.jpg'],
             'gaps': []},
'dance_meme': {'id': 'dance_meme',
              'name': 'Dance Meme Payoff',
              'group': 'dance',
              'weight': 0.0214,
              'one_line': '4 beats of setup, 4 beats of half-beat tension cuts, 8 beats of payoff dance in one take.',
              'refs': ['vnCJ-MhrY-0'],
              'kind': ['dance'],
              'signals': {'bpm': [90, 115], 'needs_reaction_shot': True, 'min_take_s': 4},
              'clips': 'a reaction shot, 5-6 tight close-ups of the same character (details: eyes, hands, earring), and '
                       'one sway / dance take',
              'song': '95-110 BPM with a clear 4-bar build into a payoff',
              'length': '9-12 s Short',
              'template': 'short_raw',
              'style': 'dance_sync',
              'look': 'source',
              'format': {'aspect': '9:16 or 1:1', 'fps': 60, 'frame': 'full'},
              'pacing': {'interval': 'setup 4 beats on one shot, tension 1/2 beat x6, payoff one take of 8 beats',
                        'lead_ms': -60,
                        'hero_every': 'the payoff entry'},
              'speed': {'mode': 'normal, interpolated to every frame', 'params': 'duplicate ratio ~0, strong sharpening'},
              'camera': {'moves': 'static', 'handheld': 'NONE', 'bounce': 'NONE'},
              'effects': [['hard', 0.7, ''], ['defocus_in', 0.2, ''], ['zoom_blur_in', 0.1, '']],
              'grade': {'look': 'source with added sharpening', 'numbers': '2.2-3.9x the source Laplacian variance'},
              'text': 'watermark only',
              'audio': 'music only, -14 LUFS',
              'never': [],
              'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/vncj_cuts_f70.jpg',
                          'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/vncj_sway_f146.jpg'],
              'gaps': ['no planner template: planners.STYLE has short_cute_parade / short_smooth_velocity / short_raw but '
                      'no short_meme']},
'dance_long_amv': {'id': 'dance_long_amv',
                  'name': 'Dance Long AMV',
                  'group': 'dance',
                  'weight': 0.03599,
                  'one_line': '16:9 dance mix: vignette fade-in, one cut per beat in the hooks, the hook block copied '
                              'exactly, 3D title, lens zoom-through, fade out.',
                  'refs': ['1L7mzyGhXCA'],
                  'kind': ['long', 'dance'],
                  'signals': {'bpm': [110, 128], 'duration_s': [120, 300], 'min_shots': 60},
                  'clips': 'many dance takes (about 40% group or couple shots are fine here), atmosphere shots for the '
                           'intro',
                  'song': '115-125 BPM dance mix with clear hooks; 2-5 min',
                  'length': '2-5 min 16:9',
                  'template': 'long_dance',
                  'style': 'dance_amv',
                  'look': 'amv_bloom',
                  'format': {'aspect': '16:9', 'fps': 30, 'frame': 'full'},
                  'pacing': {'interval': 'hook intervals 31% one beat, 22% 1.5, 21% half or less, 14% 3/4, 11% 2-4 beats; '
                                        'intro ~2 cuts per bar',
                            'lead_ms': -60,
                            'transition_window': 'starts -0.18 beat, switches at -0.12, settles at +0.08 (+40 ms)',
                            'structure': 'intro 4 bars -> hook (title card on its first downbeat) -> verse with velocity '
                                         "hero takes -> hook B repeats hook A's cut pattern with new clips -> slow-motion "
                                         'bridge -> final hook -> one-take outro -> 1.5 s fade'},
                  'speed': {'mode': 'velocity u_shape / hero takes',
                           'params': 'incoming shots settle from x1.13-1.26 in 3-4 frames'},
                  'camera': {'moves': 'light push / drift', 'handheld': 'NONE', 'bounce': 'NONE'},
                  'effects': [['vignette_fade_in',
                             'intro',
                             'exposure 0 -> 1 over 2.4 s with corners x0.1 -> x0.7, relaxing to x0.85 over 1 bar'],
                             ['defocus_in',
                             'house',
                             'outgoing exposure 98 -> 135 over 4 frames, incoming defocused 3-4 frames'],
                             ['flash_white',
                             'hero .70',
                             'frames 372-374 pure white centred on the kick, glow ramp at 367-368, incoming blown out and '
                             'blurred 375-377'],
                             ['lens_zoomthrough',
                             'hero .30',
                             'into the outro: zoom blur 0.5 + x2 exposure, barrel k1 0.25 -> 0 with chromatic aberration, '
                             '9 deg roll, x1.35 -> 1.0'],
                             ['title_3d_letters',
                             'once per hook',
                             'glyphs extruded 8 px, entering from random positions with rotation 60-120 deg and scale 2 -> '
                             '1 over 0.4 s staggered 3 frames; a WHOLE-FRAME zoom blur 2 frames before the assembly beat; '
                             'letters settle on the kick'],
                             ['particles', 'bokeh', 'purple bokeh overlay'],
                             ['repeat_block',
                             'hook B',
                             'the same cut pattern with new clips - NOT IMPLEMENTED in plan_long_edit'],
                             ['fade_out_black', 'outro', '1.5 s']],
                  'grade': {'look': 'amv_bloom',
                           'numbers': 'lifted blacks (p2 48), highlights > 0.7 blurred sigma 24 screen-blended 0.4, '
                                      'overexposure accents on transitions, vignette only in the intro'},
                  'text': '3D name title once per hook; watermark',
                  'audio': 'music only, -14 LUFS; render budget < 90 min on a 2-core runner',
                  'never': [],
                  'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/1L7_intro_vignette_fade.jpg',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/1L7_flash_white_f372.jpg',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/1L7_flash_title_run_f366.jpg',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/1L7_lens_zoomthrough_f1137.jpg',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/dance/sheets/1L7_outro_fade.jpg'],
                  'gaps': ['repeat_block (plan_long_edit has no block replay)',
                          'title_text (no whole-frame zoom blur before the assembly)']},
'kpop_postcard_drop': {'id': 'kpop_postcard_drop',
                      'name': 'K-pop Postcard Drop',
                      'group': 'kpop',
                      'weight': 0.05739,
                      'one_line': "The owner's K-pop favourite: four photo cards over black, the drop clip develops out of "
                                  'photo paper, zooms to fill and the grade pops on the beat.',
                      'refs': ['mFHp6Dd2ISk'],
                      'kind': ['kpop'],
                      'signals': {'bpm': [85, 115],
                                 'needs_drop': True,
                                 'drums_share_min': 0.5,
                                 'min_stills': 3,
                                 'one_member': True},
                      'clips': 'ONE idol in every beat-section shot: MV frames, stage close-ups, fancams, pictorial stills '
                               'and concept films. 60% CU/ECU, 30% MS, 10% FS/WS. Posing, hair flips, eye contact, wink, '
                               'hand-to-camera. Needs 3-4 pictorial STILLS for the cards. No dialogue, no talking heads, '
                               'no subtitles (reject news lower-thirds). Consecutive shots alternate backdrop and '
                               'brightness (mean |dLuma| 0.12)',
                      'song': 'slowed / "super slowed" edit audio with heavy drums (drum stem 51-80% of energy), 85-115 '
                              'BPM, a quiet 4-9 s hook then a drop on a downbeat',
                      'length': '11-20 s Short',
                      'template': 'postcard_member_edit',
                      'style': 'postcard_kpop',
                      'look': 'kpop_crisp',
                      'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full'},
                      'pacing': {'interval': 'quiet bars 0-1 cut, loud bars 2-4 (1 beat 60%, 2 beats 35%, 3 beats 5%); 4 of '
                                            '11 cuts on downbeats (all section starts)',
                                'lead_ms': 'blur cuts -40 (3 frames @60 before the attack so the incoming blur peaks on '
                                           'it); HERO cuts sit ON the attack; the dip lands +30 ms',
                                'hero_every': 'section start; the last beat before a section cut carries a preview of the '
                                              'next clip'},
                      'speed': {'mode': 'velocity kpop_u',
                               'params': 'lo 0.45, hi 1.2, tau 0.18 (edges 2-3x faster than the middle); duplicate ratio '
                                         '0.00-0.13 = smooth interpolated slow motion'},
                      'camera': {'moves': 'entry settle 70% punch-in / 30% pull-out, A 0.12-0.45 (median 0.25), tau 0.085 '
                                         's; pre-cut pull-back 0.84-0.92 per 4 frames over the last 6-8 frames',
                                'handheld': 'NONE',
                                'bounce': 'NONE - scale change is about 1.00 per 4 frames in the middle of a 2-beat shot'},
                      'effects': [['postcard_intro',
                                 'intro',
                                 '4 cards 0.614 W with a 1.8% white border and a top-to-bottom shade x1.0 -> x0.65, no '
                                 'shadow, over black with purple / blue particles. Entries in-out sine 1.35-1.45 s from '
                                 'fully off-frame, exits in-quad 0.77-0.87 s extrapolated, NO holds, a card every 1.45-1.6 '
                                 's free-running. Card 4 is the drop clip already playing, developing out of light-grey '
                                 'paper (a = (1-u)^1.15 over 4.80-5.70 s) then zooming 0.44 -> 1.09 W over 5.00-5.93 s and '
                                 'straightening +7.8 -> -1.1 deg. Motion blur 0.55 x displacement per frame'],
                                 ['caption_bar',
                                 'intro',
                                 'white #FEFEFE bar 0.069 H, dark #171110 uppercase heavy sans at cap 0.026 H; down over '
                                 '0.10-0.60 s, up over 4.98-5.53 s'],
                                 ['grade_pop',
                                 'on the drop',
                                 "flat f' = 0.085 + 0.93 f^0.8 with sat x0.53 -> kpop_crisp in ONE frame; no cut, no "
                                 'flash, no blur'],
                                 ['blur_in_punch',
                                 'house 0.50',
                                 'out 8 frames sigma 0.11 -> 0.40% W; in sigma_k = 0.0050 W (1-k/15)^0.9 over 15 frames; '
                                 'gain 1 + 0.10 e^(-t/0.1); entry settle'],
                                 ['vslide_wrap',
                                 'house 0.20',
                                 'dy = 0.20 H (1 - ease_out(k/8)) over 8 frames @60 with vertical blur and a 4% punch'],
                                 ['hard_punch', 'house 0.15', 'settle only, A U(0.08, 0.3), cut 25-33 ms early'],
                                 ['cutout_prereveal',
                                 'hero 0.40',
                                 'person matte of the NEXT shot as a white-outlined sticker rising from +0.6 H ease-out '
                                 'over 15 frames, background defocusing to 0.8% W and dimming to x0.75 over 0.4 s; starts '
                                 '0.30-0.37 s before the cut - GRABCUT ONLY TODAY'],
                                 ['slices_reveal',
                                 'hero 0.15',
                                 '3 slices 0.45 W x 0.13 H over eyes / mouth / chin, popping on 8th notes, 1 white frame '
                                 'each'],
                                 ['photo_clones',
                                 'hero 0.15',
                                 '3 tilted 4:5 stills at 0.35-0.45 W, rotation +-(12..30) deg, popping on 8th notes at the '
                                 'corners'],
                                 ['zoom_blur_flash',
                                 'hero 0.15',
                                 'out 9 frames x1 -> 1.12 with zoom blur 0 -> 0.25; in punch A 0.45 with zoom blur 0.30 -> '
                                 '0 and gain 1.14 -> 1'],
                                 ['peek_window',
                                 'hero (mF 9.97 s)',
                                 '0.38 W x 0.31 H tilted window over the lower face showing the next clip, white-tinted '
                                 'for 3 frames - IN NO POOL TODAY'],
                                 ['exposure_bleach',
                                 'hero modifier',
                                 'out gain 1 + G u^2 (G 0.2-0.35) with sat 1 - D u^2 (D 0.3-0.5); in gain 1 + G_in '
                                 '(1-j/M)^2, sat recovering; peak luma 0.76 - NEVER a solid white frame; <= 1 per 4 bars'],
                                 ['dip_black',
                                 'outro',
                                 'fade [.92 .82 .69 .53 .34 .17 .03], 1-2 black frames, fade in [.03 .14 .30 .48 .65 .79 '
                                 '.90 .99]']],
                      'grade': {'look': 'kpop_crisp',
                               'numbers': 'luma 0.35, std 0.31, p02 0.008, p98 0.99, white clip 9.5%, sat 0.46, '
                                          'colourfulness 40, edge p95 140 @1080, grain 0.46 (measured, not added). Neutral '
                                          'white balance, NO split toning, NO added grain, NO vignette, NO RGB split (max '
                                          'R/B misalignment 0.5 px at 180 px wide)'},
                      'text': 'the meme caption bar in the intro; nothing over the beat section except the watermark; '
                              'Latin fonts only',
                      'audio': 'music only (no clip sound, no dialogue, no SFX in any of the five references); -14 LUFS, '
                               'TP < -1 dBTP',
                      'never': [],
                      'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_postcard_model_vs_ref.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_card_entries.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_intro.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_intro2.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_tr1.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_tr2.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_pair1.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_pair2.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_ov1.jpg',
                                  'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/mf_ov2.jpg'],
                      'gaps': ['cutout_prereveal (GrabCut matte)',
                              'peek_window (in no pool)',
                              'per_shot_zoom (no pre-cut pull-back outside blur_in_punch)']},
'kpop_stage_push': {'id': 'kpop_stage_push',
                   'name': 'K-pop Stage Push',
                   'group': 'kpop',
                   'weight': 0.03599,
                   'one_line': 'A group wide shot accelerating into a zoom-through onto the member, a scrolling name, '
                               'stickers on a wink, hard punch cuts, a dip chain outro.',
                   'refs': ['upHhDIO0B0g'],
                   'kind': ['kpop'],
                   'signals': {'bpm': [95, 118], 'needs_group_shot': True, 'needs_drop': True, 'one_member': True},
                   'clips': "a group establishing / stage wide shot PLUS one member's close-ups; stage and MV mix; a news "
                            'lower-third must be rejected',
                   'song': 'same family, 95-115 BPM, drums-heavy, drop on a downbeat',
                   'length': '11-16 s Short',
                   'template': 'stage_mv_member_edit',
                   'style': 'kpop_stage',
                   'look': 'kpop_crisp',
                   'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full'},
                   'pacing': {'interval': '1-2 beat shots (1,1,2,2,2,1,1,1,1)',
                             'lead_ms': 'hard -33 (weak attacks -20..-30, loud snares -100..-120); zoom_through / cutout '
                                        'on the beat',
                             'hero_every': 'section; 3 of 10 cuts on downbeats'},
                   'speed': {'mode': 'velocity kpop_u',
                            'params': 'lo 0.45, hi 1.2, tau 0.18; motion bins 1.36 .56 .54 .71 1.84'},
                   'camera': {'moves': 'establishing push ln s = 0.098 t + 0.094 t^2; entry settle A 0.05-0.30 in the first '
                                      '4 frames',
                             'handheld': 'NONE',
                             'bounce': 'NONE'},
                   'effects': [['zoom_through',
                              'hero 0.25',
                              'last 12 frames zoom blur 0 -> 0.35 and +20% extra scale; incoming 1.3 -> 1.0 over 13 '
                              'frames, roll 3 -> 0, sigma 0.28% W held 8 frames'],
                              ['hard_punch', 'house 0.60', 'cut 33 ms early, settle 5-30% over the first 4 frames'],
                              ['blur_in_punch', 'house 0.40', 'peak sigma 0.28% W held ~8 frames, gone by frame 16'],
                              ['cutout_prereveal',
                              'hero 0.50',
                              "white-outlined cutout of the next shot's subject pops in 0.3 s before the cut; the cut then "
                              'bleaches to gain 1.34 and sat x0.51'],
                              ['zoom_blur_flash', 'hero 0.25', ''],
                              ['exposure_bleach',
                              'hero modifier',
                              'upHh 6.30: out luma x1.00 -> x1.35 and sat x1.00 -> x0.51 over 11 frames; in sat 0.10 -> '
                              'settled over 14 frames'],
                              ['kinetic_name',
                              'hero close-up',
                              'huge purple condensed italic #9B30D9 with a 6 px dark stroke, letter height 0.083 H at y '
                              '0.80-0.92 H, scrolling 0.17 W/s'],
                              ['stickers',
                              'on a wink 0.3 s',
                              'hearts / heart eyes / sparkles, then eye lightning at 7.93-8.33 s'],
                              ['dip_black', 'outro x3', 'one beat apart: 7-8 frames out, 1-2 black, 8-9 frames in']],
                   'grade': {'look': 'kpop_crisp',
                            'numbers': 'luma 0.44, std 0.26, p02 0.055, p98 0.94, white clip 10.5%, sat 0.41, '
                                       'colourfulness 64, edge p95 177 @1080; highlights slightly warm'},
                   'text': 'the scrolling group name; no captions over the beat section; watermark',
                   'audio': 'music only, -14 LUFS',
                   'never': [],
                   'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_ov1.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_tr1.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_tr2.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_ovl.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_a.jpg',
                               'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/up_b.jpg'],
                   'gaps': ['cutout_prereveal (GrabCut matte)',
                           'stickers (no face / eye detector - the lightning will not start at an eye)']},
'kpop_meme_reveal': {'id': 'kpop_meme_reveal',
                    'name': 'K-pop Meme Reveal',
                    'group': 'kpop',
                    'weight': 0.0214,
                    'one_line': 'A 1:1 picture band, near-still comparison shots with a white label, then a glowing cutout '
                                'rises and part 2 cuts on every beat as vertical wrap slides.',
                    'refs': ['PsdUhnuh-AE'],
                    'kind': ['kpop'],
                    'signals': {'bpm': [100, 122], 'needs_drop': True, 'two_part': True},
                    'clips': 'part 1 needs 5-6 near-still portrait close-ups of one theme, part 2 needs 6+ beat-section '
                             'shots of the payoff subject',
                    'song': '105-120 BPM with a clear downbeat reveal point',
                    'length': '11-15 s Short',
                    'template': 'single_clip_trend',
                    'style': 'kpop_trend',
                    'look': 'tint_hue_push',
                    'format': {'aspect': '9:16',
                              'fps': 60,
                              'frame': 'band1x1 (1:1 picture band, black outside, the band bottom fading to black with a '
                                       'red glow)'},
                    'pacing': {'interval': 'part 1 hard cuts every ~1.3 s, free-running; part 2 one cut per beat',
                              'lead_ms': '-103 (the picture lands on the beat, -13..+100 ms)',
                              'hero_every': 'the reveal (1.4 s before the downbeat cut)'},
                    'speed': {'mode': 'freeze / normal',
                             'params': 'part 1 is 60-100% duplicate frames (near-freeze stills) with a slow +1-2%/s push; '
                                       'part 2 is normal'},
                    'camera': {'moves': 'slow push in part 1 (A 0.08, tau 0.25 s)', 'handheld': 'NONE', 'bounce': 'NONE'},
                    'effects': [['hard_punch', 'part 1', 'settle only, A 0.08 tau 0.25 s'],
                               ['cutout_prereveal',
                               'the reveal',
                               'a white-glow cutout rises from the bottom over the blurring, dimming shot for 1.4 s; keeps '
                               'rising out of the top in 6 frames as the next shot lands - GRABCUT ONLY TODAY'],
                               ['vslide_wrap',
                               'part 2 house',
                               'offset 0.2 H with wrap-around, vertically motion-blurred (equivalent sigma 0.20-0.22% W '
                               'for 4 frames @30), settled in 3-4 frames'],
                               ['center_label', 'both parts', 'white 2-line label turning red on the reveal downbeat']],
                    'grade': {'look': 'kpop_crisp (band)',
                             'numbers': 'luma 0.17 including the black band, std 0.23, p98 0.83, sat 0.70, colourfulness '
                                        '35'},
                    'text': 'a 2-line centre label (white, then red on the reveal); watermark',
                    'audio': 'music only, -14 LUFS',
                    'never': [],
                    'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/ps_ov1.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/ps_ov2.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/ps_tr1.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/ps_a.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/ps_slide.jpg'],
                    'gaps': ['cutout_prereveal (GrabCut matte)',
                            'delivery_layout (band1x1 has no bottom fade / red glow)']},
'kpop_single_take_smear': {'id': 'kpop_single_take_smear',
                          'name': 'K-pop Single Take Smear',
                          'group': 'kpop',
                          'weight': 0.0214,
                          'one_line': 'No cuts at all: one stage take, a freeze with total silence before the drop, then '
                                      'vertical smear accents on every loud onset.',
                          'refs': ['fSMk1eTrSX0'],
                          'kind': ['kpop'],
                          'signals': {'bpm': [95, 118], 'min_take_s': 8, 'needs_drop': True, 'single_clip': True},
                          'clips': 'ONE stage take or fancam of at least 8 s, well framed, no subtitles',
                          'song': 'a slowed edit with a hard drop and clear loud onsets afterwards, 95-115 BPM',
                          'length': '10-13 s Short',
                          'template': 'single_clip_trend',
                          'style': 'kpop_trend',
                          'look': 'tint_hue_push',
                          'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full or window3x4'},
                          'pacing': {'interval': 'NO CUTS; accents on loud onsets',
                                    'lead_ms': 'onset-locked (|offset| 32 ms)',
                                    'hero_every': 'n/a'},
                          'speed': {'mode': 'normal + freeze',
                                   'params': 'a 1.5 s freeze ending exactly on the drop downbeat'},
                          'camera': {'moves': 'static', 'handheld': 'NONE', 'bounce': 'NONE'},
                          'effects': [['freeze_silence',
                                     'before the drop',
                                     'the frame repeats for 1.5 s (2.6 beats) while the music is hard-muted with 5 ms '
                                     'ramps, resuming on the drop'],
                                     ['vsmear',
                                     '9 accents',
                                     'vertical kernel 0.6 H on the accent frame, 0.35-0.45 H plateau for 10 frames @60, '
                                     'snapping to 0 in 2 frames; vertical offset up to 17% W decaying in 3 frames'],
                                     ['echo_ghost', '~2 bars', 'the frame at x1.35 shifted -0.15 H at 35% opacity'],
                                     ['fade_in_out', 'ends', 'fade in 0.5-1.0 s, fade out 0.3-1.1 s, linear']],
                          'grade': {'look': 'monochrome red (tint_hue_push)',
                                   'numbers': 'luma 0.24, std 0.16, p98 0.75, sat 0.67, colourfulness 75, edge p95 89; a '
                                              'red edge glow throughout'},
                          'text': 'watermark only',
                          'audio': 'music only, with a hard mute for the freeze window; -14 LUFS',
                          'never': [],
                          'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/fs_ov1.jpg',
                                      'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/fs_d1.jpg',
                                      'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/fs_d2.jpg',
                                      'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/fs_fx.jpg'],
                          'gaps': []},
'kpop_text_strobe': {'id': 'kpop_text_strobe',
                    'name': 'K-pop Text Strobe',
                    'group': 'kpop',
                    'weight': 0.0214,
                    'one_line': 'Six and a half seconds of words on black timed to the vocal, then one fancam clip '
                                'strobing on the 8th-note grid.',
                    'refs': ['pB7PRQKPUuA'],
                    'kind': ['kpop'],
                    'signals': {'bpm': [85, 108], 'needs_vocal_intro': True, 'single_clip': True},
                    'clips': 'ONE fancam / MV clip of at least 4 s',
                    'song': 'a slowed edit with a clear vocal line in the first 7 s and a downbeat drop, 85-105 BPM',
                    'length': '10-13 s Short',
                    'template': 'single_clip_trend',
                    'style': 'kpop_trend',
                    'look': 'tint_pastel_cool',
                    'format': {'aspect': '9:16', 'fps': 60, 'frame': 'full'},
                    'pacing': {'interval': 'no cuts after the drop; 6 strobe accents on 8th-note grid points that carry an '
                                          'onset',
                              'lead_ms': 'onset-locked',
                              'hero_every': 'n/a'},
                    'speed': {'mode': 'normal', 'params': ''},
                    'camera': {'moves': 'static', 'handheld': 'NONE', 'bounce': 'NONE'},
                    'effects': [['text_on_black',
                               'intro 0-6.66 s',
                               'lowercase white words, alpha 0 -> 1 over 0.3-0.4 s starting at each vocal onset (Demucs '
                               'vocal stem); the phrase holds then fades 0.3 s; 3 short phrases over 6.7 s'],
                               ['strobe_smear',
                               '6 accents',
                               'pre 10 frames gain 1 -> 0.55 with defocus 0 -> 0.4% W; accent frame gain x1.5 (luma ~0.75) '
                               'with a 0.25 H vertical blur and zoom 1.03; post 18 frames vertical sigma 0.22 -> 0.05% W '
                               'with a sawtooth gain to x0.7; shake ty = A(-1)^k e^(-k/3), A 4-6% W'],
                               ['vsmear', 'the drop entry', 'the clip drops in on a smear']],
                    'grade': {'look': 'tint_pastel_cool',
                             'numbers': 'luma 0.48, std 0.20, p02 0.063, p98 0.93, sat 0.21, colourfulness 25, edge p95 '
                                        '144'},
                    'text': 'lowercase white words on black in the intro, timed to the vocal; nothing afterwards; Inter '
                            'SemiBold / a thin sans',
                    'audio': 'music only, -14 LUFS',
                    'never': [],
                    'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/pb_ov1.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/pb_d1.jpg',
                                'C:/Users/Home/AppData/Local/Temp/edit-refs/kpop/sheets/pb_fx.jpg'],
                    'gaps': ['the words need vocal-onset timings from the Demucs vocal stem (music.py has onsets; the '
                            'planner branch is untested)']},
'sound_hit_sync': {'id': 'sound_hit_sync',
                  'name': 'Sound Hit Sync',
                  'group': 'sound',
                  'weight': 0.10117,
                  'one_line': 'Every shot adds one audible hit from its own anime audio, landing on a song onset, with the '
                              'cut a pre-roll earlier so the wind-up is seen.',
                  'refs': ['FB15TNFAHVU', 'N1gQySyUGXk', 'jKSxAkOS3JY', 'n_NvC1IQUMQ', 'qgzBYo7gvhE', 'h0HNYocSDE8'],
                  'kind': ['sound'],
                  'signals': {'bpm': [85, 140], 'onsets_per_s': [1.6, 3.2], 'needs_clip_audio': True, 'min_shots': 18},
                  'clips': 'about 80% close-ups of hands, objects or feet making a crisp transient (punch, click, clap, '
                           'stomp, gunshot, cork pop, match strike, gun slide rack, chess piece, Go stone). Faces only as '
                           'intro / outro stingers. 11-20 different anime per 10 s edit, 1-2 shots per anime. No blood, no '
                           'fan-service shows',
                  'song': 'a percussive 4-bar hook, 85-140 BPM, 1.6-3.2 onsets per second with a 16th pattern that repeats '
                          'bar to bar; the original master at speed 1.0, untouched. When the song NAMES a sound, the edit '
                          'acts it out (HandClap -> hands, Wellerman -> stomps)',
                  'length': '8.5-11.5 s Short, 4 bars',
                  'template': 'sound_hit_sync',
                  'style': 'sound_edit',
                  'look': 'source',
                  'format': {'aspect': '9:16',
                            'fps': 60,
                            'frame': 'rot90 (p 0.6) or letterbox_black (p 0.4); NEVER a centre crop'},
                  'pacing': {'interval': 'shot median 9 frames @24 (0.375 s), p10 6, p90 13; 4-8 cuts per bar; >= 75% of '
                                        'the 16th positions repeat from the previous bar',
                            'lead_ms': 'the CUT is a pre-roll before the HIT: p25 82, p50 140, p75 210 ms. Only 10% of '
                                       'cuts land within 40 ms of a song onset',
                            'hero_every': 'none - the cut rate is constant, no build-up and no drop'},
                  'speed': {'mode': 'normal, NATIVE frames',
                           'params': 'speed 1.0, no interpolation, no ramps, no frame blending; 17-19.4 unique drawings '
                                     'per second; 24 fps drawings in a 60 fps file with 3:2 repeats'},
                  'camera': {'moves': 'NONE (per-shot zoom p10/50/90 -1.2 / -0.02 / +0.55%)',
                            'handheld': 'NONE',
                            'bounce': 'NONE'},
                  'effects': [['hard_cut', 0.95, 'no flash, no motion blur, no camera punch'],
                             ['cross_dissolve',
                             0.05,
                             '2-3 source frames with a = (k+1)/(n+1) while both shots keep playing; only between shots of '
                             'similar tone, never on the strongest hits'],
                             ['hit_sync_cut',
                             'every shot',
                             'cut_i = round((H_i - preroll_i) x fps); clip time = a_j + (t - H_i); the audio hit is placed '
                             'sample-accurately at H_i; the visible impact must land within +-2 frames of the hit'],
                             ['clip_audio_gate_and_mix',
                             'every shot',
                             'atrim [a-30 ms, min(a+0.9 s, next hit-25 ms)], highpass 100, +2 dB at 3.5 kHz, 8 ms fade-in, '
                             '30 ms fade-out, adelay to H-30 ms; gain = music RMS +5 dB - hit peak, clamped to +-12 dB '
                             '(stack a duplicate past +9)'],
                             ['loop_end',
                             'optional',
                             'the last shot is the first clip again so the Short loops seamlessly'],
                             ['rot90_layout',
                             0.6,
                             'cv2.ROTATE_90_CLOCKWISE on the whole 16:9 master including the watermark; no crop, no scale'],
                             ['letterbox_layout', 0.4, 'scale=1152:648, crop=1080:648, pad=1080:1920:0:636:black']],
                  'grade': {'look': 'source (NONE)',
                           'numbers': 'no tint, no bloom, no grain, no vignette; shot luma medians 0.27-0.56 and dark '
                                      'shots stay dark (shot p10 0.13-0.26)'},
                  'text': 'NO lyrics, titles, captions, emoji or particles. Only a faint watermark: italic, 25-35% '
                          'opacity, ~2.5% of the master height, centred near (0.45, 0.07) of the 16:9 master, rotated with '
                          'it',
                  'audio': 'the original master untouched at a constant level (music gain changes <= 1 dB at cuts, it '
                           'never ducks). Clip hits at +5 dB over the music RMS (p25 +3, p75 +7), envelope -17 dB at -60 '
                           'ms, -0.5 at 0, -4 at +60, -7 at +120, -10 at +200 ms, 12 dB decay in 484 ms, centroid ~3.4 '
                           'kHz. Master at -14 LUFS with TP < -1 dBTP (the references clip at -5.7..-9.8 LUFS)',
                  'never': [],
                  'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/sound_hits_overview.png',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/FB15TNFAHVU_audio_split.png',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/FB15TNFAHVU_rotation_check.png',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/N1gQySyUGXk_timeline_tracks.png',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/FB15TNFAHVU_cuts_00.png',
                              'C:/Users/Home/AppData/Local/Temp/edit-refs/sound/analysis/qgzBYo7gvhE_shots.png'],
                  'gaps': []},
'fx_typography': {'id': 'fx_typography',
                 'name': 'FX Typography',
                 'group': 'fx',
                 'weight': 0.02918,
                 'one_line': 'The text is the subject and the footage is a backdrop: words fly through the lens, every '
                             'join hides inside an exponential whip, a rack focus or a spin settle.',
                 'refs': ['WGvtliE1298'],
                 'kind': ['song'],
                 'signals': {'bpm': [70, 100],
                            'needs_drop': False,
                            'allow_no_drums': True,
                            'min_shots': 4,
                            'drums_share_max': 0.35,
                            'prefers_words': True},
                 'clips': 'slow, wide, shallow-focus anime plates that hold 1-3 s, with empty sky / snow / field where a '
                          'word can sit at full contrast; 4 clips is enough for 17 s. Reject busy action, faces filling '
                          'the frame and any shot with its own text',
                 'song': 'a slow vocal track (80 BPM in the reference) with clear, spaced lyrics; a dense-percussion track '
                         'would fight the words',
                 'length': '15-25 s Short',
                 'template': 'fx_heavy',
                 'style': 'fx_typography',
                 'look': 'neutral',
                 'format': {'aspect': '9:16',
                           'fps': 60,
                           'frame': 'full (at 16:9 a constant 5.0 % letterbox = a 2:1 picture)'},
                 'pacing': {'interval': 'shots hold 1.0-3.0 s, median 2.30 (0.42 cuts/s, 1.25 cuts/bar)',
                           'lead_ms': 'OFF the beat: join phase 0.64 +- 0.12 of the percussion beat, never within 0.12 of '
                                      'a hit',
                           'hero_every': '1 per 2 phrases, max 2',
                           'drop': 'no drop device: the lyric is the clock'},
                 'speed': {'mode': 'normal',
                          'params': 'every plate runs at native speed - no ramps, no freeze frames, no slow-mo'},
                 'camera': {'moves': 'push_in .55 / drift .25 / static .20, |ln s| 0.05-0.14, linear ease',
                           'handheld': 'none (0.0): the reference has no handheld noise at all',
                           'bounce': 0},
                 'effects': [['whip_exp_white',
                            0.34,
                            '14 frames @30, displacement x1.30 per frame to 16.8 %W, alpha 0 -> 0.72, cut ON the brightest '
                            'frame, no white hold'],
                            ['whip_exp_black',
                            0.22,
                            '13 frames, x1.40 to 15.4 %W, alpha 0 -> 0.71, the join hidden in a 4-frame dark hold'],
                            ['rack_focus',
                            0.18,
                            '17 frames: sigma 1.7 %W saturated -> 0.40 -> 0.14 %W, tau 5 frames, FLAT exposure'],
                            ['spin_settle',
                            0.12,
                            '57 deg unwinding x0.807 per frame with a coupled 68 % scale settle, tau 4.7 frames'],
                            ['zoom_settle',
                            0.14,
                            'ln(1.73) with tau 5.5 frames plus a 10 px defocus; the outgoing pushes AWAY and darkens'],
                            ['whip_exp_soft',
                            'hero .34',
                            '12 frames to alpha 0.83, cutting into a shot whose own background is white'],
                            ['glass_shatter',
                            'hero .22',
                            '11 frames: a 20-arm crack star then Voronoi shards, gradient x2.9, luma -43 %'],
                            ['dolly_out_white',
                            'hero .22',
                            'a scale ramp, not a pan: the subject shrinks and the white set is all that is left'],
                            ['word3d',
                            'every lyric word',
                            'per-word depth, x1.11 per frame @30 from cap 10.8 % to 25.3 % of the picture height, up to 3 '
                            'alive, motion blurred, NEVER fading'],
                            ['ground_text',
                            '1 per edit',
                            'the word flat on the plate ground plane at rx 74 deg carrying the plate radial blur'],
                            ['radial_blur',
                            'every pushing shot',
                            'sustained radtan 0.72-0.79 (static reference 1.17), strength 0.9 x the zoom rate per frame'],
                            ['glass_cracks', '1 set piece', 'spider web + 4-6 specular sparks'],
                            ['filmstrip', '1 set piece', '35 mm strip at 28 deg carrying 6-8 thumbnails'],
                            ['polaroids',
                            '1 set piece',
                            '3 bordered photos tilted 10-30 deg about their own Y, 4-6 frames apart'],
                            ['bokeh', 'intro', '57 blobs/frame behind the now-playing card'],
                            ['letters_fly',
                            'the set word',
                            'letters from off-screen right, 2-4 frame intervals, smeared 60-140 px @1920'],
                            ['zoom_blur_hit',
                            'before the set word',
                            'whole-frame zoom blur 2 frames before the assembly beat'],
                            ['typewriter', 'intro lyric line', '13.2 chars/s, 3-frame per-character fade'],
                            ['closing_3d',
                            'outro',
                            'extruded caps: wall 10-14 % of the cap, cream/gold body, lit top edge, cast shadow offset 3 % '
                            'cap, sigma 2 %W, alpha 0.35']],
                 'grade': {'look': 'neutral - the reference deliberately does NOT unify the look',
                          'numbers': 'p2 = 0 in every shot, p98 175-254; saturation runs 4 (white set) to 168 (the blue '
                                     'card); grain is INHERITED from the source (0.00 CG / 0.32-0.51 anime / 1.20 live), '
                                     'never added; warm highlights (Lab b* +16..+18) over neutral or cool shadows'},
                 'text': 'hero words at 13-25 % of the picture height in a geometric rounded lowercase sans, pure white, '
                         'two-radius glow (sigma 0.31 %W gain 0.70 + sigma 4.0 %W gain 0.30 as rendered); legible 1-4 '
                         'frames (median 2.5) after the vocal onset of the syllable; no emoji, no CJK',
                 'audio': 'the song alone. NO sfx, risers, sweeps or ducking - spectral flatness <= 0.003 across the whole '
                          'reference; -14 LUFS, TP < -1 dBTP',
                 'never': ['rgb_split / chromatic aberration (|shift| < 1 px @1920)',
                          'pure white or pure black frames',
                          'strobe, invert, halftone, any frame flicker',
                          'cross dissolve',
                          'camera shake / handheld',
                          'speed ramps',
                          'freeze frames',
                          'added SFX',
                          'split screens / tiled layouts',
                          'emoji or CJK glyphs',
                          'a face filling the frame while a word is on screen'],
                 'evidence': ['C:/Users/Home/AppData/Local/Temp/edit-refs/fx/analysis'],
                 'gaps': []}}


# ---------------------------------------------------------------------------------------------- clip rules
# The owner's clip rules per preset (2026-10-01), read by core/edit/clip_rules.py + library.py
# (--preset <id>; every key can be overridden on the command line: --subject --solo --mood --motion-min).
#   subject_pref  'female' boosts beautiful female characters (cute / beautiful edits), 'any' leaves them
#   solo          'strict' one dominant subject, no gag / conversation / two-shot compositions; 'soft' only
#                 rejects gag / conversation compositions; 'off'
#   mood          'beauty' bright / warm / dreamy / wistful-beautiful, hard-rejects gloomy / sad / creepy;
#                 'aura' intense / confident / powerful, rejects comedic / sad; 'any'
#   motion_min    0-1 residual motion at 1x of the best `take` seconds (hair, cloth, water, particles, a turn);
#                 near-still windows are rejected for slowed / hero / beauty slots (0 = no gate)
#   still_max     largest share of near-still steps allowed in that window
#   slow          the slow factor the slot is played at (deeper slow-motion needs more source motion)
#   dialogue      'reject' (always: no talking heads / phone calls)
#   appeal        True: boost attractive, glamorous, eye-catching shots (striking eyes, hair in motion, water /
#                 light / sparkle around the character, pretty outfits, summer / night-light settings; a pool /
#                 beach / water SCENE is never a reason to reject)
#   male_reject   False: a male-dominant shot is not rejected for a 'female' preset (a couple's partner)
# A preset without an entry (and no command-line rule) is selected exactly as before.
_BEAUTY = dict(subject_pref='female', solo='strict', mood='beauty', dialogue='reject', appeal=True)
_AURA = dict(subject_pref='any', solo='soft', mood='aura', dialogue='reject')
CLIP_RULES = {
    'blur_flow': dict(_BEAUTY, motion_min=0.22, still_max=0.6, slow=0.5),
    'cinematic_slowmo': dict(_BEAUTY, motion_min=0.28, still_max=0.5, slow=0.45),
    'cinematic_long': dict(_BEAUTY, solo='soft', motion_min=0.2, still_max=0.6, slow=0.7),
    'char_soft_romance': dict(_BEAUTY, solo='soft', male_reject=False, motion_min=0.22, still_max=0.6, slow=0.6),
    'kpop_postcard_drop': dict(_BEAUTY, solo='soft', motion_min=0.2, still_max=0.6, slow=1.0),
    'kpop_meme_reveal': dict(_BEAUTY, solo='soft', motion_min=0.0),
    'hype_typography': dict(_AURA, motion_min=0.2, still_max=0.7),
    'char_quick_velocity': dict(_AURA, motion_min=0.2, still_max=0.7),
    'char_raw_crisp': dict(_AURA, motion_min=0.2, still_max=0.7),
    'fx_typography': dict(_AURA, mood='any', solo='off'),
    'sound_hit_sync': dict(_AURA, mood='any', solo='off', motion_min=0.0),
    'kpop_stage_push': dict(_AURA, solo='off'),
}


def clip_rules(preset):
    """The clip-rule dict of a preset (empty for one without rules); see CLIP_RULES."""
    return dict(CLIP_RULES.get(preset) or {})


KINDS = sorted({k for p in PRESETS.values() for k in p['kind']})


# ---------------------------------------------------------------------------------------------- selection

def song_features(A):
    """The signals choose_preset reads out of a core/edit/music.py analysis (missing keys stay None)."""
    A = A or {}
    e = np.asarray(A.get('beat_energy') or [], float)
    perc = np.asarray(A.get('beat_perc_db') or [], float)
    loud = np.asarray(A.get('beat_loud_db') or [], float)
    drums = None
    if len(perc) and len(perc) == len(loud):
        # how much of the beat level is percussion: 10^((perc - loud)/20) on the median beat
        drums = float(np.clip(10 ** (float(np.median(perc - loud)) / 20.0), 0.0, 1.0))
    drops = A.get('drops') or []
    dur = float(A.get('duration') or 0.0)
    ons = A.get('onsets') or []
    return dict(
        bpm=float(A.get('bpm') or 0.0),
        duration_s=dur,
        energy_p90=float(np.percentile(e, 90)) if len(e) else None,
        drums_share=drums,
        has_drums=(drums is None or drums > 0.12),
        needs_drop=bool(any(float(d.get('jump_db') or 0) >= 4 for d in drops)),
        four_on_floor=bool(A.get('straight_grid')) and float(A.get('meter') or 4) == 4,
        onsets_per_s=(len(ons) / dur if dur > 0 else None),
        vocal=float(np.mean(A.get('beat_voc') or [0.0])) if A.get('beat_voc') else None,
    )


def shot_features(shots):
    """The signals choose_preset reads out of a shots.json pool."""
    shots = shots or []
    n = len(shots)
    durs = sorted(float(s.get('duration') or 0.0) for s in shots)
    sharp = sorted(float(s.get('sharpness') or 0.0) for s in shots if s.get('sharpness'))
    chars = {s.get('character') for s in shots if s.get('character')}
    return dict(
        min_shots=n,
        min_distinct_sources=len({s.get('source_id') for s in shots if s.get('source_id')}),
        min_take_s=(durs[len(durs) // 2] if durs else 0.0),
        max_take_s=(durs[-1] if durs else 0.0),
        min_stills=sum(1 for s in shots if s.get('is_still') or float(s.get('motion') or 1.0) < 0.02),
        sharpness_p50=(sharp[len(sharp) // 2] if sharp else None),
        one_character=(len(chars) == 1 if chars else None),
        one_member=(len(chars) == 1 if chars else None),
        needs_periodic_motion=any(float(s.get('periodic') or 0.0) >= 0.25 for s in shots),
        needs_clip_audio=any(s.get('has_audio') for s in shots) if any('has_audio' in s for s in shots) else None,
        single_clip=(len({s.get('source_id') for s in shots if s.get('source_id')}) == 1),
        tags={t for s in shots for t in (s.get('tags') or [])},
    )


def _score_signals(sig, f, s):
    """(score, notes, hard_fail) for one preset's signals against the song / shot features."""
    score, notes, fail = 0.0, [], None
    for key, want in (sig or {}).items():
        if key == 'bpm':
            got = f.get('bpm') or 0.0
            lo, hi = float(want[0]), float(want[1])
            if not got:
                continue
            if lo <= got <= hi:
                score += 2.0
                notes.append(f'bpm {got:.0f} in [{lo:.0f},{hi:.0f}]')
            else:
                d = (lo - got) if got < lo else (got - hi)
                if d > 18:
                    fail = fail or f'bpm {got:.0f} outside [{lo:.0f},{hi:.0f}]'
                else:
                    score -= d / 12.0
                    notes.append(f'bpm {got:.0f} near [{lo:.0f},{hi:.0f}]')
        elif key == 'duration_s':
            got = f.get('duration_s') or 0.0
            if got and not (float(want[0]) * 0.8 <= got <= float(want[1]) * 1.4):
                fail = fail or f'duration {got:.0f}s outside {want}'
        elif key == 'needs_drop':
            if want and not f.get('needs_drop'):
                fail = fail or 'no drop in the song'
            elif want:
                score += 1.0
        elif key == 'allow_no_drums':
            if want and not f.get('has_drums'):
                score += 1.0
                notes.append('drumless, allowed')
        elif key == 'drums_share_min':
            got = f.get('drums_share')
            if got is not None:
                if got >= float(want):
                    score += 1.0
                else:
                    fail = fail or f'drum share {got:.2f} < {want}'
        elif key == 'drums_share_max':
            got = f.get('drums_share')
            if got is not None:
                if got <= float(want):
                    score += 1.0
                else:
                    score -= 1.5
                    notes.append(f'drum share {got:.2f} > {want}')
        elif key == 'energy_p90_min':
            got = f.get('energy_p90')
            if got is not None and got < float(want):
                fail = fail or f'energy p90 {got:.2f} < {want}'
            elif got is not None:
                score += 0.5
        elif key == 'onsets_per_s':
            got = f.get('onsets_per_s')
            if got is not None and not (float(want[0]) * 0.7 <= got <= float(want[1]) * 1.3):
                fail = fail or f'onset rate {got:.2f} outside {want}'
            elif got is not None:
                score += 1.0
        elif key == 'four_on_floor':
            if want and not f.get('four_on_floor'):
                score -= 1.0
            elif want:
                score += 0.5
        elif key in ('min_shots', 'min_distinct_sources', 'min_take_s', 'min_stills'):
            got = s.get(key)
            if got is None:
                continue
            if got < float(want):
                fail = fail or f'{key} {got} < {want}'
            else:
                score += 0.5
        elif key == 'sharpness_p50_min':
            got = s.get('sharpness_p50')
            if got is None:
                continue
            if got < float(want):
                fail = fail or f'sharpness p50 {got:.0f} < {want}'
            else:
                score += 1.0
        elif key in ('one_character', 'one_member'):
            got = s.get(key)
            if want and got is False:
                fail = fail or 'the pool is not one character'
            elif want and got:
                score += 1.5
        elif key in ('needs_periodic_motion', 'needs_clip_audio'):
            got = s.get(key)
            if want and got is False:
                fail = fail or f'{key} not satisfied by the pool'
            elif want and got:
                score += 1.0
        elif key == 'single_clip':
            if want and not s.get('single_clip'):
                fail = fail or 'more than one source in the pool'
            elif want:
                score += 1.0
        elif key in ('needs_group_shot', 'needs_reaction_shot', 'needs_vocal_intro', 'two_part', 'quality_first',
                     'age_min_18', 'framing', 'prefers_words'):
            notes.append(f'{key}: not machine-checkable here, left to the caller')
    return score, notes, fail


def choose_preset(kind, song_analysis=None, shots=None, rng=None, prefer=None, allow_fallback=True):
    """
    Pick the preset that fits this song and this clip pool.

    kind           'song' | 'dance' | 'character' | 'kpop' | 'sound' | 'long'
    song_analysis  a core/edit/music.py analysis dict (or None)
    shots          a shots.json list from core/edit/library.py (or None)
    prefer         a preset id to use if it is eligible (the owner's favourite for the day)

    Returns (preset_id, why). Among the eligible presets the choice is sampled by posting weight x fit, so
    the channel does not post the same style every day; pass rng=random.Random(seed) to make it reproducible.
    """
    f, s = song_features(song_analysis), shot_features(shots)
    cands, rejected = [], []
    for pid, p in PRESETS.items():
        if kind and kind not in p['kind']:
            continue
        sc, notes, fail = _score_signals(p['signals'], f, s)
        if fail:
            rejected.append((pid, fail))
            continue
        cands.append((pid, sc, notes))
    if prefer and any(c[0] == prefer for c in cands):
        p = PRESETS[prefer]
        return prefer, f"{prefer}: requested and eligible (template {p['template']}, style {p['style']})"
    if not cands:
        if not allow_fallback:
            return None, 'no preset fits: ' + '; '.join(f'{a} ({b})' for a, b in rejected[:6])
        pool = [(pid, 0.0, []) for pid, p in PRESETS.items() if kind in p['kind']]
        if not pool:
            return None, f'no preset declares kind {kind!r}'
        cands = pool
    base = np.array([max(0.02, PRESETS[c[0]]['weight']) for c in cands], float)
    fit = np.array([math_exp(c[1]) for c in cands], float)
    w = base * fit
    w = w / w.sum()
    if rng is None:
        i = int(np.argmax(w))
    else:
        i = int(np.searchsorted(np.cumsum(w), rng.random() * float(np.sum(w))))
        i = min(i, len(cands) - 1)
    pid, sc, notes = cands[i]
    p = PRESETS[pid]
    why = (f"{pid}: fit {sc:+.1f}, weight {p['weight']:.3f} -> p {w[i]:.2f}; template {p['template']}, "
           f"style {p['style']}, look {p['look']}")
    if notes:
        why += ' (' + '; '.join(notes[:3]) + ')'
    if rejected:
        why += ' | rejected: ' + ', '.join(f'{a} ({b})' for a, b in rejected[:4])
    return pid, why


def math_exp(x):
    return float(np.exp(np.clip(x, -4.0, 4.0) * 0.35))


def resolve(name, kind=None, song_analysis=None, shots=None, rng=None):
    """'auto' (or a preset id / a planner template name) -> (preset_id or None, preset dict or None)."""
    if not name or name == 'auto':
        pid, _ = choose_preset(kind, song_analysis, shots, rng)
        return pid, (PRESETS.get(pid) if pid else None)
    if name in PRESETS:
        return name, PRESETS[name]
    hit = [pid for pid, p in PRESETS.items() if p['template'] == name]
    if hit:
        return hit[0], PRESETS[hit[0]]
    return None, None


def apply_to_opts(opts, preset):
    """Feed a preset into planner opts: the template (and, through it, the style and look)."""
    if not preset:
        return opts
    opts = dict(opts)
    opts['template'] = preset['template']
    opts.setdefault('preset', preset['id'])
    if preset['kind'] and not opts.get('type'):
        opts['type'] = preset['kind'][0]
    return opts


def lyric_text_rule(preset_id):
    """The preset's lyric-text track (core/edit/lyric_text.py RULES), or None: its references show no lyric text."""
    try:
        import lyric_text as LT  # noqa: E402
        return LT.RULES.get(preset_id)
    except ImportError:
        return None


# ---------------------------------------------------------------------------------------------- audio (AV study)
# What each preset's references do with SOUND at the transitions, measured against the aligned original songs
# (core/edit/refs/{general,fx,kpop,character,dance,sound}-av.md, 2026-09-26). Read by core/edit/song_fx.py (song edits
# + the few overlays, one reference recipe per render: `variants` = (reference, share)) and core/edit/clip_audio.py
# (`clip_audio`). transition_sfx is 'none' everywhere: no reference lays a sound on ORDINARY cuts; sfx.PRESET_SFX
# 'auto' follows this. cut_to_song_hit_ms = the song's own kick / snare after the picture switch [p25, p50, p75] (what
# the owner hears as the transition sound; the planners' lead places it).
AUDIO_AV = {
    'blur_flow': dict(transition_sfx='none', clip_audio='none', variants=[('ZkWQ', 0.5), ('cj12', 0.5)],
                      cut_to_song_hit_ms=[-3, 57, 83], report='general-av.md 2.2, 2.3, 3.1',
                      song_fx='cj12: hero impact (boom + bright top, +10 dB for 100-200 ms) on up to 3 hero cuts, low-pass '
                              'into the fade-out; ZkWQ: nothing (its bar re-sequencing = an explicit song_cut). Both play '
                              'a slowed + reverb + bass version (song_fx.make_version 0.909-0.925)'),
    'hype_typography': dict(transition_sfx='none', clip_audio='none', variants=[('i4f', 1 / 3), ('m3lv', 1 / 3), ('zawB', 1 / 3)],
                            cut_to_song_hit_ms=[42, 85, 117], report='general-av.md 2.4-2.6, 3.2',
                            song_fx='i4f: 5 kHz whoosh on every cut, sub riser + impact on the drop, low-pass muffle '
                                    'opening 60-90 ms after flash cuts; m3lv: impacts on 3 hero cuts, +8 dB swells into '
                                    'cuts; zawB: sub swell into flash cuts + trailing sub boom'),
    'cinematic_slowmo': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[-14, 17, 50],
                             report='general-av.md 2.1, 3.3', song_fx='none (the song slowed 0.95 = make_version)'),
    'band_parade': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[-73, -23, 80],
                        report='general-av.md 2.7, 3.4', song_fx='none (unlocked; nothing measurable added)'),
    'cinematic_long': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=None,
                           report='general-av.md (general group)', song_fx='none'),
    'char_quick_velocity': dict(transition_sfx='none', clip_audio='intro_line',
                                variants=[('yUui', 0.34), ('YH', 0.33), ('TG', 0.33)], cut_to_song_hit_ms=[8, 23, 64],
                                report='character-av.md 1, 4',
                                song_fx='yUui: song stop before the drop filled (clip effect or wash, -6.5 dB), echo '
                                        'throw + duck into chorus cuts, end ride; YH: bass cut before chorus cuts, '
                                        'fade-in intro; TG: bass-ringing drop-out, -18 dB end ride'),
    'char_soft_romance': dict(transition_sfx='none', clip_audio='intro_line', variants=[('eEPZ', 0.5), ('rfsj', 0.5)],
                              cut_to_song_hit_ms=[None, 28, None], report='character-av.md 1, 4',
                              song_fx='eEPZ: >90 Hz drop-outs while the bass rings, one echo throw, 180 ms end '
                                      'slowdown; rfsj: song only, -15 dB end ride'),
    'char_raw_crisp': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, -85, None],
                           report='character-av.md 1', song_fx='none (the white flash starts on the kick)'),
    'dance_burst_parade': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, 14, None],
                               report='dance-av.md', song_fx='none'),
    'dance_smooth_velocity': dict(transition_sfx='none', clip_audio='none', variants=[('91tp', 0.5), ('ONlH', 0.5)],
                                  cut_to_song_hit_ms=None, report='dance-av.md song edit A / B',
                                  song_fx='91tp: build ducked -5.7 dB + darkened, the song muted 0.4 s and replaced by '
                                          'one note gliding up ~8.5 st into the drop; ONlH: +64-beat re-sequence '
                                          '(explicit song_cut)'),
    'dance_raw': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, -60, None],
                      report='dance-av.md', song_fx='none'),
    'dance_meme': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, 18, None],
                       report='dance-av.md', song_fx='none (slowed 0.8975 = make_version)'),
    'dance_long_amv': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, 58, None],
                           report='dance-av.md song edit B / C', song_fx='none (re-sequencing = explicit song_cut)'),
    'kpop_postcard_drop': dict(transition_sfx='none', clip_audio='pop_line', variants=[], cut_to_song_hit_ms=[0, 48, 51],
                               report='kpop-av.md mF',
                               song_fx='flanger over the intro to the grade pop, sub swell + air lift into the drop, '
                                       'duck before the cutout reveal, highs rolled off + 0.12 s end fade'),
    'kpop_stage_push': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[20, 109, 119],
                            report='kpop-av.md upHh', song_fx='sparkle chime on the heart sticker only; 0.4 s end fade '
                                                              '(super slowed x0.89 = make_version)'),
    'kpop_meme_reveal': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=None,
                             report='kpop-av.md PsdUh', song_fx='hard stop + gap (the plan\'s mute overlay)'),
    'kpop_single_take_smear': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=[None, 45, None],
                                   report='kpop-av.md fSMk', song_fx='1.51 s silence (mute overlay), then +6 dB bass'),
    'kpop_text_strobe': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=None,
                             report='kpop-av.md pB7P', song_fx='none (sped up x1.10 = make_version; hard stop)'),
    'sound_hit_sync': dict(transition_sfx='none', clip_audio='hits', variants=[], cut_to_song_hit_ms=[92, 148, 215],
                           report='sound-av.md', song_fx='none: the song untouched; the clip hit sits on its drum '
                                                         '(engine per-shot sfx)'),
    'fx_typography': dict(transition_sfx='none', clip_audio='none', variants=[], cut_to_song_hit_ms=None,
                          report='fx-av.md 1', song_fx='intro card held -3.8 dB, +3.8 dB swell over 180 ms from the '
                                                       'first cut; whips in vocal gaps, reveals 0-4 frames after a snare'),
}


def audio_rule(preset_id):
    """The preset's measured audio design (AUDIO_AV row) or {}."""
    return AUDIO_AV.get(preset_id) or {}


# ---------------------------------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description='named preset pipelines for the edit engine')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show')
    ap.add_argument('--choose', help='kind: ' + ', '.join(KINDS))
    ap.add_argument('--analysis')
    ap.add_argument('--shots')
    ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    if a.list:
        rows = sorted(PRESETS.values(), key=lambda p: (-p['weight'], p['id']))
        for p in rows:
            print(f"{p['weight']:6.3f}  {p['id']:26s} {p['group']:10s} {','.join(p['kind']):18s} "
                  f"{p['template']:24s} {p['style'] or '-':16s} {len(p['effects']):2d} fx  {p['one_line'] or ''}")
        print(json.dumps(dict(ok=True, presets=len(PRESETS), weight_sum=round(sum(p['weight'] for p in rows), 4))))
        return
    if a.show:
        pid, p = resolve(a.show)
        if not p:
            print(json.dumps(dict(ok=False, error=f'no preset {a.show!r}')))
            raise SystemExit(2)
        print(json.dumps(p, indent=1, ensure_ascii=False, default=list))
        return
    if a.choose:
        import random
        A = json.load(open(a.analysis, encoding='utf-8')) if a.analysis and os.path.exists(a.analysis) else None
        sh = json.load(open(a.shots, encoding='utf-8')) if a.shots and os.path.exists(a.shots) else None
        if isinstance(sh, dict):
            sh = sh.get('shots') or []
        pid, why = choose_preset(a.choose, A, sh, random.Random(a.seed) if a.seed is not None else None)
        p = PRESETS.get(pid) or {}
        print(json.dumps(dict(ok=bool(pid), preset=pid, why=why, template=p.get('template'), style=p.get('style'),
                              look=p.get('look')), ensure_ascii=False))
        return
    ap.print_help()


if __name__ == '__main__':
    sys.exit(main())
