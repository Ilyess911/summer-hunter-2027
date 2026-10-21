"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }

const state = { view: "new", q: "", items: [] };
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const DECISION = { ELIGIBLE: ["Éligible", "ok"], NEEDS_VERIFICATION: ["À vérifier", "warn"], REJECTED: ["Rejeté", "bad"] };
const REASON_FR = { cold: "Trop de cold calling", role: "Ce type de poste ne m'intéresse pas", company: "Cette entreprise ne m'intéresse pas",
  country: "Ce pays ne m'intéresse pas", other: "Autre raison" };
const ICON = { PASS: "✅", FAIL: "❌", UNKNOWN: "❔", NOT_APPLICABLE: "➖" };
const LABEL = { internship_status: "Type de contrat", dates: "Dates", duration: "Durée", country: "Pays", safety: "Sécurité",
  language: "Langues", academic: "Diplôme", graduation: "Date de diplôme", citizenship: "Nationalité",
  work_authorization: "Autorisation de travail", qualifications: "Qualifications", compensation: "Rémunération" };

async function api(path, opts = {}) {
  const headers = { "X-Telegram-Init-Data": tg ? tg.initData : "", ...(opts.body ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 2600);
}

function chip(text, cls = "") { return `<span class="chip ${cls}">${esc(text)}</span>`; }

function row(o) {
  const [d, cls] = DECISION[o.decision] || ["?", ""];
  const prio = o.priority == null ? "" : chip(`prio ${Math.round(o.priority)}`);
  return `<button class="item" data-id="${o.id}">
    <div class="t">${esc(o.title)}</div>
    <div class="s">${esc(o.company)} · ${esc(o.country)}</div>
    <div class="chips">${chip(`fit ${o.fit ?? "?"}`)}${prio}${chip(d, cls)}${o.user_status !== "new" ? chip(o.user_status) : ""}</div>
  </button>`;
}

async function loadList() {
  const list = $("#list");
  list.innerHTML = `<div class="empty">Chargement…</div>`;
  try {
    const data = await api(`/api/opportunities?view=${state.view}&q=${encodeURIComponent(state.q)}`);
    state.items = data.items;
    list.innerHTML = data.items.length ? data.items.map(row).join("") : `<div class="empty">Rien ici pour l'instant.</div>`;
  } catch (e) {
    list.innerHTML = `<div class="empty">Impossible de charger : ${esc(e.message)}.<br>Ouvre l'app depuis le bouton du bot.</div>`;
  }
}

function section(title, html) { return html ? `<h2>${esc(title)}</h2>${html}` : ""; }
function bullets(arr) { return arr && arr.length ? `<ul class="plain">${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""; }

async function openDetail(id) {
  $("#list").hidden = true; $(".top").hidden = true;
  const box = $("#detail"); box.hidden = false; box.innerHTML = `<div class="empty">Chargement…</div>`;
  if (tg) { tg.BackButton.show(); }
  history.replaceState(null, "", `#/o/${id}`);
  let o;
  try { o = await api(`/api/opportunities/${id}`); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const fit = o.fit_report || {}, net = o.networking_report || {};
  const [d, cls] = DECISION[o.decision] || ["?", ""];
  const unknowns = o.criteria.filter((c) => c.status !== "PASS" && c.status !== "NOT_APPLICABLE")
    .map((c) => `${LABEL[c.criterion] || c.criterion} : ${c.explanation}`);
  const crit = o.criteria.map((c) => `<div class="crit"><div>${ICON[c.status] || "?"}</div><div>
      <div class="n">${esc(LABEL[c.criterion] || c.criterion)}</div><div>${esc(c.explanation)}</div>
      ${c.evidence && c.evidence.length ? `<div class="e">« ${esc(c.evidence[0])} »</div>` : ""}</div></div>`).join("");
  const contacts = o.contacts.map((c) => `<li>${c.url ? `<a href="#" data-link="${esc(c.url)}">${esc(c.name)}</a>` : esc(c.name)}
      <span class="muted">· ${esc(c.relationship.replaceAll("_", " "))} · ${Math.round(c.confidence * 100)} %</span></li>`).join("");
  box.innerHTML = `
    <h1>${esc(o.title)}</h1>
    <div class="muted">${esc(o.company)} · ${esc(o.location || o.country)}</div>
    <div class="chips">${chip(d, cls)}${o.family ? chip(o.family.replaceAll("_", " ")) : ""}${o.user_status !== "new" ? chip(o.user_status) : ""}</div>
    <div class="scores">
      <div class="score"><b>${o.fit ?? "?"}</b><span>Fit carrière</span></div>
      <div class="score"><b>${o.networking ?? "?"}</b><span>Networking</span></div>
      <div class="score"><b>${o.priority == null ? "—" : Math.round(o.priority)}</b><span>Priorité</span></div>
    </div>
    <div class="actions">
      <button class="btn primary" data-act="apply">Postuler sur le site</button>
      <button class="btn primary" data-act="cv">Générer mon CV adapté</button>
      <div class="cv" id="cvbox" style="grid-column:1/-1"></div>
      <button class="btn ${o.user_status === "saved" ? "on" : ""}" data-act="save">Sauver</button>
      <button class="btn ${o.user_status === "applied" ? "on" : ""}" data-act="applied">J'ai postulé</button>
      <button class="btn" data-act="dismiss" style="grid-column:1/-1">Écarter</button>
      <div class="reasons" id="reasons" hidden style="grid-column:1/-1">
        ${Object.entries(o.dismiss_reasons).map(([k, v]) => `<button class="btn" data-reason="${k}">${esc(REASON_FR[k] || v)}</button>`).join("")}
      </div>
    </div>
    ${section("Pourquoi ça colle", bullets(fit.reasons))}
    ${section("À vérifier", bullets(unknowns.slice(0, 5).concat((fit.weaknesses || []).filter((w) => !w.startsWith("Unverified")))))}
    ${section("Contact", contacts ? `<ul class="plain">${contacts}</ul>` : `<div class="muted">${esc(net.rationale || "Aucun contact vérifié.")}</div>`)}
    ${o.linkedin_post_url ? `<p><a href="#" data-link="${esc(o.linkedin_post_url)}">Voir le post LinkedIn</a></p>` : ""}
    ${section("Éligibilité, critère par critère", crit)}
    ${section("Score détaillé", bullets(Object.entries(fit.components || {}).map(([k, v]) => `${k.replaceAll("_", " ")} : ${Math.round(v)}`)
      .concat((fit.adjustments || []).map((a) => `${a.label} (${a.delta > 0 ? "+" : ""}${a.delta})`))))}
    <details><summary>Description de l'offre</summary><div class="desc">${esc(o.description)}</div></details>
    <p class="muted">Sources : ${o.sources.map((s) => esc(s.source)).join(", ")}</p>`;

  box.querySelectorAll("[data-link]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); openLink(a.dataset.link); }));
  box.querySelector('[data-act="apply"]').onclick = () => openLink(o.apply_url);
  box.querySelector('[data-act="save"]').onclick = () => act(o.id, "save", "Sauvé");
  box.querySelector('[data-act="applied"]').onclick = () => act(o.id, "applied", "Marqué comme postulé");
  box.querySelector('[data-act="dismiss"]').onclick = async () => { await act(o.id, "dismiss", "Écarté"); $("#reasons").hidden = false; };
  box.querySelectorAll("[data-reason]").forEach((b) => b.onclick = async () => { await act(o.id, "reason", "Merci, préférence mise à jour", b.dataset.reason); $("#reasons").hidden = true; });
  box.querySelector('[data-act="cv"]').onclick = (ev) => makeCV(o.id, ev.currentTarget);
}

function openLink(url) { if (!url) return; if (tg) tg.openLink(url); else window.open(url, "_blank", "noopener"); }

async function act(id, action, msg, reason) {
  try {
    await api(`/api/opportunities/${id}/action`, { method: "POST", body: JSON.stringify({ action, reason }) });
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    toast(msg);
  } catch (e) { toast(`Erreur : ${e.message}`); }
}

async function makeCV(id, btn) {
  btn.disabled = true; btn.textContent = "Génération du CV…";
  const box = $("#cvbox");
  try {
    const r = await api(`/api/opportunities/${id}/cv`, { method: "POST" });
    const dl = (path, name) => `<button class="btn" data-dl="${esc(path)}" data-name="${esc(name)}">${esc(name)}</button>`;
    box.innerHTML = (r.files.pdf ? dl(r.files.pdf, "Télécharger le PDF") : `<div class="muted">PDF non compilé : ${esc(r.log)}</div>`)
      + dl(r.files.tex, "Télécharger le code LaTeX")
      + `<button class="btn" id="sendtg">Recevoir les fichiers dans Telegram</button>`;
    box.querySelectorAll("[data-dl]").forEach((b) => b.onclick = () => download(b.dataset.dl));
    $("#sendtg").onclick = async (ev) => {
      ev.currentTarget.disabled = true;
      try { await api(`/api/opportunities/${id}/cv/send`, { method: "POST" }); toast("Envoyé dans le chat"); }
      catch (e) { toast(`Erreur : ${e.message}`); }
      ev.currentTarget.disabled = false;
    };
    btn.textContent = "Régénérer le CV";
  } catch (e) { box.innerHTML = `<div class="muted">Erreur : ${esc(e.message)}</div>`; btn.textContent = "Générer mon CV adapté"; }
  btn.disabled = false;
}

function download(path) {
  const url = new URL(path, location.origin).toString();
  const name = decodeURIComponent(url.split("/").pop().split("?")[0]);
  if (tg && tg.downloadFile && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0")) tg.downloadFile({ url, file_name: name });
  else openLink(url);
}

function closeDetail() {
  $("#detail").hidden = true; $("#list").hidden = false; $(".top").hidden = false;
  if (tg) tg.BackButton.hide();
  history.replaceState(null, "", "#");
  loadList();
}

$("#tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-view]"); if (!b) return;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  state.view = b.dataset.view; loadList();
});
$("#list").addEventListener("click", (ev) => { const it = ev.target.closest(".item"); if (it) openDetail(it.dataset.id); });
let t; $("#search").addEventListener("input", (ev) => { clearTimeout(t); t = setTimeout(() => { state.q = ev.target.value; loadList(); }, 250); });
if (tg) tg.BackButton.onClick(closeDetail);

const start = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";
const m = location.hash.match(/#\/o\/(\d+)/) || start.match(/^o(\d+)$/);
if (m) openDetail(m[1]); else loadList();
