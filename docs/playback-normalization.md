# Playback Normalization & Filter Chains

## Problem

Albums have different mastering loudness, dynamic range, and peaks. A single output session may contain multiple albums destined for one medium (e.g., 3 albums on a VHS tape). The challenge is aligning all material to utilize the medium's dynamic range optimally without destroying the music's characteristics.

Existing ReplayGain tags in files are explicitly ignored — they are unreliable (unknown provenance, unknown reference level) and per-track normalization breaks the intentional dynamics of a mastered album. All measurements are performed independently using EBU R128.

## Core Concepts

### Album as Atomic Unit

A mastered album is an atomic piece of work. The relative loudness between its tracks is intentional and must be preserved. All gain decisions within an album use a single album-level gain value derived from the album's integrated loudness and maximum true peak.

### Session

A session is a list of albums (or individual tracks) assigned to one output medium. Examples:
- 3 Metallica albums → VHS tape
- 1 classical + 1 rock album → VHS tape (possibly incompatible)
- Friday playlist → digital playback

The session is the unit of mastering — all material in a session must be analyzed together to find optimal processing for the target medium.

### Medium Profile

Each output medium has physical constraints. The key variable is
**dynamic range** — it determines when the compressor triggers. All other
rendering parameters (target LUFS, peak ceiling, gap durations) are
constant across media.

| Property | VHS Hi-Fi | Cassette (Type I–IV) | Open Reel | MiniDisc | Vinyl LP |
|----------|-----------|---------------------|-----------|----------|----------|
| Dynamic range | 90 dB | 55–98 dB | 65–96 dB | 93 dB | 70 dB |
| Signal encoding | FM | Amplitude | Amplitude | Digital (ATRAC) | Mechanical |
| Peak behavior | Hard clip | Soft saturation | Soft saturation | Hard clip | Groove limit |
| Duration | 118–176 min | 30–60 min/side | 15–60 min | 60–80 min | 22 min/side |
| Sides | 1 | 2 | 1 | 1 | 2 |

Dynamic range varies widely within cassette and open reel because it
depends on tape type and noise reduction:

| Configuration | Dynamic Range | Compressor triggers at LRA > |
|---------------|--------------|------------------------------|
| Cassette Type I, no NR | 57 dB | 14.25 LU |
| Cassette Type II, Dolby B | 75 dB | 18.75 LU |
| Cassette Type IV, Dolby C | 94 dB | 23.5 LU |
| Open reel half-track, no NR | 65–66 dB | 16.25 LU |
| Open reel + Dolby SR | 87–88 dB | 21.75–22.0 LU |
| VHS Hi-Fi | 90 dB | 22.5 LU |
| MiniDisc SP | 93 dB | 23.25 LU |

See `medium-selection-guide.md` for the full preset reference (61 presets
covering all practical tape type + NR combinations, plus CD-DA and vinyl).

Lead-in and lead-out are mandatory silent sections at the start/end of the
medium. Tape stock at these positions may produce degraded audio (stretch,
oxide irregularities). The lead-in/lead-out durations are configurable per
profile.

## Architecture: Digital vs Physical Domain

The rendering pipeline operates entirely in the digital domain. Physical
medium characteristics (tape saturation, NR encoding, EQ curves) are
handled by the recording equipment after the DAC.

### Signal Level Diagram

```
                         0 dBFS (digital ceiling)
                            │
    ┌── peaks ──────────────┤  ← limiter catches up to 3 dB
    │                       │    automatically (budget)
    │   ← dynamic range →  │
    │                       │
    ├── average (-14 LUFS) ─┤  ← calibration maps THIS to the
    │                       │    correct recording level on
    │   ← dynamic range →  │    the deck's meters
    │                       │
    ├── quiet passages ─────┤
    │                       │
    │   ... margin ...      │  ← must exist, or tape hiss
    │                       │    becomes audible
    └── noise floor ────────┤  ← determined by medium
                                 (tape type + noise reduction)
```

Peak ceiling depends on medium type:
- Hard-clip media (VHS, MD, CD): ceiling -1 dBTP (intersample safety)
- Soft-clip media (cassette, reel): ceiling 0 dBTP (tape saturates gently)

### Four Independent Concerns

**1. Target LUFS** — where the average sits in the digital window.
Constant at -14 LUFS across all media. The calibration procedure maps
this to the correct physical recording level for each medium.

**2. Calibration** — the bridge between digital and physical. Maps the
DAC's output to the deck's meters. One-time procedure per equipment
setup. NOT part of the rendering pipeline.

**3. Limiting budget** — when gain to reach target LUFS would push peaks
above ceiling, up to 3 dB of transparent peak limiting is applied
automatically. Beyond 3 dB, gain is reduced (album plays quieter than
target). With `--limiter`, the budget is unlimited. See "Gain Alignment
& Limiting Budget" section below.

