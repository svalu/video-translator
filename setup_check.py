"""처음 실행 점검: 이 PC에서 동영상 동시 통역기가 돌아갈 준비가 됐는지 보고, 모자란 걸 버튼 하나로 채운다.

  실시간 자막   윈도우 11 기본 (LiveCaptions.exe). 영어 음성 팩은 처음 켤 때 윈도우가 받으라고 한다.
  Ollama       번역 모델을 돌리는 프로그램. 없으면 winget 으로 설치 (사용자가 버튼을 눌렀을 때만).
  번역 모델     ollama 에 받아 둔 모델. 없으면 받기 (진행률 표시).
  그래픽카드    nvidia-smi 로 VRAM. 6GB 미만이면 느리다고 미리 알려 준다.
  Whisper 귀    실시간 자막이 없는 윈10 용 (11/08). GPU 부품(cuBLAS·cuDNN)이 없으면 버튼으로 받는다.
"""
import json
import os
import shutil
import subprocess
import threading
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _ollama_exe():
    p = shutil.which("ollama")
    if p:
        return p
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return p if os.path.exists(p) else None


def _get(path, timeout=2):
    with urllib.request.urlopen(OLLAMA_URL + path, timeout=timeout) as r:
        return json.load(r)


def gpu_info():
    """{'name', 'vram_gb', 'tier', 'note'} — NVIDIA 만 본다. 없으면 CPU."""
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW).stdout.strip()
            name, mem = [x.strip() for x in out.splitlines()[0].split(",")]
            gb = round(int(mem) / 1024, 1)
            if gb >= 8:
                return {"name": name, "vram_gb": gb, "tier": "good", "note": "넉넉해요. 문장당 1초 안쪽"}
            if gb >= 6:
                return {"name": name, "vram_gb": gb, "tier": "ok", "note": "돌아가요. 문장당 1~2초 (일부를 CPU가 계산)"}
            return {"name": name, "vram_gb": gb, "tier": "slow", "note": "메모리가 작아서 느려요. 문장당 3~6초"}
        except Exception:
            pass
    return {"name": "NVIDIA 그래픽카드 없음", "vram_gb": 0, "tier": "slow",
            "note": "CPU로만 번역해서 느려요. 문장당 5~10초 — 동시통역보다는 자막 보조용"}


