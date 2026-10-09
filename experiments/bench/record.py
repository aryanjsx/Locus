"""Record test clips for Experiment 2 by reading catalog phrases aloud.

Push-to-talk style: press Enter to start, Enter again to stop. Clips are saved as
16 kHz mono WAV in data/audio/ with a manifest.jsonl of reference transcripts.
Recordings are your voice, so data/audio/ is git-ignored.

    uv run --group stt python -m bench.record --count 15
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from bench.catalog import load_catalog

AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "audio"
SAMPLE_RATE = 16_000


def record_until_enter(device: int | None) -> Any:
    import numpy as np
    import sounddevice as sd

    frames: list[np.ndarray] = []

    def callback(indata: np.ndarray, _frames: int, _time: object, status: object) -> None:
        if status:
            print(f"  [audio] {status}")
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        device=device, callback=callback):
        input("  Recording... press Enter to stop ")
    return np.concatenate(frames)[:, 0] if frames else np.zeros(0, dtype="float32")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=15, help="number of phrases to record")
    parser.add_argument("--speaker", default="me", help="speaker label, e.g. aryan, friend1")
    parser.add_argument("--device", type=int, help="input device index (see --list-devices)")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    import soundfile as sf

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    cases = [c for c in load_catalog() if c.text and not c.skip_llm]
    random.Random(args.seed).shuffle(cases)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    manifest = AUDIO_DIR / "manifest.jsonl"

    print(f"Recording {args.count} phrases. Speak naturally, at your normal distance.\n")
    with manifest.open("a", encoding="utf-8") as out:
        for i, case in enumerate(cases[: args.count], 1):
            print(f'[{i}/{args.count}] Say: "{case.text}"')
            input("  Press Enter to start ")
            audio = record_until_enter(args.device)
            seconds = len(audio) / SAMPLE_RATE
            if seconds < 0.3:
                print("  Too short, skipped.\n")
                continue
            name = f"{args.speaker}-{case.id}.wav"
            sf.write(AUDIO_DIR / name, audio, SAMPLE_RATE, subtype="PCM_16")
            out.write(json.dumps({"file": name, "text": case.text, "speaker": args.speaker,
                                  "seconds": round(seconds, 2)}) + "\n")
            print(f"  Saved {name} ({seconds:.1f}s)\n")
    print(f"Manifest: {manifest}")


if __name__ == "__main__":
    main()
