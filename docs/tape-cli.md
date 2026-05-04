# musiktool-tape(1) — tape mastering project management

## SYNOPSIS

```
musiktool tape <command> [options] [arguments]
```

## DESCRIPTION

Plan, analyze, and render audio for recording onto physical media — VHS
Hi-Fi tape, compact cassette, or vinyl. Manages tape projects: ordered
collections of albums and individual tracks with per-medium processing
(gain, limiting, compression) and proper tape structure (lead-in, gaps,
lead-out). Two-sided media (cassette, vinyl) are fully supported with
automatic album splitting at track boundaries and optional side pinning.

Calibration is handled separately via `musiktool calibrate` — a one-time
procedure to establish the level correspondence between digital output and
the deck's VU meters.

All loudness data comes from the default analytics DB populated by
`musiktool analyze` (`~/.local/share/musiktool/analytics.db`). Albums and
tracks must be analyzed before they can be added to a tape project. The
`musiktool analyze --db` option is for standalone/custom analysis workflows;
`musiktool tape` commands do not read custom DB paths in this version.

## COMMANDS

### tape list

List all tape projects.

```
musiktool tape list
```

Output:

```
Name                  Medium    Items  Duration   Remaining  Created
Friday Mix            vhs-120   4      117:42     0:18       2026-05-03
Metallica Marathon    vhs-180   6      162:30     13:10      2026-05-03
```

### tape create

Create a new tape project with a medium profile.

```
musiktool tape create <name> --medium <preset> [options]
```

**Arguments:**

- `name` — Project name (unique identifier).

**Options:**

| Option | Default | Description |
|---|---|---|
| `--medium`, `-m` | *(required)* | Medium preset (e.g. `vhs-120`, `c-90-ii-dolbyb`, `reel-10-half-15ips`, `md-80`, `cd-80`, `vinyl-lp`, `custom`). See MEDIUM PRESETS below for full list. |
| `--duration` | *(from preset)* | Usable duration in minutes. Required for `custom`, ignored for presets. |
| `--lead-in` | `25` | Lead-in silence in seconds. Allows time to start deck recording while silence plays. |
| `--lead-out` | `20` | Lead-out silence in seconds. |
| `--album-gap` | `8` | Silence between albums in seconds. |
| `--track-gap` | `4` | Silence between individual tracks (mixtape mode) in seconds. |
| `--marker` | `none` | Album boundary marker: `none` or `tone`. |
| `--marker-freq` | `400` | Marker tone frequency in Hz. |
| `--marker-level` | `-30` | Marker tone level in dBFS. |
| `--marker-duration` | `0.5` | Marker tone duration in seconds. |
| `--target-lufs` | `-14` | Target integrated loudness in LUFS. |
| `--peak-ceiling` | *(from preset)* | Peak ceiling in dBTP. Default: `-1` for hard-clip media (VHS/MD/CD), `0` for soft-clip (cassette/reel). |
| `--sample-rate` | `auto` | Output sample rate in Hz. `auto` uses most common source rate. |
| `--bit-depth` | `24` | Output bit depth: `16` or `24`. |
| `--limiter` | off | Unlimited limiting budget — force all items to target LUFS regardless of peak cost. Without this flag, up to 3 dB of transparent peak limiting is applied automatically. |
| `--compressor` | off | Enable gentle compression for wide-dynamic-range items. |

**Example:**

```
musiktool tape create "Friday Mix" --medium vhs-120
musiktool tape create "Long Play" --medium custom --duration 240
```

### tape show

Display project contents with timeline layout.

```
musiktool tape show <name>
```

**Output (compact):**

