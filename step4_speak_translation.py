"""4단계: 번역된 한국어 문장을 Heami가 읽는다.

흐름
    LiveCaptions Translator 기록 칸 → (step3) 새 한국어 문장만 → 대기열 → Heami

밀림 잡기
    - 대기열에 MAX_BACKLOG개보다 많이 쌓이면 오래된 문장은 건너뛰고 최신만 읽는다.
    - 쌓인 만큼 말 빠르기를 올린다 (BASE_RATE → MAX_RATE).

측정 (Her 프로젝트처럼 숫자로 본다)
    - 문장마다 "번역 들어옴 → 말 시작" 지연(초)을 기록한다.
    - 기록 파일: logs/읽기_YYYYMMDD_HHMM.csv  (시각, 지연, 건너뜀, Heami말하는중, 영어, 한국어)

되먹임
    Heami 목소리도 실시간 자막에 다시 잡힐 수 있다. 지금은 버리지 않고
    "Heami가 말하는 중에 들어온 문장"에 표시만 한다. 기록을 보고 판단한다.

실행: .venv\\Scripts\\python.exe step4_speak_translation.py [초]   (기본 600초, Ctrl+C로 멈춤)
"""
import csv
import os
import queue
import re
from collections import Counter
import sys
import threading
import time
from datetime import datetime

import uiautomation as auto

from step3_read_translation import SentenceFinalizer, find_window, read_current

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit() else 600
BASE_RATE = 225     # 평소 말 빠르기 (기본 200 근처)
MAX_RATE = 300      # 밀릴 때 최대
LAG_OK = 6.0        # 이보다 늦으면 늦은 만큼 빨리 말한다 (1초당 +12)
MAX_BACKLOG = 2     # 이보다 많이 쌓이면 오래된 문장은 건너뜀
ECHO_WINDOW = 1.0   # 말 끝나고 이 초 안에 들어온 문장도 "말하는 중"으로 표시
HOLD = 1.5          # 확정된 문장을 이만큼 들고 있다가 읽는다 (그 사이 더 긴 판이 오면 짧은 판은 버림)
DUP_COVER = 0.7     # 뒤에 온 문장들이 이 비율 이상 단어를 덮으면 "짧은 판 중복"으로 본다


MIN_EN_WORDS = 4    # 영어가 이보다 짧으면 토막
MIN_KO_CHARS = 6    # 한국어(공백·문장부호 빼고)가 이보다 짧으면 토막
KO_PER_WORD = (0.9, 5.0)  # 영어 1단어당 한국어 글자 수. 이 범위를 벗어나면 번역 잘림/짝 엇갈림


def fragment_reason(en, ko):
    """토막 문장이면 이유를, 아니면 빈 문자열을 돌려준다."""
    nw = len(en.split())
    kl = len(re.sub(r"[\s\.,!?'\"]", "", ko))
    ratio = kl / max(1, nw)
    if nw < MIN_EN_WORDS:
        return "짧은 영어"
    if kl < MIN_KO_CHARS:
        return "짧은 한국어"
    if ratio < KO_PER_WORD[0]:
        return "번역 잘림"
    if ratio > KO_PER_WORD[1]:
        return "짝 엇갈림"
    return ""


_KO_SPLIT = re.compile(r"(?<=[.!?])\s+")


def ko_sentences(ko):
    return [s.strip() for s in _KO_SPLIT.split(ko) if s.strip()]


def _ko_key(s):
    return re.sub(r"[\s\.,!?'\"·]", "", s)


