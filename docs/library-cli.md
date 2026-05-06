# musiktool-library(1) — agent-assisted library maintenance

## SYNOPSIS

```
musiktool audit <path> [options]
musiktool inspect <path> [options]
musiktool stats <path> [options]
musiktool apply <plan.json> [options]
```

## DESCRIPTION

Audit, inspect, and safely maintain an audio library. The command design
borrows the useful part of classic iTunes: files have a **media kind**, and
different media kinds belong in different library sections. Music, radio
shows, audiobooks, and podcasts should not all be forced into the same album
rules.

These commands are designed for two consumers: humans reading concise terminal
output, and local agents reading stable JSON so they can propose conservative
fix plans.

The library-maintenance workflow is intentionally split into three phases:

1. `audit` reports findings and evidence. It never changes files.
2. `inspect` returns deeper evidence for one album, directory, or track.
3. `apply` validates a fix plan, prints a dry run by default, and only changes
   files with `--execute`.

Agents should treat `audit` and `inspect` output as evidence, generate a fix
plan, and let `musiktool apply` perform validation and execution. Agents should
not shell out to arbitrary file-moving commands for library changes.

## OUTPUT FORMATS

All agent-facing commands support a shared output option:

| Option | Default | Description |
|---|---|---|
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |

`text` is optimized for interactive review. `json` emits one complete document
with a schema version. `ndjson` emits one JSON object per line and is intended
for very large sources such as old iTunes libraries.

JSON output must be treated as the compatibility contract. Text output may
change as the command becomes more readable.

## MEDIA KINDS AND PROFILES

Old iTunes got the core model right in the iPod era: **Media Kind** decides
where an item appears and which rules apply. A podcast is not music with a
`Podcast` genre; it is a podcast. An audiobook is not an album with a
`Spoken Word` genre; it is an audiobook. This keeps shuffle, browsing, sync,
and metadata expectations sane.

`musiktool` uses the same idea, but keeps the filesystem cleaner than the old
`iTunes Music/` folder. The intended top-level library layout is:

```
lib/
  music/
    Artist/
      Album (Year)/
        NN Title.ext
        *.cue
        *.log

  radio/
    Show/
      Collection or Season/
        NNN Episode Title.ext

  audiobooks/
    Author or Series/
      Work (Year)/
        NNN Chapter or Part.ext

  podcasts/
    Show/
      YYYY/
        YYYY-MM-DD Episode Title.ext
```

The `--profile` option controls which checks apply. Use `auto` (default) to
resolve the profile from the index classification for each album, falling back
to `music` when no classification exists:

```
musiktool audit ~/Music/lib --profile auto              # default
musiktool audit ~/Music/lib/music --profile music
musiktool audit ~/Music/lib/radio --profile radio
musiktool audit ~/Music/lib/audiobooks --profile audiobook
musiktool audit ~/Music/lib/podcasts --profile podcast
```

### Profile Rules

| Profile | Intended content | Structure | Required metadata |
|---|---|---|---|
| `music` | Albums, singles, compilations, soundtracks | `Artist/Album (Year)/NN Title.ext` | artist, album, title, track number, date/year |
| `radio` | Broadcast radio shows, radio drama, comedy series | `Show/Collection/NNN Episode Title.ext` | show/series, episode title, episode number if known |
| `audiobook` | Books, readings, lectures sold or ripped as books | `Author or Series/Work (Year)/NNN Part.ext` | author/narrator if known, work title, part/chapter number |
| `podcast` | Subscribed feed episodes | `Show/YYYY/YYYY-MM-DD Episode Title.ext` | show, episode title, publish date |

Profiles change audit expectations. Music should preserve CUE sheets and EAC
logs when available. Radio, audiobook, and podcast profiles should not report
missing CUE/EAC provenance as a problem. Music filenames are track-oriented;
spoken profiles are episode/part-oriented. Spoken content should usually be
excluded from music shuffle and music-only tape projects unless deliberately
selected.

## IGNORE POLICY

Library scans use gitignore-style rules. `musiktool` merges built-in defaults,
an optional `.musiktoolignore` file at the selected root, and repeated
`--exclude` patterns from the CLI.

Built-in defaults ignore generated and operational paths such as `.Trash-*`,
`.git/`, `.hg/`, `__pycache__/`, `_quarantine/`, `musiktool/render/`, `*.db`,
and `*.sqlite`.

