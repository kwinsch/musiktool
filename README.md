# musiktool

Music library management and tape mastering CLI.

## The Problem

A typical music library contains albums mastered at wildly different loudness levels and dynamic ranges. When you want to record a selection of albums onto physical media (VHS Hi-Fi, cassette, reel-to-reel), you face several practical problems:

- Different albums need different gain to reach the same perceived loudness.
- Some material will clip the target medium; others will be too quiet.
- Album-internal dynamics must be preserved (no per-track ReplayGain).
- Physical media have hard duration limits, mandatory lead-in/lead-out, and specific gap conventions.
- You need a proper CUE sheet and a printable track listing for the tape case or box.

Existing tools either work per-track (destroying album intent) or have no understanding of analog tape constraints.

## The Idea

`musiktool` treats a **mastered album as an atomic unit**. All gain decisions for an album use a single album-level value derived from its integrated loudness and true peak. A tape project is a session of albums (or individual tracks) assigned to one physical medium. The tool:

- Measures everything with EBU R128 (never trusts existing ReplayGain tags).
- Knows the characteristics of each medium (dynamic range, target LUFS, peak ceiling, lead-in/out times, gap rules).
- Computes per-item gain, optionally applies a limiter or gentle compressor, and warns about problematic material (high LRA, large LUFS spread).
- Renders a single master FLAC with correct silence gaps, optional marker tones, CUE sheet, and a human-readable TXT index that shows deck-counter timestamps.

Two-sided media (cassette, vinyl, some VHS) are fully supported, including automatic album splitting at the best track boundary.

## Current Features

### Library Management
- `identify` – fingerprint tracks or whole albums via AcoustID/MusicBrainz and optionally write tags.
- `flatten` – move albums out of `cd_flac/`, `cd_alac/`, `cd-loosy/` subdirectories into a clean `Artist/Album` layout.
- `analyze` – incremental EBU R128 measurement of a library or single album (mtime-based, stores results in `analytics.db`).
- `loudness` – quick per-track or per-album measurement.
- `tags` – inspect current tags.
- `audit` / `inspect` / `apply` – agent-facing JSON/NDJSON library audit and validated fix plans.

### Tape Mastering (`musiktool tape`)
- Create projects for VHS, cassette, or custom lengths.
- Add albums or individual tracks; pin items to side A or B.
- Analyze the session for compatibility and compute exact gain + processing per item.
- Render to FLAC + CUE + printable TXT (with deck counter timestamps).
- Full two-sided support with automatic album splitting and combined index.

### Calibration
- `calibrate` – generate 1 kHz reference tones at multiple levels for setting deck VU meters.
- `--rates` mode produces sample-rate test files.

See `docs/tape-cli.md` for the tape command reference and
`docs/library-cli.md` for the agent-assisted library maintenance commands
(pseudo-manpage style).

## Planned Expansions (Library Side)

- Better handling of multi-disc albums and compilations.
- More automatic suggestions for common tagging problems.
- Integration with existing ReplayGain scanners (read-only, for comparison).
- Support for more output formats (WAV, Opus, etc.) in the tape renderer.
- A simple TUI or web view for managing large tape projects.

The tape mastering pipeline is considered feature-complete for current needs; future work will focus on making day-to-day library maintenance more pleasant.

## Quick Start

### 1. Install

```bash
git clone https://.../musiktool
cd musiktool
uv sync
```

### 2. One-time Calibration (for tape work)

```bash
musiktool calibrate -o ~/tape-projects --sample-rate 44100 --bit-depth 24
# Play each file at 100% volume on your DAC and adjust the deck's input level
# until the VU meters match the file name (e.g. cal_+00dBFS).
```

### 3. Analyze Your Library (once)

```bash
musiktool analyze ~/Music
```

Tape commands read the default analytics database at
`~/.local/share/musiktool/analytics.db`. The `analyze --db` option is for
standalone/custom analysis workflows and is not used by `musiktool tape`
commands in this version.

### 4. Create a Tape Project

You can build a project from whole albums or from individual tracks (classic mixtape style).

**Album-oriented project**
```bash
musiktool tape create "Friday Mix" --medium vhs-120
musiktool tape add "Friday Mix" ~/Music/Artist/Album1 ~/Music/Artist/Album2
musiktool tape show "Friday Mix" --timeline
```

**Per-track mixtape**
```bash
musiktool tape create "Late Night Mixtape" --medium vhs-120
musiktool tape add "Late Night Mixtape" ~/Music/Artist/01-Track.flac ~/Music/Artist/03-Another.flac
```

### 5. Analyze & Render

```bash
musiktool tape analyze "Friday Mix"
musiktool tape render "Friday Mix" -o ~/tape-projects/output
# Produces: friday-mix.flac, friday-mix.cue, friday-mix.txt
```

### Real Hardware Example (Panasonic AG-7350 + Topping D10s)

1. Run the calibration tones from `~/tape-projects/` through the Topping D10s into the AG-7350 line input.
2. Adjust the deck's recording level so that `cal_+00dBFS.flac` hits 0 VU on the deck meters.
3. Create and render your project as shown above.
4. The generated `friday-mix.txt` gives you exact deck-counter timestamps for each album and track — perfect for writing on the tape label or case insert.
5. The `.cue` file lets you verify the final master in any CUE-aware player before committing to tape.

## Documentation

- `docs/tape-cli.md` – detailed command reference and output examples
- `docs/playback-normalization.md` – deeper explanation of gain, medium profiles, and dynamic-range considerations

---

`musiktool` is a personal tool for turning a digital library into high-quality analog tape masters with minimal fuss.
