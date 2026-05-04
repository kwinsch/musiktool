# Tape Recording Reference — Formats, Dynamic Range & Calibration

> Technical reference for analog tape recording with a digital source chain.
> Covers compact cassette (Types I–IV), open reel (reel-to-reel),
> VHS/SVHS Hi-Fi, and VHS linear audio.

---

## Signal Chain Assumed

```
Linux box (digital source)
  → Topping D10s USB DAC (fixed 2V RMS @ 0 dBFS, line out)
    → Target recorder (cassette deck / VHS deck)
```

---

## Key Concept: dB Scales Are Not Interchangeable

| Scale    | What it measures                         | Reference point                          |
|----------|------------------------------------------|------------------------------------------|
| dBFS     | Digital level (Full Scale)               | 0 dBFS = maximum digital amplitude       |
| dBVU     | Analog signal (VU meter, ~300 ms RMS)    | 0 VU = +4 dBu (pro) or −10 dBV (consumer) |
| dBV      | Voltage, referenced to 1V RMS           | 0 dBV = 1.000 V RMS                     |
| dBu      | Voltage, referenced to 0.775V RMS       | 0 dBu = 0.775 V RMS                     |
| LUFS     | Perceptual loudness (time-integrated, frequency-weighted) | 0 LUFS = extremely loud by any standard |

A 0 dB reading on a VU meter, a peak meter, and a digital meter are
completely different signal levels. Confusing them is the #1 source of
bad recording levels.

---

## Cassette Tape Types

### Recording Principle

Cassette audio is **amplitude-based magnetic recording**. The audio signal
directly modulates the strength of magnetization on the tape. Sound quality
depends entirely on the magnetic properties of the oxide/metal particles:
flux capacity, particle uniformity, and short-wavelength (high-frequency)
retention. Tape saturates **progressively** — there is no hard clip point,
just increasing distortion and high-frequency compression.

### Dynamic Range by Type

| Type   | IEC Class | Examples                        | Approx. Dynamic Range | Headroom above 0 VU | Notes                                    |
|--------|-----------|----------------------------------|----------------------|----------------------|------------------------------------------|
| Type I | IEC I     | TDK D, Maxell UR                | 55–60 dB            | 3–6 dB              | Ferric oxide. Saturates earliest, especially HF. Highest noise floor. |
| Type II| IEC II    | TDK SA, Maxell XLII, Sony UX   | 62–68 dB            | 6–8 dB              | Chrome or cobalt-doped ferric. Higher coercivity, better HF response. |
| Type IV| IEC IV    | TDK MA, Maxell MX, Sony Metal-ES| 70–78 dB            | 8–10+ dB            | Metal particle. Highest coercivity/remanence. Best HF saturation point. |

Type III (IEC III, ferrichrome) combined a ferric base layer with a chrome
top coat. It was discontinued in the 1980s — virtually no decks or tapes
remain in production. Ignore for practical purposes.

**With Dolby Noise Reduction:**

| Dolby Type | Effective Noise Floor Reduction | Net Dynamic Range Gain |
|------------|--------------------------------|------------------------|
| Dolby B    | ~10 dB                         | +10 dB                 |
| Dolby C    | ~20 dB                         | +20 dB                 |
| Dolby S    | ~24 dB                         | +24 dB                 |

> **Caution:** Dolby NR encodes level-dependently. Gross over- or under-recording
> causes mistracking artifacts on playback. Proper level calibration is critical
> when using Dolby.

### Cassette Calibration Procedure

1. Generate a reference tone on Linux:
   ```bash
   sox -n -r 44100 -b 16 cal_tone.wav synth 30 sine 1000 vol -20 dB
   ```
   This produces a 30-second, 1 kHz sine at **−20 dBFS**.

2. Play through the D10s into the cassette deck's line input.

3. Adjust the deck's recording level until the VU meter reads **0 dB**.

4. This places the reference 20 dB below digital full scale, which maps
   well to the tape's usable range above 0 VU (6–10 dB depending on
   formulation).

5. Music mastered at **−14 LUFS** (peaks around −1 to −3 dBFS) will land
   with peaks in the **+3 to +6 dB VU** range — the sweet spot where tape
   sounds full but not crushed.

