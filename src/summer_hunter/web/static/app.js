"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready(); tg.expand();
  try { tg.setHeaderColor("bg_color"); tg.setBackgroundColor("bg_color"); } catch (_) { /* older clients */ }
  document.documentElement.dataset.scheme = tg.colorScheme || "dark";
  tg.onEvent && tg.onEvent("themeChanged", () => { document.documentElement.dataset.scheme = tg.colorScheme; });
}

const state = { view: "new", q: "", region: "", track: "", current: null };
const $ = (s, root = document) => root.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const DECISION = { ELIGIBLE: ["Éligible", "ok"], NEEDS_VERIFICATION: ["À vérifier", "warn"], REJECTED: ["Rejeté", "bad"] };
const STATUS = { saved: "Sauvé", applied: "Postulé", dismissed: "Écarté" };
const FAMILY = { technical_sales: "Vente technique", enterprise_sales: "Vente grands comptes", product_marketing: "Product marketing",
  go_to_market: "Go-to-market", sales_development: "Prospection", sales_general: "Vente", product_management: "Produit",
  ai_solutions: "Solutions IA", program_management: "Programme", commercial_strategy: "Stratégie commerciale",
  strategy_consulting: "Stratégie", marketing_general: "Marketing", data_analytics: "Data",
  maintenance_reliability: "Maintenance", industrial_project_management: "Projets industriels", robotics_raas: "Robotique / RaaS",
  robotics_specialized: "Robotique (ingénierie)", industrial_operations: "Opérations industrielles",
  industrial_digital: "Industrie 4.0" };
const CRIT = { internship_status: "Type de contrat", dates: "Dates", duration: "Durée", country: "Pays", safety: "Sécurité",
  language: "Langues", academic: "Diplôme", graduation: "Date de diplôme", citizenship: "Nationalité",
  work_authorization: "Autorisation de travail", qualifications: "Qualifications", compensation: "Rémunération",
  technical_requirements: "Exigences techniques" };
const COMP = { career_alignment: "Alignement carrière", experience_relevance: "Expérience", mission_quality: "Mission",
  skill_development: "Compétences", long_term_value: "Valeur long terme" };
const REASON = { cold: "Trop de cold calling", role: "Ce type de poste", company: "Cette entreprise", country: "Ce pays", other: "Autre raison" };
const DOT = { PASS: "✓", FAIL: "✕", UNKNOWN: "?", NOT_APPLICABLE: "–" };
const ICONS = {
  bookmark: '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M4 12.5 9.5 18 20 6"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
};

const flag = (cc) => (cc && /^[A-Z]{2}$/.test(cc)) ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "🌐";
const ringClass = (fit) => (fit >= 85 ? "good" : fit >= 70 ? "mid" : "");
const ring = (fit, big = false) => `<div class="ring ${ringClass(fit)} ${big ? "lg" : ""}" style="--v:${fit || 0}"><b>${fit ?? "–"}</b></div>`;
const haptic = (kind) => { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(kind || "light"); } catch (_) {} };

