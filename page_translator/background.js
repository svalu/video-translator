// 페이지 통째 번역 — 뒤에서 도는 부분.
//  · 버튼(또는 Alt+T)을 누르면 그 탭의 모든 창(끼워 넣은 다른 사이트 창 포함)에 content.js 를 넣는다.
//  · 번역 요청은 여기서 보낸다 (페이지 안에서 보내면 사이트의 보안 설정에 막힐 수 있다).
// 번역: 구글 번역 무료 주소 (동영상 동시 통역기의 화면 번역과 같은 것). 페이지 글자가 구글로 나간다.

const GOOGLE = "https://translate.googleapis.com/translate_a/t?client=gtx&sl=auto&tl=ko&format=text";
const cache = new Map();          // 영어 → 한국어 (서비스 워커가 살아 있는 동안)
// 구글이 제품 이름까지 옮길 때 되돌린다 (통역기에서 실제로 본 것들)
const FIX = [["부조종사", "Copilot"], ["젠데스크", "Zendesk"], ["젠 데스크", "Zendesk"]];

async function translate(texts) {
  const out = new Array(texts.length).fill("");
  const todo = [];
  texts.forEach((t, i) => (cache.has(t) ? (out[i] = cache.get(t)) : todo.push(i)));
  // 한 번에 너무 많이 보내지 않게: 40칸 또는 4000자마다 끊는다
  let batch = [], size = 0;
  const flush = async () => {
    if (!batch.length) return;
    const body = new URLSearchParams();
    batch.forEach((i) => body.append("q", texts[i]));
    const r = await fetch(GOOGLE, { method: "POST", body });
    if (!r.ok) throw new Error("번역 서버 응답 " + r.status);
    const j = await r.json();
    batch.forEach((i, k) => {
      let ko = Array.isArray(j[k]) ? j[k][0] : j[k];   // 언어 자동 감지일 땐 [번역, 언어]
      if (typeof ko !== "string") ko = "";
      FIX.forEach(([a, b]) => (ko = ko.split(a).join(b)));
      out[i] = ko;
      if (ko) cache.set(texts[i], ko);
    });
    batch = []; size = 0;
  };
  for (const i of todo) {
    if (batch.length >= 40 || size + texts[i].length > 4000) await flush();
    batch.push(i); size += texts[i].length;
  }
  await flush();
  return out;
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.type === "translate") {
    translate(msg.texts).then((ko) => reply({ ko }), (e) => reply({ error: String(e.message || e) }));
    return true;  // 답을 나중에 보낸다
  }
});

// 번역을 켜 둔 탭 목록 (서비스 워커가 쉬었다 깨어나도 남게 session 저장소에)
async function onTabs() { return new Set((await chrome.storage.session.get("on")).on || []); }
async function saveTabs(set) { await chrome.storage.session.set({ on: [...set] }); }

function badge(tabId, text, color = "#1f6bff") {
  chrome.action.setBadgeBackgroundColor({ color, tabId });
  chrome.action.setBadgeText({ text, tabId });
}

// 탭의 창들에 content.js 를 넣고 켜거나 끈다. frameIds 를 주면 그 창만.
async function apply(tabId, on, frameIds) {
  const target = frameIds ? { tabId, frameIds } : { tabId, allFrames: true };
  await chrome.scripting.executeScript({ target, files: ["content.js"] });
  await chrome.scripting.executeScript({ target, func: (v) => window.__heerangPT && window.__heerangPT.set(v), args: [on] });
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  const set = await onTabs();
  const on = !set.has(tab.id);
  try {
    await apply(tab.id, on);
    on ? set.add(tab.id) : set.delete(tab.id);
    await saveTabs(set);
    badge(tab.id, on ? "한" : "");
  } catch (e) {
    badge(tab.id, "×", "#c77700");  // chrome:// 같은 페이지는 넣을 수 없다
  }
});

// 켜 둔 탭에서 페이지나 안쪽 창이 새로 뜨면(강의 다음 장 등) 그 창도 번역한다
chrome.webNavigation.onCompleted.addListener(async (d) => {
  if (!(await onTabs()).has(d.tabId)) return;
  try {
    await apply(d.tabId, true, [d.frameId]);
    if (d.frameId === 0) badge(d.tabId, "한");
  } catch (e) { /* 넣을 수 없는 창 */ }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const set = await onTabs();
  if (set.delete(tabId)) await saveTabs(set);
});
