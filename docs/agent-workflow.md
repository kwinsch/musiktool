# Agent Workflow

musiktool is designed for AI agents. The CLI emits structured JSON for agents
to consume. The agent reasons about findings using music knowledge and proposes
fix plans. The human reviews and approves.

## Workflow Sequence

### 0. Orient: what are we working with?

```bash
musiktool stats <library>
musiktool stats <library> --format json
```

Run `stats` first to understand the library before touching anything: file
counts, format histogram, total size, tag coverage, media-kind breakdown,
index coverage, and loudness coverage. This tells the agent whether the
index is populated, how many albums lack years, what codecs dominate, and
whether loudness data exists for tape work.

### 0bis. Ensure the index is current

```bash
musiktool index scan <library> --hash --fingerprint
```

Populates BLAKE3 file hashes, Chromaprint audio fingerprints, and tag facts
into the analytics DB. Without this:

- Audit falls back to slow live tag reads.
- Duplicate detection has no file or audio identity evidence — only weak
  metadata heuristics.
- The `propose` command still works (reads tags live) but the agent cannot
  make informed duplicate decisions.

The scan is incremental: unchanged files are skipped on subsequent runs.

**Always pass both `--hash` and `--fingerprint`.** If stale files are
re-indexed with only `--hash`, their existing Chromaprint fingerprints are
lost and fingerprint coverage silently drops. The two flags are cheap
relative to a full rescan and should always be used together.

**Hash terminology.** The index stores two distinct hashes in different
tables. `indexed_files.blake3` is the file-level identity hash (populated
by `--hash`) — it hashes the raw file bytes and detects exact duplicates
across formats. `audio_facts.audio_hash` is a schema placeholder for a
future decoded-PCM content hash — it is never populated by any current
command. When the docs or agent output say "hash," they mean BLAKE3 file
hash unless explicitly stated otherwise.

### 1. Mechanical fixes

```bash
musiktool propose <library> --type year-folders
musiktool propose <library> --type year-folders --format json | musiktool apply -
musiktool propose <library> --type media-kind-folders
musiktool propose <library> --type media-kind-folders --format json | musiktool apply -
```

`propose` generates fix plans for zero-judgment operations. Currently
supported: `year-folders` (adds `(Year)` to album directories using consensus
tag year) and `media-kind-folders` (moves classified albums into `music/`,
`radio/`, `audiobooks/`, or `podcasts/`). Review the plan, then pipe to
`apply`.

#### Handling `no_consensus_year`

`propose year-folders` skips albums where `_consensus_year()` returns None,
reporting `"reason": "no_consensus_year"`. This means the tracks in the album
do not unanimously agree on a single year. Common causes:

- **Disc-split years**: A multi-disc compilation where disc 1 tags say 1998
  and disc 2 tags say 1997. The album release year is one specific date, but
  the per-disc tags reflect original release years of the compiled material.
- **Partially tagged**: Some tracks have a year tag, others are missing it
  entirely.
- **All null**: No tracks have year tags at all.

What the agent should do:

1. `musiktool inspect <album_path> --format json` — check the `tags` section
   to see which years are present and how they split.
2. Look up the album release year (MusicBrainz, Discogs, or agent knowledge).
3. Either write consistent year tags via a `write_tags` plan, or manually
   rename the album directory with the correct `(Year)` suffix.

### 2. Classify media kinds

```bash
musiktool audit <library> --format json --severity warning
```

Review findings. Albums with wrong-profile warnings (radio show flagged for
missing `tracknumber`) need classification:

```bash
musiktool index classify <path> radio
musiktool index classify <path> audiobook
```

Profile-aware audit (`--profile auto`, the default) then applies the right
rules per album. Re-audit to verify classification reduced false warnings.

### 3. Audit remaining issues

```bash
musiktool audit <library> --format json --severity warning
```

Reports structured findings with evidence dictionaries, confidence scores,
and suggested actions. The agent reads these and decides what to fix.

#### Comparing against a curated library

Use `--against` to check a staging area, quarantine, or import source against
an existing curated library for duplicates:

```bash
musiktool audit <staging_path> --against <curated_library> --format json
```

This detects `duplicates.source_already_curated` findings — albums in the
staging area that already exist in the curated library (by hash, fingerprint,
or strong metadata match). Useful before importing from any external source.

#### Disc-track filename patterns