```
Friday Mix — VHS-120 (118:00 usable) — target -14 LUFS — 44100 Hz / 24-bit

  Pos  Type   Tracks  Duration  LUFS    Peak   LRA   Item
  1    album  8       42:35     -8.2    -0.3   6.1   Metallica / Master Of Puppets (1986)
  2    track  1        8:02    -10.1    -1.2   9.4   Led Zeppelin / Stairway To Heaven
  3    album  9       65:13     -7.4    -0.1   5.8   Metallica / ...And Justice For All (1988)

  Content:  115:50
  Overhead:   1:57  (25s lead-in + 8s gap + 4s gap + 20s lead-out)
  Total:    117:47
  Remaining:  0:13
```

**Output with `--timeline`:**

```
musiktool tape show "Friday Mix" --timeline
```

```
Friday Mix — VHS-120 (118:00 usable)

  Time      Event
  0:00      Lead-in (25s silence)
  0:25      [1] Metallica / Master Of Puppets (1986) — 8 tracks, 42:35
  43:00     Gap (8s silence)
  43:08     [2] Led Zeppelin / Stairway To Heaven — 8:02
  51:10     Gap (4s silence)
  51:14     [3] Metallica / ...And Justice For All (1988) — 9 tracks, 65:13
  116:27    Lead-out (20s silence)
  116:47    End

  Remaining: 1:13
```

**Output (two-sided, compact):**

```
Late Night — C-90 (90:00 usable, 45:00 + 45:00) — target -14 LUFS — 44100 Hz / 24-bit

  === Side A (45:00) ===
  Pos  Type   Tracks  Duration  LUFS    Peak   LRA   Item
  1    album  10      42:35     -8.2    -0.3   6.1   Artist / Album One (1986)

  Content:  42:35
  Overhead:  0:45  (25s lead-in + 20s lead-out)
  Total:    43:20
  Remaining: 1:40

  === Side B (45:00) ===
  Pos  Type   Tracks  Duration  LUFS    Peak   LRA   Item
  2    album  9       40:13     -7.4    -0.1   5.8   Artist / Album Two (1988)

  Content:  40:13
  Overhead:  0:45  (25s lead-in + 20s lead-out)
  Total:    40:58
  Remaining: 4:02
```

### tape add

Append albums or tracks to the end of a project.

```
musiktool tape add <name> <path>...
```

**Arguments:**

- `name` — Project name.
- `path` — One or more paths. Directories are treated as albums, files as
  individual tracks.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--side`, `-s` | *(none)* | Pin items to side `a` or `b` (two-sided media only). Without pinning, items are assigned automatically. |

**Behavior:**

- Albums are added as atomic units (album-level gain).
- Individual tracks get per-track gain.
- Paths must have been previously analyzed (`musiktool analyze`). Errors if
  loudness data is missing.
- Items are appended in the order given.

**Example:**

```
musiktool tape add "Friday Mix" \
  "~/Music/Metallica/Master Of Puppets (1986)" \
  "~/Music/Metallica/...And Justice For All (1988)"

musiktool tape add "Friday Mix" \
  "~/Music/Pink Floyd/Wish You Were Here (1975)/01 Shine On You Crazy Diamond (Parts I-V).flac"
```

### tape insert

Insert albums or tracks at a specific position.

```
musiktool tape insert <name> <position> <path>...
```

**Arguments:**

- `name` — Project name.
- `position` — Insert before this position (1-based). Existing items at and
  after this position shift down.
- `path` — One or more paths (same rules as `tape add`).

**Options:**

| Option | Default | Description |
|---|---|---|
| `--side`, `-s` | *(none)* | Pin items to side `a` or `b` (two-sided media only). |

**Example:**

```
# Insert before position 2 — existing items 2+ shift down
musiktool tape insert "Friday Mix" 2 "~/Music/Pink Floyd/Wish You Were Here (1975)"
```

### tape remove

Remove items by position.

```
musiktool tape remove <name> <position>...
```

**Arguments:**

- `name` — Project name.
- `position` — One or more positions to remove (1-based). Remaining items
  are renumbered.

**Example:**

```
musiktool tape remove "Friday Mix" 2
musiktool tape remove "Friday Mix" 3 5 7
```

### tape move

Move an item from one position to another.

```
musiktool tape move <name> <from> <to>
```

**Arguments:**

- `name` — Project name.
- `from` — Current position of the item.
- `to` — Target position. Other items shift accordingly.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--side`, `-s` | *(unchanged)* | Pin to side `a` or `b`, or `auto` to clear an existing pin. |

