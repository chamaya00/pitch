"""Deterministic audio fixtures, built in code rather than committed as binaries.

Everything here produces bytes a real parser accepts, so the metadata tests
exercise mutagen itself rather than a mock of it. Nothing is downloaded.
"""

import array
import math
import random
import struct
import wave
from pathlib import Path
from typing import Final

# --- WAV -------------------------------------------------------------------


def write_wav(
    path: Path,
    *,
    seconds: float = 1.5,
    sample_rate: int = 8000,
    channels: int = 1,
    sample_width: int = 2,
    frequency: float = 440.0,
) -> Path:
    """Write a PCM WAV sine tone using only the standard library.

    Low sample rates keep fixtures small: 8 kHz mono 16-bit is ~16 KB/second.
    """
    frame_count = int(sample_rate * seconds)
    peak = 2 ** (sample_width * 8 - 2)
    samples = array.array("h" if sample_width == 2 else "b")
    for index in range(frame_count):
        value = int(peak * math.sin(2 * math.pi * frequency * index / sample_rate))
        samples.extend([value] * channels)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return path


#: Bytes of RIFF/fmt/data headers written by :mod:`wave` for PCM audio.
WAV_HEADER_BYTES: Final = 44


def write_wav_of_exact_size(path: Path, total_bytes: int, *, sample_rate: int = 8000) -> Path:
    """Write a genuinely valid mono 16-bit WAV occupying exactly ``total_bytes``.

    Needed for size-limit boundary tests, where "one byte over" has to be a real
    audio file rather than padding that would fail content validation first.
    ``total_bytes`` must be even and leave room for the header.
    """
    payload = total_bytes - WAV_HEADER_BYTES
    if payload < 0 or payload % 2:
        raise ValueError("total_bytes must be even and at least the header size")

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * (payload // 2))

    actual = path.stat().st_size
    if actual != total_bytes:  # pragma: no cover - guards the header assumption
        raise AssertionError(f"expected {total_bytes} bytes, wrote {actual}")
    return path


# --- Signals for audio analysis --------------------------------------------
#
# A pure sine is the wrong fixture for testing a pitch detector: it is the one
# signal on which every method works. The interesting cases are a harmonic tone
# (which breaks naive autocorrelation), noise (which must produce nothing), and
# silence (likewise) — so those are built here rather than approximated.


def write_signal_wav(path: Path, samples: list[float], *, sample_rate: int) -> Path:
    """Write mono 16-bit PCM from floats in [-1, 1]. Values outside are clamped."""
    frames = array.array("h")
    for value in samples:
        scaled = int(max(-1.0, min(1.0, value)) * 32767)
        frames.append(scaled)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames.tobytes())
    return path


def harmonic_samples(
    frequency: float,
    *,
    seconds: float,
    sample_rate: int,
    amplitude: float = 0.5,
    partials: tuple[float, ...] = (1.0, 0.6, 0.4, 0.25, 0.15),
) -> list[float]:
    """A tone with harmonics — roughly what a voice looks like to a detector.

    This is the signal that defeats plain autocorrelation: the harmonics make
    the correlation at twice the true period as strong as at the period itself,
    so a detector taking the tallest peak reports an octave too low.
    """
    total = sum(partials)
    samples = []
    for index in range(int(sample_rate * seconds)):
        phase = 2 * math.pi * frequency * index / sample_rate
        value = sum(gain * math.sin((n + 1) * phase) for n, gain in enumerate(partials))
        samples.append(amplitude * value / total)
    return samples


def sine_samples(
    frequency: float, *, seconds: float, sample_rate: int, amplitude: float = 0.5
) -> list[float]:
    """A pure tone of a known frequency."""
    return [
        amplitude * math.sin(2 * math.pi * frequency * index / sample_rate)
        for index in range(int(sample_rate * seconds))
    ]