**4. Compression** — the only decision driven by `dynamic_range_db`.
When an album's LRA exceeds what the medium can capture (quiet passages
would fall below the noise floor), gentle compression reduces the range
to fit.

### Signal Flow

```
    Source Album (measured: LUFS, true peak, LRA)
             │
             ▼
    ┌─────────────────────────────────────┐
    │     GAIN + LIMITING BUDGET          │
    │  gain = target_LUFS - measured_LUFS │
    │  bounded by ceiling + 3 dB budget   │
    │  --limiter: unlimited budget        │
    └─────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────┐
    │     COMPRESSION DECISION            │
    │  Does LRA exceed medium's window?   │
    │  Varies per medium subtype.         │
    │  Driven by dynamic_range_db.        │
    └─────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────┐
    │         PEAK LIMITER                │
    │  Catches peaks above ceiling.       │
    │  Always present for hard-clip.      │
    │  Present when budget applied gain   │
    │  beyond max_safe.                   │
    └─────────────────────────────────────┘
             │
             ▼
        Digital Master (FLAC)
             │
             ▼
    ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
    │         CALIBRATION                 │
      Maps digital output to physical.
    │ One-time, per equipment setup.      │
      NOT part of rendering pipeline.
    │                                     │
    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
             │
             ▼
        Physical Medium
```

## Gain Alignment & Limiting Budget

### The Problem

Albums have different peak-to-loudness ratios (crest factors). A rock album
at -8 LUFS typically has peaks at 0 dBFS (crest factor 8 dB). A classical
album at -22 LUFS may also have peaks at -1 dBFS (crest factor 21 dB).

To align both to -14 LUFS target:
- Rock: gain -6 dB. Peaks drop to -6. No conflict.
- Classical: gain +8 dB. Peaks would hit +7. Exceeds any ceiling.

The naive solutions fail:
- **Cap gain** (play quieter): classical plays 8 dB below target. Volume
  drop between albums defeats the tape's purpose.
- **Full gain + unlimited limiting**: 8 dB of peak limiting crushes
  fortissimos. The dynamics that define classical music are destroyed.

### The Solution: Limiting Budget

A bounded amount of peak limiting (default 3 dB) is applied automatically.

```
TRANSPARENT_LIMITING_BUDGET_DB = 3.0

desired_gain = target_LUFS - measured_LUFS
max_safe_gain = peak_ceiling_dBTP - true_peak_dBTP

if desired_gain <= max_safe_gain:
    gain = desired_gain          # Target reached without limiting
else:
    gain = min(desired_gain, max_safe_gain + budget)
    penalty = desired_gain - gain  # How far below target
```

- In album mode: use album integrated LUFS and album max true peak
- `peak_ceiling_dBTP` defaults to -1 for hard-clip media (VHS/MD/CD),
  0 for soft-clip media (cassette/reel)
- The limiter catches `gain - max_safe_gain` dB of peaks (always ≤ budget)

### Why 3 dB

True peaks are transients — drum attacks, piano hammers, brass staccato.
The ear integrates loudness over ~20-50 ms. A limiter with 5 ms attack and
50 ms release shaving ≤3 dB catches content the auditory system doesn't
resolve as "loud."

At 3+ dB, sustained high-level content (organ pedal tones, brass fortissimo
held notes) begins to be audibly affected. At 5+ dB, the "squashing" is
obvious. At 8+ dB, dynamics are destroyed.

3 dB is the boundary of transparent operation.

### Behavior by Material

| Material | LUFS | Peak | Desired | Gain | Penalty | Limiter |
|----------|------|------|---------|------|---------|---------|
| Loudness war rock | -8 | +0.5 | -6 dB | -6 dB | 0 | 0 |
| Normal rock | -12 | -0.5 | -2 dB | -2 dB | 0 | 0 |
| Dylan-era rock | -14 | -1.5 | 0 dB | 0 dB | 0 | 0 |
| Jazz | -16 | -0.5 | +4 dB | +2.5 dB | 1.5 dB | 3 dB |
| Orchestral | -18 | -0.5 | +4 dB | +2.5 dB | 1.5 dB | 3 dB |
| Extreme classical | -22 | -1 | +8 dB | +3 dB | 5 dB | 3 dB |

The majority of CD-ripped material (LUFS -6 to -14) needs zero limiting.
Jazz and orchestral get ≤3 dB of transparent limiting. Only extreme
classical on a loud rock tape produces a significant penalty — and the
LUFS spread warning flags this as an incompatible combination.

### The --limiter Flag

With `--limiter`, the budget is unlimited. ALL albums reach exactly -14 LUFS
regardless of how much limiting is needed. This is the user's explicit choice
to prioritize volume consistency over dynamics.

Use `--limiter` when:
- Making a mixtape where volume consistency across tracks matters most
- Recording background music where dynamic surprise is unwanted
- The LUFS spread between albums is moderate (5-10 dB)

