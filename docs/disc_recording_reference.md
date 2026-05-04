# Disc Recording Reference — CD-DA, CD-R/RW & MiniDisc

> Technical reference for digital disc recording with a digital source
> chain. Covers CD-DA (Red Book), CD-R/CD-RW, and MiniDisc (standard
> and Hi-MD).

---

## CD-DA (Red Book)

### Format Specifications

The Red Book standard (IEC 60908, Philips/Sony, 1980) defines:

| Parameter | Value |
|-----------|-------|
| Sampling rate | 44,100 Hz |
| Bit depth | 16 bits per sample |
| Channels | 2 (stereo) |
| Raw data rate | 1,411,200 bit/s (176,400 byte/s) |
| Max tracks | 99 |
| Max duration | 79 min 57 s (80-min disc) |

### Dynamic Range

Theoretical maximum for 16-bit linear PCM:

```
DR = 6.02 * n + 1.76 = 6.02 * 16 + 1.76 = 98.09 dB
```

| Condition | Dynamic Range |
|-----------|--------------|
| Undithered | ~96 dB (with correlated distortion near noise floor) |
| TPDF dithered | ~93 dB (clean noise floor, no distortion) |
| Noise-shaped dither | ~110–120 dB perceived (exploits psychoacoustic masking) |

Common noise-shaping algorithms: Apogee UV22, POW-r, iZotope MBIT+.
Without dither, signals below the LSB are lost entirely and quantization
distortion is correlated with the signal. TPDF (Triangular Probability
Density Function) dither is the standard for CD mastering — it
eliminates both distortion and noise modulation at the cost of ~4.77 dB
noise floor increase.

### Physical Data Structure

**Frame** — smallest addressable entity:
- 24 bytes audio (6 stereo samples)
- 8 bytes CIRC error correction
- 1 byte subcode
- After EFM encoding: 588 channel bits
- Frame rate: 7,350 per second

**Sector** — 98 frames:
- 2,352 bytes audio
- Read at 75 sectors per second
- Duration: 1/75 second (13.33 ms)

Addressing uses Minutes:Seconds:Frames (MSF), where frames = 1/75 s.

### Eight-to-Fourteen Modulation (EFM)

Each 8-bit byte is converted to a 14-bit channel codeword via lookup
table, with 3 merging bits between codewords for DC balance and
run-length constraint (minimum 3T, maximum 11T between transitions).
Channel bit rate: 4.3218 Mbit/s.

### Subchannel Data

Each frame carries 1 subcode byte. Across 98 frames per sector, this
yields 8 subchannels (P through W):

| Channel | Purpose |
|---------|---------|
| P | Track boundary flag (1 = pause/lead-in/lead-out) |
| Q | Track/index numbers, MSF timecodes, ISRC, catalog, copy-protection, pre-emphasis flag |
| R–W | CD+G graphics, CD-TEXT (not used in basic Red Book) |

The Q subchannel carries the TOC in the lead-in area and continuous
position information during playback.

### Error Correction — CIRC

Cross-Interleaved Reed-Solomon Code uses two levels of Reed-Solomon
coding with convolutional interleaving:

1. **C2 encoder** (outer): RS(28,24) — 24 data bytes + 4 parity bytes
2. **Interleaver**: 28-way delay-line, spreading consecutive bytes
   across up to 108 frames
3. **C1 encoder** (inner): RS(32,28) — 28 bytes + 4 parity bytes

Code rate: 24/32 = 75% (25% overhead).

**Error correction capability:**
- Corrects burst errors up to ~3,500 bits (~2.4 mm on disc)
- Conceals (interpolates) burst errors up to ~12,000 bits (~8.5 mm)
- Beyond that: audible artifacts or muting

**Error classification:**

| Type | Meaning | Acceptable level |
|------|---------|-----------------|
| C1 (BLER) | Errors at inner decoder | < 220/s average (Red Book max); good disc < 50/s |
| C2 | Errors C1 could not correct | Should be zero or near-zero |
| CU | Uncorrectable by either level | Any CU = damaged/defective disc |

Audio CDs use interpolation (estimating missing samples from neighbors)
when errors exceed correction capability. Data CDs cannot tolerate any
uncorrected errors — this is why CD-ROM adds an additional EDC/ECC layer
on top of CIRC.

### Pre-emphasis