async function api(path, opts = {}) {
  const headers = { "X-Telegram-Init-Data": tg ? tg.initData : "", ...(opts.body ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 2400);
}

const day = (iso) => { try { return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" }); } catch (_) { return ""; } };

function card(o, i) {
  const [d, cls] = DECISION[o.decision] || ["?", ""];
  const place = [o.city, o.country].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(", ");
  return `<button class="card press" data-id="${o.id}" style="--i:${i}">
    ${ring(o.fit)}
    <div class="body">
      <div class="top"><span class="company">${esc(o.company)}</span>${o.discovered ? `<span class="when">${esc(day(o.discovered))}</span>` : ""}</div>
      <div class="title">${esc(o.title)}</div>
      <div class="meta">${flag(o.country_code)} ${esc(place || o.location || "Lieu inconnu")}</div>
      <div class="tags">
        ${o.family && FAMILY[o.family] ? `<span class="tag">${esc(FAMILY[o.family])}</span>` : ""}
        <span class="tag ${cls}">${esc(d)}</span>
        ${STATUS[o.user_status] ? `<span class="tag state">${STATUS[o.user_status]}</span>` : ""}
        ${o.status === "closed" ? `<span class="tag bad">Fermée</span>` : (o.open === false ? `<span class="tag bad">Date limite passée</span>` : "")}
        ${o.open && o.deadline ? `<span class="tag warn">Avant le ${esc(day(o.deadline))}</span>` : ""}
      </div>
    </div>
  </button>`;
}

async function loadList() {
  document.body.classList.toggle("cvmode", state.view === "cv" || state.view === "founders");
  if (state.view === "cv") return renderCV();
  if (state.view === "founders") return renderFounders();
  const list = $("#list");
  list.classList.remove("stagger");
  list.innerHTML = Array.from({ length: 5 }, () => `<div class="skeleton"></div>`).join("");
  $("#count").textContent = "";
  try {
    const params = new URLSearchParams({ view: state.view, q: state.q, region: state.region, track: state.track });
    const data = await api(`/api/opportunities?${params}`);
    const s = data.stats || {};
    $("#s-target").textContent = s.target ?? "–"; $("#s-new").textContent = s.new ?? "–";
    $("#s-saved").textContent = s.saved ?? "–"; $("#s-applied").textContent = s.applied ?? "–";
    const n = data.items.length;
    $("#count").textContent = n ? `${n} offre${n > 1 ? "s" : ""}` : "";
    list.innerHTML = n ? data.items.map(card).join("")
      : `<div class="empty"><b>Rien ici pour l'instant</b>Les nouvelles offres arrivent chaque nuit.</div>`;
    void list.offsetWidth; list.classList.add("stagger");
  } catch (e) {
    list.innerHTML = `<div class="empty">Chargement impossible : ${esc(e.message)}<br>Rouvre l'app depuis le bouton du bot.</div>`;
  }
}

/* Detail ------------------------------------------------------------------ */

const ACTION = { APPLY_NOW: "Postuler maintenant", PREPARE_APPLICATION: "Préparer la candidature", CONTACT_RECRUITER: "Contacter le recruteur",
  VERIFY_ELIGIBILITY: "Vérifier l'éligibilité", VERIFY_COMPENSATION: "Vérifier la rémunération", MONITOR_OPENING: "Surveiller",
  LOW_PRIORITY: "Priorité basse", DISCARD: "Écarter" };
const PAY = { PAID_CONFIRMED: "Payé (montant indiqué)", PAID_AMOUNT_UNKNOWN: "Payé, montant non indiqué", UNPAID_CONFIRMED: "Non payé",
  MIXED_OR_CONDITIONAL: "Conditionnel", COMPENSATION_UNKNOWN: "Non indiqué (≠ non payé)" };
function payBlock(o) {
  const c = o.compensation_report; if (!c) return "";
  const eur = (v) => `${Math.round(v).toLocaleString("fr-FR")} €`;
  const rows = [`<div><b>${esc(PAY[c.status] || c.status)}</b> · confiance ${esc(c.confidence)}</div>`];
  if (c.monthly_min_eur != null) rows.push(`<div>${eur(c.monthly_min_eur)}${c.monthly_max_eur && c.monthly_max_eur !== c.monthly_min_eur ? " – " + eur(c.monthly_max_eur) : ""} / mois${c.monthly_is_estimate ? " (estimation)" : ""}</div>`);
  if (c.monthly_balance_eur != null) rows.push(`<div>Solde estimé : ${eur(c.monthly_balance_eur)} / mois</div>`);
  if (c.pay && c.pay.wording) rows.push(`<div class="x">« ${esc(c.pay.wording)} »</div>`);
  return rows.join("");
}

function block(title, html) { return html ? `<div class="block"><h3>${esc(title)}</h3>${html}</div>` : ""; }
function list(items) { return items && items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""; }

async function openDetail(id) {
  $("#home").hidden = true;
  const box = $("#detail"); box.hidden = false;
  box.classList.remove("leave"); box.classList.add("enter");
  box.innerHTML = `<div class="skeleton" style="height:140px"></div>`;
  window.scrollTo(0, 0);
  if (tg) tg.BackButton.show();
  history.replaceState(null, "", `#/o/${id}`);
  let o;
  try { o = await api(`/api/opportunities/${id}`); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  state.current = o;
  const fit = o.fit_report || {}, net = o.networking_report || {};
  const [d, cls] = DECISION[o.decision] || ["?", ""];
  const open = o.criteria.filter((c) => c.status === "UNKNOWN" || c.status === "FAIL");
  const checks = o.criteria.map((c) => `<div class="check"><span class="dot ${c.status}">${DOT[c.status] || "?"}</span>
      <div><div class="n">${esc(CRIT[c.criterion] || c.criterion)}</div><div class="x">${esc(c.explanation)}</div></div></div>`).join("");
  const bars = Object.entries(fit.components || {}).map(([k, v]) =>
    `<div class="bar"><span>${esc(COMP[k] || k)}</span><i style="--w:${Math.max(0, Math.min(100, v)) / 100}"></i><span>${Math.round(v)}</span></div>`).join("");
  const contacts = o.contacts.map((c) => `<li>${c.url ? `<a href="#" data-link="${esc(c.url)}">${esc(c.name)}</a>` : esc(c.name)}
      <span class="x">· ${esc(c.relationship.replaceAll("_", " "))}</span></li>`).join("");

  box.innerHTML = `
    <div class="d-head">
      <div>
        <div class="d-company">${esc(o.company)}</div>
        <h2 class="d-title">${esc(o.title)}</h2>
        <div class="d-place">${flag(o.country_code)} ${esc(o.location || o.country)}</div>
        <div class="tags" style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
          ${o.family && FAMILY[o.family] ? `<span class="tag">${esc(FAMILY[o.family])}</span>` : ""}
          <span class="tag ${cls}">${esc(d)}</span>
          ${o.status === "closed" ? `<span class="tag bad">Offre fermée</span>` : ""}
        </div>
      </div>
      ${ring(o.fit, true)}
    </div>
    <div class="d-metrics">
      <div class="metric"><b>${o.fit ?? "–"}</b><span>Fit carrière</span></div>
      <div class="metric"><b>${o.recruitability ?? "–"}</b><span>Recrutabilité</span></div>
      <div class="metric"><b>${o.networking ?? "–"}</b><span>Networking</span></div>
      <div class="metric"><b>${o.global == null ? "–" : Math.round(o.global)}${o.provisional ? "*" : ""}</b><span>Priorité</span></div>
    </div>
    <div class="row-actions">
      <button class="act ${o.user_status === "saved" ? "on" : ""}" data-act="save">${ICONS.bookmark}Sauver</button>
      <button class="act ${o.user_status === "applied" ? "on" : ""}" data-act="applied">${ICONS.check}Postulé</button>
      <button class="act ${o.user_status === "dismissed" ? "on" : ""}" data-act="dismiss">${ICONS.x}Écarter</button>
    </div>
    <div class="reasons" id="reasons" hidden>
      ${Object.entries(REASON).map(([k, v]) => `<button data-reason="${k}">${esc(v)}</button>`).join("")}
    </div>
    ${(o.paste_skills || []).length ? `<div class="block skills-paste"><h3>Skills à coller dans le formulaire</h3>
      <div class="tags" style="display:flex;gap:6px;flex-wrap:wrap">${o.paste_skills.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>
      <button class="file wide press" id="skillscopy" style="margin-top:10px"><span>📋 Copier les skills</span><small>${o.paste_skills.length} skills, séparés par des virgules</small></button></div>` : ""}
    <div id="cvbox"></div>
    <button class="file wide press" id="letterbtn"><span>✉️ Générer ma lettre de motivation</span><small>2-3 min, reçue dans le chat</small></button>
    <div class="fallback" id="fallback" ${tg && tg.MainButton ? "hidden" : ""}>
      <button class="primary press" data-act="cv">Générer mon CV adapté</button>
      <button class="primary ghost press" data-act="apply">Postuler sur le site</button>
    </div>
    ${o.strategy_report ? block("Recommandation", `<div><b>${esc(ACTION[o.strategy_report.action] || o.strategy_report.action)}</b>`
        + `${o.strategy_report.caveat ? ` · ${esc(o.strategy_report.caveat)}` : ""}</div>`
        + list([...(o.strategy_report.arguments || []), o.strategy_report.effort ? `Préparation : ${o.strategy_report.effort}` : ""].filter(Boolean))) : ""}
    ${block("Rémunération", payBlock(o))}
    ${block("Pourquoi ça colle", list(fit.reasons))}
    ${o.recruitability_report ? block("Recrutabilité (correspondance avec ton expérience, pas une probabilité)",
        list([...(o.recruitability_report.reasons || []), ...(o.recruitability_report.missing_requirements || []).map((m) => `À combler : ${m}`)])) : ""}
    ${block("Points à vérifier", list(open.map((c) => `${CRIT[c.criterion] || c.criterion} : ${c.explanation}`).slice(0, 5)))}
    ${block("Contact", contacts ? `<ul>${contacts}</ul>` : `<div class="x">${esc(net.rationale || "Aucun contact vérifié.")}</div>`)}
    ${o.linkedin_post_url ? block("Post LinkedIn", `<a href="#" data-link="${esc(o.linkedin_post_url)}">Ouvrir le post</a>`) : ""}
    ${block("Score détaillé", bars + list((fit.adjustments || []).map((a) => `${a.label} (${a.delta > 0 ? "+" : ""}${a.delta})`)))}
    ${block("Éligibilité", checks)}
    <div class="block"><details class="desc"><summary>Description de l'offre</summary><div>${esc(o.description)}</div></details></div>
    <div class="src">Sources : ${o.sources.map((s) => esc(s.source)).join(", ")}</div>`;

  box.querySelectorAll("[data-link]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); openLink(a.dataset.link); }));
  box.querySelector('[data-act="save"]').onclick = (ev) => act(o.id, "save", "Sauvé", ev.currentTarget);
  box.querySelector('[data-act="applied"]').onclick = async (ev) => {
    const btn = ev.currentTarget;
    if (o.user_status === "applied") return toast("Déjà enregistrée comme envoyée");
    if (!(await confirmAsk("Ta candidature est bien envoyée ? Elle sera datée d'aujourd'hui dans le CRM."))) return;
    act(o.id, "applied", "Postulé : l'offre quitte ta liste", btn);
  };
  box.querySelector('[data-act="dismiss"]').onclick = async (ev) => { await act(o.id, "dismiss", "Écarté", ev.currentTarget); $("#reasons").hidden = false; };
  box.querySelectorAll("[data-reason]").forEach((b) => b.onclick = async () => {
    await act(o.id, "reason", "Merci, préférence mise à jour", null, b.dataset.reason); $("#reasons").hidden = true; });
  box.querySelector("#letterbtn").onclick = async (ev) => {
    const b = ev.currentTarget; b.disabled = true;
    try { await api(`/api/opportunities/${o.id}/letter`, { method: "POST", body: "{}" }); haptic("medium");
      toast("Lettre en cours : tu la reçois dans le chat dans 2-3 min"); b.querySelector("span").textContent = "✉️ Lettre en cours…"; }
    catch (e) { toast(`Erreur : ${e.message}`); b.disabled = false; }
  };
  const skBtn = box.querySelector("#skillscopy");
  if (skBtn) skBtn.onclick = async () => { const done = await copyText(Promise.resolve(o.paste_skills.join(", "))); haptic();
    toast(done ? "Skills copiés" : "Copie impossible : sélectionne le texte"); };
  const cvBtn = box.querySelector('#fallback [data-act="cv"]'); if (cvBtn) cvBtn.onclick = () => makeCV(o.id);
  const apBtn = box.querySelector('#fallback [data-act="apply"]'); if (apBtn) apBtn.onclick = () => openLink(o.apply_url);
  showNativeButtons(o);
}

function showNativeButtons(o) {
  if (!tg || !tg.MainButton) return;
  tg.MainButton.setParams({ text: "Générer mon CV adapté", is_visible: true, is_active: true });
  tg.MainButton.offClick(onMain); tg.MainButton.onClick(onMain);
  if (tg.SecondaryButton) {
    tg.SecondaryButton.setParams({ text: "Postuler sur le site", is_visible: true, position: "top" });
    tg.SecondaryButton.offClick(onSecondary); tg.SecondaryButton.onClick(onSecondary);
  } else {
    $("#fallback").hidden = false; $('#fallback [data-act="cv"]').hidden = true;
  }
}
function onMain() { if (state.current) makeCV(state.current.id); }
function onSecondary() { if (state.current) openLink(state.current.apply_url); }
function hideNativeButtons() {
  if (!tg) return;
  tg.MainButton.hide(); tg.MainButton.hideProgress && tg.MainButton.hideProgress();
  if (tg.SecondaryButton) tg.SecondaryButton.hide();
}

function confirmAsk(message) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) tg.showConfirm(message, (ok) => resolve(!!ok));
    else resolve(window.confirm(message));
  });
}

function openLink(url) { if (!url) return; if (tg) tg.openLink(url); else window.open(url, "_blank", "noopener"); }

async function act(id, action, msg, btn, reason) {
  try {
    const r = await api(`/api/opportunities/${id}/action`, { method: "POST", body: JSON.stringify({ action, reason }) });
    haptic("medium"); toast(msg);
    document.querySelectorAll(".row-actions .act").forEach((b) => b.classList.toggle("on", r.user_status === { save: "saved", applied: "applied", dismiss: "dismissed" }[b.dataset.act]));
  } catch (e) { toast(`Erreur : ${e.message}`); }
}

async function makeCV(id) {
  const box = $("#cvbox");
  if (tg && tg.MainButton) tg.MainButton.showProgress(false);
  box.innerHTML = `<div class="block"><h3>CV adapté</h3><div class="x">Génération et compilation…</div></div>`;
  try {
    const r = await api(`/api/opportunities/${id}/cv`, { method: "POST" });
    const file = (path, label, sub) => `<button class="file" data-dl="${esc(path)}"><span>${esc(label)}</span><small>${esc(sub)}</small></button>`;
    box.innerHTML = `<div class="block"><h3>CV adapté</h3><div class="cv-files">
      ${r.files.pdf ? file(r.files.pdf, "Télécharger le PDF", "PDF") : `<div class="x">PDF non compilé : ${esc(r.log)}</div>`}
      ${file(r.files.tex, "Télécharger le code LaTeX", ".tex")}
      <button class="file" id="sendtg"><span>Recevoir dans le chat</span><small>Telegram</small></button></div></div>`;
    box.querySelectorAll("[data-dl]").forEach((b) => b.onclick = () => download(b.dataset.dl));
    $("#sendtg").onclick = async (ev) => {
      ev.currentTarget.disabled = true;
      try { await api(`/api/opportunities/${id}/cv/send`, { method: "POST" }); haptic("medium"); toast("Envoyé dans le chat"); }
      catch (e) { toast(`Erreur : ${e.message}`); }
      ev.currentTarget.disabled = false;
    };
    if (tg && tg.MainButton) tg.MainButton.setText("Régénérer le CV");
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (e) { box.innerHTML = `<div class="block"><h3>CV adapté</h3><div class="x">Erreur : ${esc(e.message)}</div></div>`; }
  if (tg && tg.MainButton) tg.MainButton.hideProgress();
}

function download(path) {
  const url = new URL(path, location.origin).toString();
  const name = decodeURIComponent(url.split("/").pop().split("?")[0]);
  if (tg && tg.downloadFile && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0")) tg.downloadFile({ url, file_name: name });
  else openLink(url);
}

function closeDetail() {
  const box = $("#detail");
  const finish = () => {
    box.hidden = true; box.classList.remove("leave", "enter");
    const home = $("#home"); home.hidden = false; home.classList.remove("enter"); void home.offsetWidth; home.classList.add("enter");
  };
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) finish();
  else { box.classList.remove("enter"); box.classList.add("leave"); setTimeout(finish, 190); }
  state.current = null;
  if (tg) tg.BackButton.hide();
  hideNativeButtons();
  history.replaceState(null, "", "#");
  loadList();
}

/* CV tab: AI CV Builder ---------------------------------------------------- */

const cvState = { lang: "", pages: "", current: null, busy: false };
const LANG = { en: "anglais", fr: "français" };

function chipRow(id, items, on) {
  return `<div class="chips-row" id="${id}">${items.map(([v, label]) =>
    `<button data-v="${esc(v)}" class="${v === on ? "on" : ""}">${esc(label)}</button>`).join("")}</div>`;
}

function renderCV() {
  const list = $("#list");
  list.classList.remove("stagger");
  list.innerHTML = `
    <section class="block cv-new">
      <h3>Nouveau CV</h3>
      <textarea id="cvtext" class="cv-input" rows="4" maxlength="20000"
        placeholder="Colle une offre, ou écris simplement : stage AI Business Development à Singapour"></textarea>
      ${chipRow("cvlang", [["", "Langue auto"], ["en", "Anglais"], ["fr", "Français"]], cvState.lang)}
      ${chipRow("cvpages", [["", "Pages auto"], ["1", "1 page"], ["2", "2 pages"]], cvState.pages)}
      <button class="primary press cv-go" id="cvgo">Générer le CV</button>
    </section>
    <div id="cvres"></div>
    <section class="block"><h3>Profil</h3><div id="cvprofile"><div class="x">Chargement…</div></div></section>
    <section class="block"><h3>Historique</h3><div id="cvhist"><div class="x">Chargement…</div></div></section>`;
  const pick = (id, key) => $(`#${id}`).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $(`#${id}`).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    cvState[key] = b.dataset.v; haptic();
  });
  pick("cvlang", "lang"); pick("cvpages", "pages");
  $("#cvgo").onclick = createCV;
  if (cvState.current) showVersion(cvState.current);
  loadCVProfile(); loadCVHistory();
}

function busy(msg) {
  cvState.busy = true;
  $("#cvres").innerHTML = `<section class="block cv-result"><div class="cv-wait"><span class="spin"></span>${esc(msg)}</div></section>`;
  $("#cvres").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function createCV() {
  if (cvState.busy) return;
  const text = $("#cvtext").value.trim();
  if (!text) { toast("Décris le CV voulu ou colle une offre"); return; }
  const btn = $("#cvgo"); btn.disabled = true; haptic("medium");
  busy("Analyse et génération… quelques secondes");
  try {
    const v = await api("/api/cv", { method: "POST", body: JSON.stringify({ text, language: cvState.lang, pages: cvState.pages }) });
    showVersion(v); toast(`${v.tag} prêt`); loadCVHistory();
  } catch (e) { $("#cvres").innerHTML = `<section class="block"><div class="x">Erreur : ${esc(e.message)}</div></section>`; }
  cvState.busy = false; btn.disabled = false;
}

function showVersion(v) {
  cvState.current = v;
  const meta = [v.company, v.location, LANG[v.language] || v.language, v.pages ? `${v.pages} page${v.pages > 1 ? "s" : ""}` : ""]
    .filter(Boolean).join(" · ");
  const report = (v.report || []).map((r) => r.label === "Note" ? `<li>${esc(r.text)}</li>`
    : `<li><b>${esc(r.label)}</b> : ${esc(r.text)}</li>`).join("");
  $("#cvres").innerHTML = `
    <section class="block cv-result">
      <div class="cv-head">
        <div><div class="d-company">${esc(v.tag)}${v.change ? ` · ${esc(v.change)}` : ""}</div>
          <div class="cv-role">${esc(v.role || "CV")}</div><div class="x">${esc(meta)}</div></div>
        <span class="tag ${v.ok ? "ok" : "warn"}">${v.ok ? "Contrôles OK" : "À vérifier"}</span>
      </div>
      <div class="cv-files">
        ${v.files.pdf ? `<button class="file press" data-dl="${esc(v.files.pdf)}"><span>📄 Télécharger le PDF</span><small>PDF</small></button>` : ""}
        ${v.files.docx ? `<button class="file press" data-dl="${esc(v.files.docx)}"><span>📝 Télécharger le DOCX</span><small>Word</small></button>` : ""}
        <button class="file press" id="cvsend"><span>💬 Recevoir dans le chat</span><small>Telegram</small></button>
        <button class="file press" id="cvmail"><span>✉️ Générer le mail de candidature</span><small>copié, jamais envoyé</small></button>
      </div>
      <div id="cvmailbox"></div>
      <div class="row-actions cv-acts">
        <button class="act ${v.saved ? "on" : ""}" id="cvsave">${ICONS.bookmark}${v.saved ? "Sauvegardée" : "Sauvegarder"}</button>
        <button class="act" id="cvregen"><svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>Regénérer</button>
        <button class="act" id="cvlangsw"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>${v.language === "en" ? "En français" : "En anglais"}</button>
      </div>
      <h3 class="cv-sub">Retouches</h3>
      ${chipRow("cvquick", [["1 page", "1 page"], ["2 pages", "2 pages"], ["version générique", "Générique"], ["plus personnalisé", "Ciblé"]], "")}
      <div class="cv-edit">
        <input id="cvedit" class="cv-input" enterkeyhint="send" placeholder="Ex. mets en avant Nexya, retire Amundi" autocomplete="off">
        <button class="primary press" id="cvapply">OK</button>
      </div>
      ${report ? `<h3 class="cv-sub">Rapport</h3><ul class="cv-report">${report}</ul>` : ""}
    </section>`;
  const box = $("#cvres");
  box.querySelectorAll("[data-dl]").forEach((b) => b.onclick = () => download(b.dataset.dl));
  $("#cvsend").onclick = async (ev) => {
    const b = ev.currentTarget; b.disabled = true;
    try { await api(`/api/cv/${v.id}/send`, { method: "POST", body: "{}" }); haptic("medium"); toast("Envoyé dans le chat"); }
    catch (e) { toast(`Erreur : ${e.message}`); }
    b.disabled = false;
  };
  $("#cvsave").onclick = async () => {
    try { const r = await api(`/api/cv/${v.id}/save`, { method: "POST", body: "{}" }); v.saved = r.saved; haptic();
      toast(r.saved ? "Version sauvegardée ⭐" : "Sauvegarde retirée"); showVersion(v); loadCVHistory(); }
    catch (e) { toast(`Erreur : ${e.message}`); }
  };
  $("#cvregen").onclick = () => runCV(`/api/cv/${v.id}/regenerate`, {}, "Régénération avec ton profil à jour…");
  $("#cvmail").onclick = () => mailCV(v, null);
  $("#cvlangsw").onclick = () => editCV(v.language === "en" ? "en français" : "en anglais");
  $("#cvquick").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) editCV(b.dataset.v); });
  const apply = () => { const t = $("#cvedit").value.trim(); if (t) editCV(t); };
  $("#cvapply").onclick = apply;
  $("#cvedit").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); apply(); } });
}

