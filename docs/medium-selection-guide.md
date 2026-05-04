# Medium Selection Guide

> How musiktool handles different recording media, and how to choose the
> right preset for your equipment.

---

## How the Pipeline Works

musiktool's tape mastering pipeline operates **entirely in the digital
domain**. It produces a FLAC master file with optimal loudness, dynamics,
and peak control. The physical medium's characteristics (tape saturation,
noise reduction encoding, frequency response) are handled by the recording
equipment after the DAC — not by the software.

### Signal Level Diagram

```
                         0 dBFS (digital ceiling)
                            │
    ┌── peaks ──────────────┤  ← limiter catches up to 3 dB
    │                       │    automatically (budget)
    │                       │
    │   ← dynamic range →  │
    │                       │
    │                       │
    ├── average (-14 LUFS) ─┤  ← calibration maps THIS to the
    │                       │    correct recording level on your
    │   ← dynamic range →  │    deck's meters
    │                       │
    │                       │
    ├── quiet passages ─────┤
    │                       │
    │   ... margin ...      │  ← this gap must exist, or tape
    │                       │    hiss becomes audible during
    │                       │    quiet passages
    └── noise floor ────────┤  ← determined by your medium
                                 (tape type + noise reduction)
```

Peak ceiling depends on medium type:
- Hard-clip media (VHS, MD, CD): ceiling -1 dBTP (intersample safety)
- Soft-clip media (cassette, reel): ceiling 0 dBTP (tape saturates gently)

The **noise floor position** is the only thing that changes between media.
A Type I cassette without Dolby has its noise floor ~57 dB below digital
full scale (after calibration). A Type IV cassette with Dolby C pushes
that floor down to ~94 dB — quiet passages have much more room before
hiss becomes audible.

### Four Independent Concerns

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
    │  Does the album's LRA exceed what   │
    │  the medium can capture?            │
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
      Maps digital output levels to the
    │ physical recording device's meters. │
      One-time procedure per equipment.
    │ NOT part of the rendering pipeline. │
    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
             │
             ▼
        Physical Medium (tape / disc)
