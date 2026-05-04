# Vinyl Recording Reference — Cutting, Pressing & Playback

> Technical reference for vinyl record production from a digital source.
> Covers groove mechanics, RIAA equalization, mastering constraints, and
> the pressing process.

---

## Recording Principle

A vinyl record stores audio as a continuous spiral groove modulated by
the audio signal. A cutting lathe spins a lacquer-coated aluminum disc
at constant RPM while a heated stylus (sapphire, ruby, or diamond)
engraves a spiral groove from the outer edge inward.

### Lateral vs. Vertical Modulation

- **Lateral**: stylus moves side-to-side. All modern mono records use
  lateral cutting.
- **Vertical** (hill-and-dale): stylus moves up and down. Historical
  (Edison). Abandoned due to susceptibility to tracking force
  variations.

### Stereo Encoding — 45/45 System

The Westrex system (1958) encodes two channels as modulation of the two
groove walls, each inclined 45 degrees from vertical:

- **Left channel** modulates the inner groove wall
- **Right channel** modulates the outer groove wall
- **Mono (L+R)** produces pure lateral modulation — backward-compatible
  with mono cartridges
- **Difference (L-R)** produces pure vertical modulation

Out-of-phase bass creates large vertical excursions that risk the stylus
leaving the groove entirely. This is why bass must be mono for vinyl.

---

## RIAA Equalization

### Purpose

The RIAA curve (standardized 1954, replacing 100+ competing curves)
serves two functions:

1. **Groove space**: Attenuating bass during cutting reduces lateral
   excursion, allowing tighter groove spacing and longer playing time
2. **Noise reduction**: Boosting highs during cutting and cutting them
   during playback suppresses vinyl surface noise

### Time Constants and Transition Frequencies

| Time Constant | Frequency | Function |
|--------------|-----------|----------|
| 3180 us | 50.05 Hz | Low-frequency shelving begins |
| 318 us | 500.5 Hz | Mid-frequency transition |
| 75 us | 2122 Hz | High-frequency shelving begins |

Transfer function:

```
H(s) = (1 + s*T2) / ((1 + s*T1) * (1 + s*T3))
where T1=3180us, T2=318us, T3=75us
```

The IEC amendment (1976) added a 7950 us time constant (20 Hz subsonic
rolloff) to suppress turntable rumble. This was withdrawn in 2009 but
remains in some phono preamp designs.

### RIAA Curve Values (0 dB at 1 kHz)

| Frequency | Playback (boost/cut) | Recording (inverse) |
|-----------|---------------------|---------------------|
| 20 Hz | +19.3 dB | -19.3 dB |
| 50 Hz | +17.0 dB | -17.0 dB |
| 100 Hz | +13.1 dB | -13.1 dB |
| 500 Hz | +2.6 dB | -2.6 dB |
| 1 kHz | 0.0 dB | 0.0 dB |
| 2 kHz | -2.6 dB | +2.6 dB |
| 5 kHz | -8.2 dB | +8.2 dB |
| 10 kHz | -13.7 dB | +13.7 dB |
| 20 kHz | -19.6 dB | +19.6 dB |

Total swing: ~39 dB from 20 Hz to 20 kHz. During recording, bass is cut
by ~19 dB and treble is boosted by ~20 dB relative to 1 kHz.

---

## Groove Geometry

### Microgroove (LP/45 RPM)

| Parameter | Value |
|-----------|-------|
| Included angle | 90 degrees (+/- 5 degrees) |
| Top width (unmodulated) | ~56 um |
| Minimum top width (modulated stereo) | 25 um |
| Depth | ~28 um |
| Bottom radius | 6 um max |

### Groove Pitch

- Fixed pitch: typically 200–300 grooves per inch
- Variable pitch (computer-controlled, standard since ~1970s):
  dynamically adjusts spacing — wider for loud passages, tighter for
  quiet passages. Gains 20–30% more time vs fixed pitch.

Maximum modulation amplitude: ~38 um peak displacement (~76 um
peak-to-peak) before groove walls overlap adjacent tracks.

---

## Speed Standards

| Format | RPM | Primary use |
|--------|-----|-------------|
| 78 RPM | 78.26 | Shellac singles (10"/12"), ~1898–1960s |
| 33 1/3 RPM | 33.333 | LP albums (12", 10"), 1948–present |
| 45 RPM | 45 | Singles (7"), EPs, maxis (12"), 1949–present |

### Linear Groove Velocity (12" disc)

| Position | 33 1/3 RPM | 45 RPM |
|----------|-----------|--------|
| Outer groove (r=146 mm) | 51.0 cm/s | 68.8 cm/s |
| Mid-disc (r=100 mm) | 34.9 cm/s | — |
| Inner groove (r=60 mm) | 21.1 cm/s | 28.4 cm/s |

Velocity ratio outer:inner is approximately 2.4:1. This is the
fundamental cause of inner groove distortion — the same frequency is
encoded in a groove wavelength 2.4 times shorter at the inner edge.

---

## Dynamic Range