/* Cover email: drafted from the CV, copied to the clipboard, never sent. The clipboard write starts inside
   the tap (ClipboardItem with a pending blob) so WebKit's user-gesture rule still allows it after the fetch. */
function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove(); return ok;
}

async function copyText(textPromise) {
  try {
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": textPromise.then((t) => new Blob([t], { type: "text/plain" })) })]);
      return true;
    }
  } catch (_) { /* fall through */ }
  const text = await textPromise;
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* fall through */ }
  return legacyCopy(text);
}

async function mailCV(v, language, revise) {
  const box = $("#cvmailbox");
  const req = api(`/api/cv/${v.id}/email`, { method: "POST", body: JSON.stringify({ language, revise }) });
  const copied = copyText(req.then((m) => m.text));
  box.innerHTML = `<div class="cv-wait"><span class="spin"></span>${revise ? "Révision" : "Rédaction"} de l'email… (jusqu'à 3 min avec le modèle local)</div>`;
  let m;
  try { m = await req; } catch (e) { box.innerHTML = ""; toast(`Erreur : ${e.message}`); return; }
  const ok = await copied.catch(() => false);
  haptic("medium");
  toast(ok ? "Mail copié · rien n'est envoyé" : "Copie bloquée : touche « Copier »");
  const other = m.language === "fr" ? "en" : "fr";
  box.innerHTML = `
    <div class="mail">
      <div class="mail-subject"><span class="x">${m.language === "fr" ? "Objet :" : "Subject:"}</span> ${esc(m.subject)}</div>
      <pre class="mail-body">${esc(m.body)}</pre>
      ${m.warnings && m.warnings.length ? `<div class="x" style="margin-top:8px">⚠️ À relire : ${esc(m.warnings.join(" ; "))}</div>` : ""}
      <div class="chips-row" id="mailrev">${Object.entries(m.revisions || {}).map(([k, label]) =>
        `<button data-rev="${esc(k)}">${esc(label)}</button>`).join("")}</div>
      <div class="mail-btns">
        <button class="primary press" id="mailcopy">${ok ? "Recopier" : "Copier"}</button>
        <button class="primary ghost press" id="mailother">${other === "en" ? "En anglais" : "En français"}</button>
      </div>
    </div>`;
  $("#mailcopy").onclick = async () => { const done = await copyText(Promise.resolve(m.text)); haptic(); toast(done ? "Copié" : "Copie impossible : sélectionne le texte"); };
  $("#mailother").onclick = () => mailCV(v, other);
  $("#mailrev").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) { haptic(); mailCV(v, m.language, b.dataset.rev); } });
}

