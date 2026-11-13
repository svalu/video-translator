// 페이지 통째 번역 — 페이지(와 그 안의 창마다) 들어가서 글자를 바꾸는 부분.
// 여러 번 들어와도 한 번만 자리를 잡는다. 켜고 끄는 건 background.js 가 __heerangPT.set(true/false) 로 시킨다
// (창마다 따로 켜졌다 꺼졌다 하면 어떤 창은 한국어, 어떤 창은 영어로 엇갈린다).
(() => {
  if (window.__heerangPT) return;

  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "CODE", "PRE", "SVG", "MATH"]);
  const original = new Map();   // 글자 노드 → 원래 글
  const translated = new Map(); // 글자 노드 → 우리가 넣은 한국어 (우리가 바꾼 걸 다시 번역하지 않게)
  let on = false, observer = null, timer = 0, busy = false, again = false;

  // 11/13: 강의 재생기(줌 평가 화면)는 글을 그림(SVG)으로 그리면서 영어 글자 하나하나의 자리를 정해 둔다
  // (x="10 18 25 …", textLength, letter-spacing). 한국어로 바꾸면 한글이 그 좁은 자리에 놓여 서로 겹쳤다.
  // → 글을 바꾸는 줄은 '줄이 시작하는 자리'만 남기고 글자별 자리는 푼다. 끄면 되돌린다.
  const SVG_NS = "http://www.w3.org/2000/svg";
  const svgSaved = new Map();   // SVG 글 요소 → 원래 속성들
  function loosen(el) {
    for (let e = el; e && e.namespaceURI === SVG_NS && /^(text|tspan|textPath)$/.test(e.localName); e = e.parentElement) {
      if (!svgSaved.has(e)) {
        const keep = {};
        for (const a of ["x", "y", "dx", "dy"]) {          // 값이 여러 개면 글자마다 준 자리 → 첫 값만
          const v = e.getAttribute(a);
          if (v && /[\s,]/.test(v.trim())) { keep[a] = v; e.setAttribute(a, v.trim().split(/[\s,]+/)[0]); }
        }
        for (const a of ["rotate", "textLength", "lengthAdjust"]) {
          if (e.hasAttribute(a)) { keep[a] = e.getAttribute(a); e.removeAttribute(a); }
        }
        keep.style = e.getAttribute("style");
        e.style.letterSpacing = "normal";
        e.style.wordSpacing = "normal";
        svgSaved.set(e, keep);
      }
      if (e.localName === "text") break;
    }
  }
  function tighten() {
    svgSaved.forEach((keep, e) => {
      for (const a in keep) {
        if (keep[a] === null) e.removeAttribute(a); else e.setAttribute(a, keep[a]);
      }
    });
    svgSaved.clear();
  }

  // 번역할 만한 글인가: 영어 글자가 2자 이상 있고, 한글이 이미 절반 넘지 않는 것
  function worth(s) {
    const t = s.trim();
    if (t.length < 2) return false;
    const latin = (t.match(/[A-Za-z]/g) || []).length;
    const hangul = (t.match(/[가-힣]/g) || []).length;
    return latin >= 2 && hangul < latin;
  }

  function collect(root) {
    const nodes = [];
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p || SKIP.has(p.tagName.toUpperCase()) || p.isContentEditable || p.closest("[translate=no], .notranslate")) return NodeFilter.FILTER_REJECT;
        if (translated.get(n) === n.nodeValue) return NodeFilter.FILTER_REJECT;   // 이미 바꾼 것
        return worth(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    while (walk.nextNode()) nodes.push(walk.currentNode);
    return nodes;
  }

  async function run() {
    if (!on) return;
    if (busy) { again = true; return; }
    busy = true;
    try {
      const nodes = collect(document.body || document.documentElement);
      if (nodes.length) {
        const texts = nodes.map((n) => n.nodeValue.replace(/\s+/g, " ").trim());
        const res = await chrome.runtime.sendMessage({ type: "translate", texts });
        if (res && res.ko && on) {
          nodes.forEach((n, i) => {
            const ko = res.ko[i];
            if (!ko || !n.isConnected) return;
            const src = n.nodeValue;
            if (!original.has(n)) original.set(n, src);
            // 앞뒤 빈칸은 그대로 둔다 (낱말 사이가 붙지 않게)
            const out = (src.match(/^\s*/) || [""])[0] + ko + (src.match(/\s*$/) || [""])[0];
            translated.set(n, out);
            if (n.parentElement && n.parentElement.namespaceURI === SVG_NS) loosen(n.parentElement);
            n.nodeValue = out;
          });
        }
      }
    } catch (e) { /* 번역 서버가 안 되면 영어 그대로 둔다 */ }
    busy = false;
    if (again) { again = false; schedule(); }
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(run, 400); }

  function start() {
    on = true;
    run();
    // 강의 화면은 '다음'을 누르면 글이 바뀐다 → 새로 생긴 글도 번역
    observer = new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === "characterData" && translated.get(m.target) === m.target.nodeValue) continue;  // 우리가 바꾼 것
        schedule();
        return;
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  }

  function stop() {
    on = false;
    clearTimeout(timer);
    if (observer) observer.disconnect();
    original.forEach((src, n) => { if (n.isConnected && translated.get(n) === n.nodeValue) n.nodeValue = src; });
    original.clear(); translated.clear();
    tighten();
  }

  window.__heerangPT = { set: (v) => { if (v && !on) start(); else if (!v && on) stop(); } };
})();