def new_korean_part(ko, spoken, min_ratio=0.6):
    """이미 읽은 한국어 문장을 빼고, 아직 안 읽은 문장만 이어 붙여 돌려준다.

    짧은 판을 먼저 읽고 긴 판이 뒤따라오면 (…Q&A로 넘어갈게요. → …Q&A로 넘어갈게요. 질문 있나요?)
    통째로 버리지 않고 새로 붙은 문장("질문 있나요?")만 읽기 위한 것. 전부 읽은 거면 빈 문자열.
    spoken: 최근에 읽은 한국어 덩어리들 (문장으로 쪼개서 비교한다).
    """
    from difflib import SequenceMatcher
    keys = [_ko_key(s) for chunk in spoken for s in ko_sentences(chunk)]
    out = []
    for sent in ko_sentences(ko):
        k = _ko_key(sent)
        if not k:
            continue
        dup = any(k == o or (len(k) >= 4 and len(o) >= 4 and SequenceMatcher(None, k, o).ratio() >= min_ratio) or
                  (len(k) >= 6 and k in o) for o in keys)
        if not dup:
            out.append(sent)
    return " ".join(out)


def _words(s):
    return re.findall(r"[a-z0-9']+", s.lower())


def covered(a, b):
    """a 의 단어가 b 에 얼마나 들어 있나 (0~1)."""
    ca, cb = Counter(_words(a)), Counter(_words(b))
    return sum(min(n, cb[w]) for w, n in ca.items()) / max(1, sum(ca.values()))


class HeamiSpeaker:
    """목소리 부품. 나중에 GPT-SoVITS 같은 다른 엔진으로 이 클래스만 바꾸면 된다.

    pyttsx3 는 runAndWait() 를 두 번째 부르면 말하지 않고 바로 돌아오는 버그가 있어서
    (11/06 실측: 0.1초 만에 돌아옴) 윈도우 음성 엔진(SAPI.SpVoice)을 직접 부른다.
    """

    def __init__(self):
        import comtypes.client
        self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
        tokens = self.voice.GetVoices()
        for i in range(tokens.Count):
            if "Heami" in tokens.Item(i).GetDescription():
                self.voice.Voice = tokens.Item(i)
        self.voice.Volume = 100

    @staticmethod
    def _sapi_rate(rate):
        # 이 파일의 빠르기(대략 분당 단어 200 = 보통)를 SAPI 빠르기(-10~10, 0 = 보통)로 바꾼다
        return max(-10, min(10, round((rate - 200) / 12)))

    def warm_up(self):
        # 엔진을 한 번 깨워 둔다. 소리를 끄지 않고 아주 짧게 말한다 (끄고 켜기는 위험).
        self.say("준비", BASE_RATE)

    def say(self, text, rate):
        self.voice.Rate = self._sapi_rate(rate)
        t = time.time()
        self.voice.Speak(text, 0)  # 0 = 다 말할 때까지 기다림
        return time.time() - t