Do NOT use `--limiter` when:
- Recording a single classical album (defeats the mastering intent)
- The LUFS spread exceeds 10 dB (incompatible material — separate tapes)

### Interaction with Compressor

The limiter and compressor solve different problems:

| Concern | Tool | Driven by |
|---------|------|-----------|
| Peaks prevent reaching target LUFS | Limiter (budget) | Peak-to-loudness ratio |
| Quiet passages below medium noise floor | Compressor | LRA vs medium DR |

Signal chain: gain → compressor → limiter. The compressor may raise quiet
passages (creating new peaks); the limiter is always last as the final
safety net.

## Session Analysis

Before building filter chains, the session must be analyzed as a whole:

### Per-Album Metrics (from DB)

- Integrated LUFS (album-level)
- Max true peak (dBTP, worst track in album)
- LRA (loudness range — dynamic spread)
- Track count, total duration

### Session-Level Analysis

```
album_lufs_spread  = max(album_LUFS) - min(album_LUFS)    # dB spread across albums
album_lra_spread   = max(album_LRA) - min(album_LRA)      # dynamic range variance
session_duration   = sum(album_durations) + gaps + lead_in + lead_out
```

### Compatibility Assessment

Not all album combinations work on all media. A classical album (LRA ~20, LUFS ~-25) next to a loudness-war rock album (LRA ~5, LUFS ~-8) presents a 17 dB loudness gap and vastly different dynamics. On a medium with limited dynamic range (tape), this may be impossible to reconcile without audibly degrading one or both.

The analysis should flag compatibility issues:

- **LUFS spread vs. medium dynamic range**: If the spread between quietest and loudest album exceeds the medium's usable range, the session is problematic
- **LRA vs. medium dynamic range**: If any album's LRA exceeds what the medium can capture, compression is needed — assess how much and whether it alters the character
- **Duration vs. capacity**: Total session duration (including gaps, lead-in/out) must fit the medium
- **Homogeneous sessions** (similar genre/era): straightforward — small LUFS spread, similar LRA, single gain offset per album aligns them
- **Heterogeneous sessions** (mixed genre): may require per-album processing chains, or may need to be flagged as incompatible for the chosen medium

Compatibility is reported as warnings, not hard blocks — the user makes the final mastering decision.

## Filter Chain Construction

The filter chain for each album in a session depends on:

1. **Album measurements** (LUFS, true peak, LRA)
2. **Session context** (where this album sits relative to others)
3. **Medium profile** (dynamic range, saturation, peak handling)

### Digital Playback

Simplest case — gain only, peak-clamped:

```
volume=${gain}dB
```

If gain would push true peak above ceiling, two options:
- Reduce gain (play quieter) — default, preserves dynamics
- Apply true peak limiter (catches only the few offending peaks) — optional, when the loudness penalty is unacceptable

### Tape Output (VHS, Cassette)

Tape has a soft ceiling (saturation) and a noise floor. The goal is to place the music optimally within this window:

1. **Gain**: Align album to target recording level
2. **Limiter**: Catch peaks that would cause audible distortion (tape saturation is forgiving for transients but not for sustained overs)
3. **Compressor**: If album LRA exceeds medium's usable dynamic range, apply gentle compression to fit — ratio and threshold derived from the gap between LRA and medium range
4. **Pre-emphasis**: If medium profile specifies it (some tape formats use pre-emphasis for noise reduction)

```
volume=${gain}dB, acompressor=..., alimiter=...
```

Note: limiter must be last — the compressor can create new peaks, so the limiter guarantees nothing exceeds the ceiling.

The compressor parameters are not static — they are computed from the album's LRA relative to the medium's dynamic range. An album with LRA 8 on a medium with 60 dB range needs no compression. An album with LRA 25 on the same medium might.

### Future: Vinyl Cutting

Basic vinyl-lp preset exists (duration/side constraints, two-sided rendering).
Not yet implemented:

- RIAA equalization
- Groove velocity limits (bass mono summing considerations)

### Future: Broadcast

- -23 LUFS (EBU R128)
- True peak limiter at -1 dBTP
- Mandatory LRA constraints per spec

## Analytics DB