```

**1. Target LUFS** — where the average loudness sits in the digital
window. Constant at -14 LUFS (default) across all media. Does not change
based on your tape type or deck.

**2. Calibration** — the bridge between digital and physical. A one-time
procedure that maps your DAC's output level to the correct recording level
on your deck's meters. See `tape_recording_reference.md` for per-medium
calibration procedures.

**3. Limiting budget** — when gain to reach target LUFS would push peaks
above ceiling, up to 3 dB of transparent peak limiting is applied
automatically. Beyond 3 dB, gain is reduced (album plays quieter).
With `--limiter`, the budget is unlimited. See
`playback-normalization.md` for full algorithm details.

**4. Compression** — when an album's dynamic range (LRA) is wider than
what the medium can capture without quiet passages disappearing into hiss,
gentle compression reduces the range to fit. Driven by `dynamic_range_db`
in the preset.

---

## Why Target LUFS Is Constant

You might think a cassette with 55 dB dynamic range needs a different
target level than VHS Hi-Fi with 90 dB. It doesn't, because:

- **The target defines where the average sits.** Calibration handles
  mapping that average to the physical medium's optimal recording level.
- **A louder target doesn't help the noise floor.** If quiet passages are
  too close to hiss, the fix is compression (raise the quiet parts), not
  raising the entire signal (which pushes peaks into distortion).
- **A quieter target wastes headroom.** You'd move everything further from
  the ceiling without gaining anything.

The default -14 LUFS works for all media because:
- On VHS Hi-Fi (90 dB DR): peaks land safely below the hard ceiling
- On cassette (55-98 dB DR depending on type/NR): peaks land in the
  progressive saturation zone (which sounds good on tape)
- On MiniDisc (93 dB DR): peaks land safely below the digital ceiling

---

## What Dynamic Range Means for Your Recording

The `dynamic_range_db` value in a preset determines **when the compressor
triggers**. The formula:

```
lra_limit = dynamic_range_db * 0.25
```

If an album's LRA (loudness range) exceeds `lra_limit`, gentle compression
is applied to fit the dynamics within the medium's capabilities.

### Compressor Trigger Thresholds

| Medium DR | Compressor triggers at LRA > | Typical material affected |
|-----------|------------------------------|---------------------------|
| 55 dB | 13.75 LU | Classical, some jazz, film scores |
| 57 dB | 14.25 LU | Classical, wide-dynamic jazz |
| 65 dB | 16.25 LU | Only very wide classical |
| 74 dB | 18.50 LU | Extreme classical only |
| 90 dB (VHS) | 22.50 LU | Essentially nothing from CD sources |
| 93 dB (MD) | 23.25 LU | Nothing from CD sources |
| 96 dB (CD) | 24.00 LU | Nothing — source fits by definition |

**Practical guidance:**
- Pop/rock albums (LRA 4-12): never compressed on any medium
- Jazz (LRA 10-16): only compressed on bare Type I cassette
- Classical (LRA 15-23): may be compressed on cassette, never on VHS/MD/reel+NR
- Film scores (LRA 12-18): similar to jazz

---

## Choosing Your Preset

### Decision Tree

```
What format are you recording to?
│
├── VHS Hi-Fi ──────────────── What tape length?
│                               ├── T-120 (2h)  → vhs-120
│                               ├── T-160 (2h40) → vhs-160
│                               └── T-180 (3h)  → vhs-180
│
├── Compact Cassette ────────── What tape type?
│   │                           ├── Type I (ferric/normal)
│   │                           ├── Type II (chrome/high)
│   │                           └── Type IV (metal)
│   │
│   └── What noise reduction? ── What length?
│       ├── None                  ├── C-60 → c-60-{type}
│       ├── Dolby B               ├── C-90 → c-90-{type}-dolbyb
│       ├── Dolby C               └── C-120 → c-120-{type}-dolbyc
│       └── Dolby S (II/IV only)
│
├── Open Reel ───────────────── What reel size?
│   │                           ├── 7" (30 min @ 7.5ips, 15 min @ 15ips)
│   │                           └── 10.5" (60 min @ 7.5ips, 30 min @ 15ips)
│   │
│   └── What NR? ────────────── reel-{7|10}-half-{7.5|15}ips[-{nr}]
│       ├── None
│       ├── Dolby A
│       ├── Dolby SR
│       └── dbx
│
├── MiniDisc (SP mode) ─────── What disc?
│                               ├── MD-60 → md-60
│                               ├── MD-74 → md-74
│                               └── MD-80 → md-80
│
├── CD-R (audio CD) ────────── What disc capacity?
│                               ├── 74 min (650 MB) → cd-74
│                               └── 80 min (700 MB) → cd-80
│
└── Vinyl LP ───────────────── vinyl-lp (22 min/side)
```

### Naming Pattern

```
<format>-<length>[-<tape_type>][-<noise_reduction>]
```

Examples:
- `c-90-ii-dolbyb` — C-90 cassette, Type II chrome, Dolby B
- `c-60-iv-dolbyc` — C-60 cassette, Type IV metal, Dolby C
- `reel-10-half-15ips-dolbysr` — 10.5" reel, half-track, 15 ips, Dolby SR
- `md-80` — MiniDisc 80-minute disc

### Legacy Presets

The bare names `c-60`, `c-90`, `c-120` are retained for backward
compatibility and represent **Type I, no NR** (the most conservative
assumption). If you don't know your tape type, these are safe defaults.

---

## Complete Preset Reference

### VHS Hi-Fi (single-sided)

| Preset | Duration | DR | Notes |
|--------|----------|-----|-------|
| `vhs-120` | 118 min | 90 dB | Standard T-120/SE-120 |
| `vhs-160` | 158 min | 90 dB | T-160 |
| `vhs-180` | 176 min | 90 dB | T-180 |

FM-encoded audio — tape type is irrelevant to audio quality. Hard ceiling.

### Compact Cassette (two-sided)

All cassette presets are two-sided with equal side lengths.

**Without noise reduction:**

| Preset | Duration | Per Side | DR | Tape Type |
|--------|----------|----------|-----|-----------|
| `c-60` | 60 min | 30 + 30 | 55 dB | Type I (legacy) |
| `c-60-i` | 60 min | 30 + 30 | 57 dB | Type I ferric |
| `c-60-ii` | 60 min | 30 + 30 | 65 dB | Type II chrome |
| `c-60-iv` | 60 min | 30 + 30 | 74 dB | Type IV metal |
| `c-90` | 90 min | 45 + 45 | 55 dB | Type I (legacy) |
| `c-90-i` | 90 min | 45 + 45 | 57 dB | Type I ferric |
| `c-90-ii` | 90 min | 45 + 45 | 65 dB | Type II chrome |
| `c-90-iv` | 90 min | 45 + 45 | 74 dB | Type IV metal |
| `c-120` | 120 min | 60 + 60 | 55 dB | Type I (legacy) |
| `c-120-i` | 120 min | 60 + 60 | 57 dB | Type I ferric |
| `c-120-ii` | 120 min | 60 + 60 | 65 dB | Type II chrome |
| `c-120-iv` | 120 min | 60 + 60 | 74 dB | Type IV metal |

**With Dolby B (+10 dB):**

| Preset | Duration | Per Side | DR |
|--------|----------|----------|-----|
| `c-60-i-dolbyb` | 60 min | 30 + 30 | 67 dB |
| `c-60-ii-dolbyb` | 60 min | 30 + 30 | 75 dB |
| `c-60-iv-dolbyb` | 60 min | 30 + 30 | 84 dB |
| `c-90-i-dolbyb` | 90 min | 45 + 45 | 67 dB |
| `c-90-ii-dolbyb` | 90 min | 45 + 45 | 75 dB |
| `c-90-iv-dolbyb` | 90 min | 45 + 45 | 84 dB |
| `c-120-i-dolbyb` | 120 min | 60 + 60 | 67 dB |
| `c-120-ii-dolbyb` | 120 min | 60 + 60 | 75 dB |
| `c-120-iv-dolbyb` | 120 min | 60 + 60 | 84 dB |

**With Dolby C (+20 dB):**

| Preset | Duration | Per Side | DR |
|--------|----------|----------|-----|
| `c-60-i-dolbyc` | 60 min | 30 + 30 | 77 dB |
| `c-60-ii-dolbyc` | 60 min | 30 + 30 | 85 dB |
| `c-60-iv-dolbyc` | 60 min | 30 + 30 | 94 dB |
| `c-90-i-dolbyc` | 90 min | 45 + 45 | 77 dB |
| `c-90-ii-dolbyc` | 90 min | 45 + 45 | 85 dB |
| `c-90-iv-dolbyc` | 90 min | 45 + 45 | 94 dB |
| `c-120-i-dolbyc` | 120 min | 60 + 60 | 77 dB |
| `c-120-ii-dolbyc` | 120 min | 60 + 60 | 85 dB |
| `c-120-iv-dolbyc` | 120 min | 60 + 60 | 94 dB |

**With Dolby S (+24 dB, Type II and IV only):**

| Preset | Duration | Per Side | DR |
|--------|----------|----------|-----|
| `c-60-ii-dolbys` | 60 min | 30 + 30 | 89 dB |
| `c-60-iv-dolbys` | 60 min | 30 + 30 | 98 dB |
| `c-90-ii-dolbys` | 90 min | 45 + 45 | 89 dB |
| `c-90-iv-dolbys` | 90 min | 45 + 45 | 98 dB |
| `c-120-ii-dolbys` | 120 min | 60 + 60 | 89 dB |
| `c-120-iv-dolbys` | 120 min | 60 + 60 | 98 dB |

### Open Reel (single-sided)

Half-track stereo, standard play (1.5 mil) tape. All single-sided.

**Without noise reduction:**

| Preset | Duration | DR | Config |
|--------|----------|-----|--------|
| `reel-7-half-7.5ips` | 30 min | 66 dB | 7" reel, 7.5 ips |
| `reel-7-half-15ips` | 15 min | 65 dB | 7" reel, 15 ips |
| `reel-10-half-7.5ips` | 60 min | 66 dB | 10.5" reel, 7.5 ips |
| `reel-10-half-15ips` | 30 min | 65 dB | 10.5" reel, 15 ips |

**With Dolby A (+12 dB):**

| Preset | Duration | DR |
|--------|----------|-----|
| `reel-7-half-7.5ips-dolbya` | 30 min | 78 dB |
| `reel-7-half-15ips-dolbya` | 15 min | 77 dB |
| `reel-10-half-7.5ips-dolbya` | 60 min | 78 dB |
| `reel-10-half-15ips-dolbya` | 30 min | 77 dB |

**With Dolby SR (+22 dB):**

| Preset | Duration | DR |
|--------|----------|-----|
| `reel-7-half-7.5ips-dolbysr` | 30 min | 88 dB |
| `reel-7-half-15ips-dolbysr` | 15 min | 87 dB |
| `reel-10-half-7.5ips-dolbysr` | 60 min | 88 dB |
| `reel-10-half-15ips-dolbysr` | 30 min | 87 dB |

**With dbx Type I (+30 dB):**

| Preset | Duration | DR |
|--------|----------|-----|
| `reel-7-half-7.5ips-dbx` | 30 min | 96 dB |
| `reel-7-half-15ips-dbx` | 15 min | 95 dB |
| `reel-10-half-7.5ips-dbx` | 60 min | 96 dB |
| `reel-10-half-15ips-dbx` | 30 min | 95 dB |

### MiniDisc SP (single-sided)

| Preset | Duration | DR | Notes |
|--------|----------|-----|-------|
| `md-60` | 60 min | 93 dB | MD-60 disc |
| `md-74` | 74 min | 93 dB | MD-74 disc |
| `md-80` | 80 min | 93 dB | MD-80 disc |

ATRAC-encoded — hard digital ceiling like VHS. High effective DR from
the codec's psychoacoustic noise shaping.

### CD-DA (single-sided)

| Preset | Duration | DR | Notes |
|--------|----------|-----|-------|
| `cd-74` | 74 min | 96 dB | Standard Red Book (650 MB) |
| `cd-80` | 80 min | 96 dB | Extended disc (700 MB) |

16-bit PCM — hard digital ceiling. The rendered FLAC master can be burned
to CD-R via cdrdao (DAO mode, 4-8x recommended for audio).

### Vinyl LP (two-sided)

| Preset | Duration | Per Side | DR | Notes |
|--------|----------|----------|-----|-------|
| `vinyl-lp` | 44 min | 22 + 22 | 70 dB | 12" at 33 1/3 RPM |

**Warning:** The vinyl-lp preset is for **duration/side planning only**.
Vinyl mastering requires bass mono summing (<150 Hz), de-essing (4-8 kHz),
and HF rolloff (16-18 kHz) — none of which are implemented yet. Do not
send rendered masters to a cutting engineer without additional processing.
See `vinyl_recording_reference.md` for the full list of constraints.

### Custom

Use `--medium custom --duration <minutes>` for unlisted configurations.
Custom medium defaults to 90 dB dynamic range (compression triggers at
LRA > 22.5 — effectively never for most material).

---

## Calibration Procedures (Summary)

Calibration is a one-time procedure per equipment setup. It maps the
DAC's fixed output voltage to the correct recording level on your deck.

### VHS Hi-Fi (AG-7350 or similar)

1. Play `cal_+00dBFS.flac` (0 dBFS, 1 kHz) from musiktool's calibration set
2. Adjust deck's Hi-Fi record level until meter reads exactly **0 dB**
3. Verify with -10 and -20 dBFS tones — should track 1:1 (FM is linear)

**Result:** Digital full scale = deck's hard ceiling. Music at -14 LUFS
lands with peaks at -1 to -3 on the meter — safely below clipping.

### Compact Cassette

1. Play `cal_-20dBFS.flac` (-20 dBFS, 1 kHz)
2. Adjust deck's record level until VU meter reads exactly **0 dB**
3. Lock the level control

**Result:** -20 dBFS = 0 VU. Music at -14 LUFS will show VU meter
swinging around +3 to +6 — the progressive saturation sweet spot where
tape adds warmth without audible distortion. The exact safe range above
0 VU depends on your tape type (Type I: +3-6, Type II: +6-8, Type IV:
+8-10+).

### Open Reel

Same principle as cassette but the reference level depends on your
chosen operating level and tape formulation. See
`tape_recording_reference.md` for detailed procedure.

### MiniDisc

Digital input (S/PDIF optical or coaxial) bypasses the ADC entirely —
no calibration needed. The digital signal goes directly to ATRAC encoding.

For analog input: same procedure as VHS Hi-Fi — adjust line-in level
to match full-scale reference.

---

## Common Questions

**Q: My deck has Dolby B but I'm not sure what tape type I have.**
Use the bare preset (`c-90`) without NR suffix. It's the most
conservative assumption (55 dB DR). You'll get slightly more compression
than necessary on wide-dynamic material, but never too little.

**Q: Can I use a preset with higher DR than my actual medium?**
Yes, but the compressor won't trigger when it should. Quiet passages in
wide-dynamic material may disappear into hiss. Under-specifying DR is
safe (conservative); over-specifying is risky.

**Q: Why is open reel only half-track?**
Quarter-track stereo has ~3 dB less SNR and is typically a consumer
configuration. If you have a quarter-track deck, subtract ~3 dB from
the listed DR values and use the `custom` preset with an appropriate
duration, or use the closest reel preset (the DR will be slightly
optimistic but close enough for the compressor decision).

**Q: My reel deck runs at 30 ips — why isn't that listed?**
30 ips is mastering-only speed with short duration per reel (7.5 min on
a 10.5" reel). If you have this setup, use `custom` with the appropriate
duration. DR is similar to 15 ips (~65 dB base).

---

*See also: `tape_recording_reference.md` for detailed format specifications,
`playback-normalization.md` for the gain algorithm design,
`tape-cli.md` for full command reference.*
