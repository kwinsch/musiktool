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

Each output medium has physical constraints:

| Property | Digital | VHS (Hi-Fi) | Cassette | Broadcast |
|----------|---------|-------------|----------|-----------|
| Dynamic range | ~144 dB (24-bit) | ~80 dB | ~55-65 dB | per spec |
| Target LUFS | -14 | medium-dependent | medium-dependent | -23 (EBU R128) |
| Peak ceiling | -1 dBTP | soft (saturation) | soft (saturation) | -1 dBTP |
| Duration limit | unlimited | ~180 min (T-120) | 45/60/90 min | unlimited |
| Lead-in | none | 15 s silence | 5 s silence | none |
| Lead-out | none | 15 s silence | 5 s silence | none |
| Inter-album gap | 3 s | 5 s silence | 5 s silence | per schedule |

Lead-in and lead-out are mandatory silent sections at the start/end of the medium. Tape stock at these positions may produce degraded audio (stretch, oxide irregularities). The lead-in/lead-out durations are configurable per profile.

## Gain Calculation

```
gain = min(target_LUFS - measured_LUFS, -(true_peak_dBTP + headroom))
```

- In album mode: use album integrated LUFS and album max true peak
- `headroom` is a positive value in dB (e.g., 1.0 means peak ceiling at -1 dBTP)
- Accept that some content can't reach target without clipping — play it quieter rather than destroy dynamics, unless a limiter is explicitly part of the medium profile

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

- RIAA equalization
- Groove velocity limits (bass mono summing considerations)
- Side duration constraints

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

CREATE TABLE medium_profile (
    name TEXT PRIMARY KEY,          -- 'digital-14', 'vhs-hifi', 'cassette-c90'
    target_lufs REAL NOT NULL,
    peak_ceiling_dbtp REAL,         -- NULL for soft-ceiling media (tape)
    dynamic_range_db REAL NOT NULL,
    max_duration_sec REAL,          -- NULL for unlimited
    lead_in_sec REAL NOT NULL DEFAULT 0,
    lead_out_sec REAL NOT NULL DEFAULT 0,
    inter_album_gap_sec REAL NOT NULL DEFAULT 3,
    notes TEXT
);

CREATE TABLE session (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,             -- 'Friday VHS Mix', 'Metallica Marathon'
    medium TEXT NOT NULL REFERENCES medium_profile(name),
    created_at TEXT NOT NULL
);

CREATE TABLE session_album (
    session_id INTEGER NOT NULL REFERENCES session(id),
    album_path TEXT NOT NULL REFERENCES album_loudness(path),
    position INTEGER NOT NULL,      -- playback order
    -- computed per session analysis:
    computed_gain_db REAL,
    needs_limiter INTEGER,          -- 0/1
    needs_compressor INTEGER,       -- 0/1
    compressor_params TEXT,         -- JSON: ratio, threshold, knee, attack, release
    limiter_params TEXT,            -- JSON: limit, attack, release
    warnings TEXT,                  -- compatibility warnings
    PRIMARY KEY (session_id, position)
);
```

### Incremental Scan

`musiktool analyze` compares `file_mtime` in the DB against the file's current mtime. Only re-analyzes tracks whose files have changed. Album-level aggregates are recomputed when any constituent track is re-analyzed.

## Session Workflow

```
1. Analyze library (once, then incremental)
   musiktool analyze ~/Music  →  ~/.local/share/musiktool/analytics.db

2. Create session
   musiktool session create "Friday VHS" --medium vhs-hifi \
     "~/Music/Metallica/Master Of Puppets (1986)" \
     "~/Music/Metallica/...And Justice For All (1988)" \
     "~/Music/Metallica/Metallica (1991)"

3. Analyze session (compatibility check + gain computation)
   musiktool session analyze "Friday VHS"
   → reports: LUFS spread, LRA spread, duration fit, warnings
   → computes: per-album gain, limiter/compressor decisions
   → stores results in session_album table

4. Review & adjust
   musiktool session show "Friday VHS"
   → displays per-album chain with computed parameters

5. Render / play
   musiktool session render "Friday VHS" -o output.wav
   musiktool session play "Friday VHS"
   → builds ffmpeg filter chain per album
   → inserts lead-in, inter-album gaps, lead-out
   → outputs to file / PipeWire / device
```

## Architecture

```
musiktool analyze ~/Music  →  ~/.local/share/musiktool/analytics.db
                                      │
           session definition ────────────┤
           (albums + medium profile)      │
                                          ▼
                              session analyzer
                    (compatibility check, gain/chain decisions)
                                          │
                                          ▼
                              filter chain builder
              (per-album: gain + limiter + compressor + medium-specific)
                                          │
                                          ▼
              lead-in ─ album1 ─ gap ─ album2 ─ gap ─ album3 ─ lead-out
                                          │
                                          ▼
              ffmpeg pipeline → output (PipeWire / file / tape deck)
```

## Implementation Steps

1. ~~`musiktool analyze` — bulk EBU R128 scan → SQLite DB (incremental)~~ **done** (2026-05-03: 124 albums, 1851 tracks)
2. Medium profiles — built-in defaults (digital, vhs-hifi, cassette, broadcast)
3. Session management — create, list, show, delete sessions
4. Session analyzer — compatibility assessment, gain computation, chain decisions
5. Filter chain builder — translates decisions into ffmpeg filter graphs
6. Renderer/player — assembles full output with gaps and padding