Examples:

```
# .musiktoolignore
incoming-dumps/
transcoded-preview/
*.tmp.flac
```

Rules are interpreted relative to the selected command root.

### Philip Maloney

`Philip Maloney` is a Swiss radio show, not a music album. It should be
managed as `radio`, for example:

```
lib/radio/
  Philip Maloney/
    Die haarsträubenden Fälle des Philip Maloney/
      001 Rosen.mp3
      002 Blind Date.mp3
      003 Das Appartement.mp3
```

Under the music profile those files correctly trigger missing music tags and
filename findings. Under the radio profile the useful checks are different:
episode numbering, episode titles, duplicate episodes, and optional broadcast
or collection metadata.

## COMMANDS

### audit

Scan a library root or staging source and report actionable findings.

```
musiktool audit <path> [options]
```

**Arguments:**

- `path` — Library root, staging source, album directory, or track file.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--against` | *(none)* | Existing curated library to compare against for duplicate detection. |
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |
| `--severity` | `info` | Minimum severity to report: `info`, `warning`, or `error`. |
| `--include-ok` | off | Include successful checks in JSON output. |
| `--db` | default DB | Optional index DB path. Explicit DB failures are errors. |
| `--index` / `--no-index` | `--index` | Use current indexed facts when available. Implicit default DB use is best-effort. |
| `--refresh-index` | off | Refresh missing or stale index facts before auditing. |
| `--sidecar-root` | default sidecar root | Out-of-tree sidecar root for index refresh. |
| `--exclude` | repeatable | Additional gitignore-style pattern to exclude. |
| `--similarity-threshold` | `0.08` | Chromaprint BER threshold for audio duplicate detection. Lower = stricter (0.0 = exact only, 0.01 = codec transcodes, 0.08 = default, 0.15 = catches remasters). |
| `--profile` | `auto` | Audit profile: `music`, `radio`, `audiobook`, `podcast`, or `auto` (resolves per-album from index classification). |

**Checks:**

- Structure depth under `lib/` matches `Artist/Album (Year)/`.
- Album folder has a parseable year where expected.
- Album folder contains audio files directly, without accidental nested album
  copies.
- Stray files exist at library root or in unusual locations.
- Audio filenames follow `NN Title.ext` where possible.
- Required tags exist: artist, album, title, track number, date/year.
- Compilation albums preserve per-track artists while using album artist
  consistently.
- CUE sheets and EAC logs are preserved when present.
- Duplicate candidates are layered by evidence: exact BLAKE3 file identity,
  Chromaprint audio identity, or strong title/duration/artist/album agreement.
  Title-only matches are informational, not actionable warnings.
- Staging sources can be compared against the curated library with `--against`.

**Examples:**

```
musiktool audit ~/Music/lib
musiktool audit ~/Music/lib --format json
musiktool audit ~/Music/incoming --against ~/Music/lib --format ndjson
musiktool audit ~/Music/iTunes --against ~/Music/lib --severity warning
musiktool audit ~/Music --exclude 'incoming-dumps/'
```

**Text output example:**

```
~/Music/lib

  warning  structure.album_year_missing
           ~/Music/lib/Soulfly/Primitive
           Album directory does not end in "(Year)".

  warning  structure.nested_album
           ~/Music/lib/Soulfly/Primitive/Primitive
           Nested album-like directory with matching track count.

  info     provenance.cue_missing
           ~/Music/lib/Soulfly/Primitive
           No CUE sheet found.

