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
- `stats` — active library overview with audio formats, side files, provenance, tag coverage, and index coverage
- `itunes` — parse old iTunes XML libraries, list albums, and generate copy-only import plans
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

### Playback (`musiktool play` / `musiktool player`)

Non-blocking playback via mpv with EBU R128 loudness normalization (-14 LUFS
target). Uses album-level gain — all tracks in an album share one gain value,
preserving the mastering intent.

- `play <file|album>` — play with automatic gain normalization from analytics DB
- `play --queue` / `--next` — append or insert into running playlist
- `player status|pause|skip|prev|stop` — control running mpv instance
- `tape play <name>` — play rendered tape output or source tracks with processing
- Graceful degradation: plays without normalization if loudness data is missing

mpv runs as a background subprocess with JSON IPC — the agent conversation
continues while music plays.

### Calibration

- `calibrate` — generate reference tones for setting deck VU meters
- `--rates` mode for DAC sample rate verification (44.1k–384k)

## Planned

- **Playlists** — named track lists, M3U export, agent-buildable from prompts

No UI is planned. The CLI + agent conversation is the interface.

## Dependencies

**Python**: 3.13+

**System packages** (must be on PATH):

| Tool | Used for | Required |
|------|----------|----------|
| `ffmpeg` | EBU R128 measurement, format conversion, tape rendering | Yes |
| `mpv` | Playback (`play`, `player`, `tape play`) | For playback |
| `fpcalc` | Chromaprint fingerprinting (`identify`, `index --fingerprint`) | For identification |
| `shnsplit` | Splitting single-file rips via CUE sheet | For CUE splitting |
| `cuetag.sh` | Writing tags from CUE to split tracks | For CUE splitting |
| `mac` | Monkey's Audio (APE) decoding | For APE files only |

On Arch Linux: `pacman -S ffmpeg mpv chromaprint shntool cuetools`

On Debian/Ubuntu: `apt install ffmpeg mpv libchromaprint-tools shntool cuetools`

## Configuration

musiktool stores its data in `~/.local/share/musiktool/` by default (analytics
DB, mpv socket, sidecar cache). Machine-specific settings go in
`~/.config/musiktool/config.toml`:

```toml
# Where rendered tape masters live (required for 'tape play' without --render-dir)
render_dir = "/path/to/rendered/output"

# Override data directory (default: ~/.local/share/musiktool)
# data_dir = "/path/to/data"
```

Both paths respect `XDG_CONFIG_HOME` and `XDG_DATA_HOME` if set.

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
- `docs/pipewire-airplay-sonos.md` — streaming to AirPlay speakers (Sonos, HomePod)