**Example:**

```
# Move item 3 to position 1
musiktool tape move "Friday Mix" 3 1

# Move item 2 to position 1 and pin to side B
musiktool tape move "Mixtape" 2 1 --side b
```

### tape analyze

Analyze the project for compatibility and compute processing parameters.

```
musiktool tape analyze <name>
```

**Performs:**

1. **Duration check** — does content + overhead fit the medium?
2. **Sample rate detection** — determines output sample rate (if `auto`).
3. **LUFS spread** — range of album loudness across items. Reports if spread
   is large (heterogeneous material).
4. **LRA assessment** — flags albums whose dynamic range may not survive the
   medium's noise floor.
5. **Gain computation** — per-item gain to reach target LUFS without exceeding
   peak ceiling (default 0 dBTP).
6. **Limiter/compressor decisions** — whether peaks need limiting, whether
   dynamics need compression for the medium.

**Output:**

```
Friday Mix — VHS-120

  Source sample rates: 44100 Hz (24 tracks), 96000 Hz (2 tracks)
  Output sample rate:  44100 Hz (auto — majority)

  Pos  Item                                         LUFS    Gain    Peak→   Limiter  Comp
  1    Metallica / Master Of Puppets (1986)          -8.2   -5.8    -6.1    no       no
  2    Led Zeppelin / Stairway To Heaven            -10.1   -3.9    -5.1    no       no
  3    Metallica / ...And Justice For All (1988)      -7.4   -6.6    -6.7    no       no

  LUFS spread:  2.7 dB (homogeneous — good)
  LRA range:    5.8 - 9.4 LU (fits medium)
  Duration:     117:47 / 118:00 (fits)

  No issues found.
```

**Warnings (examples):**

```
  ! Duration 122:30 exceeds medium capacity 118:00 by 4:30
  ! LUFS spread 14.2 dB — consider separating classical and rock material
  ! Album "Beethoven / Symphony No. 9" LRA 22.1 LU may exceed medium floor
  ! Track 3 gain +2.1 dB would push true peak to +0.8 dBTP — limiter applied
```

### tape render

Render the project to a directory with FLAC master, CUE sheet, and printable TXT index.

```
musiktool tape render <name> -o <base_directory>
```

**Arguments:**

- `name` — Project name.

**Options:**

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | *(required)* | Base output directory. A subdirectory named after the project (slugified) is created inside. |

**Behavior:**

1. Runs `tape analyze` implicitly if not yet analyzed (or if items changed).
2. Builds the full timeline: lead-in → calibration tone → item 1 → gap →
   item 2 → ... → lead-out.
3. Applies per-item gain, limiter, compressor as computed by analysis.
4. Resamples any tracks not matching output sample rate (SoX-quality resampling).
5. Renders to output format at configured bit depth.
6. Writes metadata tags: project name, item list, rendering parameters.
7. Reports total duration and file size.

**Output (single-sided):**

```
Rendering "Friday Mix" → ./output/friday-mix/ (44100 Hz, 24-bit)

  0:00   Lead-in (25s)
  0:25   [1] Metallica / Master Of Puppets (1986) — gain -5.8 dB
  43:00  Gap (8s)
  43:08  [2] Led Zeppelin / Stairway To Heaven — gain -3.9 dB
  51:10  Gap (4s)
  51:14  [3] Metallica / ...And Justice For All (1988) — gain -6.6 dB
  116:27 Lead-out (20s)

  Done: ./output/friday-mix/ (1.17 GB, 116:47)
    friday-mix.flac
    friday-mix.cue
    friday-mix.txt
```

