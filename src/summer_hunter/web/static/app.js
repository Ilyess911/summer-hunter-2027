"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready(); tg.expand();
  try { tg.setHeaderColor("bg_color"); tg.setBackgroundColor("bg_color"); } catch (_) { /* older clients */ }
  document.documentElement.dataset.scheme = tg.colorScheme || "dark";
  tg.onEvent && tg.onEvent("themeChanged", () => { document.documentElement.dataset.scheme = tg.colorScheme; });
}

const state = { view: "new", q: "", region: "", current: null };
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
        ${o.status === "closed" ? `<span class="tag bad">Fermée</span>` : ""}
      </div>
    </div>
  </button>`;
}

async function loadList() {
  const list = $("#list");
  list.classList.remove("stagger");
  list.innerHTML = Array.from({ length: 5 }, () => `<div class="skeleton"></div>`).join("");
  $("#count").textContent = "";
  try {
    const params = new URLSearchParams({ view: state.view, q: state.q, region: state.region });
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
$("#list").addEventListener("click", (ev) => { const c = ev.target.closest(".card"); if (c) { haptic(); openDetail(c.dataset.id); } });
let debounce; $("#search").addEventListener("input", (ev) => { clearTimeout(debounce); debounce = setTimeout(() => { state.q = ev.target.value; loadList(); }, 250); });
if (tg) tg.BackButton.onClick(closeDetail);

const start = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || "";
const m = location.hash.match(/#\/o\/(\d+)/) || start.match(/^o(\d+)$/);
if (m) openDetail(m[1]); else loadList();