class Setup:
    def __init__(self, get_model):
        self.get_model = get_model          # 지금 설정된 번역 모델 이름을 돌려주는 함수
        self.job = {"kind": "", "status": "", "percent": 0, "error": ""}
        self._gpu = None

    # ---------- 점검 ----------
    def check(self):
        model = self.get_model()
        captions = os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "LiveCaptions.exe"))
        exe = _ollama_exe()
        running, version, have = False, "", []
        try:
            version = _get("/api/version")["version"]
            running = True
            have = [m["name"] for m in _get("/api/tags").get("models", [])]
        except Exception:
            pass
        present = any(h == model or h == model + ":latest" for h in have)
        if self._gpu is None:
            self._gpu = gpu_info()
        # 귀: 실시간 자막이 없으면(윈10) Whisper 가 대신 듣는다 (11/08). GPU 부품이 없으면 CPU 로라도 돈다.
        import whisper_ear
        nvidia = self._gpu["vram_gb"] > 0
        cuda = whisper_ear.cuda_ready()
        wmodel = whisper_ear.model_cached(whisper_ear.GPU_MODEL if cuda else whisper_ear.CPU_MODEL)
        if cuda:
            wdetail = "GPU 로 받아써요 · " + ("모델 받아 뒀어요" if wmodel else "처음 '읽기 시작' 때 모델을 받아요 (약 1.6GB)")
        elif nvidia:
            wdetail = "GPU 부품이 없어서 지금은 CPU 로 (느려요). 받으면 빨라져요 (약 1.2GB)"
        else:
            wdetail = "그래픽카드가 없어서 CPU 로 받아써요 (느려요)"
        items = [
            {"key": "captions", "label": "귀 · 윈도우 실시간 자막", "ok": captions, "warn": not captions,
             "detail": "있어요 (처음 켤 때 '영어(미국)' 음성 팩을 받으라고 하면 받아 주세요)" if captions
             else "이 PC엔 없어요 (윈도우 11 22H2+ 전용) → 아래 Whisper 가 대신 들어요"},
            {"key": "whisper", "label": "귀 · Whisper (PC 소리 직접 받아쓰기)", "ok": cuda, "warn": not cuda,
             "detail": wdetail, "action": "cuda" if (nvidia and not cuda) else None},
            {"key": "ollama", "label": "Ollama (번역 엔진)", "ok": running,
             "detail": f"실행 중 · {version}" if running else ("설치돼 있는데 꺼져 있어요" if exe else "설치가 필요해요"),
             "action": None if running else ("start" if exe else "install")},
            {"key": "model", "label": f"번역 모델 · {model}", "ok": present,
             "detail": "받아 뒀어요" if present else ("받기가 필요해요 (약 5GB)" if running else "Ollama 먼저"),
             "action": None if present or not running else "pull"},
            {"key": "gpu", "label": f"그래픽카드 · {self._gpu['name']}", "ok": self._gpu["tier"] != "slow",
             "warn": self._gpu["tier"] == "slow",
             "detail": (f"{self._gpu['vram_gb']}GB · " if self._gpu["vram_gb"] else "") + self._gpu["note"]},
        ]
        ready = running and present  # 귀는 실시간 자막이든 Whisper(CPU 라도)든 하나는 늘 있다
        return {"ready": ready, "items": items, "job": dict(self.job)}

    def get_cuda(self):
        """Whisper 를 GPU 로 돌릴 부품(cuBLAS·cuDNN)을 받는다. exe 에는 안 넣는다 (약 2GB)."""
        def fn():
            import whisper_ear

            def prog(pct, text):
                self.job["percent"] = pct
                self.job["detail"] = text
            whisper_ear.download_cuda(prog)
        self._run("cuda", fn)

    # ---------- 채우기 (버튼) ----------
    def _run(self, kind, fn):
        if self.job["status"] == "running":
            return
        self.job = {"kind": kind, "status": "running", "percent": 0, "error": ""}

        def wrap():
            try:
                fn()
                self.job["status"] = "done"
                self.job["percent"] = 100
            except Exception as e:
                self.job["status"] = "error"
                self.job["error"] = str(e)[:200]
        threading.Thread(target=wrap, daemon=True).start()

    def start_ollama(self):
        def fn():
            exe = _ollama_exe()
            app = os.path.join(os.path.dirname(exe), "ollama app.exe") if exe else None
            if app and os.path.exists(app):
                subprocess.Popen([app], creationflags=NO_WINDOW)
            else:
                subprocess.Popen([exe, "serve"], creationflags=NO_WINDOW)
            for _ in range(30):
                try:
                    _get("/api/version", 1)
                    return
                except Exception:
                    threading.Event().wait(1)
            raise RuntimeError("Ollama 가 켜지지 않았어요")
        self._run("start", fn)

    def install_ollama(self):
        def fn():
            winget = shutil.which("winget")
            if not winget:
                os.startfile("https://ollama.com/download/windows")  # winget 이 없으면 내려받기 페이지
                raise RuntimeError("winget 이 없어서 내려받기 페이지를 열었어요. 설치 후 다시 점검을 눌러 주세요")
            self.job["percent"] = 10
            r = subprocess.run([winget, "install", "--id", "Ollama.Ollama", "-e", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
                               capture_output=True, timeout=900, creationflags=NO_WINDOW)
            if r.returncode != 0 and not _ollama_exe():
                raise RuntimeError("설치가 끝나지 않았어요 (회사 PC면 설치 권한이 막혀 있을 수 있어요)")
            self.job["percent"] = 90
            for _ in range(30):
                try:
                    _get("/api/version", 1)
                    return
                except Exception:
                    threading.Event().wait(1)
        self._run("install", fn)

    def pull_model(self):
        model = self.get_model()

        def fn():
            body = json.dumps({"model": model, "stream": True}).encode()
            req = urllib.request.Request(OLLAMA_URL + "/api/pull", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("error"):
                        raise RuntimeError(d["error"])
                    if d.get("total"):
                        self.job["percent"] = int(d.get("completed", 0) * 100 / d["total"])
                    self.job["detail"] = d.get("status", "")
        self._run("pull", fn)
