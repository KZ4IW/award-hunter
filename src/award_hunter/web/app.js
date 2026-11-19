"use strict";
// award-hunter browser UI: polls /api/status, renders panels, posts edits as JSON.
const $ = (sel, root = document) => root.querySelector(sel);
let token = "";
try { token = localStorage.getItem("ah-token") || ""; } catch (e) { /* storage blocked */ }
let colors = null;
const panels = {};

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

async function api(path, body) {
  const opts = { headers: {} };
  if (token) opts.headers.Authorization = "Bearer " + token;
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    token = prompt("award-hunter access token (from web.token_file):") || "";
    try { localStorage.setItem("ah-token", token); } catch (e) { /* ignore */ }
    throw new Error("token required");
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function age(sec) {
  if (sec < 60) return Math.round(sec) + "s";
  return Math.round(sec / 60) + "m";
}

function callChip(call, lotw) {
  const s = el("span", call, "call");
  if (colors) {
    s.style.background = lotw ? colors.lotw_bg : colors.other_bg;
    s.style.color = lotw ? colors.lotw_fg : colors.other_fg;
  }
  return s;
}

function renderHeard(rows) {
  const tb = $("#heard tbody");
  tb.replaceChildren();
  const now = Date.now() / 1000;
  for (const r of rows) {
    const tr = el("tr");
    const c = el("td"); c.append(callChip(r.call, r.lotw)); tr.append(c);
    tr.append(el("td", r.label + " — " + r.reason));
    tr.append(el("td", r.lotw ? "yes" : "no"));
    tr.append(el("td", r.band || "?"));
    tr.append(el("td", (r.snr > 0 ? "+" : "") + r.snr));
    tr.append(el("td", r.message, "msg"));
    tr.append(el("td", age(now - r.last)));
    tb.append(tr);
  }
  $("#heard-empty").hidden = rows.length > 0;
}

function renderBands(s) {
  const tb = $("#bands tbody");
  tb.replaceChildren();
  const b = s.bands;
  const meta = $("#bands-meta");
  if (!b) {
    meta.textContent = "";
    $("#bands-empty").textContent = s.bandtip_error ? "PSK Reporter: " + s.bandtip_error
      : "Waiting for the first PSK Reporter query (runs while WSJT-X is decoding, at most every 5 min).";
    return;
  }
  meta.textContent = `(${b.grid}, ${b.reports} reports, ${age(Date.now() / 1000 - b.time)} ago` +
    (s.bandtip_next ? `, next in ${age(s.bandtip_next)})` : ")");
  const entries = Object.entries(b.bands);
  $("#bands-empty").textContent = entries.length ? "" : "No needed stations reported in your grid in the last 15 min.";
  for (const [band, v] of entries) {
    const tr = el("tr");
    const bt = el("td", band + (band === s.wsjtx.band ? " ◀ you" : ""));
    if (band === s.wsjtx.band) bt.className = "youband";
    tr.append(bt, el("td", String(v.score)));
    const td = el("td", null, "chips");
    for (const x of v.stations.slice(0, 12)) {
      const w = el("span");
      w.append(callChip(x.call, x.lotw), document.createTextNode(` ${x.label} ${x.snr > 0 ? "+" : ""}${x.snr}`));
      w.title = "heard by " + x.rx.join(", ");
      td.append(w);
    }
    tr.append(td);
    tb.append(tr);
  }
}

function msg(p, text, ok) {
  const m = $(".msg", p);
  m.textContent = text;
  m.className = "msg " + (ok ? "ok" : "err");
}

async function act(key, name, body, p) {
  try {
    const r = await api(`/api/awards/${key}/${name}`, body);
    if (r.message) msg(p, r.message, true);
    refresh();
  } catch (e) { msg(p, e.message, false); }
}

function buildAward(key, a) {
  const p = $("#award-tpl").content.firstElementChild.cloneNode(true);
  $("#awards").append(p);
  if (a.kind !== "states") {
    for (const sel of [".states", ".hint", ".credits", ".schedule", ".w1aw"]) $(sel, p).remove();
  } else {
    $(".do-credits", p).addEventListener("click", () =>
      act(key, "credits", { text: $("textarea", p).value }, p));
    if (a.upcoming === undefined) $(".schedule", p).remove();
    else $(".pdf", p).addEventListener("change", (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      const rd = new FileReader();
      rd.onload = () => act(key, "schedule_pdf", { data: rd.result.split(",")[1] }, p);
      rd.readAsDataURL(f);
    });
  }
  panels[key] = p;
  return p;
}

function renderAward(key, a) {
  const p = panels[key] || buildAward(key, a);
  $(".title", p).textContent = a.title;
  let summary = "";
  if (a.kind === "states") {
    summary = a.ended ? "Event ended." : `${a.needed_count} still needed.`;
    const box = $(".states", p);
    box.replaceChildren();
    for (const s of a.states) {
      const b = el("button", s.code);
      b.type = "button";
      b.title = s.name + (s.pending ? " — worked, awaiting credit: " + s.pending + " (click: have it)"
        : s.needed ? " — needed (click: worked, awaiting credit)" : " — have it (click: needed)");
      if (s.pending) b.className = "pending";
      else if (s.needed) b.className = "needed";
      b.addEventListener("click", () => act(key, "cycle", { state: s.code }, p));
      box.append(b);
    }
    const w = $(".w1aw", p);
    if (w) {
      w.replaceChildren();
      if (a.w1aw_now && a.w1aw_now.length) {
        w.append(el("span", "On the air this week for needed states: ", "muted"));
        for (const x of a.w1aw_now) {
          const s = el("span", null, "now");
          s.append(callChip(x.call, true), document.createTextNode(" " + x.states.join("/") + "  "));
          w.append(s);
        }
      } else if (a.w1aw_now) {
        w.append(el("span", "No W1AW/n station for a needed state this week.", "muted"));
      }
    }
    const up = $(".upcoming tbody", p);
    if (up) {
      $(".sched-info", p).textContent = `${a.schedule_rows} activations loaded. Import a newer ARRL schedule PDF:`;
      up.replaceChildren();
      for (const u of a.upcoming) {
        const tr = el("tr");
        tr.append(el("td", u.state), el("td", u.call), el("td", `${u.start} – ${u.end}${u.on_now ? " (on now)" : ""}`));
        up.append(tr);
      }
    }
  } else {
    summary = (a.items && a.items.length ? "Watching: " + a.items.join(" ") + ". " : "") +
      `${a.worked_count} worked` + (a.total ? ` of ${a.total}` : "") + ".";
  }
  $(".summary", p).textContent = summary;
}

function renderAbout(a) {
  if (!a) return;
  const f = $("#about");
  f.replaceChildren(`award-hunter ${a.version} · `);
  if (a.url && /^https:\/\//.test(a.url)) {
    const l = el("a", a.credit);
    l.href = a.url; l.target = "_blank"; l.rel = "noopener";
    f.append(l);
  } else f.append(a.credit);
}

const SOURCE_TEXT = {
  web: "Set here. This overrides your config file and WSJT-X.",
  config: "From your config file ([station] in config.toml).",
  wsjtx: "From WSJT-X (My Call / My Grid). Change it there, or set it here.",
  "": "Not set. Enter your callsign and grid square.",
};
let settingsDirty = false;

function renderSettings(st) {
  const call = $("#set-call"), grid = $("#set-grid");
  if (!settingsDirty && document.activeElement !== call && document.activeElement !== grid) {
    call.value = st.call || "";
    grid.value = st.grid || "";
  }
  let src = SOURCE_TEXT[st.source] || "";
  if (st.source === "web" && st.default && st.default.call) src += ` (Default would be ${st.default.call} ${st.default.grid || ""}.)`;
  $("#set-source").textContent = src;
  $("#set-reset").hidden = st.source !== "web";
}

async function saveSettings(body) {
  const m = $("#set-msg");
  try {
    const r = await api("/api/settings", body);
    settingsDirty = false;
    m.textContent = r.message;
    m.className = "msg ok";
    refresh();
  } catch (e) {
    m.textContent = e.message;
    m.className = "msg err";
  }
}

for (const id of ["#set-call", "#set-grid"]) $(id).addEventListener("input", () => { settingsDirty = true; });
$("#station-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  saveSettings({ call: $("#set-call").value, grid: $("#set-grid").value });
});
$("#set-reset").addEventListener("click", () => { settingsDirty = false; saveSettings({ reset: true }); });

