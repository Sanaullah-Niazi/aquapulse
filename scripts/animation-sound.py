"""Background music and sound effects for the animation (make animation-sound).

Everything is made here from sine waves and noise, so there is no copyright
question: soft chords that follow the scenes, and small sounds at the moments
the picture shows them (pump, dripping leak, AI ping, valve, alerts, payment).
The times match docs/animation/system.html (LOOP = 34 s).

Usage: python scripts/animation-sound.py <video.mp4> <out.mp4>
Needs numpy and ffmpeg.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 44100
LENGTH = 34.0
rng = np.random.default_rng(7)


def note(name: str) -> float:
    """Frequency of a note such as 'A3' or 'C#4'."""
    steps = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    base, octave = name[:-1], int(name[-1])
    n = steps[base[0]] + (1 if base.endswith("#") else 0) + 12 * (octave - 4)
    return 440.0 * 2 ** (n / 12)


def tone(
    freq: float, dur: float, attack: float = 0.02, decay: float = 0.3
) -> np.ndarray:
    """A soft bell-like tone that fades out."""
    t = np.arange(int(dur * RATE)) / RATE
    wave_ = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
    env = np.minimum(t / attack, 1) * np.exp(-t / decay)
    return wave_ * env


def pad(freqs: list[float], dur: float) -> np.ndarray:
    """A warm held chord with slow fade in and out."""
    t = np.arange(int(dur * RATE)) / RATE
    out = np.zeros_like(t)
    for f in freqs:
        for detune in (-0.6, 0.6):  # two slightly detuned voices sound wider
            out += np.sin(2 * np.pi * (f + detune) * t) + 0.15 * np.sin(
                6 * np.pi * (f + detune) * t
            )
    fade = np.minimum(1, np.minimum(t / 1.0, (dur - t) / 1.0))
    return out / (2 * len(freqs)) * np.clip(fade, 0, 1)


def add(track: np.ndarray, sound: np.ndarray, at: float, gain: float) -> None:
    start = int(at * RATE)
    end = min(len(track), start + len(sound))
    track[start:end] += gain * sound[: end - start]


def lowpass(x: np.ndarray, k: int) -> np.ndarray:
    return np.convolve(x, np.ones(k) / k, mode="same")


def music() -> np.ndarray:
    track = np.zeros(int(LENGTH * RATE))
    # (start, length, chord): day in C major, night in A minor, alert in D minor, bill bright again
    chords = [
        (0.0, 4.6, ["C3", "G3", "E4"]),
        (4.5, 4.6, ["F3", "C4", "A4"]),
        (9.0, 4.4, ["A2", "E3", "C4"]),
        (13.3, 4.3, ["F2", "C3", "A3"]),
        (17.5, 3.1, ["D3", "A3", "F4"]),
        (20.5, 3.1, ["A#2", "F3", "D4"]),
        (23.5, 3.4, ["F3", "C4", "A4"]),
        (26.8, 3.4, ["G3", "D4", "B4"]),
        (30.0, 4.0, ["C3", "G3", "E4", "C5"]),
    ]
    for start, dur, names in chords:
        add(track, pad([note(n) for n in names], dur + 0.4), start, 0.22)
    # a gentle arpeggio on top during the day scenes
    arp = {
        0.0: ["C5", "E5", "G5", "E5"],
        4.5: ["F5", "A5", "C6", "A5"],
        23.5: ["A4", "C5", "F5", "C5"],
        26.8: ["B4", "D5", "G5", "D5"],
        30.0: ["C5", "E5", "G5", "C6"],
    }
    for start, names in arp.items():
        for i in range(8):
            add(track, tone(note(names[i % 4]), 0.6, decay=0.25), start + i * 0.5, 0.05)
    return track


def effects() -> np.ndarray:
    track = np.zeros(int(LENGTH * RATE))
    t = np.arange(int(4.8 * RATE)) / RATE
    hum = (
        np.sin(2 * np.pi * 110 * t) + 0.5 * np.sin(2 * np.pi * 220 * t)
    ) * np.minimum(1, np.minimum(t / 0.3, (4.8 - t) / 0.3))
    add(track, hum, 1.2, 0.05)  # pump running 1.2 to 6 s
    click = lowpass(rng.standard_normal(int(0.03 * RATE)), 4) * np.exp(
        -np.arange(int(0.03 * RATE)) / 200
    )
    for at in (1.2, 6.0, 26.5):
        add(track, click, at, 0.25)  # relay and button clicks
    drip = np.concatenate([tone(1400, 0.12, attack=0.002, decay=0.03), np.zeros(10)])
    at = 9.8
    while at < 14.2:  # the leak drips until the valve closes
        add(track, drip, at, 0.08)
        at += 0.55 + 0.15 * rng.random()
    add(track, tone(note("E6"), 0.5, decay=0.15), 12.0, 0.10)  # AI ping
    add(track, tone(note("B6"), 0.5, decay=0.15), 12.12, 0.08)
    thud = np.sin(2 * np.pi * 70 * t[: int(0.35 * RATE)]) * np.exp(
        -t[: int(0.35 * RATE)] / 0.08
    )
    add(track, thud, 14.2, 0.35)  # valve closing
    for at in (14.5, 20.0, 20.4):  # alert chimes
        add(track, tone(note("A5"), 0.4, decay=0.12), at, 0.09)
        add(track, tone(note("F5"), 0.5, decay=0.15), at + 0.13, 0.09)
    for i, n in enumerate(["C6", "E6", "G6"]):  # payment received
        add(track, tone(note(n), 0.6, decay=0.2), 26.8 + 0.09 * i, 0.08)
    return track


def main() -> None:
    video, out = Path(sys.argv[1]), Path(sys.argv[2])
    mix = music() + effects()
    fade = np.minimum(1, (LENGTH - np.arange(len(mix)) / RATE) / 1.5)
    mix *= np.clip(fade, 0, 1)
    mix = mix / max(1e-9, np.abs(mix).max()) * 0.8
    stereo = np.repeat((mix * 32767).astype("<i2")[:, None], 2, axis=1)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "sound.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(stereo.tobytes())
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video),
                "-i",
                str(wav),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out),
            ],
            check=True,
        )
    print(out)


if __name__ == "__main__":
    main()
