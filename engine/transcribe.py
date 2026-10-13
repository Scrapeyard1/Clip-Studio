"""Transcribe a 16 kHz mono WAV into word-level timestamps.

Streams the file in chunks so memory stays flat for multi-hour recordings
(a 5 GB source is fine: only the extracted 16 kHz audio is read, ~110 MB/hour).

Output JSON: {"duration": s, "segments": [{"start","end","text","words":[{"w","s","e"}]}]}
"""
import argparse, json, os, sys, time, wave
import numpy as np
import sherpa_onnx

import paths


def make_recognizer(threads):
    d = paths.PARAKEET_DIR
    j = lambda f: os.path.join(d, f)
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=j("encoder.int8.onnx"), decoder=j("decoder.int8.onnx"),
        joiner=j("joiner.int8.onnx"), tokens=j("tokens.txt"),
        num_threads=threads, model_type="nemo_transducer", decoding_method="greedy_search")


def make_vad():
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = paths.SILERO
    cfg.silero_vad.threshold = 0.4
    cfg.silero_vad.min_silence_duration = 0.35
    cfg.silero_vad.min_speech_duration = 0.2
    cfg.silero_vad.max_speech_duration = 20
    cfg.sample_rate = 16000
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)


def words_from(result, offset):
    words, cur = [], None
    for tok, ts in zip(result.tokens, result.timestamps):
        starts = tok.startswith(" ") or tok.startswith("▁") or cur is None
        piece = tok.replace("▁", " ")
        if starts and piece.strip() and not (cur and piece.strip() in ".,?!'" and not piece.startswith(" ")):
            if cur:
                words.append(cur)
            cur = {"w": piece.strip(), "s": round(offset + ts, 2)}
        elif cur is not None:
            cur["w"] += piece.strip() if not piece.startswith(" ") else " " + piece.strip()
    if cur:
        words.append(cur)
    # end time = next word's start (capped), last word ~ +0.4s
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["s"] + 0.5
        w["e"] = round(min(nxt, w["s"] + 1.2), 2)
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav"); ap.add_argument("out")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--limit", type=float, default=0, help="seconds to process (0 = all)")
    a = ap.parse_args()

    rec, vad = make_recognizer(a.threads), make_vad()
    wf = wave.open(a.wav, "rb")
    assert wf.getframerate() == 16000 and wf.getnchannels() == 1
    total = wf.getnframes() / 16000
    if a.limit:
        total = min(total, a.limit)
    segs, t0, fed, window = [], time.time(), 0, 512

    def drain():
        while not vad.empty():
            sp = vad.front
            start = sp.start / 16000
            s = rec.create_stream(); s.accept_waveform(16000, np.asarray(sp.samples, dtype=np.float32))
            rec.decode_stream(s)
            r = s.result
            if r.text.strip():
                w = words_from(r, start)
                segs.append({"start": round(start, 2), "end": round(start + len(sp.samples) / 16000, 2),
                             "text": r.text.strip(), "words": w})
            vad.pop()

    chunk = 16000 * 30
    carry = np.zeros(0, dtype=np.float32)
    while fed < total * 16000:
        raw = wf.readframes(chunk)
        if not raw:
            break
        x = np.concatenate([carry, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0])
        n = len(x) // window * window
        for i in range(0, n, window):
            vad.accept_waveform(x[i:i + window])
        carry = x[n:]          # keep the remainder so the timeline never drifts
        fed += len(raw) // 2
        drain()
        el = time.time() - t0
        print(f"{fed/16000:7.0f}s / {total:.0f}s  speed {fed/16000/el:5.1f}x", flush=True)
    vad.flush(); drain()
    tmp = a.out + ".partial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"duration": total, "segments": segs}, f)
    os.replace(tmp, a.out)
    print("done", len(segs), "segments", flush=True)


if __name__ == "__main__":
    main()