**Output (two-sided):**

For two-sided media (cassette, vinyl), each side is rendered as a separate
FLAC with its own CUE sheet. A combined TXT index covers both sides.

```
Rendering "Road Trip" → ./output/road-trip/ (44100 Hz, 24-bit)

  === Side A ===
  0:00   Lead-in (25s)
  0:25   [1] Artist / Album One (1986) — gain -5.8 dB
  43:00  Lead-out (20s)

  === Side B ===
  0:00   Lead-in (25s)
  0:25   [2] Artist / Album Two (1988) — gain -3.9 dB
  40:38  Lead-out (20s)

  Done: ./output/road-trip/
    road-trip-side-a.flac (530 MB)
    road-trip-side-a.cue
    road-trip-side-b.flac (490 MB)
    road-trip-side-b.cue
    road-trip.txt
```

### tape index

Generate a CUE sheet and printable text index for a tape project.

```
musiktool tape index <name> -o <basename>
```

**Arguments:**

- `name` — Project name.

**Options:**

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | *(required)* | Output basename (without extension). Generates `<basename>.cue` and `<basename>.txt`. |

**Behavior:**

1. Computes the project timeline (same as `tape show --timeline`).
2. Reads track titles from audio file tags (falls back to filename).
3. Generates a CUE sheet referencing the rendered FLAC master.
4. Generates a printable TXT index for the tape case insert. For two-sided
   media, the TXT includes both sides; each side gets its own CUE sheet.

The CUE sheet uses standard `MM:SS:FF` timestamps (FF = 1/75th second).
Each track in each album gets its own `TRACK` entry with `INDEX 00` (gap
start) and `INDEX 01` (music start). Compatible with `shnsplit`, foobar2000,
and other CUE-aware tools.

**Generated files:**

`<basename>.cue`:
```
REM COMMENT "Rendered by musiktool"
REM DATE 2026-05-03
TITLE "Dylan Classic"
FILE "dylan-classic.flac" WAVE
  TRACK 01 AUDIO
    TITLE "Tangled Up In Blue"
    PERFORMER "Bob Dylan"
    INDEX 00 00:00:00
    INDEX 01 00:20:00
  TRACK 02 AUDIO
    TITLE "Simple Twist Of Fate"
    PERFORMER "Bob Dylan"
    INDEX 01 05:59:50
  ...
```

`<basename>.txt`:
```
DYLAN CLASSIC
VHS Hi-Fi SE-120 | 2026-05-03 | -14 LUFS target

0:20    Blood On The Tracks (1975)                    gain -1.9 dB
          01 Tangled Up In Blue
          02 Simple Twist Of Fate
          03 You're a Big Girl Now
          ...
52:05   Nashville Skyline (1969)                      gain -1.2 dB
          01 Girl From The North Country
          02 Nashville Skyline Rag
          ...
1:19:25 John Wesley Harding (1967)                    gain -1.3 dB
          01 John Wesley Harding
          02 As I Went Out One Morning
          ...
```

### tape delete

Delete a tape project.

```
musiktool tape delete <name>
```

Removes the project and all associated items from the database. Does not
affect source audio files or previously rendered output.

## MEDIUM PRESETS

The `dynamic_range_db` value determines when the compressor triggers.
Higher DR = less compression needed. Choose the preset matching your
actual tape type and noise reduction system. See `medium-selection-guide.md`
for detailed selection help.

### Naming Convention

```
<format>-<length>[-<tape_type>][-<noise_reduction>]
```

Examples: `c-90-ii-dolbyb`, `reel-10-half-15ips-dolbysr`, `md-80`

### VHS Hi-Fi (single-sided)

| Preset | Duration | DR | Notes |
|---|---|---|---|
| `vhs-120` | 118 min | 90 dB | Standard T-120/SE-120 |
| `vhs-160` | 158 min | 90 dB | T-160 |
| `vhs-180` | 176 min | 90 dB | T-180 |