Audit reports `tracks.filename_pattern` (severity: info) for filenames that
don't match `NN Title.ext`. In multi-disc albums, the expected format is
`D-NN Title.ext` (e.g. `1-01 Title.flac`). This disc-track naming is
correct and should not be "fixed" — the agent should leave these as-is.
A future `propose --type filenames` will handle this pattern explicitly.

### 4. Inspect before acting

```bash
musiktool inspect <path> --format json
```

Returns deeper evidence for one album, directory, or track. Use before
proposing destructive or uncertain fixes — especially duplicate resolution.

### 5. Compose a fix plan

The agent builds a JSON fix plan using whitelisted actions:

| Action | Purpose |
|---|---|
| `rename_album_dir` | Add/correct year, fix canonical name |
| `rename_track_file` | Fix filename to match `NN Title.ext` |
| `move_file` | Move file within library root |
| `copy_file` | Copy one external source file into the library for imports |
| `copy_album_dir` | Copy one external album directory into the library for imports |
| `quarantine` | Move duplicate or uncertain item to quarantine |
| `write_tags` | Update metadata on audio files |
| `ignore_finding` | Acknowledge a finding as acceptable |

There is no `delete` action. Quarantine first; delete manually after review.

### 6. Dry-run

```bash
musiktool apply plan.json
```

Validates the plan without modifying files. Check output before proceeding.

### 7. Execute

```bash
musiktool apply plan.json --execute
```

Only after human approval. The agent should never execute without confirmation.

### 8. Post-curation: loudness analysis

```bash
musiktool analyze <library>
musiktool analyze <library> --index --hash-index --fingerprint-index
```