An optional Red Book feature: first-order shelving filter with time
constants 50 us and 15 us, boosting HF by ~9.5 dB at 20 kHz during
recording. A flag in the Q subchannel signals the player to engage
de-emphasis.

Used on some early CDs (1982–1987, especially Japanese pressings) when
16-bit DACs were noisier. Rare on modern releases. Many ripping programs
ignore the flag, producing bright-sounding rips if the flag was set.
The flag can be per-track.

### CD-TEXT

Stores textual metadata in the R–W subchannels of the lead-in area
(~5 KB available). Defined in a 1996 Red Book appendix.

**Stored in 18-byte packs:**
- Title, performer, songwriter per track and per disc
- Disc ID, genre, UPC/EAN, ISRC per track
- Character encoding: ISO 8859-1, ASCII, or MS-JIS
- Up to 8 language blocks

Requires DAO burning. Compatibility varies widely — many CD-ROM drives
cannot read R–W subchannels.

### Jitter

Timing uncertainty in digital transitions. Most audibly significant at
the DAC conversion moment — for 16-bit audio at 20 kHz, jitter must
be below ~1 ns to keep jitter-induced noise below quantization noise.

The CD's EFM stream is self-clocking — a PLL circuit locks onto
transitions to recover the 4.3218 MHz channel clock. PLL loop bandwidth
trades tracking ability (wide) against jitter rejection (narrow).

In a well-designed modern player with proper reclocking, jitter is
below audibility thresholds.

---

## CD-R

### Recording Mechanism

The substrate contains a molded spiral pregroove with a sinusoidal
wobble at 22.05 kHz encoding ATIP (Absolute Time In Pregroove) —
timing, disc type, dye type, and rated speed.

During recording, the write laser (4–11 mW vs ~0.5 mW for reading)
heats spots of an organic dye layer, decomposing the dye and altering
its optical properties. This creates reflectivity contrast between
"land" and "mark" that the reading laser interprets as data. The dye
does not create physical pits — the contrast comes from changes in dye
transparency/absorption and slight substrate deformation.

### Disc Construction (inside to outside)

1. Polycarbonate substrate with pregroove
2. Organic dye layer (spin-coated)
3. Metallic reflective layer (sputtered)
4. Protective UV-cured lacquer

### Dye Types

| Dye | Appearance | Stability | Notes |
|-----|------------|-----------|-------|
| Cyanine | Blue-green | Lowest — UV and heat sensitive. Stabilized variants much better. | First CD-R dye (1988). Claims: ~75 years. |
| Phthalocyanine | Light green/gold | Highest — inherently stable, excellent UV resistance | Best for archival. Claims: 100–200+ years (especially with gold reflector). |
| Azo | Deep blue/navy | Good — chemically stable, no stabilizers needed | Mitsubishi/Verbatim patent (1996). Claims: ~100 years. |

Reflective layer matters too: gold is inert; silver/silver-alloy can
oxidize if the lacquer is compromised. For archival: phthalocyanine dye
+ gold reflector, stored cool and dark.

CD-R reflectivity: 40–70% (specified minimum 65%), lower than pressed
CDs (~70–80%) but within Red Book reading tolerances for most players.

### Capacity

| Disc | Audio | Data | Notes |
|------|-------|------|-------|
| 74-min | 74:33 | 650 MiB | Original Red Book spec |
| 80-min | 79:57 | 700 MiB | Marginally exceeds Red Book physical spec |

Audio CDs use all 2,352 bytes/sector; data CDs use 2,048 (304 bytes
for additional EDC/ECC). This is why raw audio capacity exceeds the
"650/700 MB" data figure.

Overburning (writing past ATIP lead-out) can gain 1–2 minutes. Requires
DAO mode and drive firmware support. Data at the extreme outer edge is
more vulnerable to errors.

### Burn Speed and Quality

1x = 176.4 KB/s audio. Higher speeds increase C1/C2 error rates,
jitter, and pit geometry degradation.

- Audio CDs have less error correction than data CDs (only CIRC, no
  additional EDC/ECC), making them less tolerant of physical errors
- Recommended: no higher than 4–8x for audio
- Media-speed matching matters: dye recording characteristics are
  optimized for the rated speed range. Burning a 52x-rated disc at 4x
  may produce worse results than 16x
- Modern drives with OPC (Optimum Power Calibration) produce good
  results at moderate speeds (8–16x)

---

## CD-RW

### Phase-Change Recording