async function runCV(path, body, msg) {
  if (cvState.busy) return;
  haptic("medium"); busy(msg);
  try {
    const r = await api(path, { method: "POST", body: JSON.stringify(body) });
    const v = r.version || r;
    showVersion(v); toast(`${v.tag} prêt`); loadCVHistory();
    return r;
  } catch (e) {
    if (cvState.current) showVersion(cvState.current);
    toast(`Erreur : ${e.message}`);
  } finally { cvState.busy = false; }
}

async function editCV(text) {
  if (cvState.busy || !cvState.current) return;
  const v = cvState.current;
  haptic("medium"); busy(`Nouvelle version : ${text}…`);
  try {
    const r = await api(`/api/cv/${v.id}/edit`, { method: "POST", body: JSON.stringify({ text }) });
    if (r.version) { showVersion(r.version); toast(`${r.version.tag} prêt`); loadCVHistory(); }
    else { showVersion(v); }
    if (r.facts && r.facts.length) { toast("Fait proposé : confirme-le dans Profil"); loadCVProfile(); }
  } catch (e) {
    showVersion(v);
    toast(e.message.includes("non comprise") ? "Pas compris. Ex. : « en anglais », « 1 page », « mets en avant Nexya »" : `Erreur : ${e.message}`);
  } finally { cvState.busy = false; }
}