3 findings
```

### index

Build and inspect the sidecar-backed file index used by `audit` and `analyze`.

```
musiktool index scan <path> [options]
musiktool index rebuild <path> [options]
musiktool index status [options]
musiktool index classify <path> <media-kind> [options]
musiktool index prune <path> [options]
```

`scan` refreshes stale or missing facts. `rebuild` forces a live rescan and
rewrites sidecars. Both commands accept `--hash`, `--fingerprint`,
`--sidecar-root`, and repeatable `--exclude` patterns.

`classify` stores a confirmed media kind: `music`, `radio`, `audiobook`, or
`podcast`. Directory classifications inherit to descendants; a more specific
manual classification on a child path overrides its parent.

The index stores audio facts only: file identity, tag facts, stream facts,
hashes, fingerprints, and media-kind classifications for audio paths. Album
side files such as CUE sheets, EAC/rip logs, artwork, booklets, playlists, and
artist extras are preserved by album-directory moves and inventoried by
`stats`; they are not inserted into `indexed_files`.

`prune` removes DB rows for indexed paths that no longer exist on disk. It is
a stale-reference cleanup tool, not an audit substitute; files whose mtime has
changed are reported as stale by `audit`/`stats` and should be refreshed with
`index scan`.

### stats

Show a structured overview of a library path.

```
musiktool stats <path> [options]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |
| `--db` | default DB | Optional index DB path. |
| `--index` / `--no-index` | `--index` | Enrich filesystem counts with current index facts when available. |
| `--exclude` | repeatable | Additional gitignore-style pattern to exclude. |

`stats` walks the active filesystem view first, respecting `.musiktoolignore`,
built-in ignores such as `_quarantine/`, and CLI excludes. It then enriches
those same active audio paths from the DB. This avoids raw DB row totals leaking
quarantined or stale entries into active-library counts.

Output includes album/track counts, total/audio/side-file sizes, audio format
histograms, side-file histograms (`cue_sheet`, `rip_log`, `image`, `booklet`,
`playlist`, `text`, `other`), provenance coverage for profiles that expect it,
tag coverage, album-level media-profile counts, index coverage
(`current`/`stale`/`missing`), and loudness coverage.

### inspect

Return detailed evidence for one filesystem path.

```
musiktool inspect <path> [options]
```

**Arguments:**

- `path` — Album directory, track file, or staging directory. Agents should
  inspect one of the `paths` emitted by an audit finding.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--against` | *(none)* | Compare this path against a curated library. |
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |
| `--severity` | `info` | Minimum severity to include in embedded findings. |

**Behavior:**

`inspect` does deeper work than `audit`: full tag tables, per-track durations,
format/provenance comparison, duplicate evidence, and proposed preferred copies.
It is the command an agent should call before recommending destructive or
lossy-looking fixes.

### apply

Validate and optionally execute a fix plan generated from audit evidence.

```
musiktool apply <plan.json> [options]
```

**Arguments:**

- `plan.json` — Fix plan file. Use `-` to read from standard input.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--dry-run` | on | Validate and print planned changes without modifying files. |
| `--execute` | off | Apply the validated plan. Mutually exclusive with `--dry-run`. |
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |
| `--quarantine-dir` | `<root>/_quarantine` | Destination for quarantine actions. |
| `--skip` | repeatable | Skip action IDs without editing the plan JSON. |

**Safety model:**

- `apply` only accepts whitelisted action types.
- Every action must reference an audit `finding_id` unless explicitly marked
  as a manual action.
- On execute, the complete plan is validated before the first filesystem
  mutation.
- Destination paths must stay inside the library root or configured quarantine
  directory.
- Copy actions may read from explicit `source_roots` in the plan, but still
  write only inside the library root.
- Overlapping parent/child move plans are rejected before execution.
- Existing CUE sheets and EAC logs are never deleted by rename or cleanup
  actions.
- Destructive deletion is not a first-class action. Use `quarantine`.
- `--dry-run` is the default and must produce enough detail for review.

### propose

Generate a mechanical fix plan for zero-judgment operations. The output is a
valid fix plan JSON that can be piped directly to `apply`.

```
musiktool propose <path> --type <proposal-type> [options]
```

**Arguments:**

- `path` — Library root path to scan.

**Options:**

| Option | Default | Description |
|---|---|---|
| `--type` | *(required)* | Proposal type. Currently: `year-folders`, `media-kind-folders`. |
| `--format` | `text` | Output format: `text`, `json`, or `ndjson`. |
| `--db` | default DB | Optional index DB path. |
| `--index` / `--no-index` | `--index` | Use current indexed facts when available. |
| `--exclude` | repeatable | Additional gitignore-style pattern to exclude. |

**Proposal types:**

| Type | Actions | Logic |
|---|---|---|
| `year-folders` | `rename_album_dir` | Adds `(Year)` to album directories using consensus tag year. Skips albums where tracks disagree on year or destination already exists. |
| `media-kind-folders` | `rename_album_dir` | Moves album directories into `music/`, `radio/`, `audiobooks/`, or `podcasts/` based on confirmed/effective media-kind classification. |