Uses a silver-indium-antimony-tellurium (AgInSbTe) alloy recording
layer instead of organic dye:

| State | Structure | Reflectivity | How achieved |
|-------|-----------|-------------|-------------|
| Crystalline | Ordered | Higher ("land") | Initial state; restored by annealing (~200 C) |
| Amorphous | Disordered | Lower ("mark") | Melting (~500–700 C) then rapid cooling |

Write: max power melts spots to amorphous. Erase: intermediate power
re-crystallizes. Read: low power detects contrast.

Specified for up to 1,000 rewrite cycles.

### Compatibility

**Reflectivity: only 15–25%** — this is the critical limitation.

- Players before ~1997 generally cannot read CD-RW
- Requires MultiRead-compliant drives (OSTA, 1997) with AGC circuits
  for lower signal levels
- CD-RW discs do not meet Red Book reflectivity specs — technically
  not CD-DA compliant
- Many standalone players (home, car) have poor CD-RW compatibility
  even when CD-R works fine

---

## Disc-at-Once vs Track-at-Once

### Track-at-Once (TAO)

- Laser writes one track at a time, turning off between tracks
- Creates run-in/run-out link blocks (~2 s unplayable data) at each
  junction
- Disc can remain open for additional sessions
- Pregap content cannot be controlled — forced silence

### Disc-at-Once (DAO)

- Laser writes the entire disc in a single continuous pass
- No link blocks between tracks
- Full pregap/postgap control (silence, audio, zero-length)
- Disc is finalized and closed
- Required for: gapless albums, proper pregap audio (hidden tracks),
  accurate subchannel data, CD-TEXT, replication masters, overburning

### Session-at-Once (SAO)

Hybrid: one complete session without gaps, but allows additional
sessions later. Less common.

---

## CUE Sheets

Plain-text metadata describing CD image layout. Originally from CDRWIN,
now a de facto standard.

**Key commands:**

| Command | Purpose |
|---------|---------|
| `FILE "name" type` | Source audio file (WAVE, MP3, AIFF, BINARY) |
| `TRACK nn AUDIO` | Declare an audio track |
| `INDEX 00 mm:ss:ff` | Start of pregap (data in file) |
| `INDEX 01 mm:ss:ff` | Start of track audio |
| `PREGAP mm:ss:ff` | Generated silence before track (not in file) |
| `POSTGAP mm:ss:ff` | Generated silence after track |
| `TITLE "text"` | CD-TEXT title (disc or track) |
| `PERFORMER "text"` | CD-TEXT performer |
| `FLAGS PRE` | Pre-emphasis flag |
| `CATALOG nnnnnnnnnnnnn` | UPC/EAN barcode |
| `ISRC CCOOOOYYSSSSS` | International Standard Recording Code |

`PREGAP` generates silence not in any file. `INDEX 00` + `INDEX 01`
defines a pregap from actual file data. Only DAO preserves this
distinction.

---

## Linux Burning Tools

| Feature | cdrdao | wodim/cdrecord | K3B |
|---------|--------|---------------|-----|
| DAO mode | Only mode (native) | Supported (`-dao`) | Via cdrdao backend |
| CD-TEXT | Excellent | Unreliable | Via cdrdao backend |
| Pregap control | Full (INDEX 00/01) | Limited | Full (via cdrdao) |
| CUE input | Partial (use `cue2toc`) | No | Yes (translates) |
| Overburning | No | Yes | Yes |
| Audio format | WAV / raw PCM | WAV / raw PCM | Many (converts) |

**Workflow for audio CD from CUE+FLAC:**

```bash
# Decode FLAC to WAV
flac -d *.flac

# Convert CUE to TOC format
cue2toc input.cue > output.toc

# Burn DAO
cdrdao write --device /dev/sr0 --driver generic-mmc-raw --speed 4 output.toc
```

**Other relevant tools:**
- `cdparanoia` — secure extraction with jitter correction
- `whipper` — modern ripper with AccurateRip verification
- `sox input.wav output.wav deemph` — de-emphasis filter
- `cuetools` — CUE sheet manipulation
- `shnsplit` — split single-file images using CUE breakpoints

---

## MiniDisc

### Recording Principle — Magneto-Optical

A semiconductor laser (780 nm) heats a spot on the disc's recording
layer — an amorphous TbFeCo (terbium-iron-cobalt) alloy — to its Curie
point (~180 C). At this temperature the material loses coercivity,
allowing a magnetic head on the opposite side to write binary data as
patterns of magnetic polarity. As the spot cools, the orientation is
locked until re-heated.