async function loadCVProfile() {
  const box = $("#cvprofile"); if (!box) return;
  try {
    const p = await api("/api/cv/profile");
    const pending = p.pending.map((f) => `<div class="fact" data-id="${f.id}">
        <div><div class="n">${esc(f.kind)}</div><div>${esc(f.text)}</div>${f.note ? `<div class="x">${esc(f.note)}</div>` : ""}</div>
        <div class="fact-btns"><button class="mini ok" data-d="ok" aria-label="Confirmer">✓</button>
          <button class="mini bad" data-d="no" aria-label="Faux">✕</button></div></div>`).join("");
    box.innerHTML = `
      ${pending ? `<div class="x" style="margin-bottom:8px">À confirmer : jamais affiché sur un CV avant ✓</div>${pending}`
        : `<div class="x">Rien à confirmer.</div>`}
      <div class="cv-edit" style="margin-top:12px">
        <input id="cvfact" class="cv-input" placeholder="Ajouter un fait : certification IBM, stage…" autocomplete="off">
        <button class="primary press" id="cvfactgo">+</button>
      </div>
      ${p.confirmed.length ? `<details class="desc" style="margin-top:12px"><summary>${p.confirmed.length} fait(s) confirmé(s)</summary>
        <div>${p.confirmed.map((f) => `• ${esc(f.kind)} : ${esc(f.text)}`).join("\n")}</div></details>` : ""}`;
    box.querySelectorAll(".fact").forEach((row) => row.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-d]"); if (!b) return;
      try { await api(`/api/cv/profile/${row.dataset.id}`, { method: "POST", body: JSON.stringify({ decision: b.dataset.d }) });
        haptic("medium"); toast(b.dataset.d === "ok" ? "Ajouté au profil" : "Retiré"); loadCVProfile(); }
      catch (e) { toast(`Erreur : ${e.message}`); }
    }));
    const add = async () => {
      const t = $("#cvfact").value.trim(); if (!t) return;
      try { const r = await api("/api/cv/profile", { method: "POST", body: JSON.stringify({ text: t }) });
        toast(r.created ? "Proposé : confirme-le avec ✓" : "Déjà dans ton profil"); loadCVProfile(); }
      catch (e) { toast(`Erreur : ${e.message}`); }
    };
    $("#cvfactgo").onclick = add;
    $("#cvfact").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  } catch (e) { box.innerHTML = `<div class="x">Profil indisponible : ${esc(e.message)}</div>`; }
}