**Boundary:** `propose` only handles operations where there is exactly one
correct answer. Naming corrections (e.g. "Black Album" → "Metallica"),
duplicate resolution, and format preferences require agent judgment and should
be handled through the audit → inspect → apply workflow.

**Examples:**

```
# Preview proposed renames
musiktool propose ~/Music/lib --type year-folders

# Generate plan and dry-run it
musiktool propose ~/Music/lib --type year-folders --format json | musiktool apply -

# Generate, review, and execute
musiktool propose ~/Music/lib --type year-folders --format json > plan.json
# Agent or human reviews plan.json, possibly edits it
musiktool apply plan.json --execute
```

**Text output example:**

```
Propose: year-folders (3 rename(s), 0 skipped)

  Can I Play With Madness  →  Can I Play With Madness (1990)
  Killers  →  Killers (1981)
  No Prayer For The Dying  →  No Prayer For The Dying (1990)

Pipe JSON to apply:
  musiktool propose <path> --type year-folders --format json | musiktool apply - --execute
```

### itunes

Read an old iTunes XML library and generate copy-only import plans. The iTunes
source is never moved or modified.

```
musiktool itunes scan <itunes-root> [--format text|json|ndjson]
musiktool itunes albums <itunes-root> [--artist NAME] [--album TITLE] [--limit N]
musiktool itunes propose <itunes-root> --target <library-root>
                       (--artist NAME | --album TITLE)
                       [--include-protected] [--format text|json|ndjson]
```

`scan` summarizes the XML catalog and remaps legacy `file://localhost/Volumes/...`
locations to files under the selected iTunes root. `albums` lists matching music
albums for review. `propose` emits `copy_file` actions that can be dry-run with
`apply`; DRM-era protected AAC files (`.m4p` / "Protected AAC") are skipped by
default and only included with `--include-protected`.

Example:

```
musiktool itunes albums ~/Music/iTunes --artist Rammstein
musiktool itunes propose ~/Music/iTunes --target ~/Music/lib --artist Rammstein \
  --format json | musiktool apply -
```

## JSON SCHEMA

### Audit Document

```
{
  "schema_version": 1,
  "command": "audit",
  "root": "~/Music/lib",
  "generated_at": "2026-05-04T22:50:00+02:00",
  "summary": {
    "albums_scanned": 124,
    "tracks_scanned": 1851,
    "findings": 3,
    "by_severity": {"info": 1, "warning": 2, "error": 0}
  },
  "findings": []
}
```

### Finding Object

```
{
  "finding_id": "structure.nested_album:8d4f0b1c",
  "severity": "warning",
  "category": "structure.nested_album",
  "paths": [
    "~/Music/lib/Soulfly/Primitive",
    "~/Music/lib/Soulfly/Primitive/Primitive"
  ],
  "message": "Nested album-like directory with matching track count.",
  "evidence": {
    "parent_track_count": 12,
    "nested_track_count": 12,
    "duration_delta_sec": 0.15,
    "formats": {"parent": ["flac"], "nested": ["m4a"]},
    "provenance": {"parent_has_eac_log": true, "nested_has_eac_log": false}
  },
  "confidence": 0.92,
  "fixable": true,
  "suggested_actions": [
    {
      "type": "quarantine",
      "source": "~/Music/lib/Soulfly/Primitive/Primitive",
      "reason": "Parent copy is FLAC and has rip provenance."
    }
  ]
}
```

**Required finding fields:**

| Field | Description |
|---|---|
| `finding_id` | Stable id derived from category and canonical paths/evidence. |
| `severity` | `info`, `warning`, or `error`. |
| `category` | Dot-separated machine category. |
| `paths` | One or more absolute paths involved in the finding. |
| `message` | Human-readable summary. Agents should not parse this. |
| `evidence` | Machine-readable facts supporting the finding. |
| `confidence` | Number from `0.0` to `1.0`. |
| `fixable` | Whether `musiktool apply` can handle at least one action type. |
| `suggested_actions` | Optional candidate actions. The agent may accept, modify, or ignore them. |

### Finding Categories

Initial categories:

