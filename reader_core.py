"""읽기 엔진: 번역기 화면에서 한국어 문장을 받아 목소리로 읽는다. 앱(app.py)이 이걸 쓴다.

step3(문장 확정) + step4(중복·토막 거르기, 밀림 조절)를 하나의 Reader 로 묶었다.
목소리는 SapiVoice 하나뿐이지만, 같은 모양(voices / say)의 클래스를 더 만들면 바꿔 낄 수 있다.
"""
import csv
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime

import uiautomation as auto

from step3_read_translation import SentenceFinalizer, find_window, read_current
from step4_speak_translation import covered, fragment_reason, new_korean_part
from local_pipeline import CaptionSegmenter, LocalTranslator, find_caption_box, hide_caption_window, show_caption_window

from paths import DATA as HERE  # 설정·기록·임시 소리를 쓰는 곳 (paths.py)

DEFAULTS = {
    "base_rate": 225,        # 평소 말 빠르기 (분당 단어 기준, 200 = 보통)
    "max_rate": 300,         # 밀릴 때 최대
    "auto_speed": True,      # 늦어지면 자동으로 빨라지기
    "volume": 100,           # 0~100
    "voice": "edge:ko-KR-SunHiNeural",  # 목소리 id (edge:… 또는 sapi:…). 인터넷이 안 되면 윈도우 Heami 로 자동 대체
    "hold": 1.5,             # 중복 확인용으로 들고 있는 초 (11/06 3.0→1.5: 중복을 문장 단위로 거르게 돼서 짧아도 됨)
    "dup_cover": 0.7,        # 이 비율 이상 겹치면 중복
    "filter_fragments": True,
    "source": "local",       # local = 실시간 자막을 직접 읽고 내 PC 모델로 번역 / translator = LiveCaptions Translator 앱(구글)
    "local_model": "exaone3.5:7.8b",
    "ear": "auto",           # 귀: auto = 실시간 자막이 있으면(윈11) 그것, 없으면(윈10) Whisper / captions / whisper (11/08)
    # 한국어 자막 줄 (subtitle_overlay.py 가 0.3초마다 읽어서 바로 반영)
    "overlay_on": True,
    "overlay_size": 22,          # 한국어 글자 크기 (pt)
    "overlay_bg": "#0f1014",     # 배경색
    "overlay_alpha": 88,         # 배경 불투명도 % (0~100, 글자는 항상 불투명)
    "overlay_radius": 16,        # 모서리 둥글기 px (0~40)
    "overlay_en": True,          # 영어 원문 같이 보이기
}
LAG_OK = 6.0
MAX_BACKLOG = 2


def captions_available():
    """윈도우 실시간 자막(윈11 22H2+)이 이 PC에 있나."""
    return os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "LiveCaptions.exe"))


def _default_output_name():
    """지금 윈도우 기본 출력 장치 이름 (스피커·헤드폰). 못 알아내면 ''.
    PyAudio 는 켤 때 장치 목록을 굳혀 두니 매번 새로 만들어서 본다 (0.05초쯤)."""
    try:
        import pyaudiowpatch as pa
        p = pa.PyAudio()
        try:
            w = p.get_host_api_info_by_type(pa.paWASAPI)
            return p.get_device_info_by_index(w["defaultOutputDevice"])["name"]
        finally:
            p.terminate()
    except Exception:
        return ""


def restart_live_captions():
    """실시간 자막을 껐다 켠다 (새 소리 장치를 듣게). 켜는 건 find_caption_box(launch=True) 가 한다."""
    import subprocess
    subprocess.run(["taskkill", "/im", "LiveCaptions.exe", "/f"], capture_output=True, creationflags=0x08000000)
    time.sleep(1.5)


