import io
import struct
import numpy as np
import soundfile as sf


def float32_to_pcm16(audio: np.ndarray, sample_rate: int = 48000) -> bytes:
    if audio is None or len(audio) == 0:
        return b""
    clipped = np.clip(audio, -1.0, 1.0)
    int16_arr = (clipped * 32767.0).astype(np.int16)
    return int16_arr.tobytes()


def pcm16_to_float32(pcm_data: bytes) -> np.ndarray:
    if not pcm_data:
        return np.zeros(0, dtype=np.float32)
    int16_arr = np.frombuffer(pcm_data, dtype=np.int16)
    return (int16_arr.astype(np.float32) / 32767.0).astype(np.float32)


def wav_from_float32(audio: np.ndarray, sample_rate: int = 48000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def create_wav_header(data_size: int, sample_rate: int = 48000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    total_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        total_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header
