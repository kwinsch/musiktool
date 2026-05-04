# musiktool-library(1) — agent-assisted library maintenance

## SYNOPSIS

```
musiktool audit <path> [options]
musiktool inspect <path> [options]
musiktool apply <plan.json> [options]
```

## DESCRIPTION

Audit, inspect, and safely maintain a music library in the `Artist/Album
(Year)/NN Title.ext` layout. These commands are designed for two consumers:
humans reading concise terminal output, and local agents reading stable JSON
so they can propose conservative fix plans.

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
- Duplicate candidates are detected by track count, duration, tags, format,
  and provenance evidence.
- Staging sources can be compared against the curated library with `--against`.

**Examples:**

```
musiktool audit ~/Music/lib
musiktool audit ~/Music/lib --format json
musiktool audit ~/Music/incoming --against ~/Music/lib --format ndjson
musiktool audit ~/Music/iTunes --against ~/Music/lib --severity warning
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

**Safety model:**

- `apply` only accepts whitelisted action types.
- Every action must reference an audit `finding_id` unless explicitly marked
  as a manual action.
- Destination paths must stay inside the library root or configured quarantine
  directory.
- Existing CUE sheets and EAC logs are never deleted by rename or cleanup
  actions.
- Destructive deletion is not a first-class action. Use `quarantine`.
- `--dry-run` is the default and must produce enough detail for review.

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
| `structure.album_year_missing` | warning | Album folder does not end in `(Year)`. |
| `structure.album_year_invalid` | warning | Album folder year is malformed or implausible. |
| `structure.nested_album` | warning | Album-like directory found below another album. |
| `structure.stray_file` | info | Non-audio/non-provenance file in an unusual location. |
| `tags.missing_required` | warning | Required tag field is missing. |
| `tags.track_number_mismatch` | warning | Filename number and tag track number disagree. |
| `tags.compilation_inconsistent` | warning | Compilation artist/album-artist tags are inconsistent. |
| `tracks.filename_pattern` | info | Filename does not follow `NN Title.ext`. |
| `provenance.cue_missing` | info | No CUE sheet found for an album. |
| `provenance.eac_log_missing` | info | No EAC log found for an album. |
| `duplicates.same_album_candidate` | warning | Two album dirs appear to contain the same album. |
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

### Agent Loop

1. Run `audit --format json`.
2. For each high-confidence fixable warning, call `inspect --format json`.
3. Produce a fix plan with only whitelisted actions.
4. Run `apply --dry-run --format json` and check validation results.
5. Ask for human approval before `apply --execute`.

## SEE ALSO

- `musiktool-tape(1)` — tape mastering project management
- `musiktool-analyze(1)` — bulk EBU R128 analysis
- `docs/tape-cli.md` — tape command reference
- `docs/playback-normalization.md` — tape loudness design document