**Key insight:** On cassette, you *want* peaks above 0 VU. The progressive
saturation of analog tape compresses transients naturally, and the region
above 0 VU is usable range, not danger zone. How far you can push depends
on the tape formulation.

---

## Open Reel (Reel-to-Reel)

### Recording Principle

Same amplitude-based magnetic recording as cassette. What makes open reel
superior is wider tracks, faster tape speed, and better formulations:

- **Track width**: Quarter-track reel uses 1.0 mm tracks on 6.3 mm (1/4")
  tape vs 0.6 mm on 3.81 mm cassette tape. Half-track stereo uses
  2.0–2.8 mm — roughly 4x a cassette track. Wider tracks mean more
  magnetic particles per sample, raising signal relative to noise (~3 dB
  per doubling of track width).
- **Tape speed**: Even the slowest common speed (7.5 ips / 19 cm/s) is
  4x faster than cassette (1.875 ips). Higher speed spreads each signal
  wavelength over more tape, improving HF response and reducing dropout
  audibility.
- **Tape formulation**: Thicker oxide coatings and higher-coercivity
  particles than cassette tape allow higher magnetization levels before
  saturation.

Saturation behavior is the same as cassette — progressive, not hard clip.
The usable headroom above 0 VU is greater due to better formulations.

### Track Configurations (1/4" tape)

| Config | Tracks | Track Width | Directions | SNR vs Quarter-Track | Use Case |
|--------|--------|-------------|------------|---------------------|----------|
| Full track | 1 (mono) | ~6 mm | 1 | +6 dB | Mono mastering |
| Half track | 2 (stereo) | 2.0–2.8 mm | 1 | +3 dB | Professional stereo, master tapes |
| Quarter track | 4 (2×stereo) | 1.0 mm | 2 (flip) | baseline | Consumer, doubles recording time |

Half-track is the standard for professional stereo recording. Quarter-track
doubles recording time by interleaving tracks (1+3 forward, 2+4 reverse)
but sacrifices ~3 dB SNR and risks crosstalk between adjacent tracks.

### Tape Speeds

| Speed | Metric | Frequency Response | Typical SNR (half-track) | Use Case |
|-------|--------|-------------------|--------------------------|----------|
| 3.75 ips | 9.5 cm/s | 30–16 kHz | 62–64 dB | Consumer, voice, pre-recorded tapes |
| 7.5 ips | 19 cm/s | 30–20 kHz | 65–67 dB | Prosumer, broadcast, most versatile |
| 15 ips | 38 cm/s | 30–22 kHz | 63–67 dB | Professional music recording |
| 30 ips | 76 cm/s | 40–22 kHz+ | 65–67 dB | Mastering, highest fidelity |

Doubling speed doubles tape consumption. The SNR does not scale linearly
with speed because the replay equalization changes with speed, and tape
noise spectrum shifts. The advantage of higher speed is primarily in
frequency response, wow & flutter, and transient accuracy — not raw SNR.

### Dynamic Range

**Without noise reduction** (1/4" tape, standard/+3 dB formulation):

| Configuration | 7.5 ips | 15 ips |
|---------------|---------|--------|
| Quarter-track stereo | 60–63 dB | — |
| Half-track stereo | 65–67 dB | 63–67 dB |
| Full-track mono | 68–72 dB | — |

Using +6 dB tape (SM911 class) at 320 nWb/m gains ~5 dB over the 185
nWb/m reference. Using +9 dB tape (SM900 class) gains ~8 dB.

**With noise reduction:**

| System | Noise Reduction | Typical Result (half-track, 15 ips) | Notes |
|--------|----------------|-------------------------------------|-------|
| Dolby A | 10–15 dB | 75–82 dB | Four-band compander. Professional standard 1966–1986. |
| Dolby SR | 20–25 dB | ~90 dB | Multi-band (10 fixed + sliding). Best analog NR ever made. |
| dbx Type I | ~30 dB | 90+ dB | 2:1 broadband compander. Very effective but prone to "breathing" artifacts on tracking errors. |

### Reel Sizes and Recording Times

All times per direction in minutes. Quarter-track doubles these (two
passes per reel).

**Standard play (1.5 mil / 50 µm):**

| Reel | Tape Length | 3.75 ips | 7.5 ips | 15 ips | 30 ips |
|------|-------------|----------|---------|--------|--------|
| 5" | 600 ft | 30 | 15 | 7.5 | 3.75 |
| 7" | 1200 ft | 60 | 30 | 15 | 7.5 |
| 10.5" NAB | 2500 ft | 120 | 60 | 30 | 15 |

**Long play (1.0 mil / 35 µm):**

| Reel | Tape Length | 3.75 ips | 7.5 ips | 15 ips | 30 ips |
|------|-------------|----------|---------|--------|--------|
| 5" | 900 ft | 45 | 22.5 | 11 | 5.5 |
| 7" | 1800 ft | 90 | 45 | 22.5 | 11 |
| 10.5" NAB | 3600 ft | 180 | 90 | 45 | 22.5 |

1.5 mil is preferred for professional use — handles tension, rewind
stress, and repeated passes best. 1.0 mil (long play) is acceptable and
widely available (RTM LPR35, LPR90, ATR MDS-36). Thinner tapes
(double/triple play, 0.5 mil) are fragile and risk print-through; not
recommended for new recordings.

### Tape Formulations (currently available)

Two manufacturers produce new 1/4" tape as of 2025:

**Recording The Masters (RTM)** — Avranches, France (owns AGFA/BASF/EMTEC
formulas):

| Product | Class | Operating Level | Base | Notes |
|---------|-------|----------------|------|-------|
| SM911 | +6 dB | 320 nWb/m | 1.5 mil, back-coated | Professional workhorse. Bias-compatible with Ampex 456 class. |
| LPR35 | +6 dB | 320 nWb/m | 1.0 mil, no back-coat | Long-play version of SM911. |
| SM900 | +9 dB | 355–520 nWb/m | 1.5 mil, back-coated | Highest output. For machines calibrated to high operating levels. |
| LPR90 | +9 dB | 355 nWb/m | 1.0 mil, back-coated | Long-play version of SM900. |

**ATR Magnetics** — York, Pennsylvania:

| Product | Class | Base | Notes |
|---------|-------|------|-------|
| ATR Master Tape | +6 dB | 1.5 mil | Standard-play professional tape. |
| MDS-36 | +6 dB | 1.0 mil | Long-play, NAB hub. |

Within the same class, standard and long-play versions are nearly
bias-compatible (SM911 ↔ LPR35, SM900 ↔ LPR90).

### Equalization Standards

Two competing standards define the replay EQ curve. Using the wrong one
produces audibly wrong frequency response.

| Speed | NAB (North America) | IEC1/CCIR (Europe) |
|-------|--------------------|--------------------|
| 3.75 ips | 3180 / 90 µs | — |
| 7.5 ips | 3180 / 50 µs | ∞ / 70 µs |
| 15 ips | 3180 / 50 µs | ∞ / 35 µs |
| 30 ips | — | ∞ / 35 µs (AES/IEC2: ∞ / 17.5 µs) |

NAB uses a low-frequency shelf (3180 µs = 50 Hz turnover) that reduces
head-bump effects but adds a slight noise penalty. IEC1/CCIR is flat to
DC (∞ = no LF shelf), giving slightly better LF SNR but requiring careful
head alignment. At 7.5 ips, the NAB/IEC mismatch is the most common
source of tonal errors: NAB tapes on CCIR machines sound dull and
bass-heavy; CCIR tapes on NAB machines sound bright and thin.

### Reference Fluxivity (Operating Level)

The reference fluxivity defines what magnetic flux level on the tape
corresponds to 0 VU on the meters. Higher operating levels push the
signal further above the noise floor but closer to saturation.

| Fluxivity | Relative Level | Context |
|-----------|---------------|---------|
| 185 nWb/m | 0 dB (reference) | Original Ampex standard, NAB reference, vintage machines |
| 250 nWb/m | +3 dB | European DIN standard, US studios 1960s–70s |
| 320 nWb/m | +5 dB | European professional standard, +6 dB tapes (SM911 class) |
| 355 nWb/m | +6 dB | Common Studer calibration with +6 tapes |
| 520 nWb/m | +9 dB | High-output tapes (SM900 class), maximum practical level |

The operating level must be matched to the tape formulation. Running +9 dB
tape at 185 nWb/m wastes its headroom; running standard tape at 520 nWb/m
causes heavy saturation distortion.

### Open Reel Calibration Procedure

Open reel calibration is more involved than cassette because there are
more variables: tape formulation, operating level, EQ standard, and
per-machine bias/EQ trimpots.

1. **Playback alignment** (requires a MRL calibration tape for your speed
   and EQ standard): play the reference tape and adjust the machine's
   playback level and EQ trims until the meter reads 0 VU on the
   reference tone and the frequency response is flat.

2. **Record alignment**: record a 1 kHz tone at the target operating
   level (e.g., 320 nWb/m for SM911). Adjust bias for minimum distortion
   at the chosen level, then trim record EQ for flat frequency response.

3. **Level calibration with the D10s**: same principle as cassette but the
   reference level depends on the chosen operating level:
   - For 320 nWb/m operation (~+5 dB over 185 nWb/m): a tone at
     **−14 dBFS** played through the D10s should read 0 VU on the deck.
   - This places digital peaks (−1 to −3 dBFS) at approximately +11 to
     +13 dB above 185 nWb/m reference — well within the headroom of +6
     dB tape but approaching saturation.
   - Adjust up or down depending on tape formulation and desired warmth.

Unlike VHS Hi-Fi, there is no single "correct" calibration point. The
mapping between digital levels and tape levels is a creative choice that
depends on how much tape saturation you want in the signal.

### Notable Decks

**Consumer / Prosumer:**

| Deck | Era | Speeds | Notes |
|------|-----|--------|-------|
| Revox A77 | 1967–77 | 3.75/7.5 | ~400,000 units made. Legendary reliability. |
| Revox B77 | 1978–83 | 3.75/7.5 or 7.5/15 | Improved A77. Direct-drive capstan. |
| Revox PR99 | 1980–85 | 3.75/7.5 or 7.5/15 | Pro version of B77: balanced XLR, rack-mount. |

**Professional:**

| Deck | Era | Speeds | Notes |
|------|-----|--------|-------|
| Studer A80 | 1970–86 | 7.5/15 or 15/30 | Classic studio machine. Discrete electronics with sonic "character." |
| Studer A810 | 1983–90 | 3.75/7.5/15 or 7.5/15/30 | Microprocessor-controlled. The studio reference. |
| Studer A807 | 1986–95 | 7.5/15/30 | Last Studer analog deck. NAB/IEC switchable. Dolby HX Pro. |
| Otari MX-5050 BII | 1978+ | 7.5/15 | Broadcast/studio workhorse. Front-panel bias/EQ adjustment. |
| Tascam 42 | 1980s | 7.5/15 | Affordable professional mastering deck. |

---

## VHS / SVHS Hi-Fi Audio

### Recording Principle

VHS Hi-Fi audio is **FM-modulated**. The audio signal controls the
*frequency deviation* of a carrier, not the magnetic amplitude on tape.
The tape only needs to reliably store the carrier at full magnetization —
it is essentially binary. This makes Hi-Fi VHS audio behave **almost like
a digital system**:

- Dynamic range and linearity come from the FM electronics, not tape magnetics.
- The tape quality is nearly irrelevant to audio quality (as long as it holds the carrier).
- **Clipping is hard** — exceeding maximum FM deviation produces harsh distortion, not soft saturation.
- There is no benefit to "pushing" levels into the red.

### Specifications (Panasonic AG-7350-E)

| Parameter             | Hi-Fi Audio        | Normal (Linear) Audio |
|-----------------------|--------------------|-----------------------|
| Dynamic Range / S/N   | 90 dB (spec)       | 48 dB (Dolby NR ON)  |
| Frequency Response    | 20 Hz – 20 kHz     | 50 Hz – 12 kHz       |
| Audio Input Level     | −8 dBV (0.398V RMS), 47 kΩ | Same              |
| Audio Output Level    | −8 dBV, 600 Ω      | Same                  |
| Track Count           | 2 (Hi-Fi rotary heads) | 2 (linear stationary head) |

The 90 dB SNR is achievable because FM encoding makes the tape itself
essentially transparent — the carrier is recorded at full saturation, so
the tape's amplitude noise does not translate to audio noise. The SNR
limit comes from the analog electronics: the FM modulator/demodulator
precision, input/output amplifier noise, and component aging. In
practice, 80–90 dB usable dynamic range is a realistic expectation for
well-maintained equipment (the AG-7350 spec rates it at 90 dB).

> The linear (longitudinal) tracks are amplitude-based like cassette but
> with narrow track width and slow relative head speed — worst of both
> worlds. Only use for compatibility or timecode.

### VHS Hi-Fi Calibration Procedure (AG-7350 with Topping D10s)

The D10s outputs 2V RMS at 0 dBFS. The AG-7350 expects −8 dBV (0.398V RMS)
at its nominal reference. The difference is ~14 dB, so the deck's input gain
must be attenuated.

1. Play a **0 dBFS** 1 kHz sine tone from the D10s:
   ```bash
   sox -n -r 44100 -b 16 cal_tone_0dBFS.wav synth 30 sine 1000
   ```

2. Adjust the Hi-Fi level controls on the AG-7350 front panel (turn
   negative / attenuate) until the level meter reads exactly **0 dB**.

3. Verify linearity with additional test tones:
   ```bash
   sox -n -r 44100 -b 16 cal_m10.wav synth 30 sine 1000 vol -10 dB
   sox -n -r 44100 -b 16 cal_m20.wav synth 30 sine 1000 vol -20 dB
   sox -n -r 44100 -b 16 cal_p3.wav  synth 30 sine 1000 vol  -3 dB
   ```
   All should track 1:1 on the meter (confirmed: the FM path is linear).

4. Music mastered at **−14 LUFS** with peaks at −1 to −3 dBFS will show
   peaks at −1 to −3 on the deck's meter — safely below clipping with
   full dynamic range preserved.

**Key insight:** On VHS Hi-Fi, 0 on the meter is the **hard ceiling**, not a
nominal reference. There is no usable range above it. The meter is
essentially a direct linear readout of your digital levels after calibration.

### AG-7350 On-Screen Settings for Audio Recording

| Setting        | Recommended Value | Reason                                      |
|----------------|-------------------|---------------------------------------------|
| AUDIO LIMITER  | OFF               | Preserves dynamics; levels are pre-calibrated |
| DOLBY NR       | Match on record/play | Mistracking if mismatched               |
| HIFI REC       | ON                | Enables Hi-Fi rotary head recording         |
| METER selector | Hi-Fi             | Shows Hi-Fi level on front panel meters     |

---

## Summary: Why Calibration Differs

| Property              | Cassette (amplitude)           | Open Reel (amplitude)              | VHS Hi-Fi (FM)                  |
|-----------------------|--------------------------------|------------------------------------|---------------------------------|
| Signal encoding       | Magnetic flux strength         | Magnetic flux strength             | Frequency deviation of carrier  |
| Saturation behavior   | Progressive (soft)             | Progressive (soft)                 | Hard clip                       |
| Tape quality matters? | Critical                       | Critical                           | Minimal (carrier only)          |
| Dynamic range         | 55–78 dB (by type)             | 60–72 dB (by config, without NR)   | 90 dB (AG-7350 spec)            |
| 0 dB on meter means   | Nominal reference, headroom above | Nominal reference, headroom above | Near maximum, danger above     |
| Push above 0 dB?      | Yes, 3–10 dB usable           | Yes, 6–12+ dB usable              | No — hard distortion            |
| Calibration tone      | −20 dBFS → 0 VU               | Depends on operating level         | 0 dBFS → 0 on meter            |
| Best for −14 LUFS music | Peaks at +3 to +6 VU        | Peaks in upper headroom range      | Peaks at −1 to −3 on meter     |

---

*Document generated from calibration sessions and technical research.
Equipment: Topping D10s DAC, Panasonic AG-7350-E SVHS deck.
References: Panasonic AG-7350/AG-7150 Operating Instructions (VQT4386),
IASA TC-04, NAB 1965 standard, Richard Hess tape timing charts.*
