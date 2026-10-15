// Merkt sich pro Tab alle Video-/Stream-Adressen, die die Seite lädt.
const MEDIA_URL = /\.(m3u8|mpd|mp4|m4v|webm|mkv|mov|flv)(\?|#|$)/i;
const MEDIA_TYPES = /^(application\/(vnd\.apple\.mpegurl|x-mpegurl|dash\+xml)|video\/(mp4|webm|x-matroska|quicktime|x-flv))/i;
// Einzelne Segmente/Bruchstücke ignorieren – die Playlist reicht.
const IGNORE = /(\.ts|\.m4s|\.aac|\.vtt)(\?|$)|googlevideo\.com|[?&](range|bytestart)=/i;

const key = (tabId) => `tab-${tabId}`;

async function remember(tabId, entry) {
  const k = key(tabId);
  const data = (await chrome.storage.session.get(k))[k] || [];
  if (data.some((e) => e.url === entry.url)) return;
  data.unshift(entry);
  await chrome.storage.session.set({ [k]: data.slice(0, 30) });
  chrome.action.setBadgeText({ tabId, text: String(Math.min(data.length, 99)) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#d33" });
}

chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    if (d.tabId < 0 || IGNORE.test(d.url)) return;
    const type = (d.responseHeaders || []).find((h) => h.name.toLowerCase() === "content-type")?.value || "";
    if (!MEDIA_URL.test(d.url) && !MEDIA_TYPES.test(type)) return;
    const kind = /mpegurl/i.test(type) || /\.m3u8/i.test(d.url) ? "HLS"
      : /dash/i.test(type) || /\.mpd/i.test(d.url) ? "DASH" : "Video";
    remember(d.tabId, { url: d.url, kind, time: Date.now() });
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] },
  ["responseHeaders"]
);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) {
    chrome.storage.session.remove(key(tabId));
    chrome.action.setBadgeText({ tabId, text: "" });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(key(tabId)));
