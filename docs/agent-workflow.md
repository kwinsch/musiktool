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
musiktool propose <library> --type media-kind-folders
musiktool propose <library> --type media-kind-folders --format json | musiktool apply -
```

`propose` generates fix plans for zero-judgment operations. Currently
supported: `year-folders` (adds `(Year)` to album directories using consensus
tag year) and `media-kind-folders` (moves classified albums into `music/`,
`radio/`, `audiobooks/`, or `podcasts/`). Review the plan, then pipe to
`apply`.

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
```

The agent adds value by applying music knowledge that no rule engine has:
canonical names, remaster detection, format quality comparison, and contextual
judgment about what belongs where.
