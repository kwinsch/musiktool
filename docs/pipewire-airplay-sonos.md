# PipeWire AirPlay Output (Sonos, HomePod, etc.)

musiktool's playback outputs to the system's default PipeWire sink. This guide
documents how to add AirPlay (RAOP) speakers as output targets on Linux, so you
can switch between a local DAC and network speakers from your desktop's audio
widget.

## Prerequisites

- PipeWire (1.0+) as your audio server
- `pipewire-zeroconf` package (provides `libpipewire-module-raop-discover`)
- Avahi running for mDNS discovery (`avahi-daemon`)
- AirPlay-capable speaker on the same network (Sonos, HomePod, etc.)

### Arch Linux

```bash
pacman -S pipewire-zeroconf
```

### Debian / Ubuntu

```bash
apt install pipewire-module-raop
```

## Configuration

### 1. Enable RAOP discovery

Create `~/.config/pipewire/pipewire.conf.d/50-raop.conf`:

```conf
context.modules = [
    { name = libpipewire-module-raop-discover
        args = {
            stream.rules = [
                {
                    matches = [ { raop.ip = "~.*" } ]
                    actions = {
                        create-stream = {
                            node.virtual = false
                            object.register = true
                        }
                    }
                }
            ]
        }
        condition = [ { module.raop = !false } ]
    }
]
```

The `stream.rules` block is important: without `node.virtual = false` and
`object.register = true`, desktop audio widgets (KDE Plasma, GNOME) won't
show the AirPlay sink in their output device list. The sink will still work
via `wpctl`, but won't be visible in the GUI.

### 2. Restart PipeWire

```bash
systemctl --user restart pipewire
```

### 3. Verify

```bash
# Should show your speaker as a sink
wpctl status | grep -A5 "Sinks:"

# Detailed properties
wpctl inspect <sink-id>
```

## Usage

Switch output via your desktop audio widget, or:

```bash
# Set AirPlay speaker as default output
wpctl set-default <sink-id>

# Switch back to local output
wpctl set-default <local-sink-id>
```

musiktool requires no configuration — it plays to whatever PipeWire default
sink is active:

```bash
musiktool play ~/Music/Artist/Album
# → goes to Sonos, HomePod, or local DAC depending on current default
```

Switching output mid-playback works — PipeWire reroutes the stream live.

## Stereo pairs

For Sonos stereo pairs (or any grouped speakers), only the coordinator speaker
advertises itself via AirPlay. The secondary speaker receives audio internally
from the coordinator. You only see one sink in PipeWire regardless of how many
speakers are in the group.

To identify which speaker is the coordinator:

```bash
# Check which IP has AirPlay port open
nc -z -w 2 <speaker-ip-1> 7000 && echo "coordinator"
nc -z -w 2 <speaker-ip-2> 7000 && echo "coordinator"
```

## Limitations

- **44.1 kHz / 16-bit only** — AirPlay 1 (RAOP) is limited to CD quality.
  PipeWire automatically resamples higher sample rates. For bit-perfect hi-res
  playback, use a USB DAC.
- **~250ms latency** — network audio adds buffering. Fine for music listening,
  not suitable for video sync or real-time monitoring.
- **No volume sync** — PipeWire volume and speaker volume are independent.
  Set PipeWire to 100% and control volume on the speaker/app side for best
  dynamic range.

## Troubleshooting

**Sink doesn't appear:**

1. Check Avahi sees the speaker: `avahi-browse -t _raop._tcp`
2. Check PipeWire loaded the module: `journalctl --user -u pipewire | grep raop`
3. Verify `pipewire-zeroconf` is installed: `pacman -Qi pipewire-zeroconf`

**Sink appears but no audio:**

1. Firewall — the speaker needs to reach back to your machine. Allow incoming
   UDP from the speaker's IP.
2. Check the speaker isn't in a mode that disables AirPlay (e.g. Sonos TV mode).

**Sink disappears intermittently:**

mDNS discovery is continuous. If the speaker goes to sleep or the network is
flaky, the sink will vanish and reappear. This is normal — PipeWire will
automatically re-create the sink when the speaker is back.