| Condition | Dynamic Range |
|-----------|--------------|
| Theoretical maximum (perfect vinyl) | ~70 dB |
| Practical (quality pressings) | 55–65 dB |
| Outer groove | up to ~60 dB |
| Inner groove | drops to ~50 dB |

Dynamic range decreases toward the inner groove due to reduced linear
velocity (less headroom, increased noise floor relative to signal).

---

## Frequency Response

**Theoretical bandwidth**: below 20 Hz to beyond 50 kHz.

**Practical limits:**
- Low end: RIAA curve provides bass boost to 20 Hz
- High end: most commercial records roll off above 15–18 kHz during
  mastering; content above 23–24 kHz is typically distortion artifacts

### Inner Groove Wavelengths (33 1/3 RPM, r=60 mm)

| Frequency | Wavelength |
|-----------|-----------|
| 10 kHz | 21.1 um |
| 15 kHz | 14.0 um |
| 20 kHz | 10.5 um |

A standard 18 um stereo stylus tip is larger than the 15 kHz wavelength
at the inner groove. Line-contact/micro-ridge styli (minor axis 2.5–6
um) significantly improve inner groove tracing.

### Tracing Distortion

Caused by geometric mismatch between the triangular cutting stylus and
the rounded playback stylus. Increases with:
- Higher frequencies
- Smaller groove radius (inner grooves)
- Larger stylus tip radius
- Higher recorded velocity (louder signals)

Typical HF degradation outer to inner groove: 5–8 dB at 10 kHz.

---

## Level Constraints

### Bass Must Be Mono

Out-of-phase low frequencies cause vertical stylus movement. Large
vertical excursions risk the stylus jumping the groove.

| Frequency Range | Requirement |
|----------------|-------------|
| Below 150 Hz | Must be mono (absolute rule) |
| 150–300 Hz | Extreme caution, tight phase control |
| Above 300 Hz | Generally safe for full stereo width |

Engineers use elliptical EQ (crossover at 75–300 Hz) to progressively
narrow the stereo image toward mono in the bass.

### Sibilance and HF Distortion

- Excessive HF content creates rapid stylus acceleration during cutting
- Cutting heads have finite power and overheat above 18–20 kHz
- During playback, the stylus may fail to track aggressive HF
  modulation
- De-essing (4–8 kHz) is standard in vinyl mastering

### Groove Excursion vs. Level

Louder signal = wider groove = wider spacing = less playing time. The
relationship is directly proportional: doubling amplitude doubles
required groove width.

---

## Side Duration vs. Quality Trade-off

Every additional minute of playing time costs audio quality.

### Recommended Durations (12" LP at 33 1/3 RPM)

| Cutting Level (ref 7 cm/s) | Max Duration | Quality |
|---------------------------|-------------|---------|
| +8 dB (hot) | ~12 min | Maximum loudness |
| +6 dB | ~14 min | Loud |
| +4 dB | ~16 min | Good balance |
| +2 dB (typical) | ~18 min | Standard album level |
| 0 dB (reference) | ~22 min | Full duration, moderate level |

### Duration Guidelines by Format

| Format | RPM | Optimal | Maximum |
|--------|-----|---------|---------|
| 12" | 33 1/3 | 16–20 min | 25 min |
| 12" | 45 | 6–12 min | 15 min |
| 10" | 33 1/3 | 9 min | 14 min |
| 7" | 45 | 3 min | 5 min |

### What Degrades with Longer Sides

- Lower cutting level (reduced loudness and dynamic range)
- Tighter groove pitch (more crosstalk, less noise margin)
- Higher surface noise relative to signal
- Inner groove distortion on the last tracks

---

## Vinyl Weight

| Weight | Category |
|--------|----------|
| 120g | Economy/standard |
| 140g | Standard (most common historically) |
| 180g | "Audiophile" |
| 200g | Premium/super-heavyweight |

**Weight does not directly affect sound quality.** If the same stamper is
used, 120g and 180g pressings encode identical groove geometry. The
audio information is in the groove modulation, not the bulk material.

**Indirect benefits of heavier pressings:** greater warp resistance,
more stable on platter, potentially better centering. The perception of
better sound exists because 180g pressings are typically part of premium
programs with better mastering and quality control.

---

## Mastering for Vinyl

### Bass Management

- Sum to mono below 150 Hz (elliptical EQ or mid-side processing)
- Check phase coherence 150–300 Hz
- Reduce sub-bass below ~30 Hz (wastes groove space, warp-like
  artifacts)

### HF Control

- De-essing at 4–8 kHz
- Low-pass filter at 16–18 kHz
- Gentle HF rolloff starting ~16 kHz is common practice

### Dynamics

- Avoid brick-wall limiting (grooves cannot reproduce clipped waveforms
  cleanly)
- Target peak levels at -6 dBFS or lower for headroom
- Gentle compression may be needed if source exceeds ~60 dB dynamic
  range
- The medium rewards dynamics — avoid loudness maximization

### Track Ordering

- Place the most demanding material (loud, bass-heavy, wide stereo) at
  the beginning of each side (outer grooves)
- Save quieter, simpler arrangements for the inner grooves
- Spread material evenly across sides

