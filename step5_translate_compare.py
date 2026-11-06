"""5단계 시험: 지금 번역(LiveCaptions Translator = 구글) vs 내 PC 로컬 번역(Ollama) 나란히 비교.

기록(logs/*.csv)에 남은 영어 문장을 그대로 다시 번역해서 비교 화면(번역비교.html)을 만든다.
로컬 번역에는 구글이 못 하는 세 가지를 넣는다:
  ① 젠디스크 강의라는 맥락 + 용어집(Copilot, Escalation 같은 건 그대로)
  ② 바로 앞 문장 2개
  ③ 말버릇(you know, his his)은 빼고 뜻만 자연스럽게

실행: .venv\\Scripts\\python.exe step5_translate_compare.py exaone3.5:2.4b [exaone3.5:7.8b ...]
준비: Ollama 설치 + 모델 내려받기 (ollama pull 모델이름)
"""
import csv
import html
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
LOGS = ["읽기_20261106_1424.csv", "읽기_20261106_1508.csv"]
PER_LOG = 20          # 기록 하나에서 뽑을 문장 수
MIN_WORDS = 4
OLLAMA = "http://127.0.0.1:11434/api/chat"

SYSTEM = """너는 영어 강의를 한국어로 동시통역하는 통역사야.
강의 주제: Zendesk(고객 상담 소프트웨어) 교육. 듣는 사람은 한국 콜센터·UX 실무자.
규칙:
- 받은 영어 한 줄만 한국어로 옮긴다. 앞 문장은 맥락 참고용이고 다시 옮기지 않는다.
- 영어는 실시간 자막이라 말버릇(you know, so, his his)·반복·끊긴 말이 섞여 있다. 군더더기는 빼고 말하려는 뜻만 자연스러운 존댓말 한두 문장으로.
- 이 용어는 영어 그대로 쓴다: Zendesk, Copilot, AI agent, ticket, macro, trigger, escalation, knowledge base, resolution platform, agent workspace.
  (agent = 상담원, AI agent = AI 에이전트, ticket = 티켓)
- 자막이 잘못 알아들은 듯한 고유명사(Zenda Desk → Zendesk)는 바로잡는다.
- 설명·따옴표·영어 원문 없이 한국어 번역만 출력한다."""


def load_rows():
    rows = []
    for name in LOGS:
        with open(HERE / "logs" / name, encoding="utf-8-sig") as f:
            data = list(csv.reader(f))
        h = data[0]
        e, k, s = h.index("영어"), h.index("한국어"), h.index("건너뜀")
        picked = []
        prev = []
        for r in data[1:]:
            en, ko = r[e].strip(), r[k].strip()
            ctx = prev[-2:]
            prev.append(en)
            if r[s] or len(en.split()) < MIN_WORDS:
                continue  # 실제로 안 읽은 줄, 너무 짧은 줄은 빼고
            picked.append({"log": name, "en": en, "google": ko, "ctx": ctx})
        rows += picked[:PER_LOG]
    return rows


def translate(model, en, ctx):
    user = ""
    if ctx:
        user += "앞 문장(참고만):\n" + "\n".join(ctx) + "\n\n"
    user += "옮길 문장:\n" + en
    body = json.dumps({
        "model": model, "stream": False, "keep_alive": "10m",
        "options": {"temperature": 0.2, "num_predict": 200},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)["message"]["content"].strip()
    return out, time.time() - t


def page(rows, models):
    th = "".join(f"<th>{html.escape(m)}</th>" for m in models)
    trs = []
    for i, r in enumerate(rows, 1):
        tds = "".join(
            f"<td>{html.escape(r[m]['ko'])}<small>{r[m]['sec']:.1f}초</small></td>" for m in models)
        trs.append(f"<tr><td class=n>{i}</td><td class=en>{html.escape(r['en'])}</td>"
                   f"<td>{html.escape(r['google'])}</td>{tds}</tr>")
    avg = " · ".join(
        f"{html.escape(m)} 평균 {sum(r[m]['sec'] for r in rows) / len(rows):.1f}초" for m in models)
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>번역 비교</title><style>
:root{{--bg:#e9ebef;--panel:#fff;--ink:#111318;--ink-2:#5d6370;--line:#e2e4ea;--blue:#1f6bff}}
body{{margin:0;padding:20px;background:var(--bg);color:var(--ink);
font:500 14px/1.55 Pretendard,"Malgun Gothic",system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:var(--ink-2);margin:0 0 14px}}
.wrap{{background:var(--panel);border-radius:20px;padding:6px 14px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:900px}}
th,td{{text-align:left;vertical-align:top;padding:10px 8px;border-bottom:1px solid var(--line)}}
th{{font-size:12px;color:var(--ink-2);position:sticky;top:0;background:var(--panel)}}
td.en{{color:var(--ink-2);font-size:12.5px;width:26%}} td.n{{color:var(--ink-2);width:24px}}
small{{display:block;color:var(--ink-2);font-size:11px;margin-top:2px}}
</style></head><body><h1>번역 비교 — 지금(구글) vs 내 PC</h1>
<p>같은 영어 자막 {len(rows)}줄. 회색 영어는 실시간 자막이 받아쓴 그대로. {avg}</p>
<div class=wrap><table><tr><th>#</th><th>영어 자막</th><th>지금 (구글)</th>{th}</tr>
{''.join(trs)}</table></div></body></html>"""


def main():
    models = sys.argv[1:] or ["exaone3.5:2.4b"]
    rows = load_rows()
    print(f"{len(rows)}줄 × 모델 {len(models)}개")
    for m in models:
        translate(m, "Hello.", [])  # 모델 올려 두기 (첫 호출 시간은 빼려고)
        for i, r in enumerate(rows, 1):
            ko, sec = translate(m, r["en"], r["ctx"])
            r[m] = {"ko": ko, "sec": sec}
            print(f"[{m}] {i:>2} {sec:4.1f}초  {ko[:50]}")
    out = HERE / "번역비교.html"
    out.write_text(page(rows, models), encoding="utf-8")
    (HERE / "logs" / "번역비교.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("만듦:", out)


if __name__ == "__main__":
    main()
