"""귀: PC 소리를 직접 받아 Whisper 로 영어 문장을 받아쓴다 (윈도우 10 에서도 됨). 11/08.

윈도우 11 의 실시간 자막(LiveCaptions)은 윈10 에 없다. 그래서 스피커로 나가는 소리를 WASAPI 루프백으로 받아
조용한 틈(문장 사이 숨)에서 끊고, 덩어리마다 faster-whisper 로 받아쓴다.

  스피커로 나가는 소리 → [루프백] → 조용한 틈에서 자르기 → [Whisper, 영어로만] → 끝난 영어 문장

되먹임(우리 한국어 목소리도 루프백에 들어옴): Whisper 를 영어로 고정하면 한국어만 들릴 땐 한글을 그대로 받아쓰고,
영어 강의와 겹치면 영어만 받아쓴다(11/08 실험). → 한글이 섞인 조각만 버리면 된다. 말하는 동안 소리를 버릴 필요 없음.

GPU 부품(cuBLAS·cuDNN, 약 2GB)은 exe 에 넣지 않는다. 코드로 돌릴 땐 .venv 의 nvidia-* 패키지,
exe 일 땐 %LOCALAPPDATA%\\VideoTranslator\\cuda 에 '준비 점검'이 받아 둔 것. 없으면 CPU 로 (작은 모델, 느림).
"""
import glob
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
import zipfile

from paths import DATA

CUDA_DIR = os.path.join(DATA, "cuda")
NEED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")
# 준비 점검에서 받을 때 쓰는 PyPI 패키지 (.venv 에 깔린 것과 같은 판)
CUDA_WHEELS = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4"))

GPU_MODEL = "large-v3-turbo"   # 1.6GB, 그래픽 메모리 약 1.4GB
CPU_MODEL = "small"            # GPU 를 못 쓸 때
PROMPT = "Zendesk, Copilot, AI agent, ticket, macro, trigger, escalation, knowledge base."  # 고유명사 철자 힌트

_HANGUL = re.compile(r"[가-힣]")
_SPLIT = re.compile(r"(?<=[.?!])\s+")
_HALLUC = {"thank you.", "thank you", "thanks for watching!", "thanks for watching.", "thank you for watching.",
           "you", "bye.", "bye!", "so.", "okay."}


# ---------- GPU 부품 ----------
def _cuda_dirs():
    dirs = glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin"))  # 코드로 돌릴 때 (.venv)
    if os.path.isdir(CUDA_DIR):
        dirs.append(CUDA_DIR)
    return dirs


def cuda_ready():
    have = set()
    for d in _cuda_dirs():
        have.update(n.lower() for n in os.listdir(d))
    return all(n.lower() in have for n in NEED_DLLS)


_dll_added = False


def _add_dll_dirs():
    global _dll_added
    if _dll_added:
        return
    for d in _cuda_dirs():
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    _dll_added = True


def download_cuda(progress=None):
    """PyPI 에서 cuBLAS·cuDNN 휠(zip)을 받아 DLL 만 CUDA_DIR 에 푼다. progress(퍼센트, 글)."""
    os.makedirs(CUDA_DIR, exist_ok=True)
    tmp_dir = os.path.join(DATA, ".download")
    os.makedirs(tmp_dir, exist_ok=True)
    for i, (pkg, ver) in enumerate(CUDA_WHEELS):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{ver}/json", timeout=30) as r:
            files = json.load(r)["urls"]
        url = next(f["url"] for f in files if f["filename"].endswith("win_amd64.whl"))
        tmp = os.path.join(tmp_dir, os.path.basename(url))
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(int((i + got / total) * 100 / len(CUDA_WHEELS)), f"{pkg} {got >> 20}/{total >> 20}MB")
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                if name.lower().endswith(".dll") and "/bin/" in name:
                    with z.open(name) as src, open(os.path.join(CUDA_DIR, os.path.basename(name)), "wb") as dst:
                        while True:
                            b = src.read(1 << 20)
                            if not b:
                                break
                            dst.write(b)
        os.remove(tmp)
    if not cuda_ready():
        raise RuntimeError("받았는데 필요한 DLL 이 안 보여요")