async function loadCVHistory() {
  const box = $("#cvhist"); if (!box) return;
  try {
    const { items } = await api("/api/cv/history");
    box.innerHTML = items.length ? items.map((it) => `<div class="hist">
        <button class="file press" data-open="${it.id}"><span>${esc(it.tag)} · ${esc(it.role || "CV")}${it.saved ? " ⭐" : ""}</span>
          <small>${esc([it.company, it.location].filter(Boolean).join(" · ") || day(it.created))}</small></button>
        ${it.versions.length > 1 ? `<div class="chips-row vers">${it.versions.map((x) =>
          `<button data-open="${x.id}" title="${esc(x.change || "version initiale")}">v${x.version}${x.saved ? " ⭐" : ""}</button>`).join("")}</div>` : ""}
      </div>`).join("") : `<div class="x">Aucun CV pour l'instant.</div>`;
    box.querySelectorAll("[data-open]").forEach((b) => b.onclick = async () => {
      try { const v = await api(`/api/cv/${b.dataset.open}`); haptic(); showVersion(v);
        $("#cvres").scrollIntoView({ behavior: "smooth", block: "start" }); }
      catch (e) { toast(`Erreur : ${e.message}`); }
    });
  } catch (e) { box.innerHTML = `<div class="x">Historique indisponible : ${esc(e.message)}</div>`; }
}

