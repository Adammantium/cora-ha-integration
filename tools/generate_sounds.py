"""Generate the Cora alarm and timer sounds as WAV files into the given directory."""
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 44100
OUT = Path(sys.argv[1])


def t_of(length):
    return np.arange(int(RATE * length)) / RATE


def silence(length):
    return np.zeros(int(RATE * length))


def fade(x, ms=5):
    n = min(int(RATE * ms / 1000), len(x) // 2)
    x = x.copy()
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    return x


def tone(freq, length, harmonics=((1, 1.0),)):
    t = t_of(length)
    return fade(sum(a * np.sin(2 * np.pi * freq * k * t) for k, a in harmonics))


def bell(freq, length, decay=2.5):
    """Bell or chime: inharmonic partials with exponential decay."""
    t = t_of(length)
    partials = ((1, 1.0), (2.0, 0.5), (3.01, 0.25), (4.2, 0.12))
    x = sum(a * np.sin(2 * np.pi * freq * k * t) * np.exp(-decay * k * t) for k, a in partials)
    return fade(x, 3)


def mallet(freq, length, decay=6.0):
    """Marimba like note: fundamental plus the typical 4x partial, fast decay."""
    t = t_of(length)
    x = np.sin(2 * np.pi * freq * t) * np.exp(-decay * t)
    x += 0.35 * np.sin(2 * np.pi * freq * 3.93 * t) * np.exp(-decay * 3 * t)
    return fade(x, 2)


def chirp(f0, f1, length):
    t = t_of(length)
    freq = np.linspace(f0, f1, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    env = np.sin(np.pi * t / length) ** 2
    return np.sin(phase) * env


def mix(*parts):
    return np.concatenate(parts)


def overlay(length, events):
    """Place (start, sound) events on a timeline."""
    out = silence(length)
    for start, sound in events:
        i = int(start * RATE)
        n = min(len(sound), len(out) - i)
        out[i:i + n] += sound[:n]
    return out


def save(name, audio, peak=0.8):
    audio = audio / np.max(np.abs(audio)) * peak
    with wave.open(str(OUT / f"{name}.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((audio * 32767).astype(np.int16).tobytes())


# Pulse: the toned down alarm beep, softer tone, slower rhythm
soft = ((1, 1.0), (2, 0.25), (3, 0.1))
group = mix(*[mix(tone(1046, 0.12, soft), silence(0.1)) for _ in range(3)], silence(0.45))
save("pulse", mix(*[group] * 4))

# Sunrise: rising major arpeggio on chimes
notes = [523.25, 659.25, 783.99, 1046.5]
events = [(0.18 * i, bell(f, 1.6)) for i, f in enumerate(notes)]
events += [(2.0 + 0.18 * i, bell(f, 1.6)) for i, f in enumerate(notes)]
save("sunrise", overlay(4.0, events))

# Lighthouse: slow two tone bell, like a buoy far away
events = [(0.0, bell(440, 2.0, 1.2)), (1.0, bell(659.25, 2.0, 1.2)),
          (2.0, bell(440, 2.0, 1.2)), (3.0, bell(659.25, 2.0, 1.2))]
save("lighthouse", overlay(5.0, events), peak=0.75)

# Marimba Morning: short pentatonic phrase on a marimba
phrase = [(0.0, 523.25), (0.2, 659.25), (0.4, 783.99), (0.6, 880.0), (0.8, 783.99),
          (1.2, 659.25), (1.4, 783.99), (1.6, 1046.5)]
events = [(start, mallet(f, 0.9)) for start, f in phrase]
events += [(2.2 + start, mallet(f, 0.9)) for start, f in phrase]
save("marimba_morning", overlay(4.4, events))

# Songbird: bursts of quick chirps
events = []
for burst, base in enumerate((0.0, 1.3, 2.6)):
    for i in range(4):
        up = i % 2 == 0
        events.append((base + i * 0.13, chirp(2600 if up else 4200, 4200 if up else 2900, 0.1)))
    events.append((base + 0.62, chirp(3000, 4800, 0.18)))
save("songbird", overlay(3.9, events), peak=0.7)

# Red Alert: the urgent beeper, a bit gentler than the first version
harsh = ((1, 1.0), (3, 0.33))
group = mix(*[mix(tone(2400 if i % 2 == 0 else 2000, 0.09, harsh), silence(0.05)) for i in range(4)], silence(0.3))
save("red_alert", mix(*[group] * 5), peak=0.75)