def model_cached(name=GPU_MODEL):
    """Whisper 모델을 이미 받아 뒀는지 (허깅페이스 캐시)."""
    hub = os.path.join(os.environ.get("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface")), "hub")
    return bool(glob.glob(os.path.join(hub, f"models--*--faster-whisper-{name}", "snapshots", "*", "model.bin")))


# ---------- 귀 ----------
class WhisperEar:
    SIL_CUT = 0.5      # 말소리 뒤 이만큼 조용하면 덩어리를 끝낸다
    FIRST_TRY = 2.5    # 말하는 중에도 이만큼 쌓이면 받아써 본다
    TRY = 1.0          # 그 뒤로는 이만큼 더 쌓일 때마다 (받아쓰기 한 번 0.2초)
    HARD_MAX = 20.0    # 이보다 길면 덜 끝났어도 다 내보낸다
    PEND_MAX = 3.0     # 문장 끝 부호 없이 이만큼 멈춘 글자는 그냥 내보낸다
    PRE_ROLL = 0.3     # 말 시작 직전 소리도 조금 남긴다
    MIN_VOICE = 0.4    # 이보다 짧은 소리는 받아쓰지 않는다 (딸깍 소리 등)

    def __init__(self, status=None):
        from collections import deque
        self.trace = deque(maxlen=200)  # (시각, 종류, 글) — 무엇을 어떻게 받아썼는지 (점검용)
        self.model = None
        self.device = ""
        self.model_name = ""
        self.status = status or (lambda s: None)
        self.last_sec = 0.0   # 마지막 받아쓰기에 걸린 초
        self.dropped_ko = 0   # 우리 한국어 목소리라서 버린 조각 수

    # --- 모델 ---
    def load(self):
        if self.model is not None:
            return
        from faster_whisper import WhisperModel
        import numpy as np
        if cuda_ready():
            _add_dll_dirs()
            try:
                if not model_cached(GPU_MODEL):
                    self.status("Whisper 모델 받는 중 (처음 한 번, 약 1.6GB)")
                m = WhisperModel(GPU_MODEL, device="cuda", compute_type="float16")
                list(m.transcribe(np.zeros(16000, np.float32), language="en")[0])  # cuDNN 은 처음 돌릴 때 불러서 여기서 확인
                self.model, self.device, self.model_name = m, "GPU", GPU_MODEL
                return
            except Exception as e:
                self.status(f"GPU 로 못 돌려서 CPU 로 ({type(e).__name__})")
        if not model_cached(CPU_MODEL):
            self.status("Whisper 모델 받는 중 (처음 한 번, 약 0.5GB)")
        self.model = WhisperModel(CPU_MODEL, device="cpu", compute_type="int8")
        self.device, self.model_name = "CPU", CPU_MODEL

    # --- 소리 받기 (따로 도는 스레드) ---
    def _capture(self, q, stop):
        import numpy as np
        import pyaudiowpatch as pyaudio
        while not stop.is_set():
            p = pyaudio.PyAudio()
            try:
                wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
                dev = spk
                if not spk.get("isLoopbackDevice"):
                    dev = next((lb for lb in p.get_loopback_device_info_generator() if spk["name"] in lb["name"]), None)
                if dev is None:
                    raise RuntimeError("스피커 루프백 장치를 못 찾음")
                ch, sr = dev["maxInputChannels"], int(dev["defaultSampleRate"])
                q.put(("dev", (dev["name"], sr)))
                stream = p.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                                input_device_index=dev["index"], frames_per_buffer=1024)
                try:
                    while not stop.is_set():
                        if stream.get_read_available() < 1024:  # 아무 소리도 안 나면 루프백은 데이터를 안 준다
                            time.sleep(0.02)
                            continue
                        raw = stream.read(1024, exception_on_overflow=False)
                        q.put(("pcm", np.frombuffer(raw, np.float32).reshape(-1, ch).mean(axis=1)))
                finally:
                    stream.close()
            except Exception as e:  # 헤드폰 뺌 등 → 잠깐 뒤 기본 스피커로 다시
                q.put(("err", f"{type(e).__name__}: {e}"))
                time.sleep(1.5)
            finally:
                p.terminate()

    # --- 듣기 ---
    def _transcribe(self, a16):
        t = time.time()
        segs = list(self.model.transcribe(a16, language="en", beam_size=5, vad_filter=True, word_timestamps=True,
                                          condition_on_previous_text=False, initial_prompt=PROMPT)[0])
        self.last_sec = time.time() - t
        return segs

    def _clean(self, s):
        """받아쓴 조각 하나를 거른다. 버리면 ''."""
        txt = s.text.strip()
        if not txt:
            return ""
        if not re.search(r"[A-Za-z]", txt):         # 영어가 한 글자도 없음 (우리 한국어 목소리만)
            return ""
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            return ""
        if txt.lower() in _HALLUC and s.end - s.start < 2.0:
            return ""
        return txt

    def listen(self, stop, on_sentence, on_text=None):
        """stop(Event) 이 켜질 때까지 듣는다. 끝난 영어 문장마다 on_sentence(문장, 처음 들린 시각).
        on_text(글) 은 화면에 '지금 들리는 것'을 보여 주는 용 (덜 끝난 문장도).

        11/08 첫 시험: 조용한 틈만 기다리면 숨 안 쉬고 이어 말할 때 10초씩 늦었다.
        → 말하는 동안에도 TRY 초마다 쌓인 소리를 받아쓰고(0.2초면 됨), 끝난 조각은 내보내고 마지막 조각만 남긴다.
        조각이 문장 중간에서 끊기면('By the end of this session,') 문장 끝 부호가 올 때까지 글자를 모아 둔다."""
        import numpy as np
        q = queue.Queue()
        threading.Thread(target=self._capture, args=(q, stop), daemon=True).start()
        sr = 48000
        buf, buf_len = [], 0     # 이번 덩어리 소리 (스피커 샘플레이트)
        voiced = 0.0             # 이번 덩어리에서 말소리로 본 길이(초)
        start_wall = None        # 덩어리 맨 앞이 들린 시각
        last_voice = 0.0
        last_data = time.time()
        levels = []
        next_try = self.FIRST_TRY
        pend, pend_born, pend_time = "", None, 0.0   # 아직 문장 끝이 안 온 글자
        self.status(f"듣는 중 · Whisper({self.device})")

        def push_text(txt, born, final):
            """조각 글자를 모아 끝난 문장만 내보낸다."""
            nonlocal pend, pend_born, pend_time
            if txt:
                pend = (pend + " " + txt).strip()
                if pend_born is None:
                    pend_born = born
                pend_time = time.time()
            if not pend:
                return
            parts = _SPLIT.split(pend)
            done, rest = parts[:-1], parts[-1]
            if rest.endswith((".", "?", "!")) or (final and time.time() - pend_time > self.PEND_MAX):
                done, rest = parts, ""
            for sent in done:
                if sent.strip():
                    self.trace.append((time.time(), "문장", sent.strip()))
                    on_sentence(sent.strip(), pend_born or born)
                    pend_born = born
            pend = rest
            if not pend:
                pend_born = None
            if on_text and pend:
                on_text(pend)

        def run(final):
            nonlocal buf, buf_len, voiced, start_wall, next_try
            audio = np.concatenate(buf) if buf else np.zeros(0, np.float32)
            n16 = int(len(audio) * 16000 / sr)
            if n16 < 1600:
                buf, buf_len, voiced, start_wall = [], 0, 0.0, None
                return
            a16 = np.interp(np.linspace(0, len(audio) - 1, n16), np.arange(len(audio)), audio).astype(np.float32)
            segs = self._transcribe(a16)
            dur = n16 / 16000
            self.trace.append((time.time(), "끝" if final else "중간",
                               f"{dur:.1f}초 → " + " | ".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text.strip()}" for s in segs)))
            base = start_wall or time.time()
            # 거른 조각의 단어들 (단어마다 시각). Whisper 는 두 문장을 한 조각으로 묶을 때가 많아서
            # 조각이 아니라 '마침표 찍힌 단어' 뒤에서 자른다 → 다음 문장 첫 단어가 들리자마자 앞 문장을 내보냄.
            # 되먹임: 우리 한국어 목소리가 겹친 조각엔 한글 단어가 섞여 나온다 → 조각째 버리면 같이 있던 영어도 사라져서(11/08)
            # 한글 단어만 뺀다
            words = []
            for s in segs:
                if not self._clean(s):
                    continue
                txt = s.text
                if len(_HANGUL.findall(txt)) > len(re.findall(r"[A-Za-z]", txt)):  # 한국어가 대부분인 조각 → 통째로 (남은 'AI' 같은 부스러기도)
                    self.dropped_ko += len(s.words or [])
                    continue
                for w in (s.words or []):
                    if _HANGUL.search(w.word):
                        self.dropped_ko += 1
                    else:
                        words.append((w.start, w.word))
            cut_at = None
            if final or dur >= self.HARD_MAX:
                cut_at = len(words)
            else:
                for i in range(len(words) - 2, -1, -1):   # 마지막 단어는 아직 바뀔 수 있어서 빼고
                    if words[i][1].strip().endswith((".", "?", "!")):
                        cut_at = i + 1
                        break
            if cut_at is None:
                next_try = dur + self.TRY        # 끝난 문장이 아직 없음 → 조금 더 듣고 다시
                if on_text and words:
                    on_text((pend + " " + "".join(w for _, w in words)).strip())
                return
            sent, born = "", None
            for st, w in words[:cut_at]:
                if born is None:
                    born = base + st
                sent += w
                if w.strip().endswith((".", "?", "!")):
                    push_text(sent.strip(), born, final=False)
                    sent, born = "", None
            if sent.strip():
                push_text(sent.strip(), born, final=False)   # 끝 부호 없이 끝난 덩어리 → 모아 둠
            keep_from = None
            if cut_at < len(words):
                keep_from = max(0.0, words[cut_at][0] - 0.1)  # 아직 안 끝난 문장은 다음으로
            if keep_from is not None:
                rest = audio[int(keep_from * sr):]
                buf, buf_len = [rest], len(rest)
                voiced = len(rest) / sr
                start_wall = base + keep_from
                next_try = len(rest) / sr + self.TRY
            else:
                buf, buf_len, voiced, start_wall = [], 0, 0.0, None
                next_try = self.FIRST_TRY

        while not stop.is_set():
            try:
                kind, data = q.get(timeout=0.2)
            except queue.Empty:
                kind, data = None, None
            now = time.time()
            if kind == "dev":
                sr = data[1]
                self.status(f"듣는 중 · Whisper({self.device}) · {data[0].replace(' [Loopback]', '')}")
                buf, buf_len, voiced, start_wall = [], 0, 0.0, None
                continue
            if kind == "err":
                self.status(f"소리 받기 다시 연결 중 ({data[:60]})")
                continue
            if kind == "pcm":
                last_data = now
                rms = float(np.sqrt(np.mean(data ** 2)))
                levels.append(rms)
                levels = levels[-150:]              # 약 3초
                thr = max(0.003, sorted(levels)[len(levels) // 10] * 3)
                buf.append(data)
                buf_len += len(data)
                if rms > thr:
                    voiced += len(data) / sr
                    last_voice = now
                    if start_wall is None:
                        start_wall = now - self.PRE_ROLL
                elif start_wall is None:            # 말 시작 전 조용한 소리는 조금만 남긴다
                    while len(buf) > 1 and buf_len - len(buf[0]) > self.PRE_ROLL * sr:
                        buf_len -= len(buf.pop(0))
            # 문장 끝 부호 없이 오래 멈춘 글자는 그냥 내보낸다
            if pend and now - pend_time > self.PEND_MAX and start_wall is None:
                push_text("", now, final=True)
            if voiced < self.MIN_VOICE:
                if start_wall and now - last_voice > 1.0:   # 짧은 소리만 나고 끝 → 버림
                    buf, buf_len, voiced, start_wall = [], 0, 0.0, None
                continue
            if now - last_voice >= self.SIL_CUT or now - last_data >= self.SIL_CUT:
                run(final=True)
            elif buf_len / sr >= next_try:
                run(final=False)