### Stereo Width

- Check mono compatibility (L+R sum must be clean)
- Avoid extreme stereo width in the low-mids
- Hard-panned low-frequency content is dangerous

### File Delivery

24-bit WAV at 44.1 or 48 kHz. No dither applied — the cutting engineer
handles final processing.

---

## Lacquer vs. DMM (Direct Metal Mastering)

### Lacquer Cutting (Conventional)

- Cuts into nitrocellulose lacquer on aluminum disc
- Lacquer is soft — allows deep cuts, large modulation amplitude
- Requires three electroplating generations (lacquer -> father -> mother
  -> stamper), each adding slight noise and detail loss
- Susceptible to pre-echo/post-echo (groove deformation from adjacent
  groove modulation — heard as a ghost ~1.8 s before/after the signal)
- Sound character: warmer, fuller bass, slightly softer transients

### DMM (Direct Metal Mastering)

- Developed by Teldec and Georg Neumann GmbH
- Cuts directly into copper-plated stainless steel with a diamond stylus
  vibrating at 60 kHz (ultrasonic burnishing)
- Only one electroplating generation needed (copper master -> stamper)
- Eliminates pre-echo/post-echo (hard copper does not deform)
- Sound character: cleaner, more detailed, better transients; sometimes
  described as "forward" or "harsh" in HF
- Cannot cut as deep as lacquer — slightly less LF headroom
- Only ~30 DMM lathes were ever manufactured (all Neumann VMS80 series)

---

## Half-Speed Mastering

The cutting lathe runs at half speed (16 2/3 RPM for a 33 1/3 record)
while the source audio plays at half speed. At normal playback speed,
the original audio is reproduced.

**Why it improves quality:**
- Cutting stylus has twice as long per modulation cycle
- 20 kHz content becomes 10 kHz during cutting — within the cutter
  head's comfortable range
- Result: improved HF accuracy, better stereo imaging, reduced tracing
  distortion

**Limitations:**
- Bass drops an octave during cutting (20 Hz -> 10 Hz), where the
  cutter head is less efficient — not ideal for bass-heavy material
- Each side takes twice as long to cut
- Cutting engineer cannot monitor at correct pitch in real time

Notable practitioners: Mobile Fidelity Sound Lab (MFSL), Abbey Road
Studios (Miles Showell).

---

## Pressing Process

### Step 1 — Lacquer Cutting

The mastering engineer cuts audio into a lacquer disc on a cutting
lathe. One lacquer per side.

### Step 2 — Silvering

The lacquer surface is sprayed with a thin silver (or tin chloride)
layer to make it electrically conductive.

### Step 3 — Father (1st electroplating)

The silvered lacquer is nickel-plated in a sulfamate bath, building a
negative metal impression (the "father" or "metal master"). The lacquer
is destroyed — it peels away from the nickel.

### Step 4 — Mother (2nd electroplating)

The father is passivated and electroplated again. The resulting positive
impression (the "mother") is an exact replica of the original lacquer.

### Step 5 — Stamper (3rd electroplating)

The mother is electroplated to produce one or more stampers — negative
impressions with ridges where grooves will be. Multiple stampers can be
made from one mother for high-volume production. Stampers are trimmed,
center-punched, and back-polished.

### Step 6 — Pressing

Two stampers (one per side) are mounted in a hydraulic press. A
"biscuit" of heated PVC compound is pressed at ~100 tons and ~150 C,
forcing vinyl into every groove ridge. Labels are fused into both sides
during pressing. Cold water cools the stampers and the finished record
is extracted.

### Step 7 — Trimming and QC

Excess vinyl (flash) is trimmed. Records are visually inspected and a
percentage play-tested.

**Stamper life:** typically 1,000–1,500 records before groove quality
degrades noticeably.

**DMM shortcut:** The copper disc serves directly as the mother, skipping
the father step entirely — one electroplating generation to produce
stampers, preserving more detail.

### Test Pressings

A small run (3–5 copies) from production stampers before the full run.
Verifies:
- Correct track sequence and side splits
- A/B labeling and matrix numbers
- Absence of pressing defects (clicks, pops, non-fill, warping)
- Overall audio quality

If a defect appears at the same position on all copies, it is a stamper
defect. If it varies, it is a pressing process issue.

---

## Summary: Vinyl Constraints for Digital Mastering

| Constraint | Value / Rule |
|-----------|-------------|
| Bass mono below | 150 Hz (absolute) |
| Bass phase caution | 150–300 Hz |
| HF rolloff | 16–18 kHz |
| De-essing | 4–8 kHz |
| Dynamic range | ~55–65 dB practical |
| Optimal side duration (12" 33 1/3) | 16–20 min |
| Maximum side duration (12" 33 1/3) | ~25 min |
| Peak headroom | -6 dBFS or lower |
| Inner groove HF loss | 5–8 dB at 10 kHz |
| RIAA swing | ~39 dB (20 Hz to 20 kHz) |

---

*References: RIAA equalization standard (1954), IEC 60098, AES
standards for disc recording, Neumann VMS80 documentation.*
