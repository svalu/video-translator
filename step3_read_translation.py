"""3단계: LiveCaptions Translator가 번역한 한국어 문장을 하나씩 뽑아 오기.

번역기 홈(Caption) 화면의 기록 칸에는 확정된 문장이 쌓인다.
  OriginalCaptionLog   = 영어 원문
  TranslatedCaptionLog = 한국어 번역   (위가 최신)
같은 문장이 덜 끝난 상태로 한 번, 끝난 상태로 또 한 번 쌓이기도 해서
"앞부분이 같은 문장"은 더 긴 쪽만 남긴다.

실행: .venv\\Scripts\\python.exe step3_read_translation.py [초]   (기본 60초)
"""
import sys
import time

import uiautomation as auto

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit() else 60


def find_window():
    win = auto.WindowControl(searchDepth=1, Name="LiveCaptions Translator")
    return win if win.Exists(3, 0.5) else None


def read_pairs(win):
    """(영어, 한국어) 쌍을 오래된 것부터 돌려준다."""
    originals, translated = [], []
    for c, _ in auto.WalkControl(win, maxDepth=14):
        aid = c.AutomationId
        if aid == "OriginalCaptionLog":
            originals.append((c.Name or "").strip())
        elif aid == "TranslatedCaptionLog":
            translated.append((c.Name or "").strip())
    pairs = list(zip(originals, translated))
    pairs.reverse()  # 화면은 위가 최신 → 오래된 순으로
    return pairs


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def read_current(win):
    """지금 번역 중인 문장: (영어, 한국어). 기록 카드가 꺼져 있어도 늘 갱신된다."""
    en = ko = ""
    for c, _ in auto.WalkControl(win, maxDepth=14):
        aid = c.AutomationId
        if aid == "OriginalCaption":
            en = (c.Name or "").strip()
        elif aid == "TranslatedCaption":
            ko = (c.Name or "").strip()
    return en, ko


class SentenceFinalizer:
    """현재 문장을 지켜보다가 '다음 문장으로 넘어가면' 앞 문장을 확정해서 내보낸다.

    같은 문장인지: 영어 앞부분(정규화 PREFIX글자)이 같으면 같은 문장이 자라는 중으로 본다.
    확정할 때는 그 문장 동안 본 가장 긴 영어와, 마지막으로 본 한국어 번역을 쓴다.
    """

    PREFIX = 14

    def __init__(self):
        self.en = ""
        self.ko = ""
        self.started = 0.0
        # 한국어 칸은 영어 칸보다 늦게 바뀐다. 새 문장이 시작돼도 잠깐은 앞 문장 번역이 남아 있으므로
        # 앞 문장 번역(stale)과 같은 값은 새 문장의 번역으로 받지 않는다.
        self.stale = ""
        # 마침표로 끝나고 STABLE초 동안 안 바뀌면 다음 문장을 기다리지 않고 먼저 내보낸다 (지연 줄이기)
        self.last_change = 0.0
        self.early = 0  # 먼저 내보낸 판의 단어 수 (0이면 아직 안 내보냄)
        self.early_ko = ""

    STABLE = 0.8

    @staticmethod
    def _nwords(s):
        return len(s.split())

    def _same(self, a, b):
        a, b = norm(a), norm(b)
        n = min(self.PREFIX, len(a), len(b))
        return n > 0 and a[:n] == b[:n]

    def feed(self, en, ko, now):
        """확정된 (영어, 한국어, 그 문장이 처음 보인 시각) 목록을 돌려준다."""
        out = []
        if not en:
            return out
        if self.en and not self._same(self.en, en):
            # 먼저 내보낸 판보다 30% 이상 길어지고 번역도 바뀌었을 때만 다시 내보낸다
            if self.ko and (not self.early or (self._nwords(self.en) > self.early * 1.3 and self.ko != self.early_ko)):
                out.append((self.en, self.ko, self.started))
            self.stale = self.ko or ko  # 지금 화면의 한국어는 아직 앞 문장 것일 수 있다
            self.en, self.ko, self.started = "", "", now
            self.early = 0
        if not self.en:
            self.started = now
        if len(en) >= len(self.en) and en != self.en:
            self.en = en
            self.last_change = now
        if ko and ko != self.stale and ko != self.ko:
            self.ko = ko
            self.stale = ""  # 새 번역이 한 번 들어오면 그 뒤로는 그대로 받는다
            self.last_change = now
        # 마침표로 끝났고 잠깐 안정됐으면 먼저 내보낸다
        if (not self.early and self.ko and self.en.rstrip().endswith((".", "?", "!"))
                and now - self.last_change >= self.STABLE and self._nwords(self.en) >= 4):
            out.append((self.en, self.ko, self.started))
            self.early = self._nwords(self.en)
            self.early_ko = self.ko
        return out

    def flush(self, now):
        out = [(self.en, self.ko, self.started)] if self.en and self.ko else []
        self.en = self.ko = ""
        return out


class NewSentences:
    """처음 보는 문장만 내보낸다. 덜 끝난 문장의 앞부분이 같으면 같은 문장으로 본다."""

    def __init__(self):
        self.seen = []  # 정규화한 영어 원문

    def feed(self, pairs):
        out = []
        for en, ko in pairs:
            key = norm(en)
            if not key or not ko:
                continue
            if any(key == s or s.startswith(key) for s in self.seen):
                continue  # 이미 읽었거나, 이미 읽은 것보다 짧은 판
            # 전에 본 덜 끝난 판이 이번 문장의 앞부분이면 그건 지우고 새 판을 받는다
            self.seen = [s for s in self.seen if not key.startswith(s)]
            self.seen.append(key)
            out.append((en, ko))
        return out


if __name__ == "__main__":
    win = find_window()
    if not win:
        print("LiveCaptions Translator 창을 못 찾았어. 번역기를 켜고 홈(Caption) 화면으로 둬.")
        sys.exit(1)
    picker = NewSentences()
    picker.feed(read_pairs(win))  # 이미 화면에 있던 문장은 건너뛴다
    print(f"{SECONDS:.0f}초 동안 새 문장을 기다릴게. 영상을 틀어 봐.\n")
    end = time.time() + SECONDS
    while time.time() < end:
        try:
            for en, ko in picker.feed(read_pairs(win)):
                print("EN:", en)
                print("KO:", ko, "\n")
        except Exception as e:
            print("읽기 실패:", e)
        time.sleep(0.7)
    print("끝")
