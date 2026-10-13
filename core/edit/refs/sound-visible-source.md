# Sound edit: what makes the sound is on screen (FB15TNFAHVU, visible-source study)

Owner 2026-10-12: "the sound pool needs better clips; I need to know WHAT IS MAKING THE SOUND."
This adds only the missing per-hit "visible source" layer to the existing study of the same reference
(timing, levels, layout, song treatment: `sound.md`, `sound-av.md` / `.json`, `remake-sound_hit_sync.md`).

- Tool: `core/edit/sound_visible.py` (the measure, gate and tags). Study script: `core/edit/refs/sound_visible_study.py --ref FB15TNFAHVU --out DIR`.
  It also runs on our renders (`--video X --plan plan.json [--rot90]`).
- Evidence (session scratch): `FB15TNFAHVU_strips_00..02.jpg` show every shot from cut to next cut, with the hit frame in red, the action box in green and the 9:16 window in blue. `hits_a.jpg` / `hits_b.jpg` show hit -2..+2 frames at 400 px.
- Frames are at 24 fps, from the 16:9 master (the upload rotated back). "Off" is the visible action frame minus the hit frame.
- Sound data are from sound-av.md: centroid and the drum the hit sits on.

## Per hit (10 audible clip hits of 21 cuts)

| # | cut->hit | what is on screen at the hit | shot / framing | visible action vs hit | sound (as heard; centroid) | source visible? |
|---|---|---|---|---|---|---|
| 1 | 2.2 f | a fist crushes a crystal; shards burst | **close-up on the hand + object**, centred (x 0.44), object ~30 % of frame | shatter starts ON the hit frame (0) | glass / crystal crunch, bright (6.6 kHz) | yes: GLASS BREAK |
| 2 | 1.4 f | a crystal earring shatters beside the ear | **extreme close-up on the object**, object right of centre (x 0.79) | shatter +1 f | glass shatter (4.7 kHz) | yes: GLASS BREAK |
| 4 | 2.2 f | a bullet tracer hits a can on a post; can flies | **object close-up** (can fills the centre) | tracer reaches the can on the hit frame (0), can knocked +2 | metal can hit / pop (950 Hz) | yes: METAL hit by a bullet |
| 7 | 3.6 f | a hand strikes a match; spark and flame | **close-up on the hand + match**, dark | spark -1 f (sound on the flare) | match strike hiss (8 kHz) | yes: small but a bright spark on black |
| 9 | 5.2 f | a clawed hand flexes, fingers spread, red glow | **close-up on the hand**, hand fills the frame | finger snap-open -1..-2 f | knuckle crack / creak (2.9 kHz) | yes |
| 10 | 3.4 f | a girl bites a rice cracker | **extreme close-up of mouth + cracker** | bite ~0 | crunch (3.3 kHz) | yes: the bite (face in frame, the cause is the cracker) |
| 11 | 0.9 f | a white flash, then a mouth close-up | FX flash frame | flash -2 f, no object | ~2.5 kHz burst | **no**: an FX flash only |
| 16 | 3.4 f | a man's profile in a coat, mouth opens slightly | medium close-up of a face | no object action | 1.9 kHz | **no**: a face reacting |
| 17 | 1.9 f | a man fires a pistol in a garden; muzzle flash | **wide** shot (gun about 2 % of the frame) | muzzle flash +2 f | gunshot (1.4 kHz) | yes, but small: the flash is the cue |
| 18 | 3.2 f | Mumei (Kabaneri) spins and swings a blade; streak | **medium** (full figure, centred) | blade streak +1..+2 f | blade swish (4.2 kHz) | yes |

**Visible source by eye: 8 / 10 hits.** Six are object close-ups (#1 #2 #4 #7 #9 #10), one is medium (#18) and one is wide with a muzzle flash (#17).
The two misses are an FX flash (#11) and a face (#16).

The song-only cuts also show sound-making objects: a Stand punch (#3), Zoro's swords (#5), a gun tossed and caught (#13),
a gun on the grass (#15), rifles firing (#19), brass knuckles (#8) and an explosion (#14). The picture always "sounds like" the song's hits, even where the clip is muted.

### The owner's sound types, as used in this reference

| type | where |
|---|---|
| glass breaking / shattering | #1 (crystal crushed in a fist), #2 (crystal earring) |
| metal hit | #4 (can hit by a bullet), #8 song-only (brass knuckles) |
| gun | #17 (shot), song-only #12 #13 #15 #19 |
| blade | #18 (swing), song-only #5 |
| strike / ignition | #7 (match) |
| crunch | #10 (cracker bite) |
| hand / knuckle | #9 |
| face mask cracking, ice / stone cracking, chains, armour | **not used in this reference.** They are added as vision tags (mask / crack / chain / metal) and as pool queries (Bleach hollow mask, Tokyo Ghoul mask, Demon Slayer mask, glass shatter, ice cracking, chains) |

## The rule the reference follows (what the gate copies)

1. **Timing.** The visible action is within -2..+2 frames of the transient, median |1| frame. It is the shatter frame, the impact frame or the flare frame, not the wind-up.
2. **Framing.** The object is big: the action box covers a median 35 % of the frame, and 6 / 10 hits are object close-ups.
   The reference never reframes (it rotates the 16:9 master). Our 9:16 crop therefore has to land on the object: the best 9:16 window holds a median 93 % of the action energy at the hit (`fit`).
3. **Pre-roll.** The cut comes 1-5 frames before the hit (median 2.8 f), so the object is already on screen, still or winding up, when it sounds. That makes 0-3 frames of plain object before the action.
4. **No source, no hit.** The reference breaks this only twice (#11 flash, #16 face). We make it a hard gate.

## Metric vs eye on this reference

| version | passes | wrong |
|---|---|---|
| motion only (onset / spark / contact within ±2 f, area >= 5 %, fit >= 0.5, no global flash) | 9 / 10 | #16 passes (a face moving) |
| + SigLIP tags on the action region (gun / reload / sword / fist / kick / clap / object / impact / glass / mask / crack / chain / metal vs face / eyes / scenery / fx / wide) | **6 / 10** | #2 and #10 rejected as `face_only` (close-ups next to a face); #16 correctly rejected |

The production gate is the strict version (6 / 10 on the reference, against 8 / 10 by eye). It errs toward rejecting:
a wrong reject only costs pool size, while a wrong accept is the fault the owner reported.

Pool check by eye (8 random passes, 8 random rejects of sound/impact):
- The rejects were right: an opening-credit frame, hair in the wind, energy FX, an aura over a hand.
- The wrong passes (a tiny dark wide fight, a face close-up) led to the `wide` tag and the face margin -0.5 (`SOUND_VIS_FACE`).