FM-encoded audio — tape type is irrelevant. Hard peak ceiling. SP mode
only (LP/EP not supported). S-VHS and VHS share the same Hi-Fi audio
system.

### Compact Cassette (two-sided)

All cassette presets are two-sided with equal side durations.

**Tape types:**
- Type I (ferric/normal) — base DR 57 dB
- Type II (chrome/high) — base DR 65 dB
- Type IV (metal) — base DR 74 dB

**Noise reduction (additive):**
- No NR: +0 dB
- Dolby B: +10 dB
- Dolby C: +20 dB
- Dolby S: +24 dB (Type II and IV only)

**Available presets (same pattern for C-60 and C-120):**

| Preset | Per Side | DR | Configuration |
|---|---|---|---|
| `c-90` | 45 + 45 | 55 dB | Legacy default (Type I, no NR) |
| `c-90-i` | 45 + 45 | 57 dB | Type I, no NR |
| `c-90-ii` | 45 + 45 | 65 dB | Type II, no NR |
| `c-90-iv` | 45 + 45 | 74 dB | Type IV, no NR |
| `c-90-i-dolbyb` | 45 + 45 | 67 dB | Type I + Dolby B |
| `c-90-ii-dolbyb` | 45 + 45 | 75 dB | Type II + Dolby B |
| `c-90-iv-dolbyb` | 45 + 45 | 84 dB | Type IV + Dolby B |
| `c-90-i-dolbyc` | 45 + 45 | 77 dB | Type I + Dolby C |
| `c-90-ii-dolbyc` | 45 + 45 | 85 dB | Type II + Dolby C |
| `c-90-iv-dolbyc` | 45 + 45 | 94 dB | Type IV + Dolby C |
| `c-90-ii-dolbys` | 45 + 45 | 89 dB | Type II + Dolby S |
| `c-90-iv-dolbys` | 45 + 45 | 98 dB | Type IV + Dolby S |

The bare `c-60`, `c-90`, `c-120` presets are retained for backward
compatibility (Type I, no NR, conservative 55 dB).

### Open Reel (single-sided)

Half-track stereo, standard play (1.5 mil) tape.

| Preset | Duration | DR | Configuration |
|---|---|---|---|
| `reel-7-half-7.5ips` | 30 min | 66 dB | 7" reel, 7.5 ips, no NR |
| `reel-7-half-15ips` | 15 min | 65 dB | 7" reel, 15 ips, no NR |
| `reel-10-half-7.5ips` | 60 min | 66 dB | 10.5" reel, 7.5 ips, no NR |
| `reel-10-half-15ips` | 30 min | 65 dB | 10.5" reel, 15 ips, no NR |

Each also available with NR suffixes: `-dolbya` (+12 dB), `-dolbysr`
(+22 dB), `-dbx` (+30 dB). Example: `reel-10-half-15ips-dolbysr` = 87 dB.

### MiniDisc SP (single-sided)

| Preset | Duration | DR | Notes |
|---|---|---|---|
| `md-60` | 60 min | 93 dB | MD-60 disc |
| `md-74` | 74 min | 93 dB | MD-74 disc |
| `md-80` | 80 min | 93 dB | MD-80 disc |

ATRAC-encoded, hard digital ceiling. Use optical digital input (TOSLINK)
to bypass the recorder's ADC entirely.

### CD-DA (single-sided)

| Preset | Duration | DR | Notes |
|---|---|---|---|
| `cd-74` | 74 min | 96 dB | Standard Red Book disc |
| `cd-80` | 80 min | 96 dB | Extended (700 MB) disc |

16-bit linear PCM, hard digital ceiling. Rendered master suitable for
burning via cdrdao in DAO mode.

### Vinyl LP (two-sided)