function renderStatus(s) {
  $("#station").textContent = `${s.station.call || "(no call)"} ${s.station.grid || ""}`;
  const w = $("#wsjtx");
  const viaFile = !s.wsjtx.connected && s.wsjtx.last_decode_age !== null && s.wsjtx.last_decode_age < 120;
  w.textContent = s.wsjtx.connected ? `WSJT-X ${s.wsjtx.band || ""} ${s.wsjtx.mode || ""}`
    : viaFile ? `WSJT-X ${s.wsjtx.band || ""} ${s.wsjtx.mode || ""} (ALL.TXT, no highlighting)` : "WSJT-X: no decodes";
  w.className = "pill " + (s.wsjtx.connected || viaFile ? "ok" : "bad");
  $("#utc").textContent = s.utc;
  const dl = $("#status");
  dl.replaceChildren();
  const add = (k, v) => dl.append(el("dt", k), el("dd", v));
  add("Listening", s.wsjtx.listen);
  add("UDP packets", String(s.wsjtx.packets));
  add("Last decode", s.wsjtx.last_decode_age === null ? "never" : age(s.wsjtx.last_decode_age) + " ago");
  add("FCC calls", `${s.data.us_calls}` + (s.data.states_age_days !== null ? ` (${s.data.states_age_days} d old)` : " — not downloaded yet"));
  add("LoTW users", `${s.data.lotw_users}` + (s.data.lotw_age_days !== null ? ` (${s.data.lotw_age_days} d old)` : ""));
  add("DXCC (cty.dat)", String(s.data.dxcc_entities));
  const ev = $("#events");
  ev.replaceChildren();
  for (const e of s.events) {
    const t = new Date(e.t * 1000).toISOString().slice(11, 16) + "z ";
    ev.append(el("li", t + e.text));
  }
}

async function refresh() {
  try {
    const s = await api("/api/status");
    colors = s.colors;
    renderStatus(s);
    renderAbout(s.about);
    renderSettings(s.station);
    renderHeard(s.heard);
    renderBands(s);
    for (const [k, a] of Object.entries(s.awards)) renderAward(k, a);
  } catch (e) {
    const w = $("#wsjtx");
    w.textContent = "daemon unreachable";
    w.className = "pill bad";
  }
}

$("#refresh-data").addEventListener("click", async (ev) => {
  try { await api("/api/refresh-data", {}); ev.target.textContent = "Refresh started…"; } catch (e) { alert(e.message); }
});
refresh();
setInterval(refresh, 3000);
