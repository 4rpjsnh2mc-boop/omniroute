"use strict";
/* Тендерный калькулятор — интерфейс. Без внешних библиотек. */

/* ---------- утилиты ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => {
  if (typeof v === "number") return isFinite(v) ? v : 0;
  const s = String(v ?? "").replace(/[\s  ]/g, "").replace(/,/g, ".").replace(/[^\d.\-]/g, "");
  const x = parseFloat(s);
  return isFinite(x) ? x : 0;
};
const nf0 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const rub = (n) => (isFinite(n) ? nf0.format(Math.round(n)) + " ₽" : "—");
const rub2 = (n) => (isFinite(n) ? nf2.format(n) + " ₽" : "—");
const pct = (n, d = 1) => (isFinite(n) ? n.toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }) + "%" : "—");
const fmtIn = (v, zero = false) => {
  if (v === null || v === undefined || v === "") return "";
  const x = Number(v);
  if (!isFinite(x) || (!zero && x === 0)) return "";
  return x.toLocaleString("ru-RU", { maximumFractionDigits: 4 });
};
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const round2 = (x) => Math.round(x * 100) / 100;
const uid = () => "i" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
const getPath = (o, k) => k.split(".").reduce((a, c) => (a ? a[c] : undefined), o);
const setPath = (o, k, v) => { const ks = k.split("."); const last = ks.pop(); ks.reduce((a, c) => (a[c] = a[c] || {}), o)[last] = v; };
const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c; };

let toastT = 0;
function toast(text, action, fn) {
  const t = $("#toast");
  $("#toast-t").textContent = text;
  const a = $("#toast-a");
  a.hidden = !action;
  if (action) { a.textContent = action; a.onclick = () => { t.hidden = true; fn(); }; }
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.hidden = true; }, action ? 7000 : 3500);
}

/* ---------- API ---------- */
let ME = null, SETTINGS = null, KEY = "", PRICES = [], priceIndex = new Map(), JUST_JOINED = false;
async function api(method, path, body, headers = {}) {
  const h = { "X-Tender": "1", ...headers };
  let payload;
  if (body instanceof Blob) payload = body;
  else if (body !== undefined) { h["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, { method, headers: h, body: payload, credentials: "same-origin" }); }
  catch (e) { throw new Error("Нет связи с сервером. Проверьте интернет"); }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (res.status === 401) { ME = null; showStart(""); throw new Error((data && data.error) || "Нет доступа"); }
  if (!res.ok) throw new Error((data && data.error) || "Ошибка сервера");
  return data;
}
async function loadPrices() {
  try {
    PRICES = (await api("GET", "/api/prices")).prices;
    priceIndex = new Map(PRICES.map((p) => [p.name.trim().toLowerCase(), p]));
  } catch (e) { PRICES = []; priceIndex = new Map(); }
}

/* ---------- налоги и расчёт ---------- */
const LAWS = { "44": "44-ФЗ", "223": "223-ФЗ", commercial: "Коммерческая" };
const STAGES = [["check", "Проверить тендер"], ["quotes", "Запросить цены у поставщиков"], ["calc", "Рассчитать цену"], ["upload", "Подать заявку на площадке"], ["result", "Узнать итоги"]];
const nextStage = (d) => { const st = d.stages || {}; const s = STAGES.find(([k]) => !st[k]); return s ? s[1] : "Всё сделано"; };
const MONTHS = { янв: 1, фев: 2, мар: 3, апр: 4, май: 5, мая: 5, июн: 6, июл: 7, авг: 8, сен: 9, окт: 10, ноя: 11, дек: 12 };
function parseDeadline(text) {
  const t = String(text || "").toLowerCase();
  let m = t.match(/(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?:[^\d]{1,8}(\d{1,2}):(\d{2}))?/);
  let y, mo, d, hh = 23, mm = 59;
  if (m) { d = +m[1]; mo = +m[2]; y = +m[3]; if (m[4]) { hh = +m[4]; mm = +m[5]; } }
  else if ((m = t.match(/(\d{1,2})\s+([а-я]{3})[а-я.]*\s+(\d{4})(?:[^\d]{1,8}(\d{1,2}):(\d{2}))?/)) && MONTHS[m[2]]) {
    d = +m[1]; mo = MONTHS[m[2]]; y = +m[3]; if (m[4]) { hh = +m[4]; mm = +m[5]; }
  } else return "";
  if (y < 100) y += 2000;
  const p2 = (n) => String(n).padStart(2, "0");
  return `${y}-${p2(mo)}-${p2(d)}T${p2(hh)}:${p2(mm)}`;
}
// Время в тендерах московское: «2026-11-13T06:11» → секунды UTC
const mskTs = (v) => { const m = String(v || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 3, +m[5]) / 1000 : null; };
const tsMsk = (ts) => { const d = new Date((ts + 3 * 3600) * 1000); const p2 = (n) => String(n).padStart(2, "0"); return `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}T${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
const fmtMsk = (ts) => { const v = tsMsk(ts); return `${v.slice(8, 10)}.${v.slice(5, 7)}.${v.slice(0, 4)} ${v.slice(11)} МСК`; };
function leftText(ts) {
  if (!ts) return "";
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "срок прошёл";
  const dd = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600);
  return dd ? `осталось ${dd} дн. ${hh} ч.` : `осталось ${hh} ч. ${Math.floor((s % 3600) / 60)} мин.`;
}
const urgency = (ts) => { if (!ts) return ""; const s = ts - Date.now() / 1000; return s <= 0 ? "bad" : s < 2 * 86400 ? "bad" : s < 5 * 86400 ? "warn" : "neutral"; };
function downloadFile(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
const STATUSES = { draft: "Расчёт", submitted: "Подана заявка", won: "Выиграли", lost: "Проиграли", skipped: "Отказались" };
const REGIMES = { osno: "ОСНО", usn6: "УСН «доходы» 6%", usn15: "УСН «доходы минус расходы» 15%" };
const USN_VAT = { 0: "Без НДС (освобождение)", 5: "НДС 5% без вычета", 7: "НДС 7% без вычета", 22: "НДС 22% с вычетом" };

function taxOf(d) {
  const t = d.tax || {};
  if (t.regime === "usn6" || t.regime === "usn15") {
    const vat = num(t.usnVat);
    return { regime: t.regime, vat, deduct: vat === 22, kind: t.regime === "usn6" ? "income" : "diff", rate: num(t.usnRate) || (t.regime === "usn6" ? 6 : 15) };
  }
  return { regime: "osno", vat: 22, deduct: true, kind: "profit", rate: num(t.profitRate) || 25 };
}
function baseOf(d) {
  const tax = taxOf(d);
  let goods = 0, missing = 0;
  for (const it of d.items) {
    const q = num(it.qty), c = num(it.cost);
    goods += q * c;
    if (q > 0 && !(c > 0) && (it.name || "").trim()) missing++;
  }
  const extra = num(d.delivery) + num(d.other);
  const gross = goods + extra;
  const inVat = d.supplierVat ? (gross * 22) / 122 : 0;
  const direct = tax.deduct ? gross - inVat : gross;
  const overhead = (direct * num(d.overheadPct)) / 100;
  const a = d.adv || {};
  const nmck = num(d.nmck);
  const rate = num(a.moneyRatePct) / 100;
  const appSec = ((nmck * num(a.appSecPct)) / 100) * rate * (num(a.appDays) || 20) / 365;
  return { d, tax, goods, extra, gross, inVat, direct, overhead, appSec, nmck, rate, a, missing };
}
function calcAt(b, P) {
  const vat = b.tax.vat / 100, a = b.a;
  const R = P / (1 + vat);
  let disc = b.nmck > 0 ? (1 - P / b.nmck) * 100 : 0;
  if (Math.abs(disc) < 0.005) disc = 0;
  const antidump = b.d.meta.law === "44" && b.nmck > 0 && disc >= 25 - 1e-9;
  const secAmt = ((P * num(a.conSecPct)) / 100) * (antidump ? 1.5 : 1);
  const days = num(a.contractDays) + 30;
  const conSec = a.secMethod === "money" ? (secAmt * b.rate * days) / 365 : (secAmt * num(a.bgRatePct) / 100 * days) / 365;
  const advNet = (P * num(a.advancePct)) / 100 / (1 + vat);
  const outlay = Math.max(0, b.direct - advNet);
  const financing = (outlay * b.rate * (num(a.contractDays) / 2 + num(a.paymentDays))) / 365;
  const platform = num(a.platformFee);
  const extra = b.appSec + conSec + financing + platform;
  const expenses = b.direct + b.overhead + extra;
  const pre = R - expenses;
  const incomeTax = b.tax.kind === "income" ? (R * b.tax.rate) / 100 : (Math.max(0, pre) * b.tax.rate) / 100;
  const net = pre - incomeTax;
  const vatPay = P - R - (b.tax.deduct ? b.inVat : 0);
  return { P, R, disc, antidump, secAmt, conSec, financing, platform, extra, expenses, pre, incomeTax, net, vatPay, margin: R > 0 ? (net / R) * 100 : -Infinity };
}
function solve(b, fn) {
  let lo = 0, hi = Math.max(b.nmck * 4, b.gross * 4, 1000);
  if (fn(calcAt(b, hi)) < 0) { hi *= 25; if (fn(calcAt(b, hi)) < 0) return null; }
  for (let i = 0; i < 90; i++) { const mid = (lo + hi) / 2; if (fn(calcAt(b, mid)) >= 0) hi = mid; else lo = mid; }
  return hi;
}
function compute(d) {
  const b = baseOf(d);
  const target = num(d.targetPct);
  const has = b.gross > 0;
  const be = has ? solve(b, (c) => c.net) : null;
  const tg = has ? solve(b, (c) => c.margin - target) : null;
  let P;
  const bid = d.bid || { mode: "target" };
  if (bid.mode === "price" && num(bid.price) > 0) P = num(bid.price);
  else if (bid.mode === "disc" && b.nmck > 0) P = b.nmck * (1 - num(bid.disc) / 100);
  else if (tg) P = b.nmck > 0 ? Math.min(tg, b.nmck) : tg;
  else P = b.nmck || 0;
  const cur = calcAt(b, P);
  const beDisc = be && b.nmck > 0 ? (1 - be / b.nmck) * 100 : null;
  const tgDisc = tg && b.nmck > 0 ? (1 - tg / b.nmck) * 100 : null;
  return { b, be, tg, P, cur, beDisc, tgDisc, target, has, nm: b.nmck > 0 ? calcAt(b, b.nmck) : null };
}

/* ---------- вход по ссылке ---------- */
const personalLink = () => `${location.origin}/#/key/${KEY}`;
function showStart(code) {
  $("#top").hidden = true;
  if (!code) {
    app.innerHTML = `<div class="login"><div class="card">
      <div><h1>Тендерный калькулятор</h1></div>
      <p>Чтобы начать, откройте ссылку-приглашение, которую вам прислали.</p>
      <p class="muted small">Если вы уже работали здесь с другого устройства, откройте свою личную ссылку: она есть в разделе «Настройки» на том устройстве.</p>
    </div></div>`;
    return;
  }
  app.innerHTML = `<div class="login"><form class="card" id="join-f">
    <div><h1>Тендерный калькулятор</h1><p class="muted">Расчёт тендеров: позиции, налоги, маржа, цена для торгов</p></div>
    <div class="field"><label for="j-name">Как вас зовут?</label><div class="inp"><input id="j-name" autocomplete="name" placeholder="Например, Иван Петров" maxlength="80"></div>
      <span class="hint">Видно только вам. Можно оставить пустым.</span></div>
    <p class="err-msg" id="j-err" hidden></p>
    <button class="btn primary" type="submit">Начать работу</button>
    <p class="hint">У вас будут свои тендеры и свой прайс. Другие люди с этой ссылкой их не увидят.</p></form></div>`;
  $("#join-f").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#j-err");
    err.hidden = true;
    try {
      await api("POST", "/api/join", { code, name: $("#j-name").value.trim() });
      JUST_JOINED = true;
      history.replaceState(null, "", location.pathname + "#/");
      await boot();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
}
async function claimKey(key) {
  try {
    await api("POST", "/api/claim", { key });
    history.replaceState(null, "", location.pathname + "#/");
    await boot();
    toast("Открыты ваши тендеры");
  } catch (e) {
    history.replaceState(null, "", location.pathname + "#/");
    $("#top").hidden = true;
    app.innerHTML = `<div class="login"><div class="card"><h1>Ссылка не подошла</h1><p>${esc(e.message)}</p></div></div>`;
  }
}

/* ---------- навигация ---------- */
let leaveHook = null;
async function route() {
  if (leaveHook) { const f = leaveHook; leaveHook = null; await f(); }
  const h = location.hash || "#/";
  let km;
  if ((km = h.match(/^#\/key\/([\w-]{10,100})/))) return claimKey(km[1]);
  if (!ME) { const jm = h.match(/^#\/join\/([\w-]{4,100})/); return showStart(jm ? jm[1] : ""); }
  if (h.startsWith("#/join/")) { history.replaceState(null, "", location.pathname + "#/"); }
  const page = h.startsWith("#/t/") ? "list" : h.startsWith("#/prices") ? "prices" : h.startsWith("#/settings") ? "settings" : "list";
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.nav === page));
  window.scrollTo(0, 0);
  let m;
  try {
    if ((m = h.match(/^#\/t\/(\d+)/))) return await viewTender(Number(m[1]));
    if (page === "prices") return await viewPrices();
    if (page === "settings") return viewSettings();
    return await viewList();
  } catch (e) {
    if (ME) app.innerHTML = `<div class="card"><p class="err-msg">${esc(e.message)}</p><p><a href="#/">К списку тендеров</a></p></div>`;
  }
}
window.addEventListener("hashchange", route);
async function boot() {
  const h = location.hash || "";
  const km = h.match(/^#\/key\/([\w-]{10,100})/);
  if (km) return claimKey(km[1]);
  const r = await api("GET", "/api/me");
  if (!r.user) { ME = null; return route(); }
  ME = r.user; SETTINGS = r.settings; KEY = r.key || "";
  $("#top").hidden = false;
  $("#nav-user").textContent = ME.name ? `Настройки · ${ME.name}` : "Настройки";
  await loadPrices();
  route();
}

/* ---------- список тендеров ---------- */
function newData() {
  const s = SETTINGS || {};
  return {
    v: 1,
    meta: { number: "", customer: "", subject: "", deadline: "", deadlineAt: "", place: "", url: "", law: "44", contractTerm: "" },
    nmck: 0, items: [], supplierVat: s.supplierVat !== false, delivery: 0, other: 0, overheadPct: s.overheadPct ?? 5,
    adv: { appSecPct: 0, appDays: 20, conSecPct: 0, secMethod: "bg", bgRatePct: s.bgRatePct ?? 3, contractDays: 30, paymentDays: 10, advancePct: 0, moneyRatePct: s.moneyRatePct ?? 18, platformFee: 0 },
    tax: { ...(s.tax || { regime: "osno", usnVat: 0, usnRate: 6, profitRate: 25 }) },
    targetPct: s.targetPct ?? 10, bid: { mode: "target" }, status: "draft", missingDocs: [],
    note: "", stages: {}, important: false, flags: {}, inn: "",
  };
}
let pendingImport = null;
async function viewList() {
  app.innerHTML = `
    ${JUST_JOINED ? `<div class="card" style="border-color:var(--accent)">
      <div class="card-h"><h2>Сохраните свою личную ссылку</h2><button class="btn sm" type="button" id="w-close">Понятно</button></div>
      <p>По ней ваши тендеры откроются на телефоне или другом компьютере. Сохраните её себе в заметки или «Избранное» в мессенджере и никому не пересылайте.</p>
      <div class="row" style="margin-top:10px"><div class="inp grow"><input id="w-link" readonly value="${esc(personalLink())}" aria-label="Личная ссылка"></div><button class="btn primary" type="button" id="w-copy">Копировать</button></div>
      <p class="hint" style="margin-top:6px">Ссылку всегда можно найти в разделе «Настройки».</p></div>` : ""}
    <div class="card">
      <div class="card-h"><h1>Мои тендеры</h1></div>
      <form class="row" id="new-f">
        <div class="inp grow"><input id="new-url" type="url" inputmode="url" placeholder="Вставьте ссылку на закупку: zakupki.gov.ru, Росэлторг, B2B-Center…" aria-label="Ссылка на закупку"></div>
        <button class="btn primary" type="submit">Загрузить тендер</button>
        <button class="btn" type="button" id="new-empty">Пустой расчёт</button>
      </form>
      <p class="hint" style="margin-top:8px">Сервер откроет страницу закупки, заполнит позиции и условия и скачает документы.</p>
    </div>
    <div id="soon"></div>
    <div class="stats" id="stats"></div>
    <div class="row" id="flt" style="margin-bottom:12px"></div>
    <div class="card" style="padding:0"><div class="table-wrap" style="border:0">
      <table class="list" style="min-width:900px"><thead><tr><th>Тендер</th><th>Подача до</th><th class="r">Закупка</th><th class="r">Наша цена</th><th class="r">Маржа</th><th>Следующий шаг</th><th>Статус</th></tr></thead><tbody id="t-body"><tr><td colspan="7" class="muted">Загрузка…</td></tr></tbody></table>
    </div></div>`;
  const create = async (url) => {
    const r = await api("POST", "/api/tenders", { title: url ? "Загрузка…" : "Новый тендер", data: newData() });
    if (url) pendingImport = { id: r.id, url };
    location.hash = "#/t/" + r.id;
  };
  $("#new-f").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#new-url").value.trim();
    if (!/^https?:\/\//i.test(url)) return toast("Вставьте ссылку, она начинается с https://");
    try { await create(url); } catch (ex) { toast(ex.message); }
  });
  $("#new-empty").addEventListener("click", () => create("").catch((ex) => toast(ex.message)));
  if (JUST_JOINED) {
    $("#w-copy").onclick = () => copyText(personalLink(), $("#w-link"));
    $("#w-close").onclick = () => { JUST_JOINED = false; viewList(); };
  }
  const [{ tenders }, { reminders }] = await Promise.all([api("GET", "/api/tenders"), api("GET", "/api/reminders")]);
  LIST = tenders;
  const soon = reminders.filter((r) => !r.sent && r.at - Date.now() / 1000 < 3 * 86400).slice(0, 5);
  const hot = tenders.filter((t) => t.deadline_ts && t.deadline_ts > Date.now() / 1000 && t.deadline_ts - Date.now() / 1000 < 3 * 86400 && ["draft", "submitted"].includes(t.status));
  $("#soon").innerHTML = soon.length || hot.length ? `<div class="card"><div class="card-h"><h2>Ближайшие сроки</h2></div><div class="docs-list">
      ${hot.map((t) => `<a class="doc" href="#/t/${t.id}"><span class="name"><b>${esc(t.title)}</b><br><span class="small muted">Окончание подачи: ${esc(fmtMsk(t.deadline_ts))}</span></span><span class="pill ${urgency(t.deadline_ts)}">${esc(leftText(t.deadline_ts))}</span></a>`).join("")}
      ${soon.map((r) => `<a class="doc" href="#/t/${r.tender_id}"><span class="name">⏰ ${esc(r.text || "Напоминание")}<br><span class="small muted">${esc(r.title)} · ${esc(fmtMsk(r.at))}</span></span></a>`).join("")}</div></div>` : "";
  const f = LIST_FILTER;
  $("#flt").innerHTML = `
    <div class="inp grow"><input id="flt-q" placeholder="Поиск по названию, номеру, заказчику" value="${esc(f.q)}" aria-label="Поиск"></div>
    <div class="inp"><select id="flt-stage" aria-label="Следующий шаг"><option value="">Любой шаг</option>${STAGES.map(([, l]) => `<option${f.stage === l ? " selected" : ""}>${esc(l)}</option>`).join("")}<option${f.stage === "Всё сделано" ? " selected" : ""}>Всё сделано</option></select></div>
    <div class="inp"><select id="flt-status" aria-label="Статус"><option value="">Любой статус</option>${Object.entries(STATUSES).map(([k, v]) => `<option value="${k}"${f.status === k ? " selected" : ""}>${v}</option>`).join("")}</select></div>
    <div class="inp"><select id="flt-when" aria-label="Срок"><option value="">Любой срок</option><option value="hot"${f.when === "hot" ? " selected" : ""}>Горящие (до 3 дней)</option><option value="active"${f.when === "active" ? " selected" : ""}>Приём заявок идёт</option><option value="past"${f.when === "past" ? " selected" : ""}>Срок прошёл</option></select></div>
    <label class="check"><input type="checkbox" id="flt-imp"${f.important ? " checked" : ""}> <span>Только важные</span></label>`;
  const apply = () => {
    f.q = $("#flt-q").value.trim().toLowerCase(); f.stage = $("#flt-stage").value; f.status = $("#flt-status").value;
    f.when = $("#flt-when").value; f.important = $("#flt-imp").checked;
    try { localStorage.setItem("tender-filter", JSON.stringify(f)); } catch (e) { /* не страшно */ }
    renderList();
  };
  $("#flt").oninput = apply; $("#flt").onchange = apply;
  renderList();
  const cnt = (s) => tenders.filter((t) => t.status === s).length;
  const won = tenders.filter((t) => t.status === "won");
  const decided = cnt("won") + cnt("lost");
  const st = (v, k) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#stats").innerHTML = [
    st(tenders.length, "тендеров"),
    st(cnt("submitted") + decided, "подано заявок"),
    st(won.length, "выиграно"),
    st(decided ? pct((won.length / decided) * 100, 0) : "—", "доля побед"),
    st(won.length ? rub(won.reduce((a, t) => a + (t.profit || 0), 0)) : "—", "прибыль с побед"),
  ].join("");
}
let LIST = [];
let LIST_FILTER = (() => { try { return { q: "", stage: "", status: "", when: "", important: false, ...JSON.parse(localStorage.getItem("tender-filter") || "{}") }; } catch (e) { return { q: "", stage: "", status: "", when: "", important: false }; } })();
function renderList() {
  const f = LIST_FILTER, nowS = Date.now() / 1000;
  let rows = LIST.filter((t) => {
    if (f.q && ![t.title, t.number, t.customer].join(" ").toLowerCase().includes(f.q)) return false;
    if (f.stage && (t.stage || "Проверить тендер") !== f.stage) return false;
    if (f.status && t.status !== f.status) return false;
    if (f.important && !t.important) return false;
    if (f.when === "hot" && !(t.deadline_ts && t.deadline_ts > nowS && t.deadline_ts - nowS < 3 * 86400)) return false;
    if (f.when === "active" && !(t.deadline_ts && t.deadline_ts > nowS)) return false;
    if (f.when === "past" && !(t.deadline_ts && t.deadline_ts <= nowS)) return false;
    return true;
  });
  rows = rows.sort((a, b) => {
    const fa = a.deadline_ts && a.deadline_ts > nowS ? a.deadline_ts : 9e12, fb = b.deadline_ts && b.deadline_ts > nowS ? b.deadline_ts : 9e12;
    return fa - fb || b.updated - a.updated;
  });
  const body = $("#t-body");
  if (!LIST.length) { body.innerHTML = `<tr><td colspan="7" class="muted" style="padding:18px">Тендеров пока нет. Вставьте ссылку на закупку выше.</td></tr>`; return; }
  if (!rows.length) { body.innerHTML = `<tr><td colspan="7" class="muted" style="padding:18px">Под фильтр ничего не подходит.</td></tr>`; return; }
  body.innerHTML = rows.map((t) => {
    const m = t.margin;
    const mp = m === null || m === undefined ? "—" : `<span class="pill ${m <= 0 ? "bad" : m >= num(SETTINGS.targetPct) ? "good" : "warn"}">${pct(m)}</span>`;
    const dl = t.deadline_ts ? `${esc(fmtMsk(t.deadline_ts))}<br><span class="pill ${urgency(t.deadline_ts)}">${esc(leftText(t.deadline_ts))}</span>` : esc(t.deadline || "—");
    return `<tr class="clickable" data-id="${t.id}"><td><div class="t-title">${t.important ? "★ " : ""}${esc(t.title || "Без названия")}</div><div class="t-meta">${esc([t.number, t.customer].filter(Boolean).join(" · "))}</div></td>
      <td class="small">${dl}</td><td class="r">${t.cost ? rub(t.cost) : "—"}</td><td class="r">${t.price ? rub(t.price) : "—"}</td><td class="r">${mp}</td>
      <td><span class="pill neutral">${esc(t.stage || "Проверить тендер")}</span></td><td class="small">${esc(STATUSES[t.status] || "Расчёт")}</td></tr>`;
  }).join("");
  $$("#t-body tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { location.hash = "#/t/" + tr.dataset.id; }));
}
async function copyText(text, input, okMsg = "Ссылка скопирована") {
  try { await navigator.clipboard.writeText(text); toast(okMsg); }
  catch (e) { if (input) { input.focus(); input.select(); } toast("Выделите ссылку и скопируйте вручную"); }
}

/* ---------- тендер ---------- */
let T = null, saveTimer = 0, last = null, docsTimer = 0;

function summaryOf() {
  const d = T.data, c = last ? last.cur : null;
  return {
    title: d.meta.subject || d.meta.number || "Без названия", number: d.meta.number, url: d.meta.url, customer: d.meta.customer,
    status: d.status, nmck: num(d.nmck) || null, price: c && c.P > 0 ? round2(c.P) : null,
    profit: c && last.has ? round2(c.net) : null, margin: c && last.has && isFinite(c.margin) ? round2(c.margin) : null, deadline: d.meta.deadline,
    deadlineTs: mskTs(d.meta.deadlineAt), stage: nextStage(d), important: !!d.important, cost: last && last.has ? round2(last.b.gross) : null,
  };
}
async function saveNow() {
  clearTimeout(saveTimer);
  if (!T || !T.dirty) return;
  T.dirty = false;
  const el = $("#saved");
  try {
    await api("PUT", "/api/tenders/" + T.id, { data: T.data, summary: summaryOf() });
    if (el) el.textContent = "Сохранено";
  } catch (e) {
    T.dirty = true;
    if (el) el.textContent = "Не сохранено";
    toast(e.message);
  }
}
function changed() {
  if (!T) return;
  T.dirty = true;
  const el = $("#saved");
  if (el) el.textContent = "Сохраняю…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 700);
}
window.addEventListener("beforeunload", () => {
  if (!T || !T.dirty) return;
  try {
    fetch("/api/tenders/" + T.id, { method: "PUT", keepalive: true, credentials: "same-origin",
      headers: { "X-Tender": "1", "Content-Type": "application/json" }, body: JSON.stringify({ data: T.data, summary: summaryOf() }) });
  } catch (e) { /* лучшее, что можно сделать при закрытии */ }
});

const F = (k, l, opt = {}) => {
  const id = "f-" + k.replace(/\./g, "-");
  const control = opt.sel
    ? `<select id="${id}" data-k="${k}" data-t="sel">${Object.entries(opt.sel).map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("")}</select>`
    : `<input id="${id}" data-k="${k}" data-t="${opt.num ? "num" : "text"}" ${opt.num ? 'class="n" inputmode="decimal"' : ""} placeholder="${esc(opt.ph || "")}">`;
  return `<div class="field${opt.wide ? " wide" : ""}"${opt.id ? ` id="${opt.id}"` : ""}><label for="${id}">${esc(l)}</label><div class="inp">${control}${opt.u ? `<span class="u">${esc(opt.u)}</span>` : ""}</div>${opt.h ? `<span class="hint">${esc(opt.h)}</span>` : ""}</div>`;
};

async function viewTender(id) {
  const { tender } = await api("GET", "/api/tenders/" + id);
  const base = newData();
  const d = tender.data && tender.data.v ? tender.data : base;
  d.meta = { ...base.meta, ...(d.meta || {}) };
  d.adv = { ...base.adv, ...(d.adv || {}) };
  d.tax = { ...base.tax, ...(d.tax || {}) };
  d.items = Array.isArray(d.items) ? d.items : [];
  d.status = tender.status || d.status || "draft";
  d.stages = d.stages || {}; d.flags = d.flags || {}; d.note = d.note || "";
  if (!d.meta.deadlineAt && d.meta.deadline) d.meta.deadlineAt = parseDeadline(d.meta.deadline);
  T = { id, data: d, dirty: false, docFlags: {}, market: null };
  leaveHook = async () => { clearInterval(docsTimer); clearInterval(marketTimer); await saveNow(); T = null; };

  app.innerHTML = `
  <div class="row" style="justify-content:space-between;margin-bottom:12px">
    <a href="#/" class="btn link">← Все тендеры</a>
    <div class="row"><span class="saved" id="saved"></span>
      <div class="inp"><select id="f-status" data-k="status" data-t="sel" aria-label="Статус">${Object.entries(STATUSES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
      <button class="btn sm danger" id="t-del" type="button">Удалить</button></div>
  </div>
  <div class="layout">
    <div>
      <section class="card" id="work">
        <div class="card-h"><h2>Работа с тендером</h2><label class="check"><input type="checkbox" id="f-important" data-k="important" data-t="bool"> <span>★ Важный</span></label></div>
        <div class="row" style="align-items:flex-end">
          <div class="field"><label for="f-meta-deadlineAt">Окончание подачи (МСК)</label><div class="inp"><input type="datetime-local" id="f-meta-deadlineAt" data-k="meta.deadlineAt" data-t="text"></div></div>
          <span class="pill" id="dl-left"></span>
          <button class="btn sm" type="button" id="ics">В календарь</button>
        </div>
        <div class="stages" id="stages" style="margin-top:12px"></div>
        <div style="margin-top:14px"><h3>Напоминания</h3><div class="docs-list" id="rem-list"></div>
          <form class="row" id="rem-f" style="margin-top:8px">
            <div class="inp"><input type="datetime-local" id="rem-at" aria-label="Когда напомнить (МСК)" required></div>
            <div class="inp grow"><input id="rem-text" placeholder="О чём напомнить" value="Подать заявку" aria-label="Текст напоминания"></div>
            <button class="btn sm" type="submit">Добавить</button>
            <button class="btn sm" type="button" id="rem-day">За день до окончания</button>
          </form>
          <p class="hint" id="rem-hint" style="margin-top:6px"></p></div>
        <div class="field" style="margin-top:14px"><label for="f-note">Заметка</label><textarea id="f-note" data-k="note" data-t="text" rows="3" placeholder="Например: поставщик даёт скидку от 5 т, позвонить заказчику по срокам"></textarea></div>
        <div class="row" style="margin-top:10px"><button class="btn sm" type="button" id="note-copy">Скопировать сводку</button><span class="hint">Тендер, цены закупки и продажи, маржа после налогов, срок и следующий шаг одним текстом</span></div>
      </section>

      <section class="card">
        <div class="card-h"><h2>Закупка</h2><span class="muted small" id="imp-status"></span></div>
        <form class="row" id="imp-f" style="margin-bottom:14px">
          <div class="inp grow"><input id="f-meta-url" data-k="meta.url" data-t="text" type="url" inputmode="url" placeholder="Ссылка на закупку" aria-label="Ссылка на закупку"></div>
          <button class="btn primary" type="submit" id="imp-btn">${d.meta.url ? "Обновить с площадки" : "Загрузить с площадки"}</button>
          ${d.meta.url ? `<a class="btn" href="${esc(d.meta.url)}" target="_blank" rel="noopener">Открыть</a>` : ""}
        </form>
        <div class="fields">
          ${F("meta.subject", "Предмет закупки", { wide: true })}
          ${F("nmck", "НМЦК", { num: true, u: "₽ с НДС", h: "Начальная цена из извещения" })}
          ${F("meta.law", "Закон", { sel: LAWS })}
          ${F("meta.deadline", "Подача заявок до")}
          ${F("meta.number", "Номер")}
          ${F("meta.customer", "Заказчик", { wide: true })}
          ${F("meta.place", "Место поставки", { wide: true })}
        </div>
      </section>

      <section class="card" id="risk-card">
        <div class="card-h"><h2>Условия и подводные камни</h2><span class="hint">Из страницы закупки, документов и расчёта</span></div>
        <ul class="risks" id="risks"></ul>
      </section>

      <section class="card">
        <div class="card-h"><h2>Позиции</h2><span class="hint" id="cost-hint"></span></div>
        <div class="table-wrap"><table class="items"><thead><tr>
          <th class="c-name">Наименование</th><th class="c-unit">Ед.</th><th class="c-q r">Кол-во</th>
          <th class="c-p r">Цена НМЦК за ед.</th><th class="c-p r">Наша цена за ед.</th><th class="c-s r">Сумма у нас</th><th class="c-m r">Запас к НМЦК</th><th></th>
        </tr></thead><tbody id="items"></tbody></table></div>
        <div class="row" style="margin-top:10px;justify-content:space-between">
          <button class="btn sm" id="add-item" type="button">+ Добавить позицию</button>
          <span class="small" id="items-total"></span>
        </div>
        <datalist id="dl-prices">${PRICES.map((p) => `<option value="${esc(p.name)}">${p.cost ? esc(nf2.format(p.cost) + " ₽ / " + (p.unit || "ед.")) : ""}</option>`).join("")}</datalist>
      </section>

      <section class="card" id="mk-card">
        <div class="card-h"><h2>Цены конкурентов и похожие закупки</h2>
          <div class="row"><button class="btn sm primary" type="button" id="mk-run">Найти в ЕИС</button></div></div>
        <p class="hint">Цены за единицу из заключённых контрактов 44-ФЗ: по региону тендера и по всей России, кто поставлял, история закупок заказчика. Только открытые данные zakupki.gov.ru. Решение о цене принимаете вы.</p>
        <div id="mk-job" class="small muted" style="margin-top:6px"></div>
        <div id="mk-out" style="margin-top:10px"></div>
      </section>

      <section class="card">
        <div class="card-h"><h2>Расходы и налоги</h2></div>
        <div class="fields">
          ${F("delivery", "Доставка", { num: true, u: "₽" })}
          ${F("other", "Прочие расходы", { num: true, u: "₽", h: "Погрузка, упаковка, командировки" })}
          ${F("overheadPct", "Накладные", { num: true, u: "%", h: "Офис, зарплата менеджера и т.п." })}
          ${F("targetPct", "Целевая маржа", { num: true, u: "%", h: "Чистая прибыль к выручке без НДС" })}
        </div>
        <label class="check" style="margin-top:12px"><input type="checkbox" id="f-supplierVat" data-k="supplierVat" data-t="bool"> <span>Наши цены и расходы указаны с НДС 22% (как в счетах поставщиков)</span></label>
        <div class="fields" style="margin-top:14px">
          ${F("tax.regime", "Налоговый режим", { sel: REGIMES })}
          ${F("tax.usnVat", "НДС на УСН", { sel: USN_VAT, id: "usn-vat-f" })}
          ${F("tax.usnRate", "Ставка УСН", { num: true, u: "%", h: "В вашем регионе может быть ниже", id: "usn-rate-f" })}
        </div>
        <p class="hint" style="margin-top:10px" id="tax-hint"></p>
        <details>
          <summary>Обеспечение, аванс и отсрочка оплаты</summary>
          <div class="fields">
            ${F("adv.appSecPct", "Обеспечение заявки", { num: true, u: "% НМЦК" })}
            ${F("adv.conSecPct", "Обеспечение исполнения", { num: true, u: "% цены", h: "При снижении на 25% и больше по 44-ФЗ ×1,5" })}
            ${F("adv.secMethod", "Чем обеспечиваем", { sel: { bg: "Банковская гарантия", money: "Деньгами" } })}
            ${F("adv.bgRatePct", "Стоимость гарантии", { num: true, u: "% годовых" })}
            ${F("adv.contractDays", "Срок поставки или работ", { num: true, u: "дн." })}
            ${F("adv.paymentDays", "Оплата после поставки", { num: true, u: "дн.", h: "По 44-ФЗ обычно до 7 рабочих дней" })}
            ${F("adv.advancePct", "Аванс или предоплата", { num: true, u: "%" })}
            ${F("adv.moneyRatePct", "Стоимость денег", { num: true, u: "% годовых", h: "Ставка кредита или упущенная выгода" })}
            ${F("adv.platformFee", "Тариф площадки", { num: true, u: "₽", h: "По 44-ФЗ: 1% цены, не больше 5 000 ₽ (СМП 2 000 ₽)" })}
          </div>
        </details>
      </section>

      <section class="card" id="steps-card">
        <div class="card-h"><h2>Шаги торгов</h2><span class="hint">Сколько заработаем при снижении цены. Нажмите строку, чтобы выбрать цену.</span></div>
        <div class="steps-wrap"><table class="steps"><thead><tr><th class="r">Снижение</th><th class="r">Цена с НДС</th><th class="r">Чистая прибыль</th><th class="r">Маржа</th><th></th></tr></thead><tbody id="steps"></tbody></table></div>
      </section>

      <section class="card">
        <div class="card-h"><h2>Документы</h2>
          <label class="btn sm" for="up-file" style="position:relative;overflow:hidden">Загрузить файл<input type="file" id="up-file" multiple style="position:absolute;inset:0;opacity:0;cursor:pointer"></label></div>
        <p class="hint">Документы хранятся на вашем сервере и открываются с любого устройства.</p>
        <div id="docs-job" class="small muted" style="margin-top:6px"></div>
        <div class="docs-list" id="docs"></div>
        <div id="docs-missing"></div>
      </section>
    </div>
    <aside class="summary" id="sum" aria-label="Итог"></aside>
  </div>`;

  fillFields();
  renderItems();
  syncTaxFields();
  recalc();
  loadDocs();
  renderStages();
  loadReminders();
  loadMarket();
  $("#ics").onclick = exportIcs;
  $("#note-copy").onclick = () => copyText(noteText(), null, "Сводка скопирована");
  $("#mk-run").onclick = runMarket;
  $("#rem-f").onsubmit = async (e) => {
    e.preventDefault();
    const at = mskTs($("#rem-at").value);
    if (!at) return toast("Укажите дату и время");
    try { await api("POST", `/api/tenders/${T.id}/reminders`, { at, text: $("#rem-text").value.trim() }); loadReminders(); toast("Напоминание добавлено"); }
    catch (ex) { toast(ex.message); }
  };
  $("#rem-day").onclick = async () => {
    const dl = mskTs(T.data.meta.deadlineAt);
    if (!dl) return toast("Сначала укажите окончание подачи");
    try { await api("POST", `/api/tenders/${T.id}/reminders`, { at: dl - 86400, text: "Завтра окончание подачи. Проверить и подать заявку" }); loadReminders(); toast("Напомню за день до окончания"); }
    catch (ex) { toast(ex.message); }
  };

  $("#imp-f").addEventListener("submit", (e) => { e.preventDefault(); runImport($("#f-meta-url").value.trim()); });
  $("#t-del").addEventListener("click", (e) => confirmInline(e.currentTarget, "Удалить тендер?", async () => {
    await api("DELETE", "/api/tenders/" + T.id); T.dirty = false; location.hash = "#/";
  }));
  $("#add-item").addEventListener("click", () => {
    const x = { id: uid(), name: "", unit: "", qty: 0, nmckUnit: 0, cost: 0 };
    d.items.push(x); renderItems(); recalc(); changed();
    const inp = $(`#items tr[data-id="${x.id}"] [data-f="name"]`); if (inp) inp.focus();
  });
  $("#up-file").addEventListener("change", uploadDocs);

  if (pendingImport && pendingImport.id === id) { const u = pendingImport.url; pendingImport = null; $("#f-meta-url").value = u; d.meta.url = u; runImport(u); }
}
function confirmInline(btn, question, fn) {
  const box = document.createElement("span");
  box.className = "row";
  box.innerHTML = `<span class="small">${esc(question)}</span><button class="btn sm danger" type="button">Да</button><button class="btn sm" type="button">Нет</button>`;
  btn.replaceWith(box);
  const [yes, no] = $$("button", box);
  yes.onclick = () => fn().catch((e) => toast(e.message));
  no.onclick = () => box.replaceWith(btn);
}
function fillFields() {
  for (const el of $$("[data-k]", app)) {
    const v = getPath(T.data, el.dataset.k);
    if (el.dataset.t === "num") el.value = fmtIn(v, true);
    else if (el.dataset.t === "bool") el.checked = !!v;
    else el.value = v ?? "";
  }
}
function onField(e) {
  const el = e.target;
  if (!el.dataset || !el.dataset.k || !T) return;
  if (e.type === "change" && el.dataset.t !== "sel" && el.dataset.t !== "bool" && !/date/.test(el.type)) {
    if (el.dataset.t === "num") el.value = fmtIn(getPath(T.data, el.dataset.k), true);
    return;
  }
  if (e.type === "input" && (el.dataset.t === "sel" || el.dataset.t === "bool")) return;
  const t = el.dataset.t;
  setPath(T.data, el.dataset.k, t === "num" ? num(el.value) : t === "bool" ? el.checked : el.value);
  if (el.dataset.k.startsWith("tax.")) syncTaxFields();
  if (el.dataset.k === "supplierVat") updateItemCells();
  if (el.dataset.k === "meta.deadlineAt") renderStages();
  if (el.dataset.k === "meta.deadline" && !T.data.meta.deadlineAt) { T.data.meta.deadlineAt = parseDeadline(el.value); $("#f-meta-deadlineAt").value = T.data.meta.deadlineAt; renderStages(); }
  if (el.dataset.k === "note") { changed(); return; }
  recalc();
  changed();
}
function syncTaxFields() {
  const t = T.data.tax;
  const usn = t.regime === "usn6" || t.regime === "usn15";
  $("#usn-vat-f").hidden = !usn;
  $("#usn-rate-f").hidden = !usn;
  if (usn && !num(t.usnRate)) { t.usnRate = t.regime === "usn6" ? 6 : 15; $("#f-tax-usnRate").value = fmtIn(t.usnRate); }
  const hints = {
    osno: "ОСНО в 2026 году: НДС 22%, входной НДС от поставщиков уходит в вычет, налог на прибыль 25%.",
    usn6: "УСН «доходы» 6% считается с выручки без НДС. В 2026 году освобождение от НДС на УСН действует, только если доход за 2025 год не больше 20 млн ₽. Иначе НДС 5% или 7% без вычета (зависит от годового дохода) либо 22% с вычетом.",
    usn15: "УСН 15% считается с разницы доходов и расходов. По итогам года платится не меньше 1% от доходов. В 2026 году освобождение от НДС на УСН действует, только если доход за 2025 год не больше 20 млн ₽. Иначе НДС 5% или 7% без вычета либо 22% с вычетом.",
  };
  $("#tax-hint").textContent = (hints[t.regime] || hints.osno) + " Ставки и пороги сверьте с бухгалтером.";
}

/* позиции */
function renderItems() {
  const d = T.data;
  $("#items").innerHTML = d.items.map((x) => `
    <tr data-id="${x.id}">
      <td class="c-name"><input data-f="name" list="dl-prices" value="${esc(x.name)}" title="${esc(x.name)}" aria-label="Наименование"></td>
      <td><input data-f="unit" value="${esc(x.unit)}" aria-label="Ед."></td>
      <td><input data-f="qty" class="n" inputmode="decimal" value="${fmtIn(x.qty)}" aria-label="Количество"></td>
      <td><input data-f="nmckUnit" class="n" inputmode="decimal" value="${fmtIn(x.nmckUnit)}" aria-label="Цена НМЦК за единицу"></td>
      <td><input data-f="cost" class="n" inputmode="decimal" value="${fmtIn(x.cost)}" aria-label="Наша цена за единицу"><span class="hint-price" data-o="hint"></span><span class="hint-price" data-o="analog"></span></td>
      <td class="r" data-o="sum"></td>
      <td class="r" data-o="m"></td>
      <td><button class="icon" type="button" data-act="del" aria-label="Удалить позицию">×</button></td>
    </tr>`).join("") || `<tr><td colspan="8" class="muted" style="padding:14px">Позиций нет. Загрузите закупку по ссылке или добавьте вручную.</td></tr>`;
  updateItemCells();
  const tb = $("#items");
  tb.oninput = (e) => {
    const el = e.target, tr = el.closest("tr");
    if (!tr || !el.dataset.f) return;
    const x = T.data.items.find((i) => i.id === tr.dataset.id);
    if (!x) return;
    const f = el.dataset.f;
    x[f] = ["qty", "cost", "nmckUnit"].includes(f) ? num(el.value) : el.value;
    updateItemCells(); recalc(); changed();
  };
  tb.onchange = (e) => {
    const el = e.target, tr = el.closest("tr");
    if (!tr) return;
    const x = T.data.items.find((i) => i.id === tr.dataset.id);
    if (!x) return;
    if (["qty", "cost", "nmckUnit"].includes(el.dataset.f)) el.value = fmtIn(x[el.dataset.f]);
    if (el.dataset.f === "name") {
      const p = priceIndex.get((x.name || "").trim().toLowerCase());
      if (p && !(num(x.cost) > 0) && p.cost > 0) {
        x.cost = p.cost; if (!x.unit) x.unit = p.unit || "";
        $('[data-f="cost"]', tr).value = fmtIn(x.cost); $('[data-f="unit"]', tr).value = x.unit;
        updateItemCells(); recalc(); changed();
        toast(`Цена из прайса: ${rub2(p.cost)} за ${p.unit || "ед."}`);
      }
    }
  };
  tb.onclick = (e) => {
    const ab = e.target.closest('[data-act="analog"]');
    if (ab) return showAnalogs(ab.closest("tr"));
    const pick = e.target.closest("[data-pick]");
    if (pick) {
      const x = T.data.items.find((i) => i.id === pick.closest("tr").previousElementSibling.dataset.id);
      const p = PRICES.find((q) => String(q.id) === pick.dataset.pick);
      if (x && p) { x.cost = p.cost; x.analog = p.name; if (!x.unit) x.unit = p.unit; renderItems(); recalc(); changed(); toast("Цена аналога подставлена"); }
      return;
    }
    const b = e.target.closest('[data-act="del"]');
    if (!b) return;
    const id = b.closest("tr").dataset.id;
    const idx = T.data.items.findIndex((i) => i.id === id);
    const removed = T.data.items.splice(idx, 1)[0];
    renderItems(); recalc(); changed();
    toast("Позиция удалена", "Вернуть", () => { T.data.items.splice(idx, 0, removed); renderItems(); recalc(); changed(); });
  };
}
function priceFor(x) { return priceIndex.get((x.name || "").trim().toLowerCase()); }
const STOPW = new Set("для из на по под при без или и в с со к от до шт кг т м л гост ту марка тип вид товар поставка количестве".split(" "));
const toks = (s) => (String(s || "").toLowerCase().replace(/ё/g, "е").match(/[a-zа-я]+|\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)?/g) || [])
  .filter((w) => !STOPW.has(w) && (w.length > 2 || /\d/.test(w))).map((w) => (/^\d/.test(w) ? w : w.slice(0, 7)));
function sim(a, b) {
  const base = String(a || "").split(/[(,;]/)[0];
  const ta = [...new Set([...(toks(base).length ? toks(base) : toks(a)), ...toks(a).filter((w) => /^\d/.test(w))])].slice(0, 8);
  const tb = new Set(toks(b));
  if (!ta.length) return 0;
  const hit = ta.filter((w) => tb.has(w)).length;
  return tb.has(ta[0]) ? hit / ta.length : (hit / ta.length) * 0.5;
}
function analogsMode() { const f = { ...(T.data.flags || {}), ...(T.docFlags || {}) }; return f.analogs ? f.analogs.value : ""; }
function analogCandidates(x) {
  return PRICES.filter((p) => p.cost > 0).map((p) => ({ p, s: sim(x.name, p.name) })).filter((o) => o.s >= 0.34).sort((a, b) => b.s - a.s).slice(0, 5).map((o) => o.p);
}
function showAnalogs(tr) {
  const nx = tr.nextElementSibling;
  if (nx && nx.classList.contains("an-row")) { nx.remove(); return; }
  const x = T.data.items.find((i) => i.id === tr.dataset.id);
  const row = document.createElement("tr");
  row.className = "an-row";
  row.innerHTML = `<td colspan="8"><div class="small" style="padding:4px 6px">${analogsMode() === "allowed" ? "Заказчик допускает эквивалент. " : "Допускается ли эквивалент, проверьте в документации. "}Похожее из вашего прайса:</div>
    <div class="docs-list" style="margin:4px 6px 8px">${analogCandidates(x).map((p) => `<div class="doc"><span class="name">${esc(p.name)}<br><span class="small muted">${esc(p.unit || "ед.")} · ${rub2(p.cost)}${p.note ? " · " + esc(p.note) : ""}</span></span><button class="btn sm" type="button" data-pick="${p.id}">Взять</button></div>`).join("")}</div></td>`;
  tr.after(row);
}
function updateItemCells() {
  const d = T.data;
  $("#cost-hint").textContent = d.supplierVat ? "Наши цены с НДС 22%, как в счетах поставщиков" : "Наши цены без НДС";
  for (const tr of $$("#items tr[data-id]")) {
    const x = d.items.find((i) => i.id === tr.dataset.id);
    if (!x) continue;
    const q = num(x.qty), c = num(x.cost), n = num(x.nmckUnit);
    $('[data-o="sum"]', tr).textContent = q * c ? nf0.format(Math.round(q * c)) : "";
    const cGross = d.supplierVat ? c : c * 1.22;
    let m = "";
    if (n > 0 && c > 0) {
      const k = (n / cGross - 1) * 100;
      m = `<span class="pill ${k < 0 ? "bad" : k < num(d.overheadPct) + num(d.targetPct) ? "warn" : "good"}">${k > 0 ? "+" : ""}${pct(k, 0)}</span>`;
    }
    $('[data-o="m"]', tr).innerHTML = m;
    $('[data-f="cost"]', tr).classList.toggle("missing", q > 0 && !(c > 0));
    const p = priceFor(x);
    $('[data-o="hint"]', tr).textContent = p && p.cost > 0 && Math.abs(p.cost - c) > 0.005 ? `прайс: ${nf2.format(p.cost)}` : "";
    const an = $('[data-o="analog"]', tr);
    let ah = "";
    if (x.analog) ah = `<span class="pill neutral" title="${esc(x.analog)}">аналог</span>`;
    else if (!p && !(c > 0) && analogsMode() !== "forbidden") {
      const cand = analogCandidates(x);
      ah = cand.length ? `<button class="btn link" type="button" data-act="analog">аналоги из прайса: ${cand.length}</button>` : "";
    }
    if (an.dataset.h !== ah) { an.innerHTML = ah; an.dataset.h = ah; }
  }
  const goods = d.items.reduce((a, x) => a + num(x.qty) * num(x.cost), 0);
  const nm = d.items.reduce((a, x) => a + num(x.qty) * num(x.nmckUnit), 0);
  $("#items-total").innerHTML = `У нас: <b class="num">${rub(goods)}</b>${nm ? ` · по ценам НМЦК: <b class="num">${rub(nm)}</b>` : ""}${nm && !(num(d.nmck) > 0) ? ` <button class="btn sm" type="button" id="use-nm">Взять как НМЦК</button>` : ""}`;
  const u = $("#use-nm");
  if (u) u.onclick = () => { d.nmck = round2(nm); fillFields(); recalc(); changed(); };
}

/* итог */
function verdictOf(r) {
  const { b, cur, target } = r;
  if (!r.has) return { cls: "", t: "Заполните цены", d: "Укажите нашу цену хотя бы по одной позиции, и здесь появится вывод." };
  if (b.nmck > 0 && cur.P > b.nmck + 0.5) return { cls: "bad", t: "Цена выше НМЦК", d: "Заявку с такой ценой отклонят." };
  if (cur.net <= 0) return { cls: "bad", t: "Невыгодно", d: r.be ? `Убыток ${rub(-cur.net)}. Безубыточно от ${rub(r.be)}.` : "Расходы не покрываются." };
  const room = r.beDisc !== null && b.nmck > 0 ? ` До безубыточности можно снижать ещё на ${pct(Math.max(r.beDisc - cur.disc, 0))}.` : "";
  if (cur.margin >= target - 0.05) return { cls: "good", t: "Выгодно", d: `Чистая маржа ${pct(cur.margin)} при цели ${pct(target, 0)}.${room}` };
  return { cls: "warn", t: "На грани", d: `Чистая маржа ${pct(cur.margin)}, ниже цели ${pct(target, 0)}.${room}` };
}
function recalc() {
  if (!T) return;
  last = compute(T.data);
  renderSummary();
  renderSteps();
}
function renderSummary() {
  const r = last, { b, cur } = r, d = T.data;
  const sum = $("#sum");
  if (!$("#s-ctl", sum)) {
    sum.innerHTML = `<div id="s-head" style="display:grid;gap:14px"></div>
      <div id="s-ctl" style="display:grid;gap:10px">
        <input type="range" id="s-range" min="0" max="30" step="0.5" aria-label="Снижение от НМЦК">
        <div class="pinp"><input id="s-price" inputmode="decimal" aria-label="Наша цена"><span>₽</span></div>
        <div class="chips" id="s-chips"></div>
      </div>
      <div id="s-body" style="display:grid;gap:12px"></div>`;
    $("#s-range").oninput = (e) => { T.data.bid = { mode: "disc", disc: num(e.target.value) }; recalc(); changed(); };
    $("#s-price").oninput = (e) => { T.data.bid = { mode: "price", price: num(e.target.value) }; recalc(); changed(); };
    $("#s-price").onchange = (e) => { e.target.value = last.cur.P > 0 ? fmtIn(round2(last.cur.P)) : ""; };
    $("#s-chips").onclick = (e) => {
      const btn = e.target.closest("button[data-q]");
      if (!btn) return;
      const q = btn.dataset.q;
      if (q === "target") T.data.bid = { mode: "target" };
      else if (q === "nmck") T.data.bid = { mode: "disc", disc: 0 };
      else if (q === "be" && last.be) T.data.bid = { mode: "price", price: Math.ceil(last.be) };
      recalc(); changed();
    };
  }
  const v = verdictOf(r);
  const mode = (d.bid && d.bid.mode) || "target";
  $("#s-head").innerHTML = `<div class="verdict ${v.cls}"><strong>${esc(v.t)}</strong><span>${esc(v.d)}</span></div>
    <div><div class="big">${cur.P > 0 ? rub(cur.P) : "—"}</div><div class="sub">${b.nmck > 0 && cur.P > 0 ? `Наша цена с НДС · снижение ${pct(cur.disc)} от НМЦК` : "Наша цена с НДС"}</div></div>`;
  const rng = $("#s-range");
  rng.hidden = !(b.nmck > 0);
  rng.max = String(Math.max(30, Math.ceil(((r.beDisc || 20) + 8) / 5) * 5));
  if (document.activeElement !== rng) rng.value = String(clamp(cur.disc, 0, num(rng.max)));
  const pi = $("#s-price");
  if (document.activeElement !== pi) pi.value = cur.P > 0 ? fmtIn(round2(cur.P)) : "";
  $("#s-chips").innerHTML = `<button type="button" data-q="target" class="${mode === "target" ? "on" : ""}">По цели ${pct(r.target, 0)}</button>
    ${b.nmck > 0 ? `<button type="button" data-q="nmck">По НМЦК</button>` : ""}
    ${r.be ? `<button type="button" data-q="be">Безубыточно</button>` : ""}`;
  const rows = [];
  const add = (l, val, cls = "") => rows.push(`<tr class="${cls}"><td>${l}</td><td class="r">${rub(val)}</td></tr>`);
  if (r.has) {
    add("Цена с НДС", cur.P, "tot");
    if (b.tax.vat > 0) add(`НДС ${b.tax.vat}% в цене`, cur.P - cur.R);
    add("Выручка без НДС", cur.R, "tot");
    add(b.tax.deduct ? "Товары и работы без НДС" : "Товары и работы", (b.direct * b.goods) / (b.gross || 1));
    if (b.extra) add("Доставка и прочее", (b.direct * b.extra) / (b.gross || 1));
    if (b.overhead) add("Накладные", b.overhead);
    if (cur.extra > 0.5) add("Обеспечение, деньги в обороте, площадка", cur.extra);
    add("Прибыль до налога", cur.pre, "tot");
    add(b.tax.kind === "profit" ? `Налог на прибыль ${b.tax.rate}%` : b.tax.kind === "income" ? `УСН ${b.tax.rate}% с выручки` : `УСН ${b.tax.rate}%`, cur.incomeTax);
    add("Чистая прибыль", cur.net, cur.net >= 0 ? "pos" : "neg");
    if (b.tax.vat > 0) add(cur.vatPay >= 0 ? "НДС к уплате в бюджет" : "НДС к возмещению", Math.abs(cur.vatPay));
  }
  const warns = [];
  if (b.missing) warns.push(`У ${b.missing} ${plural(b.missing, "позиции", "позиций", "позиций")} нет нашей цены, расчёт занижен.`);
  if (cur.antidump) warns.push("Снижение 25% и больше: обеспечение исполнения ×1,5 (ст. 37 44-ФЗ).");
  if (r.has && r.tgDisc !== null && r.tgDisc < 0) warns.push(`Целевая маржа недостижима в рамках НМЦК: при НМЦК маржа ${pct(r.nm.margin)}.`);
  if (b.tax.kind === "diff") warns.push("УСН 15%: по итогам года налог не меньше 1% от всех доходов.");
  const rec = recommend(r);
  if (rec) warns.unshift(...rec.warn);
  $("#s-body").innerHTML = `${rec ? `<div class="rec"><div class="sub">Рекомендуемая цена</div><div class="rec-v">${rub(rec.price)}</div><div class="sub">${esc(rec.why)}</div>
      ${rec.comp ? `<div class="sub" style="margin-top:4px">Конкуренты за лот: ${rec.comp.region ? `регион ≈ ${rub(rec.comp.region)}` : "регион —"} · РФ ≈ ${rec.comp.rf ? rub(rec.comp.rf) : "—"}</div>` : ""}
      <button class="btn sm" type="button" id="rec-use" style="margin-top:8px">Поставить эту цену</button></div>` : ""}
    ${rows.length ? `<table class="bd">${rows.join("")}</table>` : ""}
    <div class="sub">Чистая маржа: <b style="color:var(--panel-ink)">${r.has ? pct(cur.margin) : "—"}</b> · Безубыточно от: <b style="color:var(--panel-ink)">${r.be ? rub(r.be) : "—"}</b>${r.beDisc !== null ? (r.beDisc >= 0 ? ` (снижение до ${pct(r.beDisc)})` : ` (выше НМЦК на ${pct(-r.beDisc)})`) : ""}</div>
    ${warns.length ? `<ul class="warns">${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
  const ru = $("#rec-use");
  if (ru) ru.onclick = () => { T.data.bid = { mode: "price", price: rec.price }; recalc(); changed(); toast("Цена поставлена. Окончательное решение за вами"); };
  renderRisks();
}
function competitorLot(useRegion) {
  const mk = T.market;
  if (!mk || !mk.items) return null;
  const byId = new Map(mk.items.map((i) => [i.id, i]));
  let lot = 0, covered = 0, nmckCovered = 0, nmckLot = 0, ratioNum = 0;
  const items = T.data.items.filter((x) => num(x.qty) > 0);
  for (const x of items) {
    const st = byId.get(x.id);
    const s = st && (useRegion ? (st.region && st.region.n >= 2 ? st.region : null) : st.rf);
    nmckLot += num(x.qty) * num(x.nmckUnit);
    if (!s) continue;
    lot += num(x.qty) * s.median; covered++;
    if (num(x.nmckUnit) > 0) { nmckCovered += num(x.qty) * num(x.nmckUnit); ratioNum += num(x.qty) * s.median; }
  }
  if (!covered) return null;
  if (covered === items.length) return lot;
  const nmck = num(T.data.nmck);
  if (nmck > 0 && nmckLot > 0 && nmckCovered / nmckLot >= 0.5) return nmck * (ratioNum / nmckCovered);
  return null;
}
function recommend(r) {
  if (!r.has) return null;
  const minM = num(SETTINGS.minMarginPct ?? 3);
  const floor = solve(r.b, (c) => c.margin - minM);
  const comp = { region: competitorLot(true), rf: competitorLot(false) };
  const ref = comp.region || comp.rf;
  const nmck = r.b.nmck;
  const warn = [];
  let price, why;
  if (ref) { price = ref * 0.99; why = `На 1% ниже цен конкурентов ${comp.region ? "в регионе" : "по России"}.`; }
  else if (r.tg) { price = r.tg; why = `По целевой марже ${pct(r.target, 0)}. Цен конкурентов пока нет: нажмите «Найти в ЕИС».`; }
  else return null;
  if (nmck > 0 && price > nmck) { price = nmck; why += " Ограничено НМЦК."; }
  if (floor && price < floor) {
    if (nmck > 0 && floor > nmck) { warn.push(`Даже при НМЦК маржа ниже минимальной (${pct(minM, 0)}). Участвовать невыгодно.`); }
    else { price = floor; why = `Конкуренты дешевле. Ниже ${rub(floor)} опускаться нельзя: маржа станет меньше ${pct(minM, 0)}.`; }
  }
  const c = calcAt(r.b, price);
  why += ` Маржа ${pct(c.margin)}, чистая прибыль ${rub(c.net)}.`;
  price = nmck > 0 ? Math.min(Math.round(price), Math.floor(nmck)) : Math.round(price);
  return { price, why, comp: comp.region || comp.rf ? comp : null, warn };
}
function renderSteps() {
  const r = last, card = $("#steps-card");
  if (!(r.b.nmck > 0) || !r.has) { card.hidden = true; return; }
  card.hidden = false;
  const step = r.beDisc !== null && r.beDisc > 20 ? 1 : 0.5;
  const upTo = Math.min(60, r.beDisc !== null ? Math.max(r.beDisc + 3 * step, 5) : 30);
  const out = [];
  let best = -1, diff = Infinity;
  const ds = [];
  for (let x = 0; x <= upTo + 1e-9; x += step) ds.push(round2(x));
  ds.forEach((x, i) => { const dd = Math.abs(x - r.cur.disc); if (dd < diff) { diff = dd; best = i; } });
  ds.forEach((x, i) => {
    const c = calcAt(r.b, r.b.nmck * (1 - x / 100));
    const pill = c.net <= 0 ? `<span class="pill bad">убыток</span>` : c.margin >= r.target - 0.05 ? `<span class="pill good">цель</span>` : `<span class="pill warn">в плюсе</span>`;
    out.push(`<tr class="clickable${i === best ? " cur" : ""}" data-d="${x}"><td class="r">${x ? "−" + pct(x) : "0%"}</td><td class="r">${rub(c.P)}</td><td class="r">${rub(c.net)}</td><td class="r">${pct(c.margin)}</td><td>${pill}${c.antidump ? ' <span class="pill neutral">×1,5</span>' : ""}</td></tr>`);
  });
  const tb = $("#steps");
  tb.innerHTML = out.join("");
  tb.onclick = (e) => { const tr = e.target.closest("tr[data-d]"); if (!tr) return; T.data.bid = { mode: "disc", disc: num(tr.dataset.d) }; recalc(); changed(); };
}

/* загрузка с площадки */
function parseDays(s) {
  const m = String(s || "").match(/(\d{1,4})\s*(?:\(?[а-я ]*\)?\s*)?(календарн|рабоч)?[а-я]*\s*дн/i);
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] && /рабоч/i.test(m[2]) ? Math.ceil(n * 1.4) : n;
}
async function runImport(url) {
  if (!/^https?:\/\//i.test(url)) return toast("Вставьте ссылку на закупку, она начинается с https://");
  const btn = $("#imp-btn"), st = $("#imp-status");
  btn.disabled = true; st.textContent = "Открываю страницу закупки…";
  try {
    const { info } = await api("POST", `/api/tenders/${T.id}/import`, { url });
    const d = T.data;
    d.meta.url = url;
    for (const k of ["number", "customer", "subject", "deadline", "place", "contractTerm"]) if (info[k]) d.meta[k] = info[k];
    if (info.law) d.meta.law = info.law;
    if (info.nmck) d.nmck = info.nmck;
    if (info.appSecPct !== null && info.appSecPct !== undefined) d.adv.appSecPct = info.appSecPct;
    if (info.conSecPct !== null && info.conSecPct !== undefined) d.adv.conSecPct = info.conSecPct;
    if (info.advancePct !== null && info.advancePct !== undefined) d.adv.advancePct = info.advancePct;
    const days = parseDays(info.contractTerm);
    if (days) d.adv.contractDays = days;
    if (d.meta.law !== "44") d.adv.platformFee = d.adv.platformFee || 0;
    if (info.items && info.items.length) {
      d.items = info.items.map((x) => {
        const p = priceIndex.get(x.name.trim().toLowerCase());
        return { id: uid(), name: x.name, unit: x.unit || (p && p.unit) || "", qty: x.qty, nmckUnit: x.nmckUnit || 0, cost: p && p.cost > 0 ? p.cost : 0 };
      });
    }
    d.missingDocs = info.missingDocs || [];
    d.flags = info.flags || {};
    d.inn = info.inn || d.inn || "";
    if (info.deadline) { d.meta.deadlineAt = parseDeadline(info.deadline) || d.meta.deadlineAt; }
    d.bid = { mode: "target" };
    fillFields(); renderItems(); recalc(); renderStages(); changed(); await saveNow();
    $("#imp-btn").textContent = "Обновить с площадки";
    const found = info.items ? info.items.length : 0;
    st.textContent = `Загружено: ${found} ${plural(found, "позиция", "позиции", "позиций")}, документов найдено: ${info.docs.length}`;
    if (!found) toast("Позиции на странице не нашлись. Посмотрите ТЗ в документах и добавьте вручную.");
    loadDocs();
  } catch (e) {
    st.textContent = "";
    toast(e.message);
  } finally { btn.disabled = false; }
}

/* документы */
const kb = (n) => (n > 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " МБ" : Math.max(1, Math.round(n / 1024)) + " КБ");
async function loadDocs() {
  if (!T) return;
  const id = T.id;
  let r;
  try { r = await api("GET", `/api/tenders/${id}/docs`); } catch (e) { return; }
  if (!T || T.id !== id) return;
  const list = $("#docs");
  list.innerHTML = r.docs.map((x) => x.status === "ok"
    ? `<div class="doc"><a href="/api/tenders/${id}/docs/${x.id}">${esc(x.name)}</a><span class="small muted">${kb(x.size)}</span><button class="icon" type="button" data-doc="${x.id}" aria-label="Удалить документ">×</button></div>`
    : `<div class="doc"><span class="name">${esc(x.name)}<br><span class="err">${esc(x.error || "Не скачался")}</span></span>${x.src_url ? `<a class="btn sm" href="${esc(x.src_url)}" target="_blank" rel="noopener" style="flex:none">На площадке</a>` : ""}<button class="icon" type="button" data-doc="${x.id}" aria-label="Убрать">×</button></div>`
  ).join("") || `<p class="muted small">Документов пока нет.</p>`;
  list.onclick = async (e) => {
    const b = e.target.closest("[data-doc]");
    if (!b) return;
    try { await api("DELETE", `/api/tenders/${id}/docs/${b.dataset.doc}`); loadDocs(); } catch (ex) { toast(ex.message); }
  };
  T.docFlags = r.flags || {};
  renderRisks(); updateItemCells();
  const job = r.job;
  $("#docs-job").textContent = job && job.running ? `Скачиваю документы с площадки: ${job.done} из ${job.total}…` : "";
  clearInterval(docsTimer);
  if (job && job.running) docsTimer = setInterval(loadDocs, 2500);
  const miss = (T.data.missingDocs || []).filter((n) => !r.docs.some((x) => x.name.toLowerCase().includes(n.toLowerCase().slice(0, 20))));
  $("#docs-missing").innerHTML = miss.length
    ? `<div class="note warn" style="margin-top:10px">Эти файлы видны на площадке, но скачать их можно только после входа. Скачайте их там и загрузите сюда кнопкой «Загрузить файл»:<ul style="margin:6px 0 0;padding-left:18px">${miss.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>`
    : "";
}
async function uploadDocs(e) {
  const files = Array.from(e.target.files || []);
  e.target.value = "";
  for (const f of files) {
    try {
      $("#docs-job").textContent = `Загружаю ${f.name}…`;
      await api("POST", `/api/tenders/${T.id}/docs`, f, { "X-File-Name": encodeURIComponent(f.name), "Content-Type": "application/octet-stream" });
    } catch (ex) { toast(`${f.name}: ${ex.message}`); }
  }
  loadDocs();
}

/* ---------- работа с тендером: этапы, напоминания, календарь, сводка ---------- */
let marketTimer = 0;
function renderStages() {
  const d = T.data, st = d.stages;
  $("#stages").innerHTML = STAGES.map(([k, l]) => `<label class="check stage${st[k] ? " done" : ""}"><input type="checkbox" data-stage="${k}"${st[k] ? " checked" : ""}> <span>${esc(l)}</span></label>`).join("");
  $("#stages").onchange = (e) => { const k = e.target.dataset.stage; if (!k) return; st[k] = e.target.checked; renderStages(); changed(); };
  const ts = mskTs(d.meta.deadlineAt);
  const left = $("#dl-left");
  left.textContent = ts ? leftText(ts) : "срок не указан";
  left.className = "pill " + (ts ? urgency(ts) : "neutral");
}
async function loadReminders() {
  if (!T) return;
  const id = T.id;
  let r;
  try { r = await api("GET", `/api/tenders/${id}/reminders`); } catch (e) { return; }
  if (!T || T.id !== id) return;
  $("#rem-list").innerHTML = r.reminders.map((x) => `<div class="doc"><span class="name">${x.sent ? "✓ " : "⏰ "}${esc(x.text || "Напоминание")}<br><span class="small muted">${esc(fmtMsk(x.at))}</span></span><button class="icon" type="button" data-rem="${x.id}" aria-label="Удалить напоминание">×</button></div>`).join("")
    || `<p class="small muted">Напоминаний нет.</p>`;
  $("#rem-list").onclick = async (e) => {
    const b = e.target.closest("[data-rem]");
    if (!b) return;
    try { await api("DELETE", `/api/tenders/${id}/reminders/${b.dataset.rem}`); loadReminders(); } catch (ex) { toast(ex.message); }
  };
  if (!$("#rem-at").value) $("#rem-at").value = tsMsk(Math.floor(Date.now() / 1000) + 86400).slice(0, 11) + "10:00";
  api("GET", "/api/telegram").then((tg) => {
    $("#rem-hint").innerHTML = tg.linked ? "Напоминания придут в Telegram. За сутки и за 3 часа до окончания подачи Telegram напомнит сам."
      : tg.enabled ? 'Чтобы напоминания приходили на телефон, подключите Telegram в <a href="#/settings">Настройках</a>. Или нажмите «В календарь».'
      : "Напоминания видны на главной странице. Чтобы получать их на телефон, нажмите «В календарь».";
  }).catch(() => {});
}
function noteText() {
  const d = T.data, r = last, c = r.cur, b = r.b, L = [];
  L.push(`Тендер: ${d.meta.subject || "без названия"}`);
  if (d.meta.number) L.push(`№ ${d.meta.number} · ${LAWS[d.meta.law] || ""}`);
  if (d.meta.customer) L.push(`Заказчик: ${d.meta.customer}`);
  const ts = mskTs(d.meta.deadlineAt);
  if (ts) L.push(`Подача до: ${fmtMsk(ts)} (${leftText(ts)})`);
  if (d.meta.url) L.push(`Ссылка: ${d.meta.url}`);
  if (b.nmck > 0) L.push(`НМЦК: ${rub(b.nmck)}`);
  if (r.has) {
    L.push(`Закупка: ${rub(b.gross)}${d.supplierVat ? " с НДС" : ""}`);
    L.push(`Продажа (наша цена): ${rub(c.P)}${b.nmck > 0 ? `, снижение ${pct(c.disc)}` : ""}`);
    if (b.tax.vat > 0) L.push(`НДС к уплате: ${rub(Math.max(0, c.vatPay))}`);
    L.push(`${b.tax.kind === "profit" ? "Налог на прибыль" : "УСН"}: ${rub(c.incomeTax)}`);
    L.push(`Чистая прибыль: ${rub(c.net)}, маржа ${pct(c.margin)}`);
  }
  L.push(`Следующий шаг: ${nextStage(d)}`);
  if (d.note) L.push(`Заметка: ${d.note}`);
  return L.join("\n");
}
function exportIcs() {
  const d = T.data, ts = mskTs(d.meta.deadlineAt);
  if (!ts) return toast("Сначала укажите окончание подачи");
  const z = (t) => new Date(t * 1000).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const escI = (s) => String(s).replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/[,;]/g, (m) => "\\" + m);
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tender//ru", "BEGIN:VEVENT", `UID:tender-${T.id}-${ts}@tender`, `DTSTAMP:${z(Date.now() / 1000 | 0)}`,
    `DTSTART:${z(ts - 3600)}`, `DTEND:${z(ts)}`, `SUMMARY:${escI("Окончание подачи: " + (d.meta.subject || "тендер"))}`, `DESCRIPTION:${escI(noteText())}`,
    d.meta.url ? `URL:${d.meta.url}` : "", "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Завтра окончание подачи", "TRIGGER:-P1D", "END:VALARM",
    "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Через 3 часа окончание подачи", "TRIGGER:-PT2H", "END:VALARM", "END:VEVENT", "END:VCALENDAR"].filter(Boolean).join("\r\n");
  downloadFile(`tender-${d.meta.number || T.id}.ics`, ics, "text/calendar");
}

/* ---------- подводные камни ---------- */
function renderRisks() {
  if (!T || !$("#risks")) return;
  const d = T.data, r = last, f = { ...(d.flags || {}), ...(T.docFlags || {}) }, out = [];
  const add = (cls, text, quote) => out.push(`<li class="${cls}"><span>${esc(text)}</span>${quote ? `<span class="quote">«…${esc(quote)}…»</span>` : ""}</li>`);
  if (f.analogs) f.analogs.value === "allowed" ? add("good", "Эквивалент допускается: можно предложить аналог из вашего прайса.", f.analogs.quote) : add("bad", "Эквивалент не допускается: нужен именно указанный товар.", f.analogs.quote);
  if (f.trademark && !f.analogs) add("warn", "Указаны товарные знаки. Проверьте, допускаются ли аналоги.", f.trademark.quote);
  if (f.smp) add("warn", "Закупка только для малого бизнеса (СМП/СОНКО): проверьте, что вы есть в реестре МСП.", f.smp.quote);
  if (f.national) add("warn", "Нацрежим или ограничения для иностранных товаров: понадобятся документы о стране происхождения.", f.national.quote);
  if (f.samples) add("warn", "Нужны образцы товара.", f.samples.quote);
  if (f.installation) add("warn", "Есть монтаж или пусконаладка: заложите работы в цену.", f.installation.quote);
  if (f.unload) add("warn", "Разгрузка силами поставщика: заложите грузчиков.", f.unload.quote);
  if (f.multiaddr) add("warn", "Поставка партиями или по нескольким адресам: доставка обойдётся дороже.", f.multiaddr.quote);
  if (f.penalty) add("warn", "В проекте контракта есть штрафы и пени: прочитайте условия.", f.penalty.quote);
  const ts = mskTs(d.meta.deadlineAt);
  if (ts && ts - Date.now() / 1000 < 2 * 86400 && ts > Date.now() / 1000) add("bad", "До окончания подачи меньше 2 дней.");
  if (!(num(d.nmck) > 0)) add("warn", "НМЦ не указана: побеждает, скорее всего, самая низкая цена.");
  if (num(d.adv.advancePct) === 0 && num(d.adv.paymentDays) > 30) add("warn", "Оплата позже 30 дней без аванса: деньги будут заморожены.");
  if (num(d.adv.advancePct) >= 100) add("good", "100% предоплата: свои деньги в оборот не нужны.");
  if (num(d.adv.conSecPct) >= 10) add("warn", `Высокое обеспечение исполнения: ${pct(num(d.adv.conSecPct), 0)}.`);
  if ((d.missingDocs || []).length) add("warn", "Часть документов скачивается только после входа на площадку.");
  const mk = T.market;
  if (mk && mk.customer && mk.customer.count >= 4 && mk.customer.canceled / mk.customer.count >= 0.3) add("bad", `Заказчик часто отменяет закупки: ${mk.customer.canceled} из ${mk.customer.count} последних.`);
  if (r && r.has && r.be) {
    const comp = competitorLot(true) || competitorLot(false);
    if (comp && comp < r.be) add("bad", `Конкуренты продавали дешевле нашей безубыточной цены (${rub(comp)} против ${rub(r.be)}).`);
  }
  $("#risks").innerHTML = out.join("") || `<li class="neutral"><span>Особых условий не нашлось. Загрузите закупку по ссылке или документы, чтобы проверить подробнее.</span></li>`;
}

/* ---------- цены конкурентов (ЕИС) ---------- */
async function runMarket() {
  const d = T.data;
  const items = d.items.filter((x) => (x.name || "").trim() && num(x.qty) > 0);
  if (!items.length) return toast("Сначала добавьте позиции");
  try {
    await api("POST", `/api/tenders/${T.id}/market`, { items, place: d.meta.place, customer: d.meta.customer, inn: d.inn });
    $("#mk-run").disabled = true;
    pollMarket();
  } catch (e) { toast(e.message); }
}
function pollMarket() { clearInterval(marketTimer); marketTimer = setInterval(loadMarket, 3000); }
async function loadMarket() {
  if (!T) return;
  const id = T.id;
  let r;
  try { r = await api("GET", `/api/tenders/${id}/market`); } catch (e) { return; }
  if (!T || T.id !== id) return;
  const job = r.job;
  $("#mk-job").textContent = job && job.running ? `${job.text || "Ищу"}… (${job.done || 0} из ${job.total || "?"})` : job && job.error ? job.error : "";
  $("#mk-run").disabled = !!(job && job.running);
  if (job && job.running) { if (!marketTimer) pollMarket(); } else { clearInterval(marketTimer); marketTimer = 0; }
  T.market = r.data;
  renderMarket();
  recalc();
}
function renderMarket() {
  const mk = T.market, box = $("#mk-out");
  if (!mk) { box.innerHTML = `<p class="small muted">Нажмите «Найти в ЕИС». Поиск идёт на сервере и займёт 1–3 минуты.</p>`; return; }
  const st = (s) => (s ? `<b>${rub2(s.median)}</b><br><span class="small muted">${rub2(s.min)} – ${rub2(s.max)} · ${s.n} ${plural(s.n, "контракт", "контракта", "контрактов")}</span>` : `<span class="muted">нет данных</span>`);
  const suppliers = {};
  mk.items.forEach((i) => (i.matches || []).forEach((m) => { if (m.supplier) suppliers[m.supplier] = (suppliers[m.supplier] || 0) + 1; }));
  const top = Object.entries(suppliers).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const when = new Date(mk.at * 1000).toLocaleDateString("ru-RU");
  let html = `<p class="small muted">Регион тендера: ${esc(mk.region || "не определён")} · обновлено ${when} · цены за единицу из контрактов, обычно с НДС</p>
    <div class="table-wrap" style="margin-top:8px"><table style="min-width:640px"><thead><tr><th>Позиция</th><th class="r">В регионе</th><th class="r">По России</th><th>Найти вручную</th></tr></thead><tbody>
    ${mk.items.map((i) => `<tr><td>${esc(i.name)}<div class="small muted">поиск: «${esc(i.query)}»${i.error ? ` · <span class="err">${esc(i.error)}</span>` : ""}</div></td><td class="r">${st(i.region)}</td><td class="r">${st(i.rf)}</td>
      <td class="small"><a href="${esc(i.searchUrl)}" target="_blank" rel="noopener">ЕИС</a> · <a href="https://yandex.ru/search/?text=${encodeURIComponent(i.query + " цена купить оптом " + (mk.region || ""))}" target="_blank" rel="noopener">Яндекс</a></td></tr>`).join("")}
    </tbody></table></div>`;
  if (top.length) html += `<h3 style="margin-top:14px">Кто поставлял</h3><p class="small">${top.map(([n, c]) => `${esc(n)} (${c})`).join(" · ")}</p>`;
  const all = mk.items.flatMap((i) => (i.matches || []).map((m) => ({ ...m, item: i.name }))).sort((a, b) => (a.regionKey === mk.regionKey ? -1 : 0) - (b.regionKey === mk.regionKey ? -1 : 0)).slice(0, 15);
  if (all.length) html += `<details><summary>Контракты (${all.length})</summary><div class="docs-list">${all.map((m) => `<a class="doc" href="${esc(m.url)}" target="_blank" rel="noopener"><span class="name">${esc(m.name)}<br><span class="small muted">${esc([m.region, m.date, m.supplier].filter(Boolean).join(" · "))}</span></span><span class="num">${rub2(m.price)}/${esc(m.unit || "ед.")}</span></a>`).join("")}</div></details>`;
  const cu = mk.customer;
  if (cu) html += `<h3 style="margin-top:14px">Заказчик: последние закупки</h3><p class="small">Найдено ${cu.count}, отменено ${cu.canceled}. <a href="${esc(cu.url)}" target="_blank" rel="noopener">Все в ЕИС</a></p>
    <div class="docs-list">${cu.purchases.slice(0, 6).map((p) => `<a class="doc" href="${esc(p.url)}" target="_blank" rel="noopener"><span class="name">${esc(p.subject || p.number)}<br><span class="small muted">${esc([p.date, p.status].filter(Boolean).join(" · "))}</span></span><span class="num">${p.nmck ? rub(p.nmck) : ""}</span></a>`).join("")}</div>`;
  if (!mk.items.some((i) => i.rf)) html += `<p class="note" style="margin-top:10px">Похожих контрактов с ценой за единицу не нашлось. Попробуйте упростить названия позиций (например, «Щебень 5-20» вместо полного описания) и поискать снова, или откройте поиск в ЕИС и Яндексе по ссылкам.</p>`;
  box.innerHTML = html;
}

/* ---------- прайс ---------- */
async function viewPrices() {
  await loadPrices();
  app.innerHTML = `
    <div class="card">
      <div class="card-h"><h1>Мой прайс</h1><span class="muted small">Ваши закупочные цены. Видны только вам и подставляются в тендеры по названию.</span></div>
      <div class="row">
        <div class="inp grow"><input id="p-search" placeholder="Поиск" aria-label="Поиск по прайсу"></div>
        <button class="btn primary" id="p-add" type="button">Добавить</button>
        <button class="btn" id="p-imp-t" type="button">Вставить из Excel</button>
      </div>
      <div id="p-imp" hidden style="margin-top:12px">
        <p class="hint" style="margin-bottom:6px">Скопируйте в Excel столбцы <b>Наименование</b>, <b>Ед.</b>, <b>Цена</b>, Примечание и вставьте сюда. Совпавшие по названию строки обновятся.</p>
        <textarea id="p-paste" rows="6" placeholder="Масло И-40А, бочка 180 кг	кг	135	поставщик ООО «…»"></textarea>
        <div class="row" style="margin-top:8px"><button class="btn primary" id="p-imp-go" type="button">Добавить в прайс</button><span class="small muted" id="p-imp-st"></span></div>
      </div>
    </div>
    <div class="card" style="padding:0"><div class="table-wrap" style="border:0"><table class="items" style="min-width:720px">
      <thead><tr><th>Наименование</th><th style="width:90px">Ед.</th><th class="r" style="width:140px">Цена</th><th style="width:240px">Примечание</th><th style="width:40px"></th></tr></thead>
      <tbody id="p-body"></tbody></table></div></div>`;
  const render = () => {
    const q = $("#p-search").value.trim().toLowerCase();
    const list = q ? PRICES.filter((p) => (p.name + " " + p.note).toLowerCase().includes(q)) : PRICES;
    $("#p-body").innerHTML = list.slice(0, 1000).map((p) => `<tr data-id="${p.id}">
      <td><input data-pf="name" value="${esc(p.name)}" aria-label="Наименование"></td>
      <td><input data-pf="unit" value="${esc(p.unit)}" aria-label="Ед."></td>
      <td><input data-pf="cost" class="n" inputmode="decimal" value="${fmtIn(p.cost)}" aria-label="Цена"></td>
      <td><input data-pf="note" value="${esc(p.note)}" aria-label="Примечание"></td>
      <td><button class="icon" type="button" data-del="${p.id}" aria-label="Удалить">×</button></td></tr>`).join("")
      || `<tr><td colspan="5" class="muted" style="padding:14px">Прайс пуст. Добавьте позиции или вставьте из Excel.</td></tr>`;
  };
  render();
  $("#p-search").oninput = render;
  $("#p-body").onchange = async (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const p = PRICES.find((x) => String(x.id) === tr.dataset.id);
    if (!p) return;
    const f = e.target.dataset.pf;
    p[f] = f === "cost" ? num(e.target.value) : e.target.value.trim();
    if (f === "cost") e.target.value = fmtIn(p.cost);
    try { await api("PUT", "/api/prices/" + p.id, p); priceIndex = new Map(PRICES.map((x) => [x.name.trim().toLowerCase(), x])); } catch (ex) { toast(ex.message); }
  };
  $("#p-body").onclick = async (e) => {
    const b = e.target.closest("[data-del]");
    if (!b) return;
    try { await api("DELETE", "/api/prices/" + b.dataset.del); await loadPrices(); render(); } catch (ex) { toast(ex.message); }
  };
  $("#p-add").onclick = async () => {
    try {
      await api("POST", "/api/prices", { name: "Новая позиция " + (PRICES.length + 1), unit: "", cost: 0, note: "" });
      await loadPrices(); $("#p-search").value = ""; render();
      const last = $$("#p-body tr").find((tr) => $("input", tr).value.startsWith("Новая позиция"));
      if (last) { const i = $("input", last); i.focus(); i.select(); }
    } catch (ex) { toast(ex.message); }
  };
  $("#p-imp-t").onclick = () => { $("#p-imp").hidden = !$("#p-imp").hidden; };
  $("#p-imp-go").onclick = async () => {
    const t = $("#p-paste").value;
    const delim = t.includes("\t") ? "\t" : t.includes(";") ? ";" : ",";
    let rows = t.split(/\r?\n/).map((l) => l.split(delim).map((c) => c.trim())).filter((r) => r.some(Boolean));
    if (rows.length && !(num(rows[0][2]) > 0) && /наимен|назван|цена/i.test(rows[0].join(" "))) rows = rows.slice(1);
    const out = rows.map((r) => ({ name: r[0], unit: r[1] || "", cost: num(r[2]), note: r[3] || "" })).filter((r) => r.name && r.cost > 0);
    if (!out.length) return toast("Не нашёл строк с названием и ценой");
    try {
      const r = await api("POST", "/api/prices", { rows: out });
      $("#p-imp-st").textContent = `Готово: ${r.count}`; $("#p-paste").value = "";
      await loadPrices(); render();
    } catch (ex) { toast(ex.message); }
  };
}

/* ---------- настройки ---------- */
async function renderTg() {
  const box = $("#tg-card");
  let tg;
  try { tg = await api("GET", "/api/telegram"); } catch (e) { box.hidden = true; return; }
  if (!tg.enabled) { box.innerHTML = `<div class="card-h"><h2>Напоминания в Telegram</h2></div><p class="muted small">Telegram-бот на сервере не подключён. Напоминания видны на главной странице, а срок можно добавить в календарь телефона кнопкой «В календарь» в тендере.</p>`; return; }
  box.innerHTML = `<div class="card-h"><h2>Напоминания в Telegram</h2></div>
    ${tg.linked ? `<p>Telegram подключён. Сюда придут ваши напоминания, а за сутки и за 3 часа до окончания подачи придёт напоминание о сроке.</p>
      <div class="row" style="margin-top:10px"><button class="btn sm" type="button" id="tg-test">Проверить</button><button class="btn sm danger" type="button" id="tg-off">Отключить</button></div>`
    : `<p>Подключите Telegram, и напоминания о сроках будут приходить на телефон.</p><div class="row" style="margin-top:10px"><button class="btn primary" type="button" id="tg-on">Подключить Telegram</button></div><p class="hint" id="tg-hint" style="margin-top:6px"></p>`}`;
  const on = $("#tg-on"), test = $("#tg-test"), off = $("#tg-off");
  if (on) on.onclick = async () => {
    try {
      const r = await api("POST", "/api/telegram/link", {});
      $("#tg-hint").innerHTML = `Откроется Telegram, нажмите там «Запустить» (Start). Если не открылся: <a href="${esc(r.url)}" target="_blank" rel="noopener">открыть бота</a>. Потом обновите эту страницу.`;
      window.open(r.url, "_blank", "noopener");
    } catch (ex) { toast(ex.message); }
  };
  if (test) test.onclick = async () => { try { await api("POST", "/api/telegram/test", {}); toast("Сообщение отправлено в Telegram"); } catch (ex) { toast(ex.message); } };
  if (off) off.onclick = async () => { try { await api("DELETE", "/api/telegram/link"); renderTg(); } catch (ex) { toast(ex.message); } };
}
function viewSettings() {
  const s = SETTINGS;
  app.innerHTML = `
    <div class="card">
      <div class="card-h"><h1>Настройки</h1><span class="muted small">Применяются к новым тендерам</span></div>
      <div class="fields">
        <div class="field wide"><label for="s-name">Ваше имя</label><div class="inp"><input id="s-name" maxlength="80" value="${esc(ME.name)}"></div></div>
        <div class="field"><label for="s-regime">Налоговый режим</label><div class="inp"><select id="s-regime">${Object.entries(REGIMES).map(([k, v]) => `<option value="${k}"${s.tax.regime === k ? " selected" : ""}>${v}</option>`).join("")}</select></div></div>
        <div class="field" id="s-usnvat-f"><label for="s-usnvat">НДС на УСН</label><div class="inp"><select id="s-usnvat">${Object.entries(USN_VAT).map(([k, v]) => `<option value="${k}"${Number(s.tax.usnVat) === Number(k) ? " selected" : ""}>${v}</option>`).join("")}</select></div></div>
        <div class="field" id="s-usnrate-f"><label for="s-usnrate">Ставка УСН</label><div class="inp"><input id="s-usnrate" class="n" inputmode="decimal" value="${fmtIn(s.tax.usnRate, true)}"><span class="u">%</span></div></div>
        <div class="field"><label for="s-over">Накладные</label><div class="inp"><input id="s-over" class="n" inputmode="decimal" value="${fmtIn(s.overheadPct, true)}"><span class="u">%</span></div></div>
        <div class="field"><label for="s-target">Целевая маржа</label><div class="inp"><input id="s-target" class="n" inputmode="decimal" value="${fmtIn(s.targetPct, true)}"><span class="u">%</span></div></div>
        <div class="field"><label for="s-money">Стоимость денег</label><div class="inp"><input id="s-money" class="n" inputmode="decimal" value="${fmtIn(s.moneyRatePct, true)}"><span class="u">% год.</span></div></div>
        <div class="field"><label for="s-bg">Банковская гарантия</label><div class="inp"><input id="s-bg" class="n" inputmode="decimal" value="${fmtIn(s.bgRatePct, true)}"><span class="u">% год.</span></div></div>
        <div class="field"><label for="s-minm">Минимальная маржа</label><div class="inp"><input id="s-minm" class="n" inputmode="decimal" value="${fmtIn(s.minMarginPct ?? 3, true)}"><span class="u">%</span></div><span class="hint">Ниже неё рекомендуемая цена не опустится</span></div>
      </div>
      <label class="check" style="margin-top:12px"><input type="checkbox" id="s-svat"${s.supplierVat ? " checked" : ""}> <span>Цены поставщиков обычно с НДС 22%</span></label>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="s-save" type="button">Сохранить</button></div>
    </div>
    <div class="card" id="tg-card"></div>
    <div class="card">
      <div class="card-h"><h2>Личная ссылка</h2></div>
      <p>Откройте её на телефоне или другом компьютере, и там будут ваши тендеры и прайс. Никому её не пересылайте: по ней открываются ваши данные.</p>
      <div class="row" style="margin-top:10px"><div class="inp grow"><input id="k-link" readonly value="${esc(personalLink())}" aria-label="Личная ссылка"></div><button class="btn primary" type="button" id="k-copy">Копировать</button></div>
      <div class="row" style="margin-top:14px"><span id="k-new-box"><button class="btn sm" type="button" id="k-new">Сменить ссылку</button></span><span class="hint">Старая ссылка перестанет работать на всех устройствах, кроме этого.</span></div>
    </div>
    <div class="card">
      <div class="card-h"><h2>Выйти на этом устройстве</h2></div>
      <p class="muted small">Без личной ссылки вернуться к своим тендерам будет нельзя. Сначала сохраните её.</p>
      <div class="row" style="margin-top:10px"><span id="lo-box"><button class="btn sm danger" type="button" id="lo">Выйти</button></span></div>
    </div>`;
  const syncUsn = () => {
    const usn = $("#s-regime").value !== "osno";
    $("#s-usnvat-f").hidden = !usn; $("#s-usnrate-f").hidden = !usn;
    if (usn && !num($("#s-usnrate").value)) $("#s-usnrate").value = $("#s-regime").value === "usn6" ? "6" : "15";
  };
  syncUsn();
  $("#s-regime").onchange = syncUsn;
  $("#s-save").onclick = async () => {
    try {
      const r = await api("PUT", "/api/profile", {
        name: $("#s-name").value,
        settings: {
          tax: { regime: $("#s-regime").value, usnVat: num($("#s-usnvat").value), usnRate: num($("#s-usnrate").value) },
          overheadPct: num($("#s-over").value), targetPct: num($("#s-target").value), moneyRatePct: num($("#s-money").value), bgRatePct: num($("#s-bg").value),
          supplierVat: $("#s-svat").checked, minMarginPct: num($("#s-minm").value),
        },
      });
      ME = r.user; SETTINGS = r.settings;
      $("#nav-user").textContent = ME.name ? `Настройки · ${ME.name}` : "Настройки";
      toast("Сохранено");
    } catch (ex) { toast(ex.message); }
  };
  $("#k-copy").onclick = () => copyText(personalLink(), $("#k-link"));
  renderTg();
  $("#k-new").onclick = (e) => confirmInline(e.currentTarget, "Сменить ссылку?", async () => {
    const r = await api("POST", "/api/relink", {});
    KEY = r.key; viewSettings(); toast("Новая ссылка готова. Сохраните её");
  });
  $("#lo").onclick = (e) => confirmInline(e.currentTarget, "Ссылка сохранена?", async () => {
    await api("POST", "/api/logout", {});
    ME = null; KEY = ""; history.replaceState(null, "", location.pathname + "#/"); showStart("");
  });
}

app.addEventListener("input", onField);
app.addEventListener("change", onField);
boot().catch(() => showStart(""));