| Category | Severity | Description |
|---|---|---|
| `structure.album_year_missing` | warning | Album folder does not end in `(Year)`. Evidence includes `tag_year` (consensus year from track tags, or null) and `suggested_name` (proposed folder name, or null). When a tag year is found and the destination doesn't exist, `suggested_actions` includes the computed `destination`. |
| `structure.album_year_invalid` | warning | Album folder year is malformed or implausible. |
| `structure.nested_album` | warning | Album-like directory found below another album. |
| `structure.stray_file` | info | Non-audio/non-provenance file in an unusual location. |
| `tags.missing_required` | warning | Required tag field is missing. |
| `tags.track_number_mismatch` | warning | Filename number and tag track number disagree. |
| `tags.compilation_inconsistent` | warning | Compilation artist/album-artist tags are inconsistent. |
| `tracks.filename_pattern` | info | Filename does not follow `NN Title.ext`. |
| `provenance.cue_missing` | info | No CUE sheet found for an album. |
| `provenance.eac_log_missing` | info | No EAC log found for an album. |
| `duplicates.same_album_candidate` | info/warning | Two album dirs may contain the same album. Weak title-only matches are info; exact, audio, or strong metadata matches are warning. Evidence includes `evidence_type` (`exact_file`, `audio_fingerprint`, `strong_album_metadata`, `weak_title_overlap`). For `audio_fingerprint`, also includes `similarity_ber` (bit error rate) and `similarity_threshold` used. |
| `duplicates.source_already_curated` | warning | Staging source appears to duplicate an existing library album. |

## FIX PLAN SCHEMA

Agents produce fix plans. `musiktool apply` validates and executes them.

```
{
  "schema_version": 1,
  "library_root": "~/Music/lib",
  "generated_by": "agent",
  "actions": [
    {
      "action_id": "quarantine:structure.nested_album:8d4f0b1c",
      "type": "quarantine",
      "because_finding": "structure.nested_album:8d4f0b1c",
      "source": "~/Music/lib/Soulfly/Primitive/Primitive",
      "destination": "~/Music/lib/_quarantine/Soulfly/Primitive/Primitive",
      "reason": "Parent copy is FLAC and has EAC provenance."
    }
  ]
}
```

### Whitelisted Actions

| Action | Description |
|---|---|
| `rename_album_dir` | Rename an album directory, usually to add or correct `(Year)`. |
| `rename_track_file` | Rename a track file to match `NN Title.ext`. |
| `move_file` | Move an allowed file within the library root. |
| `copy_file` | Copy one source file from `source_roots` into the library root. |
| `copy_album_dir` | Copy one source album directory from `source_roots` into the library root. |
| `quarantine` | Move a duplicate or uncertain item into quarantine. |
| `write_tags` | Write explicit tag fields to one or more audio files. |
| `ignore_finding` | Record that a finding is accepted and should not be reported again. |

`delete` is intentionally absent. Quarantine first; delete later outside the
agent-assisted workflow after manual review.

## WORKFLOWS

### Curated Library Audit

```
musiktool audit ~/Music/lib --format json > audit.json
musiktool inspect ~/Music/lib/Soulfly/Primitive --format json > primitive.json
musiktool apply fix-plan.json --dry-run
musiktool apply fix-plan.json --execute
```

### Staging Source Ingest

```
musiktool audit ~/Music/incoming --against ~/Music/lib --format ndjson > incoming.ndjson
musiktool inspect "~/Music/incoming/Apocalyptica" --against ~/Music/lib --format json
musiktool apply incoming-fix-plan.json --dry-run
```

### Mechanical Fixes (Propose)

```
musiktool propose ~/Music/lib --type year-folders --format json | musiktool apply -
musiktool propose ~/Music/lib --type year-folders --format json > plan.json
musiktool apply plan.json --execute
```

### Agent Loop

1. Run `propose --format json` for mechanical fixes; review and apply.
2. Run `audit --format json` for remaining findings.
3. For each high-confidence fixable warning, call `inspect --format json`.
4. Produce a fix plan with only whitelisted actions.
5. Run `apply --dry-run --format json` and check validation results.
6. Ask for human approval before `apply --execute`.

## SEE ALSO

- `musiktool-tape(1)` — tape mastering project management
- `musiktool-analyze(1)` — bulk EBU R128 analysis
- `docs/tape-cli.md` — tape command reference
- `docs/playback-normalization.md` — tape loudness design document