Readback uses the Kerr effect: the polarization plane of reflected
laser light rotates differently depending on magnetic polarity.

**Physical specifications:**

| Parameter | Value |
|-----------|-------|
| Disc diameter | 64 mm (in 72 x 68 x 5 mm cartridge) |
| Track pitch | 1.6 um (1.5 um on 80-min discs) |
| Modulation | EFM |
| Error correction | ACIRC |
| Rewrite cycles | ~1,000,000 (Sony spec) |

### ATRAC Codec

ATRAC (Adaptive TRansform Acoustic Coding) is a lossy psychoacoustic
codec developed by Sony for MiniDisc (1992).

**Processing chain:**

1. **Subband decomposition**: QMF split into 3 bands (0–5.5 kHz,
   5.5–11 kHz, 11–22 kHz)
2. **MDCT transform**: Frequency-domain transform with adaptive block
   length — 11.6 ms (stationary signals) or 1.45–2.9 ms (transients,
   prevents pre-echo)
3. **Block Floating Units**: 52 non-uniform BFUs aligned to critical
   bands, each with shared word length and scale factor
4. **Psychoacoustic bit allocation**: Combines fixed and variable
   allocation weighted by signal tonality, exploiting simultaneous
   masking, forward masking, and backward masking
5. **Quantization**: Scale factors and word lengths stored alongside
   quantized spectral coefficients

**ATRAC DSP version history:**

| Version | Year | Key improvement |
|---------|------|----------------|
| ATRAC 1 | 1993 | 16x16-bit multiply; audible metallic artifacts |
| ATRAC 2 | 1994 | 16x24-bit; significant quality improvement |
| ATRAC 3 | 1995 | Dynamic filtering; near-DAT quality |
| ATRAC 4.0 | 1996 | 24x24-bit; response to 19 kHz |
| ATRAC 4.5 | 1996 | Adaptive high-band; response to 20 kHz |
| Type-R | 1998 | Two-pass bit allocation with redundancy detection |
| Type-S | 2002 | Improved ATRAC3 decoding + Type-R encoding |

Type-R is the practical quality threshold — earlier versions have
audible compromises. Type-S did not improve SP recording quality over
Type-R; its benefit was LP mode playback.

### Recording Modes

| Mode | Codec | Bitrate | Compression | Time (80-min disc) |
|------|-------|---------|-------------|-------------------|
| SP | ATRAC 1 | 292 kbit/s | 5:1 | 80 min |
| LP2 | ATRAC3 | 132 kbit/s | ~10:1 | 160 min |
| LP4 | ATRAC3 | 66 kbit/s | ~20:1 | 320 min |

MDLP (LP2/LP4) introduced September 2000. LP2 uses independent stereo
and is widely considered close to SP quality. LP4 uses joint stereo and
exhibits noticeable artifacts on complex material. LP2/LP4 tracks play
as silence on non-MDLP hardware.

### Input Types

**Optical digital (S/PDIF via TOSLINK or coaxial):**
- No ADC — PCM goes directly to ATRAC encoding
- Automatic track marking from CD subcode
- Highest fidelity path

**Analog (line in):**
- Signal passes through recorder's ADC (20–24-bit delta-sigma on
  better units)
- Manual level adjustment required
- Nearly inaudible difference from digital with proper levels on
  good hardware

**NetMD (USB, 2001):**
- PC-to-MD via SonicStage software
- Limited to LP2/LP4 — no SP transfer on most units
- Download only (PC to MD); upload blocked by DRM
- Modern alternatives: Web MiniDisc, Platinum-MD

### SCMS (Serial Copy Management System)

Copy-generation control in S/PDIF channel status bits, mandated by the
Audio Home Recording Act (1992).

| Source status | Recording permitted? |
|--------------|---------------------|
| Not copyrighted | Yes, unlimited |
| Copyrighted original | Yes, one generation (marked as copy) |
| Copyrighted copy | No — recorder blocks |

Practical effect: CD to MD (digital) works. MD to MD (digital) is
blocked. Analog copying is unaffected — SCMS has no effect on analog
connections.

Professional decks (MDS-E12 etc.) may have SCMS disabled, as pro
equipment is exempt.

### Disc Formats