| Preset | Duration | Per Side | DR | Notes |
|---|---|---|---|---|
| `vinyl-lp` | 44 min | 22 + 22 | 70 dB | 12" at 33 1/3 RPM |

**Warning:** Vinyl mastering constraints (bass mono summing below 150 Hz,
de-essing at 4-8 kHz, HF rolloff at 16-18 kHz) are **not yet implemented**.
This preset is for duration and side planning only. Do not send rendered
masters to a cutting engineer without additional vinyl-specific processing.

### Custom

| Preset | Duration | DR | Notes |
|---|---|---|---|
| `custom` | user-defined | 90 dB (default) | Requires `--duration` |

### Two-sided behavior

Two-sided media produce separate renders for each side. Items are assigned
to sides automatically (greedy fill: side A first, overflow to side B). If
an album straddles the side boundary, it is split at the best track
boundary — the first N tracks that fit go on side A, the rest on side B.

Use `--side a` or `--side b` on `tape add`, `tape insert`, or `tape move`
to pin items to a specific side. Use `--side auto` on `tape move` to clear
a pin.

### Reference equipment

- **Recorder**: Panasonic AG-7350-E (S-VHS, professional deck)
- **DAC**: Topping D10s (USB, up to 384 kHz / 32-bit)
- **Signal path**: PC → USB → D10s (line out) → AG-7350-E (audio in)

The D10s line output is fixed-level — volume is controlled entirely by the
rendered file's amplitude. The AG-7350-E has calibrated VU meters for
verifying record level.

## DEFAULTS SUMMARY

| Parameter | Default | Rationale |
|---|---|---|
| Lead-in | 25 s | Reaction time to start deck after pressing play on PC |
| Lead-out | 20 s | End-of-tape margin |
| Album gap | 8 s | Clear album boundary, not tedious |
| Track gap | 4 s | Mixtape track separation |
| Marker | none | Opt-in (tone pip between albums) |
| Target LUFS | -14 | Good balance for Hi-Fi tape |
| Bit depth | 24 | Headroom for gain processing |
| Sample rate | auto | Match source material |
| Peak ceiling | -1 dBTP (hard-clip) / 0 dBTP (soft-clip) | Derived from medium preset. Hard-clip media (VHS/MD/CD) use -1 for intersample safety. |
| Limiting budget | 3 dB | Transparent auto-alignment. Use `--limiter` for unlimited. |

## GAIN BEHAVIOR BY GENRE

The gain algorithm targets -14 LUFS with up to 3 dB of transparent peak
limiting. This produces correct behavior across all genres without manual
intervention.

### Pop/Rock (LUFS -8 to -14, LRA 4-10)

Typical modern masters are loud (high LUFS) with peaks already at or above
0 dBFS. The algorithm attenuates by 0-3 dB to bring peaks under ceiling.
Albums land near target. Multiple albums from the same era/genre align well
(small LUFS spread).

```
Metallica / Black Album:  -11.7 LUFS, peak +1.9 → gain -2.3 dB, plays at -14.0
Bob Dylan / Infidels:     -14.0 LUFS, peak +1.4 → gain -1.4 dB, plays at -15.4
```

### Classical / Orchestral (LUFS -18 to -25, LRA 15-23)

Wide dynamic range with rare fortissimo peaks near 0 dBFS. Integrated loudness
is low because most of the performance is piano/mezzo-forte. The 3 dB limiting
budget allows modest gain (+2-3 dB) with transparent peak shaving, bringing
the album closer to target without audibly affecting dynamics.

```
Vivaldi / Four Seasons:   -18.5 LUFS, peak +0.1 → gain +2.9 dB (3 dB limiting), plays at -15.6
Wagner / Symphonic Ring:  -23.1 LUFS, peak -0.9 → gain +2.1 dB (3 dB limiting), plays at -21.0
```

