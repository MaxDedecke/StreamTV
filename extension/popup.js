const API = "http://127.0.0.1:8765/api/cast";
const msg = document.getElementById("msg");

function show(text, err = false) {
  msg.textContent = text;
  msg.className = err ? "err" : "";
}

async function send(tab, media) {
  show("Wird gesendet …");
  try {
    const r = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: tab.url, media: media || "", title: tab.title, referer: tab.url }),
    });
    const data = await r.json();
    show(data.message || (data.ok ? "Gesendet." : "Fehler"), !data.ok);
  } catch (e) {
    show("StreamTV-App läuft nicht. Bitte StreamTV.exe starten.", true);
  }
}

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  document.getElementById("page").onclick = () => send(tab);
  const k = `tab-${tab.id}`;
  const items = (await chrome.storage.session.get(k))[k] || [];
  const order = { HLS: 0, DASH: 1, Video: 2 };
  items.sort((a, b) => order[a.kind] - order[b.kind]);
  const list = document.getElementById("list");
  if (!items.length) {
    document.getElementById("hint").textContent =
      "Noch kein Stream erkannt. Starte das Video auf der Seite kurz und öffne dieses Fenster erneut – oder sende einfach die ganze Seite.";
  }
  for (const it of items) {
    const li = document.createElement("li");
    const kind = document.createElement("span");
    kind.className = "k";
    kind.textContent = it.kind;
    const url = document.createElement("span");
    url.className = "u";
    url.textContent = it.url;
    url.title = it.url;
    const btn = document.createElement("button");
    btn.textContent = "▶ TV";
    btn.onclick = () => send(tab, it.url);
    li.append(kind, url, btn);
    list.append(li);
  }
})();