| Type | SP duration | Data capacity |
|------|-------------|--------------|
| MD-60 | 60 min | ~128 MB |
| MD-74 | 74 min | ~160 MB |
| MD-80 | 80 min | ~177 MB |

MD-80 achieves greater capacity via tighter track pitch (1.5 um vs
1.6 um). Premastered MDs (factory-pressed, pit-based like CDs) were
produced for ~3,000 commercial releases, primarily in Japan.

### Dynamic Range and Frequency Response

**Format specification:**
- Frequency response: 5–20,000 Hz +/-0.3 dB
- Dynamic range: 105 dB
- Sampling: 44.1 kHz, 16-bit (ATRAC input/output)

**Premium decks (ES series):** >106–108 dB SNR (digital input).
**Professional decks (MDS-E12):** 97 dB dynamic range (digital), 92 dB
(analog).

These figures represent analog output stage performance. The actual
audio dynamic range is constrained by ATRAC's psychoacoustic coding to
approximately 18–19 effective bits.

### Generation Loss

Every digital-to-digital copy between MDs requires a full
decode/re-encode cycle (PCM via S/PDIF — consumer hardware cannot
transfer compressed ATRAC data directly). Re-encoding degrades quality
through:

1. Quantization noise accumulation (prior noise becomes signal)
2. Block boundary misalignment between encoders
3. Floating-point rounding drift

Empirical results: ATRAC 1 degrades unacceptably after ~5 generations.
ATRAC 2+ shows "no hearable difference" at 5 generations. Type-R is
substantially better.

The only way to avoid generation loss is to re-record from the original
source each time.

### UTOC (User Table of Contents)

Fragment-based directory structure on recordable discs:

- **Sound group**: 11.6 ms mono audio (212 bytes) — smallest unit
- **Sector**: 11 sound groups (2,332 bytes)
- **Cluster**: 32 sectors = 2.03 seconds stereo — smallest writable unit
- Maximum: 255 tracks per disc

Each track is a linked list of fragments (contiguous disc regions),
enabling non-contiguous storage with seamless playback.

**Editing operations** are metadata-only (no audio rewritten):
- Divide, combine, move, delete — all instant and non-destructive

The UTOC lives in memory during use and is written to disc on eject or
power-off. Power loss during editing can corrupt the UTOC.

### Hi-MD

Introduced January 2004. Uses higher-density MO media in the same
cartridge.

| Mode | Codec | Bitrate | Time (1 GB disc) | Time (80-min MD, Hi-MD fmt) |
|------|-------|---------|------------------|---------------------------|
| PCM | Linear PCM 16/44.1 | 1,411 kbit/s | 94 min | 28 min |
| Hi-SP | ATRAC3plus | 256 kbit/s | 7h 55m | 2h 20m |
| Hi-LP | ATRAC3plus | 64 kbit/s | 34h | 10h 10m |

PCM mode is the only MiniDisc format offering truly lossless recording.

Standard 80-min blanks can be reformatted to Hi-MD (~305 MB, roughly
double). Hi-MD formatted media cannot be read by standard MD hardware.
FAT file system — discs are readable as USB Mass Storage.

All Hi-MD hardware plays standard MD recordings (backward-compatible).

### Notable Hardware

**Sony ES series (premium home decks):**

| Model | Year | ATRAC | Notes |
|-------|------|-------|-------|
| MDS-JA50ES | 1996 | 4.5 | 24-bit delta-sigma ADC |
| MDS-JA555ES | 1999 | Type-R | Finest consumer MD deck. Dual R-Core transformers, current-pulse DAC. |
| MDS-JA33ES | 2000 | Type-R | MDLP, Scale Factor Edit |

**Sony professional:**

| Model | Notes |
|-------|-------|
| MDS-E12 | 1U rackmount, Type-R, MDLP, balanced XLR, SCMS configurable |

**Sony portables:**

| Model | Year | Notes |
|-------|------|-------|
| MZ-R909 | 2000 | Compact recorder, excellent build |
| MZ-RH1 | 2006 | Final MD portable. Hi-MD. Unique ability to upload standard MD recordings to PC. Most sought-after MD portable. |

**Sharp** was the most significant non-Sony manufacturer, producing
OEM mechanisms for Kenwood, Panasonic, and others.

---

*References: IEC 60908 (Red Book), ECMA-359 (CD-R), minidisc.org
technical papers, IASA TC-04, Hydrogenaudio ATRAC article.*