SQLite at `~/.local/share/musiktool/analytics.db` (local filesystem — SQLite doesn't work reliably over CIFS/SMB):

```sql
CREATE TABLE track_loudness (
    path TEXT PRIMARY KEY,
    integrated_lufs REAL NOT NULL,
    true_peak_dbtp REAL NOT NULL,
    lra REAL NOT NULL,
    duration_sec REAL NOT NULL,
    album_path TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,       -- ISO 8601
    file_mtime REAL NOT NULL        -- for incremental scan
);

CREATE TABLE album_loudness (
    path TEXT PRIMARY KEY,
    integrated_lufs REAL NOT NULL,
    true_peak_dbtp REAL NOT NULL,   -- max across all tracks
    lra REAL NOT NULL,
    track_count INTEGER NOT NULL,
    duration_sec REAL NOT NULL,     -- sum of tracks
    analyzed_at TEXT NOT NULL
);

CREATE TABLE tape_project (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    medium TEXT NOT NULL,            -- preset name (e.g. 'vhs-120', 'c-90')
    duration_sec REAL NOT NULL,
    lead_in_sec REAL NOT NULL DEFAULT 25,
    lead_out_sec REAL NOT NULL DEFAULT 20,
    album_gap_sec REAL NOT NULL DEFAULT 8,
    track_gap_sec REAL NOT NULL DEFAULT 4,
    marker TEXT NOT NULL DEFAULT 'none',
    marker_freq REAL NOT NULL DEFAULT 400,
    marker_level_dbfs REAL NOT NULL DEFAULT -30,
    marker_duration_sec REAL NOT NULL DEFAULT 0.5,
    target_lufs REAL NOT NULL DEFAULT -14,
    peak_ceiling_dbtp REAL NOT NULL DEFAULT 0,
    sample_rate TEXT NOT NULL DEFAULT 'auto',
    bit_depth INTEGER NOT NULL DEFAULT 24,
    use_limiter INTEGER NOT NULL DEFAULT 0,
    use_compressor INTEGER NOT NULL DEFAULT 0,
    side_a_duration_sec REAL,       -- NULL for single-sided
    side_b_duration_sec REAL,       -- NULL for single-sided
    created_at TEXT NOT NULL
);

CREATE TABLE tape_item (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES tape_project(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL,         -- 'album' or 'track'
    path TEXT NOT NULL,
    side TEXT DEFAULT NULL,          -- 'a', 'b', or NULL (auto-assign)
    UNIQUE(project_id, position)
);
```

Medium presets (duration, dynamic range, side count) are hardcoded in `constants.py`, not stored in the database. Computed fields (gain, limiter/compressor decisions) are calculated at runtime by the analysis engine and stored in `ItemAnalysis` dataclasses — they are not persisted in the DB.

### Incremental Scan

`musiktool analyze` compares `file_mtime` in the DB against the file's current mtime. Only re-analyzes tracks whose files have changed. Album-level aggregates are recomputed when any constituent track is re-analyzed.

## Tape Workflow

```
1. Analyze library (once, then incremental)
   musiktool analyze ~/Music

2. Create project
   musiktool tape create "Friday VHS" --medium vhs-120

3. Add content
   musiktool tape add "Friday VHS" \
     "~/Music/Metallica/Master Of Puppets (1986)" \
     "~/Music/Metallica/...And Justice For All (1988)" \
     "~/Music/Metallica/Metallica (1991)"

4. Analyze (compatibility check + gain computation)
   musiktool tape analyze "Friday VHS"
   → reports: LUFS spread, LRA range, duration fit, warnings
   → computes: per-item gain, limiter/compressor decisions (runtime only, not persisted)

5. Review & adjust
   musiktool tape show "Friday VHS"
   musiktool tape show "Friday VHS" --timeline

6. Render
   musiktool tape render "Friday VHS" -o ./output
   → builds ffmpeg filter chain per item
   → inserts lead-in, inter-album gaps, lead-out
   → outputs FLAC + CUE + TXT to project directory
   → two-sided media: per-side FLAC/CUE + combined TXT
```

## Architecture

```
musiktool analyze ~/Music  →  ~/.local/share/musiktool/analytics.db
                                      │
           tape project ──────────────┤
           (items + medium preset)    │
                                      ▼
                          project analyzer
                (compatibility check, gain/chain decisions)
                                      │
                                      ▼
                          filter chain builder
          (per-item: gain + limiter + compressor + medium-specific)
                                      │
                                      ▼
          lead-in ─ item1 ─ gap ─ item2 ─ gap ─ item3 ─ lead-out
                                      │
                                      ▼
          ffmpeg pipeline → FLAC + CUE + TXT (per side for two-sided)
```

## Implementation Status

All steps are complete as of 2026-05-03:

1. ~~`musiktool analyze` — bulk EBU R128 scan → SQLite DB (incremental)~~ (124 albums, 1851 tracks)
2. ~~Medium presets — VHS, cassette, vinyl hardcoded in `constants.py`~~
3. ~~Tape project management — create, list, show, add, insert, remove, move, delete~~
4. ~~Project analyzer — compatibility assessment, gain computation, limiter/compressor decisions~~
5. ~~Segment builder — translates decisions into ffmpeg filter graphs~~
6. ~~Renderer — assembles full output with gaps, markers, CUE + TXT indices~~
7. ~~Two-sided media — cassette and vinyl with auto-split and side pinning~~