/* Réseau tab: French founders abroad, verified on their source page, never contacted automatically ---- */

const fdState = { filter: "todo", poll: null };
const money = (v) => `${Number(v || 0).toFixed(2).replace(".", ",")} $`;

function fdCard(f, i) {
  const place = [f.city, f.country].filter(Boolean).join(", ");
  const links = [f.source && ["source", "Page source"], f.website && ["website", "Site"], f.linkedin && ["linkedin", "LinkedIn"]]
    .filter(Boolean).map(([k, label]) => `<button class="press" data-link="${k}">${label}</button>`).join("");
  const state = f.dismissed ? "Retiré de ta liste" : f.contacted ? "Contacté" : "";
  return `<article class="fd" data-id="${f.id}" style="--i:${i}">
    <div class="who"><span class="name">${esc(f.name)}</span>${f.today ? `<span class="new">Aujourd'hui</span>` : ""}</div>
    <div class="co">${esc(f.company)}</div>
    <div class="meta">${flag(f.country_code)} ${esc(place || "Lieu inconnu")}${f.city && !f.city_verified ? " · lieu à confirmer" : ""}${f.sector ? ` · ${esc(f.sector)}` : ""}${f.team_size ? ` · ${esc(f.team_size)}` : ""}</div>
    ${f.what ? `<p class="what">${esc(f.what)}</p>` : ""}
    ${f.why ? `<p class="why">${esc(f.why)}</p>` : ""}
    <div class="links">${links}</div>
    <div class="msg">${esc(f.message)}</div>
    <div class="msg-n">${f.message.length} / 300</div>
    ${state ? `<div class="done"><span>${state}</span><button data-fd="undo">Annuler</button></div>` : `
    <div class="fd-acts">
      <button class="primary press" data-fd="copy">Copier</button>
      <button class="primary ghost press" data-fd="sent">Contacté</button>
      <button class="primary ghost press" data-fd="no">Pas pour moi</button>
    </div>`}
  </article>`;
}

async function renderFounders() {
  const list = $("#list");
  list.classList.remove("stagger");
  if (!$("#fdbox")) {
    list.innerHTML = `<section class="block fd-head" id="fdhead"><div class="x">Chargement…</div></section>
      <div id="fdfilter"></div><div id="fdbox"></div>`;
  }
  let data;
  try { data = await api(`/api/founders?filter=${fdState.filter}`); }
  catch (e) { $("#fdbox").innerHTML = `<div class="empty">Chargement impossible : ${esc(e.message)}</div>`; return; }
  if (state.view !== "founders") return;
  const t = data.today, c = data.counts;
  const date = day(t.date);
  const status = t.running ? `<div class="cv-wait" style="margin-top:12px"><span class="spin"></span>Recherche en cours (1 à 3 min)…</div>`
    : t.can_run ? `<button class="primary press" id="fdrun">Lancer la recherche du jour</button>`
    : `<div class="x">${t.done ? `Liste du ${esc(date)} faite (${t.ids.length} vérifiés). Prochaine demain matin.` : !t.enabled ? "Désactivé." : "Plafond du jour atteint. Prochaine liste demain matin."}</div>`;
  $("#fdhead").innerHTML = `<div class="fd-row"><span class="fd-title">Fondateurs à contacter</span>
      <span class="fd-budget">${money(t.spent)} / ${money(t.cap)}</span></div>
    <div class="x">Zones du jour : ${esc(t.areas.join(", "))}</div>${status}`;
  $("#fdfilter").innerHTML = chipRow("fdchips", [["todo", `À contacter · ${c.todo}`], ["sent", `Contactés · ${c.sent}`], ["all", `Tous · ${c.all}`]], fdState.filter);
  $("#fdchips").onclick = (ev) => { const b = ev.target.closest("button"); if (!b) return; fdState.filter = b.dataset.v; haptic(); renderFounders(); };
  const box = $("#fdbox");
  box.innerHTML = data.items.length ? data.items.map(fdCard).join("") + `<p class="fd-note">Vérifié sur la page source : nom, entreprise et rôle de fondateur ou dirigeant. Le reste est un résumé IA à confirmer. Rien n'est envoyé à personne.</p>`
    : `<div class="empty"><b>${fdState.filter === "todo" ? "Personne à contacter" : "Rien ici"}</b>${fdState.filter === "todo" ? "La liste arrive chaque matin après le résumé." : ""}</div>`;
  const items = Object.fromEntries(data.items.map((f) => [String(f.id), f]));
  box.onclick = (ev) => {
    const el = ev.target.closest("[data-fd],[data-link]"); const card = ev.target.closest(".fd"); if (!el || !card) return;
    const f = items[card.dataset.id]; if (!f) return;
    if (el.dataset.link) { haptic(); openLink({ source: f.source, website: f.website, linkedin: f.linkedin }[el.dataset.link]); return; }
    fdAct(f, el.dataset.fd);
  };
  const run = $("#fdrun");
  if (run) run.onclick = async () => {
    run.disabled = true;
    try { await api("/api/founders/run", { method: "POST", body: "{}" }); haptic("medium"); toast("Recherche lancée"); }
    catch (e) { toast(e.message); }
    renderFounders();
  };
  clearTimeout(fdState.poll);
  if (t.running) fdState.poll = setTimeout(() => { if (state.view === "founders") renderFounders(); }, 8000);
}

