"""6단계 실험: '귀' 바꾸기 — 윈도우 실시간 자막(윈11 전용) 대신 PC 소리를 직접 받아 Whisper 로 받아쓰기.

  PC 스피커로 나가는 소리 → [WASAPI 루프백, pyaudiowpatch] → 16kHz 모노 → [faster-whisper, GPU] → 영어 글자

실행:
  .venv\\Scripts\\python.exe step6_whisper_ear.py --selftest           정답 아는 영어를 Edge 목소리로 틀면서 받아써서 정확도 재기
  .venv\\Scripts\\python.exe step6_whisper_ear.py --seconds 30         지금 PC 에서 나는 소리(영상 등) 30초 받아쓰기
  --model small | large-v3-turbo (기본 large-v3-turbo)

이 PC(윈10, 11/08)에는 CUDA 툴킷이 없어서 cuBLAS·cuDNN 은 pip(nvidia-*-cu12)로 .venv 안에만 넣고 DLL 경로를 여기서 붙인다.
"""
import argparse
import asyncio
import ctypes
import glob
import os
import re
import sys
import threading
import time
import wave

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

# pip 로 받은 CUDA DLL (cublas, cudnn) 을 찾을 수 있게
for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
    os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

import numpy as np  # noqa: E402
import pyaudiowpatch as pyaudio  # noqa: E402

REFERENCE = (
    "Welcome to this introduction to customer service AI. "
    "Agents often waste time navigating disconnected tools. "
    "With AI agents, common questions are resolved automatically, so your team can focus on complex issues. "
    "Let's look at three builders: the knowledge builder, the action builder, and the app builder. "
    "By the end of this session, you will know how to measure customer satisfaction and reduce handle time."
)


# ---------- 귀: PC 소리 받기 ----------
def loopback_device(p):
    """기본 스피커의 루프백 장치 (스피커로 나가는 소리를 그대로 받는 입력)."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not spk.get("isLoopbackDevice"):
        for lb in p.get_loopback_device_info_generator():
            if spk["name"] in lb["name"]:
                return spk, lb
    return spk, spk


def record(seconds, stop=None):
    """루프백으로 seconds 초 받아서 (16kHz 모노 float32, 원래 샘플레이트, 장치 이름)."""
    p = pyaudio.PyAudio()
    try:
        spk, dev = loopback_device(p)
        ch = dev["maxInputChannels"]
        sr = int(dev["defaultSampleRate"])
        frames = []
        stream = p.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                        input_device_index=dev["index"], frames_per_buffer=1024)
        t_end = time.time() + seconds
        while time.time() < t_end and not (stop and stop.is_set()):
            frames.append(stream.read(1024, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
    finally:
        p.terminate()
    a = np.frombuffer(b"".join(frames), dtype=np.float32).reshape(-1, ch).mean(axis=1)
    # 16kHz 로 (선형 보간 — 실험용으로 충분)
    n16 = int(len(a) * 16000 / sr)
    a16 = np.interp(np.linspace(0, len(a) - 1, n16), np.arange(len(a)), a).astype(np.float32)
    return a16, sr, dev["name"]


def save_wav(path, a16):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(a16, -1, 1) * 32767).astype(np.int16).tobytes())


# ---------- 시험용 영어 소리 ----------
def play_reference(path_holder, done):
    import edge_tts
    path = os.path.join(HERE, ".tts", "step6_ref.mp3")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    asyncio.run(edge_tts.Communicate(REFERENCE, "en-US-AndrewNeural").save(path))
    path_holder.append(path)
    mci = ctypes.windll.winmm.mciSendStringW
    mci(f'open "{path}" type mpegvideo alias ref', None, 0, None)
    mci("play ref wait", None, 0, None)
    mci("close ref", None, 0, None)
    done.set()


# ---------- 받아쓰기 ----------
def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def wer(ref, hyp):
    """단어 오류율 (편집 거리 / 정답 단어 수)."""
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--model", default="large-v3-turbo")
    args = ap.parse_args()

    from faster_whisper import WhisperModel
    t = time.time()
    model = WhisperModel(args.model, device="cuda", compute_type="float16")
    print(f"[모델] {args.model} 올림 {time.time() - t:.1f}초")

    if args.selftest:
        done, holder = threading.Event(), []
        th = threading.Thread(target=play_reference, args=(holder, done), daemon=True)
        th.start()
        time.sleep(1.5)  # 합성 받는 동안 (받기 시작은 조금 일찍)
        a16, sr, name = record(60, stop=done)
    else:
        print(f"[귀] {args.seconds:.0f}초 동안 PC 소리를 받는다…")
        a16, sr, name = record(args.seconds)
    level = float(np.sqrt(np.mean(a16 ** 2))) if len(a16) else 0.0
    print(f"[귀] 장치: {name} · {sr}Hz → 16kHz · {len(a16) / 16000:.1f}초 · 소리 크기(RMS) {level:.4f}")
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    wav = os.path.join(HERE, "logs", "step6_last.wav")
    save_wav(wav, a16)
    if level < 1e-4:
        print("[귀] ⚠ 거의 무음 — 스피커 음소거/다른 장치로 나가는지 확인")

    t = time.time()
    segs, info = model.transcribe(a16, language="en", beam_size=5, vad_filter=True)
    segs = list(segs)
    secs = time.time() - t
    text = " ".join(s.text.strip() for s in segs)
    print(f"[받아쓰기] {secs:.2f}초 (소리 {len(a16) / 16000:.1f}초 → 실시간의 {len(a16) / 16000 / max(secs, 1e-6):.0f}배 빠름)")
    for s in segs:
        print(f"  [{s.start:5.1f}–{s.end:5.1f}] {s.text.strip()}")
    if args.selftest:
        print(f"[정확도] 단어 오류율 {wer(REFERENCE, text) * 100:.1f}%  (정답 {len(words(REFERENCE))}단어)")
        for p in holder:
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()