class SapiVoice:
    """윈도우 음성 엔진(SAPI). 반드시 만든 스레드에서만 부른다. id = "sapi:<이름>" """

    def __init__(self):
        import comtypes.client
        self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
        self.current = ""

    def voices(self):
        toks = self.voice.GetVoices()
        out = []
        for i in range(toks.Count):
            d = toks.Item(i).GetDescription()
            out.append({"id": "sapi:" + d, "label": "윈도우 · " + d.replace("Microsoft ", "").replace(" Desktop", "")})
        return out

    def use(self, voice_id):
        name = voice_id.split(":", 1)[1] if ":" in voice_id else voice_id
        if name == self.current:
            return
        toks = self.voice.GetVoices()
        for i in range(toks.Count):
            if name.lower() in toks.Item(i).GetDescription().lower():
                self.voice.Voice = toks.Item(i)
                self.current = name
                return

    def say(self, text, rate, volume):
        self.voice.Rate = max(-10, min(10, round((rate - 200) / 12)))
        self.voice.Volume = int(max(0, min(100, volume)))
        t = time.time()
        self.voice.Speak(text, 0)  # 다 말할 때까지 기다림
        return time.time() - t


class EdgeVoice:
    """Edge 읽어주기 목소리(edge-tts). 인터넷 필요. id = "edge:<edge 음성 이름>"

    문장을 mp3 로 받아(0.5~3초) 윈도우 MCI 로 튼다. 다음 문장은 지금 문장을 트는 동안 미리 받아 둔다(prefetch).
    """

    VOICES = [
        ("edge:ko-KR-SunHiNeural", "Edge · 선희 (SunHi · 여)"),
        ("edge:ko-KR-InJoonNeural", "Edge · 인준 (InJoon · 남)"),
        ("edge:ko-KR-HyunsuMultilingualNeural", "Edge · 현수 (Hyunsu · 남)"),
    ]

    def __init__(self):
        import ctypes
        from concurrent.futures import ThreadPoolExecutor
        self._mci = ctypes.windll.winmm.mciSendStringW
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._pre = {}  # (text, voice, rate) -> future(mp3 경로)
        self.dir = os.path.join(HERE, ".tts")
        os.makedirs(self.dir, exist_ok=True)
        self.last_synth = 0.0  # 마지막 합성에 걸린 초

    def voices(self):
        return [{"id": i, "label": l} for i, l in self.VOICES]

    @staticmethod
    def _rate_str(rate):
        return f"{round((rate - 200) / 2):+d}%"   # 200 → +0%, 225 → +12%, 300 → +50%

    def _synth(self, text, voice_id, rate):
        import asyncio
        import uuid
        import edge_tts
        name = voice_id.split(":", 1)[1]
        path = os.path.join(self.dir, f"{uuid.uuid4().hex}.mp3")
        t = time.time()
        asyncio.run(edge_tts.Communicate(text, name, rate=self._rate_str(rate)).save(path))
        self.last_synth = time.time() - t
        return path

    def prefetch(self, text, voice_id, rate):
        key = (text, voice_id, rate)
        if key not in self._pre:
            self._pre[key] = self._pool.submit(self._synth, text, voice_id, rate)

    def say(self, text, voice_id, rate, volume, speed=1.0):
        """speed: 미리 받아 둔 mp3 를 더 빨리 틀 때 (1.0 = 그대로, 최대 1.3). 다시 합성하지 않아도 되게."""
        fut = self._pre.pop((text, voice_id, rate), None)
        path = fut.result(timeout=20) if fut else self._synth(text, voice_id, rate)
        t = time.time()
        try:
            self._mci(f'open "{path}" type mpegvideo alias tts', None, 0, None)
            self._mci(f"setaudio tts volume to {int(max(0, min(100, volume)) * 10)}", None, 0, None)
            if speed > 1.02:
                self._mci(f"set tts speed {int(min(1.3, speed) * 1000)}", None, 0, None)
            self._mci("play tts wait", None, 0, None)
        finally:
            self._mci("close tts", None, 0, None)
            try:
                os.remove(path)
            except OSError:
                pass
        return time.time() - t