On VHS Hi-Fi (90 dB dynamic range), even at -21 LUFS average the quietest
passages are ~60 dB above the noise floor — tape hiss is inaudible. The
penalty is reported by `tape analyze`. Use `--limiter` to force full
alignment at the cost of dynamics.

### Film Soundtracks (LUFS -15 to -20, LRA 12-17)

Similar to classical — wide dynamics by design (dialogue vs. action). The
budget typically covers the gap: a -17 LUFS soundtrack with peaks at -1
needs only 2 dB of limiting to reach target.

### Compilations / Mixed (variable LUFS, LRA varies)

Albums like "Greatest Hits" or "Kuschelrock" have heterogeneous tracks from
different eras/masters. The album-level LUFS and peak are aggregates — some
tracks may be louder than others within the album. The per-album gain keeps
the overall tape balanced, but individual track loudness variation within a
compilation is preserved (as intended by the compilation mastering).

### Mixing genres on one tape

The LUFS spread warning (>10 dB) flags when albums on the same tape have
very different loudness profiles. For homogeneous tapes (all rock, or all
classical), the spread is typically 1-5 dB. For mixed genre tapes, the
budget brings them closer together (typically within 2-3 dB) but extreme
combinations (classical + loudness-war rock) will still show a gap.
Use `--limiter` for full alignment if volume consistency is the priority.

## EXAMPLES

**Simple album recording:**

```
musiktool tape create "Dylan" --medium vhs-120
musiktool tape add "Dylan" \
  "~/Music/Bob Dylan/Bringing It All Back Home (1965)" \
  "~/Music/Bob Dylan/Blonde On Blonde (1966)" \
  "~/Music/Bob Dylan/Blood On The Tracks (1975)"
musiktool tape analyze "Dylan"
musiktool tape render "Dylan" -o ./output
```

**Mixtape with individual tracks:**

```
musiktool tape create "Road Trip" --medium vhs-120 --marker tone
musiktool tape add "Road Trip" \
  "~/Music/Led Zeppelin/Led Zeppelin IV (1971)/04 Stairway To Heaven.flac" \
  "~/Music/Pink Floyd/Wish You Were Here (1975)/01 Shine On You Crazy Diamond (Parts I-V).flac" \
  "~/Music/Metallica/Master Of Puppets (1986)/01 Battery.flac"
musiktool tape show "Road Trip"
musiktool tape insert "Road Trip" 2 \
  "~/Music/Deep Purple/Machine Head (1972)/02 Smoke On The Water.flac"
musiktool tape analyze "Road Trip"
musiktool tape render "Road Trip" -o ./output
```

**Cassette with side pinning:**

```
musiktool tape create "Road Trip" --medium c-90
musiktool tape add "Road Trip" --side a \
  "~/Music/Roxette/Look Sharp! (1988)"
musiktool tape add "Road Trip" --side b \
  "~/Music/Roxette/Joyride (1991)"
musiktool tape show "Road Trip"
musiktool tape render "Road Trip" -o ./output
# Produces: road-trip-side-a.flac, road-trip-side-b.flac,
#           road-trip-side-a.cue, road-trip-side-b.cue, road-trip.txt
```

**Mixed albums and tracks:**

```
musiktool tape create "Evening" --medium vhs-180 --album-gap 10
musiktool tape add "Evening" \
  "~/Music/Miles Davis/Kind Of Blue (1959)"
musiktool tape add "Evening" \
  "~/Music/Bill Evans/Waltz For Debby (1962)/01 My Foolish Heart.flac"
musiktool tape add "Evening" \
  "~/Music/John Coltrane/A Love Supreme (1965)"
musiktool tape analyze "Evening"
musiktool tape render "Evening" -o ./output
```

## CALIBRATION

Calibration is separate from tape projects. Run once per equipment setup.

### calibrate

Generate calibration tone files for establishing level correspondence
between the DAC output and the deck's VU meters.

