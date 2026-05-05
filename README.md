# musiktool

Agent-first music library manager. The CLI is designed for AI agents — humans
interact through prompt conversations with the LLM, which uses musiktool to
inspect, organize, and master the collection.

## Why Not Beets

Beets makes opaque decisions you can't audit or override. Its autotagger
matches something wrong, you fight the config, and next import it happens
again. The same pattern repeats with MusicBrainz Picard: rule-based matching
that fails on edge cases with no way to inject judgment.

musiktool inverts this. The tool reports structured evidence (tags, fingerprints,
loudness, file identity). The agent reasons about it — with actual music
knowledge — and proposes a fix plan. The human reviews and approves. The tool
validates and executes.

An LLM can tell you "this is a 1997 remaster, not the 1975 original — the
tracklist has bonus tracks" in a way no rule engine ever will.

## Architecture

```
  Human  <-->  LLM agent  <-->  musiktool CLI
                                    |
                              JSON / NDJSON
                              structured evidence
                              validated fix plans
```

All agent-facing commands emit schema-versioned JSON with confidence scores
and evidence dictionaries. Text output is available for interactive use but
the JSON contract is the compatibility surface.

The library workflow is split into three phases:

1. **audit** — report findings and evidence. Never changes files.
2. **inspect** — deeper evidence for one album, directory, or track.
3. **apply** — validate a fix plan, dry-run by default, whitelisted actions only.

## Features

### Library Management

- `identify` — fingerprint tracks or albums via AcoustID/MusicBrainz, optionally write tags
- `tags` — inspect current tags
- `flatten` — restructure format-based subdirs into `Artist/Album (Year)/` layout
- `analyze` — incremental EBU R128 measurement (mtime-based, stores in analytics DB)
- `index` — sidecar-backed file index with BLAKE3 hashes and Chromaprint fingerprints
- `loudness` — quick per-track or per-album EBU R128 measurement
- `audit` / `inspect` / `propose` / `apply` — agent-facing library audit with validated fix plans
- Chromaprint similarity matching (BER) for detecting transcoded duplicates (FLAC vs M4A)

The library model uses media-kind separation (music, radio, audiobooks, podcasts)
so different content types get appropriate rules instead of being forced into
music album conventions. See `docs/library-cli.md`.

### Tape Mastering (`musiktool tape`)

Remaster albums or individual tracks onto physical media (VHS Hi-Fi, cassette,
reel-to-reel, MiniDisc, vinyl, CD-R). The tool treats albums as atomic units —
all gain decisions use album-level integrated loudness, never per-track.

- Create projects for 59+ medium presets with known characteristics
- Add albums or tracks, pin items to sides, automatic album splitting
- Analyze for compatibility, compute gain + optional limiter/compressor
- Render to FLAC + CUE + printable TXT with deck counter timestamps
- Full two-sided support for cassette, vinyl, and partitioned VHS

See `docs/tape-cli.md` for the command reference.

### Calibration

- `calibrate` — generate reference tones for setting deck VU meters
- `--rates` mode for DAC sample rate verification (44.1k–384k)

## Planned

- **Playlists** — named track lists, M3U export, agent-buildable from prompts
- **Playback** — `musiktool play <album|playlist|tape>` via PipeWire/mpv
- **Profile-aware audit** — separate rules for music, radio, audiobook, podcast
- **Operator report** — grouped text output for day-to-day use without agent

No UI is planned. The CLI + agent conversation is the interface.

## Quick Start

```bash
cd /path/to/musiktool
uv sync

# Analyze your library
musiktool analyze ~/Music

# Audit for problems (agent-facing JSON)
musiktool audit ~/Music --format json

# Identify an album via fingerprinting
musiktool identify ~/Music/Artist/Album

# Create and render a tape project
musiktool tape create "Friday Mix" --medium vhs-120
musiktool tape add "Friday Mix" ~/Music/Artist/Album1
musiktool tape analyze "Friday Mix"
musiktool tape render "Friday Mix" -o ~/tape-projects/output
```

## Documentation

- `docs/agent-workflow.md` — how AI agents should use musiktool (start here)
- `docs/library-cli.md` — agent-assisted library maintenance commands
- `docs/tape-cli.md` — tape mastering command reference
- `docs/playback-normalization.md` — gain, medium profiles, dynamic range
- `docs/medium-selection-guide.md` — choosing the right recording medium
- `docs/tape_recording_reference.md` — cassette, reel, VHS technical reference
- `docs/disc_recording_reference.md` — CD, MiniDisc technical reference
- `docs/vinyl_recording_reference.md` — vinyl mastering reference
