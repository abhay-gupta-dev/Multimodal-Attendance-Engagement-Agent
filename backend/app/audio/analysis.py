"""Local, anonymous audio activity features. Raw samples are never persisted."""
import io
import math
import wave


def analyze_wav(content: bytes) -> dict[str, float]:
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            if width not in (1, 2, 3, 4) or rate < 1000 or frames == 0:
                raise ValueError("Unsupported or empty PCM WAV audio")
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError("Upload a valid PCM WAV file") from exc
    step = width * channels
    window_frames = max(1, rate // 2)
    active = total = 0
    peak = 0.0
    for start in range(0, frames, window_frames):
        block = raw[start * step:min(frames, start + window_frames) * step]
        values = []
        for offset in range(0, len(block) - width + 1, width):
            sample = block[offset:offset + width]
            if width == 1:
                value = (sample[0] - 128) / 128
            else:
                value = int.from_bytes(sample, "little", signed=True) / (2 ** (width * 8 - 1))
            values.append(value)
        rms = math.sqrt(sum(v * v for v in values) / len(values)) if values else 0
        peak = max(peak, rms)
        total += 1
        active += rms >= 0.02
    return {"duration_seconds": round(frames / rate, 2), "activity_percentage": round(active / total * 100, 1), "peak_rms": round(peak, 4)}