class Reader:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self._load_settings()  # 지난번에 바꾼 설정 (settings.json)
        self.lock = threading.Lock()
        self.running = False
        self.status = "대기 중"
        self.current = {"en": "", "ko": ""}
        self.now_speaking = ""
        self.recent = deque(maxlen=8)
        self.stats = {"spoken": 0, "dups": 0, "frags": 0, "skipped": 0, "lat_sum": 0.0}
        self.voice_names = []
        self._q = queue.Queue()        # 읽을 문장
        self._tasks = queue.Queue()    # 목소리 스레드에 보낼 일 (들어보기 등)
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self._last_speak_end = 0.0
        self._log = None
        self._log_file = None
        self._recent_ko = []
        threading.Thread(target=self._voice_loop, daemon=True).start()

    # ---------- 바깥에서 부르는 것 ----------
    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self._stop.clear()
            self.stats = {"spoken": 0, "dups": 0, "frags": 0, "skipped": 0, "lat_sum": 0.0}
            self.recent.clear()
            os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
            path = os.path.join(HERE, "logs", f"읽기_{datetime.now():%Y%m%d_%H%M}.csv")
            self._log_file = open(path, "w", newline="", encoding="utf-8-sig")
            self._log = csv.writer(self._log_file)
            self._log.writerow(["시각", "지연초", "건너뜀", "영어", "한국어", "빠르기", "확정까지", "줄대기", "합성대기", "말한초", "번역초", "번역"])
            whisper = self.ear_mode() == "whisper"
            local = self.settings["source"] == "local" or whisper  # 번역기 앱(구글)은 실시간 자막이 있어야 해서 Whisper 귀면 내 PC 번역
            self.status = "귀(Whisper) 준비 중" if whisper else ("실시간 자막 찾는 중" if local else "번역기 찾는 중")
        threading.Thread(target=self._local_loop if local else self._watch_loop, daemon=True).start()

    def ear_mode(self):
        """지금 쓸 귀: 'captions'(윈도우 실시간 자막) 또는 'whisper'(PC 소리 직접 받아쓰기)."""
        ear = self.settings.get("ear", "auto")
        if ear == "auto":
            return "captions" if captions_available() else "whisper"
        return ear

    def stop(self):
        with self.lock:
            self.running = False
            self._stop.set()
            self.status = "멈춤"
            while not self._q.empty():
                self._q.get_nowait()
            if self._log_file:
                self._log_file.close()
                self._log_file = None
        try:
            show_caption_window()  # 숨겼던 회색 자막 창 되돌리기
        except Exception:
            pass
        tr = getattr(self, "_translator", None)
        if tr:
            threading.Thread(target=tr.unload, daemon=True).start()  # 그래픽 메모리 비우기

    SETTINGS_FILE = os.path.join(HERE, "settings.json")

    def _load_settings(self):
        try:
            import json
            with open(self.SETTINGS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if k in DEFAULTS:
                    self.settings[k] = type(DEFAULTS[k])(v)
        except Exception:
            pass

    def update(self, new):
        with self.lock:
            for k, v in new.items():
                if k in DEFAULTS:
                    self.settings[k] = type(DEFAULTS[k])(v)
            try:  # 껐다 켜도 그대로이게 저장
                import json
                with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.settings, f, ensure_ascii=False, indent=1)
            except OSError:
                pass

    def test_voice(self):
        self._tasks.put("test")

    def state(self):
        with self.lock:
            s = self.stats
            avg = s["lat_sum"] / s["spoken"] if s["spoken"] else 0.0
            return {
                "running": self.running,
                "status": self.status,
                "current": dict(self.current),
                "speaking": self.now_speaking,
                "recent": list(self.recent),
                "stats": {**{k: s[k] for k in ("spoken", "dups", "frags", "skipped")}, "avg_delay": round(avg, 1)},
                "settings": dict(self.settings),
                "voices": list(self.voice_names),
                "ear": self.ear_mode(),
                "captions_available": captions_available(),
            }

    # ---------- 안에서 도는 것 ----------
    def _write(self, row):
        if self._log:
            self._log.writerow(row)
            self._log_file.flush()

    def _watch_loop(self):
        with auto.UIAutomationInitializerInThread():
            win = None
            while not self._stop.is_set() and win is None:
                win = find_window()
                if win is None:
                    self.status = "번역기가 안 보여. 바탕화면 'LiveCaptions Translator'로 켜 줘"
                    time.sleep(2)
            if self._stop.is_set():
                return
            self.status = "듣는 중"
            fin = SentenceFinalizer()
            held = []
            first_en = read_current(win)[0]
            while not self._stop.is_set():
                try:
                    en, ko = read_current(win)
                    self.current = {"en": en, "ko": ko}
                    now = time.time()
                    if not (en and en == first_en):
                        first_en = None
                        for f_en, f_ko, started in fin.feed(en, ko, now):
                            held.append([now, f_en, f_ko, started])
                            # 확정되는 순간 미리 합성 시작 (들고 있는 1.5초 동안 받아 둔다). 중복으로 버려지면 그냥 버림
                            st = self.settings
                            if st["voice"].startswith("edge:") and getattr(self, "_edge", None):
                                try:
                                    self._edge.prefetch(f_ko, st["voice"], st["base_rate"])
                                except Exception:
                                    pass
                    keep = []
                    hold, cover = self.settings["hold"], self.settings["dup_cover"]
                    for i, item in enumerate(held):
                        later = " ".join(h[1] for h in held[i + 1:])
                        if later and covered(item[1], later) >= cover:
                            self.stats["dups"] += 1
                            self._write([datetime.fromtimestamp(item[0]).strftime("%H:%M:%S"), "", "중복", item[1], item[2], ""])
                        elif now - item[0] >= hold:
                            self._q.put(tuple(item))
                        else:
                            keep.append(item)
                    held[:] = keep
                except Exception:
                    # 번역기 창이 닫혔을 수 있다 → 다시 찾기
                    self.status = "번역기 연결이 끊겼어. 다시 찾는 중"
                    time.sleep(1)
                    win = find_window()
                    if win is not None:
                        self.status = "듣는 중"
                time.sleep(0.4)

    def _local_loop(self):
        """번역기 앱 없이: 실시간 자막 → 문장 끊기 → 내 PC 번역 → 읽을 줄."""
        tr = LocalTranslator(self.settings["local_model"])
        self._translator = tr
        self.status = "번역 모델 올리는 중 (처음엔 10초쯤)"
        try:
            tr.warm()
        except Exception as e:
            self.status = f"번역 모델을 못 불렀어 (Ollama 꺼짐?): {type(e).__name__}"
            self.running = False
            return
        jobs = queue.Queue()

        def translate_worker():
            while not self._stop.is_set():
                try:
                    t, en, born = jobs.get(timeout=0.3)
                except queue.Empty:
                    continue
                try:
                    ko = tr.translate(en)
                except Exception as e:
                    self.status = f"번역 실패({type(e).__name__}) — Ollama 확인"
                    continue
                self._last_tr = tr.last_sec
                if not ko:
                    self.stats["frags"] += 1
                    self._write([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "추임새", en, "", "", "", "", "", "", f"{tr.last_sec:.2f}", tr.model])
                    continue
                st = self.settings
                if st["voice"].startswith("edge:") and getattr(self, "_edge", None):
                    try:
                        self._edge.prefetch(ko, st["voice"], st["base_rate"])
                    except Exception:
                        pass
                self.current = {"en": en, "ko": ko}
                self._q.put((time.time(), en, ko, born, tr.last_sec))

        threading.Thread(target=translate_worker, daemon=True).start()
        if self.ear_mode() == "whisper":
            return self._whisper_listen(jobs)
        with auto.UIAutomationInitializerInThread():
            box = self._find_box_hard()
            if box is None:
                return
            self.status = f"듣는 중 · 내 PC 번역"
            try:
                hide_caption_window()  # 한국어 자막 줄이 대신 보여 주니까 회색 영어 창은 숨긴다
            except Exception:
                pass
            seg = CaptionSegmenter()
            try:
                seg.prime(box.Name or "")
            except Exception:
                pass
            out_dev, next_dev_check = _default_output_name(), time.time() + 5
            while not self._stop.is_set():
                try:
                    text = box.Name or ""
                    now = time.time()
                    lines = [l for l in text.splitlines() if l.strip()]
                    if lines and not self.now_speaking:
                        self.current = {"en": lines[-1], "ko": self.current.get("ko", "")}
                    for en, born in seg.feed(text, now):
                        jobs.put((now, en, born))
                    # 11/10: 소리 장치를 바꾸면(블루투스 헤드폰 연결 등) 실시간 자막은 예전 장치를 계속 들어서 영어가 안 나온다.
                    # → 기본 출력 장치가 바뀌면 실시간 자막을 껐다 켜서 새 장치를 듣게 한다.
                    if now >= next_dev_check:
                        next_dev_check = now + 5
                        dev = _default_output_name()
                        if dev and out_dev and dev != out_dev:
                            self.status = f"실시간 자막 다시 켜는 중 · 소리 장치가 {dev}(으)로 바뀜"
                            restart_live_captions()
                            box = self._find_box_hard()
                            if box is None:
                                return
                            try:
                                hide_caption_window()
                                seg.prime(box.Name or "")
                            except Exception:
                                pass
                            self.status = "듣는 중 · 내 PC 번역"
                        if dev:
                            out_dev = dev
                except Exception:
                    self.status = "실시간 자막 연결이 끊겼어. 다시 찾는 중"
                    time.sleep(1)
                    box = self._find_box_hard()  # 11/10: 예전엔 못 찾으면 box=None 인 채로 돌아서 계속 '끊겼어'만 떴다
                    if box is None:
                        return
                    if box is not None:
                        try:
                            hide_caption_window()  # 11/10: 다시 켜진 실시간 자막 창이 화면에 그대로 떠 있던 것
                        except Exception:
                            pass
                        self.status = "듣는 중 · 내 PC 번역"
                time.sleep(0.3)

    def _find_box_hard(self):
        """실시간 자막 창을 찾는다. 못 찾으면 앱이 직접 껐다 켠다. 멈추면 None.
        11/10: 프로세스는 살아 있는데 창을 못 찾는 때가 있었다. 그때 'Win+Ctrl+L 로 켜 줘'라고 하면
        단축키가 켜고 끄는 토글이라 살아 있던 걸 꺼 버리거나, 앱이 2초마다 켜는 것과 엇갈려 잘 안 떠졌다.
        → 사람한테 맡기지 않는다. 끄고 새로 켜기를 되풀이하고, 세 번 넘게 안 되면 그때 알려 준다."""
        tries = 0
        while not self._stop.is_set():
            box = find_caption_box(launch=True)
            if box is not None:
                return box
            if self._stop.is_set():
                break
            tries += 1
            if tries <= 3:
                self.status = "실시간 자막 다시 켜는 중" + (f" · {tries}번째" if tries > 1 else "")
            else:
                self.status = "실시간 자막이 안 떠 · 10초 뒤 다시 해 볼게. 단축키는 누르지 마"
                self._stop.wait(10)
            restart_live_captions()
        return None

    def _whisper_listen(self, jobs):
        """귀 = Whisper: PC 소리를 직접 받아쓴다 (윈10 에서도). 끝난 영어 문장은 실시간 자막 때와 같은 번역 줄로."""
        from whisper_ear import WhisperEar

        def status(s):
            if self.running:
                self.status = s
        ear = getattr(self, "_ear", None)
        if ear is None:
            ear = self._ear = WhisperEar(status=status)
        ear.status = status
        try:
            if ear.model is None:
                status("Whisper 올리는 중 (처음엔 몇 초)")
            ear.load()  # 한 번 올리면 앱이 켜져 있는 동안 그대로 (다음 '읽기 시작'은 바로)
        except Exception as e:
            self.status = f"Whisper 를 못 불렀어: {type(e).__name__} — 준비 점검을 열어 봐"
            self.running = False
            return

        def on_sentence(en, born):
            jobs.put((time.time(), en, born))

        def on_text(t):
            if not self.now_speaking:
                self.current = {"en": t, "ko": self.current.get("ko", "")}

        t_start = time.time()
        try:
            ear.listen(self._stop, on_sentence, on_text)
        finally:
            # 귀가 무엇을 어떻게 받아썼는지 남긴다 (문장이 사라지면 여기서 원인을 본다)
            try:
                path = os.path.join(HERE, "logs", f"귀_{datetime.now():%Y%m%d_%H%M}.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# Whisper 귀 · {ear.model_name} · {ear.device} · 한국어(되먹임) 단어 버림 {ear.dropped_ko}\n")
                    for tt, kind, text in list(ear.trace):
                        if tt >= t_start:
                            f.write(f"{datetime.fromtimestamp(tt):%H:%M:%S}.{int(tt * 10) % 10} {kind}: {text}\n")
            except OSError:
                pass

    def _voice_loop(self):
        import comtypes
        comtypes.CoInitialize()
        sapi = SapiVoice()
        edge = EdgeVoice()
        self.voice_names = edge.voices() + sapi.voices()

        self._edge = edge  # 듣는 쪽 스레드가 문장이 들어오자마자 미리 합성하게

        def speak(text, rate, volume):
            """설정된 목소리로 말한다. Edge 가 안 되면(인터넷 등) 윈도우 Heami 로 대신한다.
            Edge 는 평소 빠르기(base_rate)로 미리 받아 둔 mp3 를 쓰고, 더 빨리 읽어야 하면 재생 속도로 맞춘다."""
            vid = self.settings["voice"]
            if vid.startswith("edge:"):
                try:
                    base = self.settings["base_rate"]
                    return edge.say(text, vid, base, volume, speed=rate / base)
                except Exception as e:
                    self.status = f"Edge 목소리 실패({type(e).__name__}) → 윈도우 목소리로 대신 읽는 중"
                    vid = "sapi:Heami"
            sapi.use(vid)
            return sapi.say(text, rate, volume)

        while True:
            try:
                task = self._tasks.get_nowait()
            except queue.Empty:
                task = None
            if task == "test":
                st = self.settings
                speak("목소리 들어보기야. 이 빠르기와 음량으로 읽어 줄게.", st["base_rate"], st["volume"])
                continue
            try:
                item = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            pending = [item]
            while not self._q.empty():
                pending.append(self._q.get_nowait())
            if len(pending) > MAX_BACKLOG:
                for t, en, ko, started, *_ in pending[:-MAX_BACKLOG]:
                    self.stats["skipped"] += 1
                    self._write([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "밀림", en, ko, ""])
                pending = pending[-MAX_BACKLOG:]
            for i, (t, en, ko, started, *extra) in enumerate(pending):
                tr_sec = f"{extra[0]:.2f}" if extra else ""
                if not self.running:
                    break
                st = self.settings
                # 내 PC 번역은 군더더기를 줄여 짧게 옮기므로 길이 비율 검사는 구글 번역에만 (추임새는 번역 단계에서 이미 거름)
                if st["filter_fragments"] and not extra and fragment_reason(en, ko):
                    self.stats["frags"] += 1
                    self._write([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "토막", en, ko, ""])
                    continue
                # 이미 읽은 한국어 문장은 빼고 새 문장만 읽는다
                part = new_korean_part(ko, self._recent_ko[-4:])
                if not part:
                    self.stats["dups"] += 1
                    self._write([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "중복", en, ko, ""])
                    continue
                ko = part
                delay = time.time() - started
                backlog = len(pending) - 1 - i + self._q.qsize()
                rate = st["base_rate"]
                if st["auto_speed"]:
                    rate = int(min(st["max_rate"], rate + 30 * backlog + max(0.0, delay - LAG_OK) * 12))
                with self.lock:
                    self.now_speaking = ko
                    self.recent.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "en": en, "ko": ko,
                                            "delay": round(delay, 1), "rate": rate})
                    self.stats["spoken"] += 1
                    self.stats["lat_sum"] += delay
                self._recent_ko.append(ko)
                self._speaking.set()
                t_deq = time.time()
                spoke = speak(ko, rate, st["volume"]) or 0.0
                self._speaking.clear()
                # 지연을 쪼개서 기록: 확정까지(들린 뒤 문장 끝), 줄대기(확정 뒤 차례까지), 합성대기(차례 뒤 소리 날 때까지)
                synth_wait = max(0.0, (time.time() - t_deq) - spoke)
                self._write([datetime.fromtimestamp(t).strftime("%H:%M:%S"), f"{delay:.2f}", "", en, ko, rate,
                             f"{t - started:.2f}", f"{t_deq - t:.2f}", f"{synth_wait:.2f}", f"{spoke:.2f}",
                             tr_sec, st["local_model"] if extra else "구글"])
                self._last_speak_end = time.time()
                with self.lock:
                    self.now_speaking = ""