async function fdAct(f, action) {
  if (action === "copy") {
    const ok = await copyText(Promise.resolve(f.message));
    haptic(); toast(ok ? "Message copié. Colle-le sur LinkedIn." : "Copie impossible, sélectionne le texte");
    return;
  }
  try {
    const r = await api(`/api/founders/${f.id}`, { method: "POST", body: JSON.stringify({ action }) });
    haptic(action === "sent" ? "medium" : "light"); toast(r.note); renderFounders();
  } catch (e) { toast(e.message); }
}

/* Wiring ------------------------------------------------------------------ */

function bindChips(containerSel, key, attr) {
  $(containerSel).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $(containerSel).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    state[key] = b.dataset[attr]; haptic(); loadList();
  });
}
function movePill() {
  const on = $("#tabs button.on"), pill = $("#tabs .seg-pill"); if (!on || !pill) return;
  pill.style.width = `${on.offsetWidth}px`; pill.style.transform = `translateX(${on.offsetLeft}px)`;
}
bindChips("#tabs", "view", "view");
$("#tabs").addEventListener("click", movePill);
window.addEventListener("resize", movePill);
requestAnimationFrame(movePill);
bindChips("#regions", "region", "region");
bindChips("#tracks", "track", "track");
// CRM web runs on the Mac only (make dev); from the phone the link cannot reach it
const CRM_URL = "http://localhost:3000";
$("#crmbtn").addEventListener("click", () => { haptic(); openLink(CRM_URL); });
$("#list").addEventListener("click", (ev) => { const c = ev.target.closest(".card"); if (c) { haptic(); openDetail(c.dataset.id); } });
let debounce; $("#search").addEventListener("input", (ev) => { clearTimeout(debounce); debounce = setTimeout(() => { state.q = ev.target.value; loadList(); }, 250); });
if (tg) tg.BackButton.onClick(closeDetail);

const start = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";
const m = location.hash.match(/#\/o\/(\d+)/) || start.match(/^o(\d+)$/);
if (m) openDetail(m[1]); else loadList();
