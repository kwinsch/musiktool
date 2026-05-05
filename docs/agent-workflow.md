# Agent Workflow

musiktool is designed for AI agents. The CLI emits structured JSON for agents
to consume. The agent reasons about findings using music knowledge and proposes
fix plans. The human reviews and approves.

## Workflow Sequence

### 0. Ensure the index is current

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

### 1. Mechanical fixes

```bash
musiktool propose <library> --type year-folders
musiktool propose <library> --type year-folders --format json | musiktool apply -
```

`propose` generates fix plans for zero-judgment operations. Currently
supported: `year-folders` (adds `(Year)` to album directories using consensus
tag year). Review the plan, then pipe to `apply`.

### 2. Audit remaining issues

```bash
musiktool audit <library> --format json --severity warning
```

Reports structured findings with evidence dictionaries, confidence scores,
and suggested actions. The agent reads these and decides what to fix.

### 3. Inspect before acting

```bash
musiktool inspect <path> --format json
```

Returns deeper evidence for one album, directory, or track. Use before
proposing destructive or uncertain fixes — especially duplicate resolution.

### 4. Compose a fix plan

The agent builds a JSON fix plan using whitelisted actions:

| Action | Purpose |
|---|---|
| `rename_album_dir` | Add/correct year, fix canonical name |
| `rename_track_file` | Fix filename to match `NN Title.ext` |
| `move_file` | Move file within library root |
| `quarantine` | Move duplicate or uncertain item to quarantine |
| `write_tags` | Update metadata on audio files |
| `ignore_finding` | Acknowledge a finding as acceptable |

There is no `delete` action. Quarantine first; delete manually after review.

### 5. Dry-run

```bash
musiktool apply plan.json
```

Validates the plan without modifying files. Check output before proceeding.

### 6. Execute

```bash
musiktool apply plan.json --execute
```

Only after human approval. The agent should never execute without confirmation.

## Decision Boundary

**musiktool handles** (mechanical, zero-judgment):
- Adding `(Year)` to folder names from consensus tag year
- Filesystem safety (path validation, collision detection, dry-run default)
- Audio processing (fingerprinting, loudness measurement, tape rendering)

**The agent handles** (requires music knowledge or judgment):
- Canonical album names ("Black Album" is actually the self-titled "Metallica")
- Duplicate resolution (which copy to keep — same mastering? better format?)
- Format preferences (FLAC is lossless archival, M4A may be lossy iTunes rip)
- Structure reorganization (spoken content to `radio/`, `audiobooks/`, etc.)
- Missing tag repair (via `musiktool identify` + MusicBrainz matching)
- Compilation vs. split album detection
- Remaster identification (bonus tracks, different loudness, different year)

**Boundary test:** if a reasonable person could disagree about the right
action, it is an agent decision, not a tool decision.

## Safety

- **Always dry-run** before executing any fix plan.
- **Snapshot before bulk changes.** If your filesystem supports snapshots (ZFS,
  Btrfs, LVM), take one before applying large fix plans. musiktool does not
  manage snapshots itself.
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

## Typical Agent Session

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

The agent adds value by applying music knowledge that no rule engine has:
canonical names, remaster detection, format quality comparison, and contextual
judgment about what belongs where.