def noise_samples(
    *, seconds: float, sample_rate: int, amplitude: float = 0.4, seed: int = 1234
) -> list[float]:
    """Deterministic white noise: plenty of level, no pitch to find."""
    generator = random.Random(seed)
    return [generator.uniform(-amplitude, amplitude) for _ in range(int(sample_rate * seconds))]


def silence_samples(*, seconds: float, sample_rate: int) -> list[float]:
    """Digital silence."""
    return [0.0] * int(sample_rate * seconds)


# --- MP3 -------------------------------------------------------------------
#
# A minimal but genuinely valid MPEG-1 Layer III stream. Each 32-bit frame
# header is:
#
#   syncword          11 bits  all set                      0xFF 0xE0
#   MPEG version       2 bits  11 = MPEG-1
#   layer              2 bits  01 = Layer III
#   protection         1 bit   1  = no CRC
#   bitrate index      4 bits  1001 = 128 kbps
#   sampling rate      2 bits  00 = 44100 Hz
#   padding            1 bit   0
#   private            1 bit   0
#   channel mode       2 bits  11 = mono, 00 = stereo
#   mode extension     2 bits  0
#   copyright          1 bit   0
#   original           1 bit   0
#   emphasis           2 bits  0
#
# giving 0xFF 0xFB 0x90 0xC0 for mono. Frame payloads are zero-filled: mutagen
# reads headers and never decodes, so silent frames parse exactly like audio.

MP3_SAMPLE_RATE: Final = 44100
MP3_BITRATE: Final = 128_000
MP3_SAMPLES_PER_FRAME: Final = 1152
MP3_FRAME_BYTES: Final = 144 * MP3_BITRATE // MP3_SAMPLE_RATE  # 417
#: Duration one frame contributes, ~0.026 s.
MP3_FRAME_SECONDS: Final = MP3_SAMPLES_PER_FRAME / MP3_SAMPLE_RATE

_MP3_MONO_HEADER: Final = bytes([0xFF, 0xFB, 0x90, 0xC0])
_MP3_STEREO_HEADER: Final = bytes([0xFF, 0xFB, 0x90, 0x00])


def mp3_bytes(*, frames: int = 46, channels: int = 1) -> bytes:
    """Build a valid constant-bitrate MP3 stream of ``frames`` frames."""
    header = _MP3_MONO_HEADER if channels == 1 else _MP3_STEREO_HEADER
    return (header + b"\x00" * (MP3_FRAME_BYTES - len(header))) * frames


def write_mp3(path: Path, *, frames: int = 46, channels: int = 1) -> Path:
    path.write_bytes(mp3_bytes(frames=frames, channels=channels))
    return path


def mp3_expected_duration(frames: int) -> float:
    """Nominal duration of a stream with ``frames`` frames.

    mutagen derives length from file size and bitrate, so its answer lands
    within a fraction of a percent of this rather than exactly on it.
    """
    return frames * MP3_FRAME_SECONDS


# --- Hostile content -------------------------------------------------------


def deterministic_bytes(size: int, *, seed: int = 1234) -> bytes:
    """Arbitrary but reproducible bytes — a fixed seed keeps failures debuggable."""
    return random.Random(seed).randbytes(size)


#: ELF header: an executable that has merely been renamed.
ELF_BYTES: Final = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + struct.pack("<HH", 2, 62)

#: DOS/PE header, the Windows equivalent.
PE_BYTES: Final = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 56 + b"PE\x00\x00"

SHELL_SCRIPT_BYTES: Final = b"#!/bin/sh\necho compromised\n" * 40

#: Just enough of each container to be recognised, for the rejection-message
#: path. These are not playable files and are not meant to be.
FLAC_SIGNATURE: Final = b"fLaC\x00\x00\x00\x22" + b"\x00" * 200
OGG_SIGNATURE: Final = b"OggS\x00\x02" + b"\x00" * 200
M4A_SIGNATURE: Final = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 200
AIFF_SIGNATURE: Final = b"FORM\x00\x00\x10\x00AIFF" + b"\x00" * 200