Run `analyze` after curation is complete. This populates the loudness DB
(`track_loudness`, `album_loudness`) with EBU R128 measurements — required
for tape mastering and useful for library health overview. The scan is
incremental (skips tracks whose mtime hasn't changed). Pass `--index` to
also refresh the file index during the same walk.

After bulk moves or quarantine operations, stale loudness rows for removed
paths may linger. Clean them up:

```bash
musiktool index prune <library> --execute
```

`prune` removes DB rows (index, loudness, classifications) for paths that
no longer exist on disk.

## Decision Boundary

**musiktool handles** (mechanical, zero-judgment):
- Adding `(Year)` to folder names from consensus tag year
- Moving albums into media-kind subtrees from classifications
- Standardizing filenames to profile patterns (once implemented)
- Profile-aware audit (different rules for music vs radio vs audiobook vs podcast)
- Filesystem safety (path validation, collision detection, dry-run default)
- Audio processing (fingerprinting, loudness measurement, tape rendering)

**The agent handles** (requires music knowledge or judgment):
- Media-kind classification (is this music or a radio show? tool suggests, agent decides)
- Canonical album names ("Black Album" is actually the self-titled "Metallica")
- Duplicate resolution (which copy to keep — same mastering? better format?)
- Format preferences (FLAC is lossless archival, M4A may be lossy iTunes rip)
- Missing tag repair (via `musiktool identify` + MusicBrainz matching)
- Compilation vs. split album detection
- Remaster identification (bonus tracks, different loudness, different year)

**Boundary test:** if a reasonable person could disagree about the right
action, it is an agent decision, not a tool decision.

## Safety

- **Always dry-run** before executing any fix plan.
- **Never delete.** Use `quarantine` — it moves items to `_quarantine/` within
  the library root, preserving them for manual review.
- **Tape project references update automatically.** When `apply --execute`
  renames or moves paths, all DB references (tape items, loudness data, index
  entries) are updated in the same transaction. The dry-run output shows
  which tape projects are affected. `propose` also shows tape references
  inline so the agent can review before committing.
- **Hidden path warning.** If a rename destination starts with `.` (hidden on
  Unix), the dry-run output warns. This catches cases like `...And Justice
  For All` which becomes invisible in directory listings.
- **Preserve provenance.** CUE sheets and EAC logs must never be deleted or
  separated from their album. They prove rip quality and enable exact disc
  reconstruction.

## Importing from External Sources

When importing albums from an external source, the agent **must** check the
curated library for existing copies before executing any copy plan.
`itunes propose` generates mechanical copy actions — it has no knowledge of
what the library already contains. That judgment is the agent's responsibility.

**Required sequence:**

1. Check what the library already has for this artist/album (by listing the
   target directory or running `audit --against`).
2. If the album already exists in a superior or equal format (FLAC > M4A > MP3),
   do not import. The curated copy wins.
3. If the album does not exist in the library, proceed with the import proposal
   and `apply`.
4. If the library has an inferior format (e.g. MP3) and the import source has
   a better one (e.g. AAC), the agent decides whether to import and quarantine
   the old copy — but this is a judgment call, not automatic.

Import tools like `itunes propose` are mechanical. The agent provides the
intelligence: knowing what's already there, comparing formats, and deciding
whether an import adds value.

## Output Formats

All agent-facing commands support `--format text|json|ndjson`:

- **text** — human-readable, may change between versions.
- **json** — one complete document with `schema_version`. This is the
  compatibility contract for agents.
- **ndjson** — one JSON object per line, for streaming large libraries.

Agents should use `--format json` and parse the output. The `schema_version`
field enables forward-compatible parsing.

## Finding Evidence

Audit findings include an `evidence` dictionary with machine-readable facts.
Key fields vary by finding category:

- `structure.album_year_missing` — includes `tag_year` (consensus year from
  tags, or null) and `suggested_name` (proposed folder name). When both are
  available and the destination path is free, `suggested_actions` includes the
  computed `destination`.
- `duplicates.same_album_candidate` — includes `evidence_type` (`exact_file`,
  `audio_fingerprint`, `strong_album_metadata`, or `weak_title_overlap`),
  `albums` list with format scores, and `preferred_path`.
- `tags.missing_required` — includes `missing` (list of absent tag fields).

See `docs/library-cli.md` for the complete finding category reference and
JSON schema.

## Duplicate Resolution

When audit reports `duplicates.same_album_candidate`, the agent must decide
which copy to keep. Decision factors, in priority order:

1. **Format score.** FLAC (100) > WAV (95) > APE (90) > M4A (60) > MP3 (50).
   Lossless always wins over lossy for archival.
2. **Provenance.** Copy with CUE sheet + EAC log proves rip quality. Prefer
   the one with provenance files.
3. **Tag completeness.** Copy with all required tags is preferred.
4. **Path correctness.** Copy already in the right Artist/Album (Year)/
   structure is preferred over one in a dump directory.

Action: quarantine the inferior copy. Never delete — the human reviews
quarantine later.

## Typical Agent Sessions

### Targeted cleanup

```
Human: "Clean up the Metallica albums"

Agent:
  1. musiktool index scan /path/to/lib/Metallica --hash --fingerprint
  2. musiktool propose /path/to/lib/Metallica --type year-folders
     → reviews: "Black Album → Black Album (1991)" — agent knows this
       should be "Metallica (1991)" instead, edits the plan
     → reviews: "Reload/ReLoad → Reload/ReLoad (1997)" — agent
       recognizes nested duplicate, changes to quarantine action
  3. musiktool apply edited-plan.json   (dry-run)
  4. Shows plan to human for approval
  5. musiktool apply edited-plan.json --execute
```

### Full library organization (from unstructured dump)

```
Human: "Organize this 1TB of mixed audio"

Agent:
  0. musiktool stats /dump
     → orient: file counts, formats, tag coverage, size
  1. musiktool index scan /dump --hash --fingerprint
     → builds file index with hashes, fingerprints, tags
  2. musiktool audit /dump --format json
     → gets overview: what has tags, what's missing, duplicates
  3. musiktool identify /dump/unknown-album-1 -w
     → identifies albums via AcoustID, writes tags
     → repeats for each unidentified album directory
  4. musiktool index classify /dump/radio-shows radio
     musiktool index classify /dump/audiobooks audiobook
     → classifies non-music content (agent judges from tags/genres)
  5. musiktool propose /dump --type year-folders --format json | musiktool apply -
     → adds (Year) to album folders from tag consensus
     → reviews plan, applies
  6. musiktool propose /dump --type media-kind-folders --format json | musiktool apply -
     → moves albums into music/, radio/, audiobooks/ subtrees
     → reviews plan, applies
  7. musiktool audit /dump --format json --severity warning
     → final audit: remaining tag gaps, duplicates, structural issues
  8. Agent builds fix plans for remaining issues:
     - write_tags for missing metadata
     - quarantine for confirmed duplicates
     - rename_album_dir for canonical name corrections
  9. musiktool apply final-plan.json --execute
 10. musiktool analyze /dump
     → populates loudness DB for tape mastering and health overview
 11. musiktool index prune /dump --execute
     → cleans up stale DB rows for quarantined/removed files
```

The agent adds value by applying music knowledge that no rule engine has:
canonical names, remaster detection, format quality comparison, and contextual
judgment about what belongs where.