def main():
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", f"읽기_{datetime.now():%Y%m%d_%H%M}.csv")
    log_file = open(log_path, "w", newline="", encoding="utf-8-sig")
    log = csv.writer(log_file)
    log.writerow(["시각", "지연초", "건너뜀", "Heami말하는중", "영어", "한국어", "확정까지초", "대기초", "빠르기"])
    recent_spoken = []  # 방금 읽은 영어 (거꾸로 된 중복 막기)

    q = queue.Queue()
    stop = threading.Event()
    speaking = threading.Event()
    last_speak_end = [0.0]
    dup_log = []  # 짧은 판이라 버린 문장 (기록용)

    def watcher():
        # uiautomation 은 스레드마다 초기화가 필요하다
        with auto.UIAutomationInitializerInThread():
            win = find_window()
            if not win:
                print("LiveCaptions Translator 창을 못 찾았어. 번역기를 켜고 홈(Caption) 화면으로 둬.")
                stop.set()
                return
            fin = SentenceFinalizer()
            held = []  # [확정 시각, 영어, 한국어, 되먹임표시, 시작 시각]
            first_en = read_current(win)[0]  # 시작할 때 화면에 있던 문장은 건너뜀
            while not stop.is_set():
                try:
                    en, ko = read_current(win)
                    now = time.time()
                    if not (en and en == first_en):
                        first_en = None
                        for f_en, f_ko, started in fin.feed(en, ko, now):
                            # 그 문장이 들어오기 시작한 때에 Heami가 말하고 있었는지
                            echo = speaking.is_set() or (started - last_speak_end[0] < ECHO_WINDOW)
                            held.append([now, f_en, f_ko, echo, started])
                    # 들고 있는 문장 정리: 뒤에 온 문장들이 덮으면 버리고, HOLD 지나면 읽기로 넘긴다
                    keep = []
                    for i, item in enumerate(held):
                        later = " ".join(h[1] for h in held[i + 1:])
                        if later and covered(item[1], later) >= DUP_COVER:
                            dup_log.append(item)
                        elif now - item[0] >= HOLD:
                            q.put(tuple(item))
                        else:
                            keep.append(item)
                    held[:] = keep
                except Exception as e:
                    print("읽기 실패:", e)
                time.sleep(0.4)
            for item in held:  # 멈출 때 남은 것
                q.put(tuple(item))

    speaker = HeamiSpeaker()
    speaker.warm_up()
    threading.Thread(target=watcher, daemon=True).start()
    print(f"시작. {SECONDS:.0f}초 동안 읽을게. 영상을 틀어 줘. (기록: {log_path})\n")

    end = time.time() + SECONDS
    spoken = skipped = dups = frags = 0
    try:
        while time.time() < end and not stop.is_set():
            while dup_log:  # 중복이라 버린 문장 기록
                t, en, ko, echo, started = dup_log.pop(0)
                dups += 1
                log.writerow([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "중복", "Y" if echo else "", en, ko])
                print("  (중복이라 버림)", ko)
            try:
                item = q.get(timeout=0.3)
            except queue.Empty:
                continue
            # 밀렸으면 오래된 문장은 건너뛴다
            pending = [item]
            while not q.empty():
                pending.append(q.get_nowait())
            if len(pending) > MAX_BACKLOG:
                for t, en, ko, echo, started in pending[:-MAX_BACKLOG]:
                    skipped += 1
                    log.writerow([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "밀림", "Y" if echo else "", en, ko])
                    print("  (밀려서 건너뜀)", ko)
                pending = pending[-MAX_BACKLOG:]
            for i, (t, en, ko, echo, started) in enumerate(pending):
                why = fragment_reason(en, ko)
                if why:
                    frags += 1
                    log.writerow([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "토막:" + why, "Y" if echo else "", en, ko, "", "", ""])
                    print(f"  (토막이라 버림: {why})", ko)
                    continue
                # 이미 읽은 한국어 문장은 빼고 새 문장만 읽는다 (긴 판이 뒤따라와도 새로 붙은 부분은 살림)
                part = new_korean_part(ko, recent_spoken[-4:])
                if not part:
                    dups += 1
                    log.writerow([datetime.fromtimestamp(t).strftime("%H:%M:%S"), "", "중복", "Y" if echo else "", en, ko, "", "", ""])
                    print("  (이미 읽은 내용이라 버림)", ko)
                    continue
                if part != ko:
                    print("  (앞부분은 읽었으니 새 부분만)", part)
                ko = part
                backlog = len(pending) - 1 - i + q.qsize()
                delay = time.time() - started  # 그 문장이 처음 보인 때부터 말 시작까지
                rate = int(min(MAX_RATE, BASE_RATE + 30 * backlog + max(0.0, delay - LAG_OK) * 12))
                mark = " [Heami 말하는 중에 들어옴]" if echo else ""
                print(f"▶ ({delay:.1f}초 = 확정까지 {t - started:.1f} + 대기 {time.time() - t:.1f}, 빠르기 {rate}){mark}\n  {en}\n  {ko}")
                log.writerow([datetime.fromtimestamp(t).strftime("%H:%M:%S"), f"{delay:.2f}", "", "Y" if echo else "", en, ko,
                              f"{t - started:.2f}", f"{time.time() - t:.2f}", rate])
                recent_spoken.append(ko)
                log_file.flush()
                speaking.set()
                speaker.say(ko, rate)
                speaking.clear()
                last_speak_end[0] = time.time()
                spoken += 1
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        log_file.close()
        print(f"\n끝. 읽음 {spoken} · 중복 버림 {dups} · 토막 버림 {frags} · 밀려서 건너뜀 {skipped} · 기록 {log_path}")


if __name__ == "__main__":
    main()