```
musiktool calibrate -o <directory> [options]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | *(required)* | Output directory for calibration files. |
| `--sample-rate` | `44100` | Sample rate in Hz (for level tones). |
| `--bit-depth` | `24` | Bit depth: `16` or `24`. |
| `--rates` | off | Also generate sample rate test files. |

**Generated files:**

One file per reference level (2s silence + 30s tone + 2s silence):

```
cal_-20dBFS.flac    1 kHz @ -20 dBFS — primary reference level
cal_-10dBFS.flac    1 kHz @ -10 dBFS — verify linearity
cal_-03dBFS.flac    1 kHz @  -3 dBFS — near-peak behavior
cal_+00dBFS.flac    1 kHz @   0 dBFS — full scale / clipping boundary
```

With `--rates`, also generates `sample-rates/` subdirectory with one file per
standard sample rate (44100–384000 Hz). Each file is a 10s 1 kHz tone at -20 dBFS,
24-bit. Play them to verify the DAC switches natively without resampling.

```
sample-rates/
  44100hz.flac    48000hz.flac    88200hz.flac    96000hz.flac
  176400hz.flac   192000hz.flac   352800hz.flac   384000hz.flac
```

Separate files allow clear identification of each level during playback —
the filename tells you exactly what the deck's meter should read.

**Procedure:**

1. Connect DAC to deck (line out → audio in).
2. Play `cal_-03dBFS.flac` at 100% system volume (no OS mixer attenuation).
3. Adjust the deck's record level control until the meter reads -3. Lock.
4. Verify with the other files: -20, -10, and 0 should all match their
   respective marks on the meter (confirms linearity).

This only needs to be repeated if the signal chain changes (different DAC,
different cables, different deck).

## OUTPUT DIRECTORY

All rendered output goes to the directory you pass to `-o` / `--output`:

```
./output/
├── _calibration/
│   ├── cal_-20dBFS.flac        # Reference tones (one per level)
│   ├── cal_-10dBFS.flac
│   ├── cal_-03dBFS.flac
│   ├── cal_+00dBFS.flac
│   └── sample-rates/           # DAC rate switching test files
│       ├── 44100hz.flac
│       ├── 48000hz.flac
│       └── ...
├── dylan-classic/              # Single-sided project (VHS)
│   ├── dylan-classic.flac      # Rendered tape master
│   ├── dylan-classic.cue       # CUE sheet (for splitting)
│   └── dylan-classic.txt       # Printable index (case insert)
└── road-trip/                  # Two-sided project (cassette)
    ├── road-trip-side-a.flac   # Side A master
    ├── road-trip-side-a.cue    # Side A CUE sheet
    ├── road-trip-side-b.flac   # Side B master
    ├── road-trip-side-b.cue    # Side B CUE sheet
    └── road-trip.txt           # Combined index (both sides)
```

## RECORDING PROCEDURE

1. **Calibrate** (once): `musiktool calibrate -o .../render/_calibration/ --rates`
   → play `cal_-03dBFS.flac` → adjust record level → verify others.
   → play sample rate files to verify DAC rate switching.
2. **Create project**: `musiktool tape create ... → tape add ... → tape analyze`.
3. **Render**: `musiktool tape render "Project" -o .../render/`
   → creates `project/` directory with `.flac`, `.cue`, and `.txt`.
4. **Record**: Play the master FLAC at 100% volume. Press record on the deck
   during the lead-in silence. The remaining silence lets the tape transport
   stabilize before music begins.
5. **Stop**: After the lead-out silence, stop the deck.
6. **Label**: Print the `.txt` index and insert into VHS case.

## SEE ALSO

- `musiktool-library(1)` — agent-assisted library audit and fix plans
- `musiktool-analyze(1)` — bulk EBU R128 analysis
- `musiktool-loudness(1)` — single track/album loudness measurement
- `docs/library-cli.md` — library-maintenance command reference
- `docs/playback-normalization.md` — design document for gain calculation and filter chains
