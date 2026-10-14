/* miso internet sales 통화 대시보드 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);
  const el = (tag, attrs = {}, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "style") n.style.cssText = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) n.append(c);
    return n;
  };

  // ---------- 색 ----------
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const isDark = () => false; // 라이트 모드만 지원
  // 에이전트 비교 선 색 — 에이전트 ID 순서에 고정(필터로 다시 칠하지 않음). 색약 검증된 순서.
  const CAT = {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  };
  let agentOrder = [];
  const agentColor = (id) => {
    const i = agentOrder.indexOf(id);
    const pal = CAT[isDark() ? "dark" : "light"];
    return i >= 0 && i < pal.length ? pal[i] : css("--text-3");
  };
  // 히트맵: 토스 블루 단계(적음 → 많음)
  const HEAT = {
    light: ["#e8f3ff", "#c9e2ff", "#90c2ff", "#64a8ff", "#4593fc", "#3182f6", "#2272eb", "#1b64da", "#1957c2"],
    dark: ["#1a2a44", "#1d3a66", "#21508f", "#2563bb", "#2b74db", "#3182f6", "#4593fc", "#64a8ff", "#90c2ff"],
  };
  const heatColor = (v, max) => {
    if (!v || !max) return null;
    const ramp = HEAT[isDark() ? "dark" : "light"];
    const idx = Math.min(ramp.length - 1, Math.floor((v / max) * (ramp.length - 1) + 1e-4));
    const white = isDark() ? idx < 7 : idx >= 3;
    return { bg: ramp[idx], text: white ? "on-dark" : "on-light" };
  };

  // ---------- 숫자·시간 표기 ----------
  const nf = new Intl.NumberFormat("ko-KR");
  const fmtInt = (n) => (n == null ? "–" : nf.format(n));
  /** 사람이 읽는 시간: 45초 / 2분 54초 / 71시간 40분 */
  const fmtDur = (sec) => {
    if (sec == null) return "–";
    sec = Math.round(sec);
    if (sec < 60) return `${sec}초`;
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return `${nf.format(h)}시간 ${m}분`;
    return s ? `${m}분 ${s}초` : `${m}분`;
  };
  /** 좁은 자리용: 2:54 / 1:02:03 */
  const fmtClock = (sec) => {
    if (sec == null) return "–";
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const p = (x) => String(x).padStart(2, "0");
    return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
  };
  const fmtPct = (r) => (r == null ? "–" : `${(r * 100).toFixed(1)}%`);
  const fmtBytes = (b) => {
    if (!b) return "0B";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return `${i ? b.toFixed(1) : b}${u[i]}`;
  };
  const DOW = ["일", "월", "화", "수", "목", "금", "토"];
  const toDate = (iso) => new Date(iso.length <= 10 ? iso + "T00:00:00" : iso.replace(" ", "T"));
  const md = (iso) => { const d = toDate(iso); return `${d.getMonth() + 1}.${d.getDate()}`; };
  const dow = (iso) => DOW[toDate(iso).getDay()];
  const isWeekend = (iso) => [0, 6].includes(toDate(iso).getDay());
  const dateTimeKo = (iso) => {
    const d = toDate(iso);
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getMonth() + 1}월 ${d.getDate()}일 (${DOW[d.getDay()]}) ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  /** 방금 / 3분 전 / 2시간 전 / 오늘·어제 18:20 / 10월 11일 (토) 09:12 */
  const fmtAgo = (iso) => {
    if (!iso) return "–";
    const d = toDate(iso), min = Math.floor((Date.now() - d) / 60000);
    if (min < 1) return "방금";
    if (min < 60) return `${min}분 전`;
    if (min < 6 * 60) return `${Math.floor(min / 60)}시간 전`;
    const p = (x) => String(x).padStart(2, "0");
    const y = new Date(); y.setDate(y.getDate() - 1);
    if (d.toDateString() === new Date().toDateString()) return `오늘 ${p(d.getHours())}:${p(d.getMinutes())}`;
    if (d.toDateString() === y.toDateString()) return `어제 ${p(d.getHours())}:${p(d.getMinutes())}`;
    return dateTimeKo(iso);
  };

  // ---------- API ----------
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error(data.error || "로그인이 필요해요."); }
    if (!res.ok) { const e = new Error(data.error || `요청에 실패했어요 (${res.status})`); e.status = res.status; throw e; }
    return data;
  }
  const toast = (msg) => {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
  };
  /** 토스식 확인 창. 지원 안 되는 브라우저는 기본 confirm. */
  function ask(title, body, okLabel, danger = false) {
    const d = $("#confirm");
    if (typeof d.showModal !== "function") return Promise.resolve(confirm(`${title}\n\n${body}`));
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    $("#confirm-ok").textContent = okLabel;
    $("#confirm-ok").className = `btn btn-lg ${danger ? "btn-danger-fill" : "btn-primary"}`;
    d.returnValue = "";
    d.showModal();
    return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }));
  }

  // ---------- 상태 ----------
  const state = {
    user: null, preset: "today", start: null, end: null, agentId: "",
    data: null, shownAgents: new Set(), rankKey: "handled", sheetAgent: null,
  };
  try {
    const saved = JSON.parse(localStorage.getItem("cti.filters") || "{}");
    if (saved.preset) Object.assign(state, { preset: saved.preset, start: saved.start, end: saved.end, agentId: saved.agentId || "" });
  } catch { /* 저장소 없음 */ }
  const saveFilters = () => {
    try {
      localStorage.setItem("cti.filters", JSON.stringify({ preset: state.preset, start: state.start, end: state.end, agentId: state.agentId }));
    } catch { /* 무시 */ }
  };
  const periodQuery = () => {
    const p = new URLSearchParams({ preset: state.preset });
    if (state.preset === "custom") { p.set("start", state.start); p.set("end", state.end); }
    return p;
  };
  const PERIOD = {
    today: { now: "오늘", prev: "어제" },
    yesterday: { now: "어제", prev: "그저께" },
    this_week: { now: "이번 주", prev: "지난주" },
    this_month: { now: "이번 달", prev: "지난달" },
    custom: { now: "선택한 기간", prev: "이전 기간" },
  };

  // ---------- 로그인 ----------
  let signupMode = false;
  function renderLoginMode() {
    const newPw = signupMode;
    $("#login-title").replaceChildren(...(signupMode ? ["회원가입"]
      : [el("span", { class: "login-brand" }, "miso internet sales"), "통화 대시보드"]));
    $("#login-sub").textContent = signupMode ? "회사 이메일의 슬랙으로 인증번호를 보내 드려요" : "아이디와 비밀번호를 입력해 주세요";
    $("#code-row").hidden = !signupMode;
    $("#username-label").textContent = signupMode ? "회사 이메일" : "아이디";
    const name = $("#login-form [name=username]"), pw = $("#login-form [name=password]");
    name.placeholder = signupMode ? "name@getmiso.com" : "";
    name.type = signupMode ? "email" : "text";
    pw.autocomplete = newPw ? "new-password" : "current-password";
    pw.placeholder = newPw ? "8자 이상" : "";
    $("#password2-row").hidden = !newPw;
    $("#login-btn").textContent = signupMode ? "가입하고 시작하기" : "로그인";
    $("#signup-toggle").textContent = signupMode ? "로그인으로 돌아가기" : "회원가입";
    $("#login-error").textContent = "";
  }
  $("#signup-toggle").addEventListener("click", () => { signupMode = !signupMode; renderLoginMode(); });
  $("#send-code").addEventListener("click", async () => {
    const email = $("#login-form [name=username]").value.trim(), err = $("#login-error"), btn = $("#send-code");
    err.textContent = "";
    if (!/@getmiso\.com$/i.test(email)) { err.textContent = "@getmiso.com 이메일을 먼저 입력해 주세요."; return; }
    btn.disabled = true;
    try {
      await api("/api/signup/code", { method: "POST", body: JSON.stringify({ username: email }) });
      toast("슬랙 DM 으로 인증번호를 보냈어요");
      btn.textContent = "다시 받기";
      $("#login-form [name=code]").focus();
      setTimeout(() => { btn.disabled = false; }, 60000);
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
  async function showLogin() {
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    closeSheet();
    setTimeout(() => $("#login-form [name=username]").focus(), 0);
    try {
      const st = await api("/api/status");
      if (st.needs_setup) signupMode = true;  // 계정이 없으면 바로 회원가입
      const s = $("#storage-status");
      s.className = `storage-status ${st.storage === "temporary" ? "temp" : "ok"}`;
      s.textContent = st.storage === "permanent" ? "저장소 연결됨"
        : st.storage === "temporary" ? "임시 저장소 · 데모 데이터" : "이 컴퓨터에 저장 중";
    } catch { /* 무시 */ }
    renderLoginMode();
  }
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const err = $("#login-error");
    err.textContent = "";
    if (!f.username || !f.password) { err.textContent = "아이디와 비밀번호를 모두 입력해 주세요."; return; }
    if (signupMode && f.password !== f.password2) { err.textContent = "두 비밀번호가 달라요."; return; }
    if (signupMode && !/@getmiso\.com$/i.test(f.username.trim())) { err.textContent = "@getmiso.com 이메일로만 가입할 수 있어요."; return; }
    if (signupMode && !/^\d{6}$/.test((f.code || "").trim())) { err.textContent = "슬랙으로 받은 인증번호 6자리를 입력해 주세요."; return; }
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      state.user = await api(signupMode ? "/api/signup" : "/api/login",
        { method: "POST", body: JSON.stringify({ username: f.username, password: f.password, code: f.code }) });
      signupMode = false;
      e.target.reset();
      await startApp();
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });

  // 사용자 메뉴
  const userMenu = $("#user-menu"), userBtn = $("#user-btn");
  const setMenu = (open) => { userMenu.hidden = !open; userBtn.setAttribute("aria-expanded", String(open)); };
  userBtn.addEventListener("click", (e) => { e.stopPropagation(); setMenu(userMenu.hidden); });
  document.addEventListener("click", (e) => { if (!userMenu.contains(e.target)) setMenu(false); });
  $("#logout").addEventListener("click", async () => {
    setMenu(false);
    await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {});
    state.user = null; showLogin();
  });

  async function startApp() {
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    const u = state.user;
    $("#user-initial").textContent = (u.username || "?").slice(0, 1).toUpperCase();
    $("#user-name").textContent = u.username;
    $("#user-role").textContent = u.role === "admin" ? "관리자" : "회원";
    $("#demo-badge").hidden = !u.demo;
    $$('.nav-item[data-view="admin"]').forEach((a) => { a.hidden = u.role !== "admin"; });
    const agents = await api("/api/agents");
    agentOrder = agents.map((a) => a.id).sort((a, b) => a - b);
    const sel = $("#agent-filter");
    sel.replaceChildren(el("option", { value: "" }, "에이전트 전체"),
      ...agents.map((a) => el("option", { value: a.id }, a.name)));
    if (!agents.some((a) => String(a.id) === String(state.agentId))) state.agentId = "";
    sel.value = state.agentId;
    syncFilterUI();
    setupLookup();
    try {
      const st = await api("/api/settings");
      state.minSec = st.min_call_sec; state.source = st.source;
      state.webapp = !!st.drive_webapp_url; state.webappUrl = st.drive_webapp_url;
    } catch { state.minSec = 0; }
    updateSyncButton();
    loadDriveStatus();
    dashboardStale = true;
    callsLoaded = false;
    await route();
  }

  // ---------- 메뉴 ----------
  const VIEWS = ["calls", "stats", "settings", "admin"];
  let dashboardStale = true;
  let callsLoaded = false;
  async function route() {
    if (!state.user) return;
    let view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "calls";
    if (view === "admin" && state.user.role !== "admin") view = "calls";
    $$(".nav-item").forEach((a) => {
      const on = a.dataset.view === view;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    $("#calls-view").hidden = view !== "calls";
    $("#dashboard-view").hidden = view !== "stats";
    $("#settings-view").hidden = view !== "settings";
    $("#admin-view").hidden = view !== "admin";
    closeSheet();
    closeCall();
    window.scrollTo(0, 0);
    if (view === "calls") {
      if (!callsLoaded || dashboardStale) { callsLoaded = true; await loadCalls(); }
    } else if (view === "stats") {
      if (dashboardStale) { dashboardStale = false; await load(); }
    } else if (view === "admin") {
      await loadUsers();
    } else {
      await loadSettings();
    }
  }
  window.addEventListener("hashchange", route);

  // ---------- 필터 ----------
  const todayIso = () => {
    const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  };
  function syncFilterUI() {
    $$(".chip[data-preset]").forEach((b) => {
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(b.dataset.preset === state.preset));
    });
    $("#custom-range").hidden = state.preset !== "custom";
    $("#start-date").value = state.start || todayIso();
    $("#end-date").value = state.end || todayIso();
    const sel = $("#agent-filter");
    $("#agent-chip-label").textContent = sel.selectedOptions[0]?.textContent || "에이전트 전체";
    sel.closest(".chip-select").classList.toggle("on", !!state.agentId);
  }
  $$(".chip[data-preset]").forEach((b) => b.addEventListener("click", () => {
    state.preset = b.dataset.preset;
    if (state.preset === "custom") {
      state.start = state.start || todayIso(); state.end = state.end || todayIso();
      syncFilterUI(); return; // 조회 버튼을 눌러야 불러옴
    }
    syncFilterUI(); saveFilters(); load();
  }));
  $("#apply-range").addEventListener("click", () => {
    const s = $("#start-date").value, e = $("#end-date").value;
    if (!s || !e) return toast("시작일과 종료일을 골라 주세요.");
    [state.start, state.end] = s <= e ? [s, e] : [e, s];
    syncFilterUI(); saveFilters(); load();
  });
  $("#trend-to-week").addEventListener("click", () => {
    state.preset = "this_week"; syncFilterUI(); saveFilters(); load();
  });
  $("#agent-filter").addEventListener("change", (e) => {
    state.agentId = e.target.value; syncFilterUI(); saveFilters(); load();
  });

  // ---------- 불러오기 ----------
  let loadSeq = 0;
  async function load() {
    const seq = ++loadSeq;
    const q = periodQuery();
    if (state.agentId) q.set("agent_id", state.agentId);
    try {
      const data = await api(`/api/dashboard?${q}`);
      if (seq !== loadSeq) return;
      state.data = data;
      render();
    } catch (err) {
      if (state.user) toast(err.message);
    }
  }
  function render() {
    const d = state.data;
    renderHero(d);
    renderTrend(d.daily_trend);
    renderHourly(d.hourly);
    renderRank(d.agent_performance);
    renderMatrix(d.daily_matrix);
  }

  // ---------- 핵심 지표 ----------
  /** 증감: 한국식 색(증가 빨강·감소 파랑). kind: "pct"(변화율) | "pp"(비율 지표, %p) */
  function delta(m, kind = "pct") {
    if (!m.delta || m.prev == null) return { cls: "flat", text: "비교할 수 없음" };
    const v = m.delta.abs;
    if (!v) return { cls: "flat", text: "변화 없음" };
    const up = v > 0, arrow = up ? "▲" : "▼";
    if (kind === "pp") return { cls: up ? "up" : "down", text: `${arrow} ${Math.abs(v * 100).toFixed(1)}%p` };
    if (m.delta.pct == null) return { cls: up ? "up" : "down", text: `${arrow} 새로 생김` };
    return { cls: up ? "up" : "down", text: `${arrow} ${Math.abs(m.delta.pct).toFixed(1)}%` };
  }

  function renderHero(d) {
    const k = d.kpis, p = PERIOD[state.preset] || PERIOD.custom;
    const agentName = state.agentId ? $("#agent-filter").selectedOptions[0]?.textContent : null;
    const calls = k.total_calls, dc = delta(calls);
    const diff = calls.prev == null ? null : calls.value - calls.prev;
    const diffText = diff == null ? "" : diff === 0 ? "변화 없어요" : `${p.prev}보다 ${fmtInt(Math.abs(diff))}건 ${diff > 0 ? "많아요" : "적어요"}`;

    const top = [...d.agent_performance].sort((a, b) => b.handled - a.handled)[0];
    const insight = calls.value === 0
      ? "아직 통화 기록이 없어요."
      : !state.agentId && top && top.handled
        ? [`가장 많이 처리한 에이전트는 `, el("b", {}, `${top.name}님`), `이에요 · ${fmtInt(top.handled)}건`]
        : [`처리 건수는 `, el("b", {}, `${fmtInt(d.agent_performance.reduce((s, r) => s + r.handled, 0))}건`), `이에요 (짧은 통화 포함)`];

    const minis = [
      { label: "총 통화시간", m: k.total_sec, fmt: fmtDur },
      { label: "평균 통화시간", m: k.att_sec, fmt: fmtDur, title: "짧은 통화를 포함한 전체 통화의 평균(ATT)" },
      { label: "고객 수", m: k.customers, fmt: (v) => `${fmtInt(v)}명`, title: "같은 번호는 한 번만 셉니다" },
      { label: `짧은 통화 (${d.min_call_sec}초 미만)`, m: k.short_ratio, fmt: fmtPct, kind: "pp" },
    ];

    $("#hero").replaceChildren(
      el("div", { class: "hero-label" }, `${agentName ? agentName + "님의 " : ""}${p.now} 통화`),
      el("div", { class: "hero-value" }, fmtInt(calls.value), el("small", {}, "건")),
      el("div", { class: "delta-line" },
        el("span", { class: `delta ${dc.cls}` }, dc.text),
        diffText ? el("span", { class: "delta-text" }, diffText) : null),
      el("div", { class: "mini-grid" }, minis.map((x) => {
        const dd = delta(x.m, x.kind);
        return el("div", { class: "mini", title: x.title || null },
          el("div", { class: "k" }, x.label),
          el("div", { class: "v" }, x.fmt(x.m.value)),
          el("div", { class: `d ${dd.cls}` }, dd.cls === "flat" ? dd.text : `${p.prev}보다 ${dd.text}`));
      })),
      el("p", { class: "hero-insight" }, insight));
  }

  // ---------- 차트 공통 ----------
  const charts = {};
  const FONT = '"Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif';
  if (window.Chart) {
    Chart.defaults.font.family = FONT;
    Chart.defaults.font.size = 12;
  }
  if (document.fonts) document.fonts.ready.then(() => { if (state.data && !$("#dashboard-view").hidden) render(); });

  function baseOptions() {
    const t3 = css("--text-3");
    return {
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      layout: { padding: { top: 4 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: isDark() ? "#3a3a44" : "#333d4b", titleColor: "#fff", bodyColor: "#fff",
          padding: 12, cornerRadius: 12, displayColors: false, titleFont: { weight: "600" },
          bodyFont: { size: 13 }, titleMarginBottom: 4,
        },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: t3, maxRotation: 0, autoSkipPadding: 16 } },
        y: { beginAtZero: true, grid: { color: css("--chart-grid") }, border: { display: false }, ticks: { color: t3, precision: 0, maxTicksLimit: 5, padding: 8 } },
      },
    };
  }
  const upsert = (id, config) => {
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), config);
  };
  /** 선 아래 옅은 블루 그라데이션 */
  const areaFill = (ctx) => {
    const { chart } = ctx;
    const area = chart.chartArea;
    if (!area) return "transparent";
    const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, isDark() ? "rgba(5,92,245,.35)" : "rgba(5,92,245,.16)");
    g.addColorStop(1, "rgba(5,92,245,0)");
    return g;
  };

  // ---------- 일별 통화량 ----------
  function renderTrend(t) {
    const n = t.dates.length, total = t.total.reduce((a, b) => a + b, 0);
    const single = n === 1;
    $("#trend-caption").textContent = single
      ? `${md(t.dates[0])} (${dow(t.dates[0])}) 하루 ${fmtInt(total)}건`
      : `하루 평균 ${fmtInt(Math.round(total / n))}건`;
    $("#trend-box").hidden = single;
    $("#trend-empty").hidden = !single;
    if (single) {
      $("#trend-legend").hidden = true;
      if (charts["trend-chart"]) { charts["trend-chart"].destroy(); delete charts["trend-chart"]; }
      return;
    }

    const series = t.series.map((s) => ({ ...s, color: agentColor(s.agent_id) }));
    $("#trend-legend").hidden = series.length < 2;
    $("#trend-legend").replaceChildren(...series.map((s) => el("button", {
      class: "chip", style: `--c:${s.color}`,
      "aria-pressed": String(state.shownAgents.has(s.agent_id)),
      onclick: () => {
        state.shownAgents.has(s.agent_id) ? state.shownAgents.delete(s.agent_id) : state.shownAgents.add(s.agent_id);
        renderTrend(t);
      },
    }, el("span", { class: "dot" }), s.name)));

    const blue = css("--blue");
    const opts = baseOptions();
    opts.interaction = { mode: "index", intersect: false };
    opts.plugins.tooltip.displayColors = true;
    opts.plugins.tooltip.boxPadding = 4;
    opts.plugins.tooltip.callbacks = {
      title: (items) => { const iso = t.dates[items[0].dataIndex]; return `${md(iso)} (${dow(iso)})`; },
      label: (item) => ` ${item.dataset.label} ${fmtInt(item.raw)}건`,
    };
    const datasets = [{
      label: state.agentId ? "통화" : "전체", data: t.total, borderColor: blue, backgroundColor: areaFill, fill: true,
      borderWidth: 2.5, pointRadius: 0, pointBackgroundColor: blue, pointHoverRadius: 5,
      pointHoverBackgroundColor: blue, pointHoverBorderColor: css("--card"), pointHoverBorderWidth: 2,
      cubicInterpolationMode: "monotone", order: 2,
    }];
    for (const s of series) {
      if (!state.shownAgents.has(s.agent_id)) continue;
      datasets.push({
        label: s.name, data: s.counts, borderColor: s.color, backgroundColor: s.color, fill: false,
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, cubicInterpolationMode: "monotone", order: 1,
      });
    }
    upsert("trend-chart", { type: "line", data: { labels: t.dates.map(md), datasets }, options: opts });
  }

  // ---------- 시간대별 ----------
  function renderHourly(h) {
    const max = Math.max(...h);
    const peak = h.indexOf(max);
    $("#hourly-caption").textContent = max ? `가장 바쁜 시간은 ${peak}시예요 · ${fmtInt(max)}건` : "통화 기록이 없어요";
    const opts = baseOptions();
    opts.scales.x.ticks.callback = (v, i) => (i % 3 === 0 ? `${i}시` : "");
    opts.scales.x.ticks.autoSkip = false;
    opts.plugins.tooltip.callbacks = {
      title: (items) => `${items[0].dataIndex}시 ~ ${items[0].dataIndex + 1}시`,
      label: (item) => `${fmtInt(item.raw)}건`,
    };
    const blue = css("--blue"), soft = isDark() ? "#2b4a75" : "#c9e2ff";
    upsert("hourly-chart", {
      type: "bar",
      data: {
        labels: h.map((_, i) => `${i}시`),
        datasets: [{
          data: h, backgroundColor: h.map((v) => (v === max && max ? blue : soft)),
          hoverBackgroundColor: blue, borderRadius: 6, borderSkipped: false,
          categoryPercentage: 0.9, barPercentage: 0.85, minBarLength: 0,
        }],
      },
      options: opts,
    });
  }

  // ---------- 에이전트 순위 ----------
  const RANK = [
    { key: "handled", label: "처리건수", fmt: (v) => `${fmtInt(v)}건` },
    { key: "total_sec", label: "통화시간", fmt: fmtDur },
    { key: "att_sec", label: "평균", fmt: fmtDur },
    { key: "longest_sec", label: "최장", fmt: fmtDur },
  ];
  function renderRank(rows) {
    const cur = RANK.find((r) => r.key === state.rankKey) || RANK[0];
    $("#rank-sort").replaceChildren(...RANK.map((r) => el("button", {
      role: "tab", "aria-selected": String(r.key === cur.key),
      onclick: () => { state.rankKey = r.key; renderRank(rows); },
    }, r.label)));
    const sorted = [...rows].sort((a, b) => (b[cur.key] ?? -1) - (a[cur.key] ?? -1) || a.name.localeCompare(b.name, "ko"));
    const max = Math.max(1, ...sorted.map((r) => r[cur.key] || 0));
    $("#rank-list").replaceChildren(...(sorted.length ? sorted.map((r, i) => el("li", {},
      el("button", { class: "rank-row", onclick: () => openSheet(r.agent_id) },
        el("span", { class: `rank-no${i < 3 && r[cur.key] ? " top" : ""}` }, String(i + 1)),
        el("span", { class: "avatar" }, r.name.slice(0, 1)),
        el("span", { class: "rank-main" },
          el("div", { class: "rank-name" }, r.name),
          el("div", { class: "rank-sub" },
            `처리 ${fmtInt(r.handled)}건 · 평균 ${fmtClock(r.att_sec)} · 최장 ${fmtClock(r.longest_sec)}`),
          el("div", { class: "rank-bar" }, el("i", { style: `width:${((r[cur.key] || 0) / max) * 100}%` }))),
        el("span", { class: "rank-val" }, cur.fmt(r[cur.key]))))) : [el("li", { class: "empty" }, "에이전트가 없어요")]));
  }

  // ---------- 일별 처리건수(히트맵) ----------
  function renderMatrix(m) {
    const thead = el("thead", {}, el("tr", {},
      el("th", { class: "name" }, ""),
      m.dates.map((d) => el("th", { class: isWeekend(d) ? "weekend" : null, title: d }, md(d), el("span", { class: "dow" }, dow(d)))),
      el("th", {}, "합계")));
    const tbody = el("tbody", {}, m.rows.map((r) => el("tr", {},
      el("td", { class: "name" }, el("button", { onclick: () => openSheet(r.agent_id) }, r.name)),
      r.counts.map((v, i) => {
        const c = heatColor(v, m.max_cell);
        return el("td", {
          class: `cell${c ? " " + c.text : ""}`, style: c ? `background:${c.bg}` : null,
          title: `${r.name} · ${m.dates[i]} · ${v}건`,
        }, v ? fmtInt(v) : "–");
      }),
      el("td", { class: "tot" }, fmtInt(r.total)))));
    const tfoot = el("tfoot", {}, el("tr", {},
      el("td", { class: "name" }, "합계"),
      m.col_totals.map((v) => el("td", {}, fmtInt(v))),
      el("td", { class: "tot" }, fmtInt(m.grand_total))));
    $("#matrix").replaceChildren(thead, tbody, tfoot);
  }

  // ---------- 에이전트 상세 시트 ----------
  async function openSheet(agentId) {
    state.sheetAgent = agentId;
    const sheet = $("#sheet");
    const row = state.data?.agent_performance.find((r) => r.agent_id === agentId);
    $("#sheet-title").textContent = row ? `${row.name}님` : "불러오는 중…";
    $("#sheet-avatar").textContent = row ? row.name.slice(0, 1) : "";
    $("#sheet-sub").textContent = "";
    $("#backup-msg").textContent = "";
    sheet.classList.add("open");
    sheet.setAttribute("aria-hidden", "false");
    $("#sheet-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    try {
      const d = await api(`/api/agents/${agentId}?${periodQuery()}`);
      if (state.sheetAgent !== agentId) return;
      renderSheet(d, row);
      $("#sheet-close").focus();
    } catch (err) {
      toast(err.message);
    }
  }
  function closeSheet() {
    state.sheetAgent = null;
    $("#sheet").classList.remove("open");
    $("#sheet").setAttribute("aria-hidden", "true");
    $("#sheet-backdrop").hidden = true;
    document.body.style.overflow = "";
  }
  $("#sheet-close").addEventListener("click", closeSheet);
  $("#sheet-backdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.sheetAgent != null) closeSheet(); });
  // 모바일: 시트 손잡이를 아래로 끌면 닫기
  (() => {
    let y0 = null;
    const head = $(".sheet-grabber");
    const sheet = $("#sheet");
    const start = (e) => { y0 = e.touches[0].clientY; sheet.style.transition = "none"; };
    const move = (e) => { if (y0 == null) return; const dy = Math.max(0, e.touches[0].clientY - y0); sheet.style.transform = `translateY(${dy}px)`; };
    const end = (e) => {
      if (y0 == null) return;
      const dy = e.changedTouches[0].clientY - y0; y0 = null;
      sheet.style.transition = ""; sheet.style.transform = "";
      if (dy > 80) closeSheet();
    };
    for (const t of [head, $(".sheet-head")]) {
      t.addEventListener("touchstart", start, { passive: true });
      t.addEventListener("touchmove", move, { passive: true });
      t.addEventListener("touchend", end);
    }
  })();

  function renderSheet(d, row) {
    const a = d.agent, p = PERIOD[state.preset] || PERIOD.custom;
    $("#sheet-title").textContent = `${a.name}님`;
    $("#sheet-avatar").textContent = a.name.slice(0, 1);
    $("#sheet-sub").textContent = [a.extension ? `내선 ${a.extension}` : null, `${p.now} 기준`].filter(Boolean).join(" · ");
    const handled = d.trend.handled.reduce((s, v) => s + v, 0);
    $("#sheet-stats").replaceChildren(
      el("div", { class: "stat" }, el("div", { class: "k" }, "처리건수"), el("div", { class: "v" }, `${fmtInt(handled)}건`)),
      el("div", { class: "stat" }, el("div", { class: "k" }, "평균 통화"), el("div", { class: "v" }, fmtClock(row?.att_sec))),
      el("div", { class: "stat" }, el("div", { class: "k" }, "총 통화"), el("div", { class: "v" }, fmtClock(row?.total_sec))));

    const opts = baseOptions();
    opts.interaction = { mode: "index", intersect: false };
    opts.plugins.tooltip.callbacks = {
      title: (items) => { const iso = d.trend.dates[items[0].dataIndex]; return `${md(iso)} (${dow(iso)})`; },
      label: (item) => `처리 ${fmtInt(item.raw)}건 · 평균 ${fmtClock(d.trend.att_sec[item.dataIndex])}`,
    };
    const blue = css("--blue"), single = d.trend.dates.length === 1;
    upsert("sheet-chart", {
      type: "line",
      data: {
        labels: d.trend.dates.map(md),
        datasets: [{
          data: d.trend.handled, borderColor: blue, backgroundColor: areaFill, fill: true, borderWidth: 2.5,
          pointRadius: single ? 5 : 0, pointBackgroundColor: blue, pointHoverRadius: 5, cubicInterpolationMode: "monotone",
        }],
      },
      options: opts,
    });

    const minSec = state.data?.min_call_sec ?? 0;
    $("#recent").replaceChildren(...(d.recent_calls.length ? d.recent_calls.map((c) => el("li", {},
      el("div", { class: "main" },
        el("div", { class: "t" }, dateTimeKo(c.started_at)),
        el("div", { class: "s" }, c.customer_masked || "번호 없음")),
      el("div", { class: `r${c.duration_sec < minSec ? " short" : ""}`, title: c.duration_sec < minSec ? "짧은 통화" : null },
        fmtDur(c.duration_sec)))) : [el("li", { class: "empty" }, "통화 기록이 없어요")]));

    const isAdmin = state.user.role === "admin" && d.sync_folder;
    $("#admin-section").hidden = !isAdmin;
    if (isAdmin) renderAdmin(a, d.sync_folder, d.backups);
  }

  function renderAdmin(agent, folder, backups) {
    const box = $("#sync-status");
    if (folder.error) {
      box.replaceChildren(el("div", { class: "k" }, "폴더 설정을 확인해 주세요"), el("div", { class: "path" }, folder.error));
    } else {
      box.replaceChildren(
        el("div", { class: "k" }, "아직 백업하지 않은 녹취"),
        el("div", { class: "big" }, folder.exists ? `${fmtInt(folder.file_count)}개 · ${fmtBytes(folder.total_bytes)}` : "폴더 없음"),
        el("div", { class: "path" }, folder.path));
    }
    const btn = $("#backup-btn");
    btn.disabled = !!folder.error || !folder.file_count;
    btn.onclick = () => runBackup(agent, folder);
    $("#backups").replaceChildren(...(backups.length ? backups.map((b) => el("li", {},
      el("div", { class: "main" },
        el("div", { class: "t" }, dateTimeKo(b.created_at)),
        el("div", { class: "s" }, `${b.performed_by} · ${b.archive_path.split(/[\\/]/).pop()}`)),
      el("div", { class: "r" }, `${fmtInt(b.file_count)}개 · ${fmtBytes(b.total_bytes)}`))) : [el("li", { class: "empty" }, "백업 기록이 없어요")]));
  }

  async function runBackup(agent, folder) {
    const ok = await ask(`녹취 ${fmtInt(folder.file_count)}개를 백업하고 비울까요?`,
      `${agent.name}님 폴더의 파일을 압축해서 보관한 뒤 폴더에서 지워요. 통화 기록은 먼저 저장돼요.`, "비우기", true);
    if (!ok) return;
    const btn = $("#backup-btn"), msg = $("#backup-msg");
    btn.disabled = true;
    msg.className = "form-msg"; msg.textContent = "백업하는 중이에요…";
    try {
      const r = await api(`/api/agents/${agent.id}/backup`, { method: "POST", body: "{}" });
      const b = r.backup;
      const notes = b.delete_failed
        ? [`${fmtInt(b.file_count)}개를 백업했지만 ${fmtInt(b.delete_failed)}개는 지우지 못했어요. 폴더 삭제 권한을 확인해 주세요. 백업 파일은 안전해요.`]
        : [`${fmtInt(b.file_count)}개(${fmtBytes(b.total_bytes)})를 백업하고 폴더를 비웠어요.`];
      if (b.ingested) notes.push(`새 통화 ${fmtInt(b.ingested)}건을 먼저 기록했어요.`);
      if (b.left_recent) notes.push(`올라오는 중인 파일 ${fmtInt(b.left_recent)}개는 남겨 뒀어요.`);
      if (b.unparsed) notes.push(`이름을 읽지 못한 ${fmtInt(b.unparsed)}개는 백업만 했어요.`);
      msg.className = `form-msg ${b.delete_failed ? "err" : "ok"}`;
      msg.textContent = notes.join(" ");
      if (b.ingested) load();
      if (state.sheetAgent === agent.id) renderAdmin(agent, r.sync_folder, r.backups);
    } catch (err) {
      msg.className = "form-msg err"; msg.textContent = err.message;
      btn.disabled = false;
    }
  }

  // ---------- 통화 조회 ----------
  const QUICK_DATES = [["today", "오늘"], ["yesterday", "어제"], ["this_week", "이번 주"], ["this_month", "이번 달"], ["all", "전체"]];
  const cs = {
    start: null, end: null, hfrom: 0, hto: 24, agentId: "", num: "", hideShort: false,
    sameAs: null, sameLabel: "", items: [], cursor: null, total: 0, seq: 0, current: null, error: null,
  };
  const iso = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
  function quickRange(key) {
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const add = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    if (key === "today") return [iso(t), iso(t)];
    if (key === "yesterday") return [iso(add(t, -1)), iso(add(t, -1))];
    if (key === "this_week") return [iso(add(t, -((t.getDay() + 6) % 7))), iso(t)];
    if (key === "this_month") return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)];
    return ["", ""]; // 전체
  }
  const hourLabel = (h) => (h === 24 ? "24시" : `${String(h).padStart(2, "0")}시`);

  function setupLookup() {
    $("#lk-hfrom").replaceChildren(...Array.from({ length: 24 }, (_, h) => el("option", { value: h }, hourLabel(h))));
    $("#lk-hto").replaceChildren(...Array.from({ length: 24 }, (_, h) => el("option", { value: h + 1 }, hourLabel(h + 1))));
    const agents = [...$("#agent-filter").options].slice(1);
    $("#lk-agent").replaceChildren(el("option", { value: "" }, "전체"), ...agents.map((o) => el("option", { value: o.value }, o.textContent)));
    resetLookup();
  }
  function resetLookup() {
    [cs.start, cs.end] = quickRange("today");
    Object.assign(cs, { hfrom: 0, hto: 24, agentId: "", num: "", hideShort: false, sameAs: null });
    fillLookup();
  }
  function fillLookup() {
    $("#lk-start").value = cs.start; $("#lk-end").value = cs.end;
    $("#lk-hfrom").value = cs.hfrom; $("#lk-hto").value = cs.hto;
    $("#lk-agent").value = cs.agentId; $("#lk-num").value = cs.num; $("#lk-short").checked = cs.hideShort;
    markQuickDate();
  }
  function markQuickDate() {
    const s = $("#lk-start").value, e = $("#lk-end").value;
    // 같은 기간이 여러 개면(월요일의 오늘=이번 주) 앞의 것 하나만 표시
    const hit = QUICK_DATES.findIndex(([key]) => { const [qs, qe] = quickRange(key); return qs === s && qe === e; });
    $("#lk-dates").replaceChildren(...QUICK_DATES.map(([key, label], i) => {
      return el("button", {
        type: "button", class: "chip", role: "radio", "aria-checked": String(i === hit),
        onclick: () => { [$("#lk-start").value, $("#lk-end").value] = quickRange(key); markQuickDate(); },
      }, label);
    }));
  }
  $("#lk-start").addEventListener("change", markQuickDate);
  $("#lk-end").addEventListener("change", markQuickDate);
  // ---------- [동기화] 버튼 ----------
  function updateSyncButton() {
    // 드라이브 연결일 때만. 웹 앱 주소가 없으면 관리자에게만 보여서 설정으로 안내
    $("#sync-now").hidden = state.source !== "drive" || (!state.webapp && state.user?.role !== "admin");
  }
  // 드라이브 연결 상태 → [점 색, 한 줄 문구]. 녹취 화면과 설정 화면이 같이 쓴다
  function driveStatusText(st) {
    switch (st.state) {
      case "never": return ["", "아직 드라이브와 연결되지 않았어요"];
      case "error": return ["error", `동기화 오류 · ${fmtAgo(st.last_run?.at || st.last_seen)}`];
      case "stale": return ["stale", `자동 동기화가 멈춘 것 같아요 · 마지막 ${fmtAgo(st.last_seen)}`];
      default: return ["ok", `연결됨 · ${fmtAgo(st.last_seen)} 동기화`];
    }
  }
  function renderSyncLine() {
    const line = $("#sync-status"), st = state.driveStatus;
    line.hidden = state.source !== "drive" || !st;
    if (line.hidden) return;
    const [cls, text] = driveStatusText(st);
    line.className = `conn-status small ${cls}`;
    line.textContent = text;
  }
  async function loadDriveStatus() {
    if (state.source !== "drive") return;
    try { state.driveStatus = await api("/api/drive/status"); } catch { return; }
    renderSyncLine();
    if (!$("#drive-card").hidden) renderDriveConn(state.driveStatus);
  }
  // 'N분 전'이 멈춰 있지 않게
  setInterval(() => { if (state.driveStatus) renderSyncLine(); }, 30000);
  // 방식: server(대시보드 서버가 부름, '모든 사용자' 배포) | tab(새 탭에서 회사 계정으로, '회사의 모든 사용자' 배포)
  const MODE_KEY = "cti.syncMode";
  const getMode = () => { try { return localStorage.getItem(MODE_KEY); } catch { return null; } };
  const setMode = (m) => { try { m ? localStorage.setItem(MODE_KEY, m) : localStorage.removeItem(MODE_KEY); } catch { /* 무시 */ } };
  let tabSyncPending = false;
  const tabUrl = () => `${state.webappUrl}?action=sync`;
  function syncInTab(win) {
    tabSyncPending = true;
    if (win) win.location.href = tabUrl(); else window.open(tabUrl(), "_blank");
    toast("동기화하고 있어요. 잠시 열리는 탭은 끝나면 저절로 닫혀요.");
  }
  // 새 탭(구글 스크립트 결과 화면)이 보내는 결과 → 알림으로 보여 주고 목록 새로 고침
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.type !== "cti-sync") return;
    if (!/^https:\/\/([a-z0-9-]+\.)*(googleusercontent\.com|google\.com)$/.test(e.origin)) return;
    tabSyncPending = false;
    toast(d.ok ? d.message : `${d.title} · ${d.message}`);
    if (d.ok) { dashboardStale = true; loadCalls(); }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && tabSyncPending) {
      tabSyncPending = false; dashboardStale = true;
      setTimeout(() => loadCalls().then(() => toast("동기화 결과를 반영했어요.")), 300);
    }
  });
  $("#sync-now").addEventListener("click", async () => {
    const btn = $("#sync-now");
    if (!state.webapp) { toast("설정에서 웹 앱 주소를 먼저 넣어 주세요."); location.hash = "#settings"; return; }
    const mode = getMode();
    if (mode === "tab") return syncInTab(null);
    // 처음엔 어느 방식인지 모르니, 팝업 차단을 피하려고 빈 탭을 먼저 열어 둔다(서버 방식이 되면 닫음)
    const pre = mode ? null : window.open("about:blank", "_blank");
    btn.disabled = true; btn.classList.add("spinning"); btn.querySelector("span").textContent = "동기화 중…";
    try {
      const r = await api("/api/drive/sync-now", { method: "POST", body: "{}" });
      if (pre) pre.close();
      setMode("server");
      const parts = [];
      if (r.pending) parts.push("동기화를 시작했어요. 잠시 후 다시 조회해 주세요.");
      else {
        parts.push(r.added ? `새 녹취 ${fmtInt(r.added)}건을 가져왔어요.` : "새 녹취가 없어요.");
        if (r.backups) parts.push(`백업 ${fmtInt(r.backups)}건을 처리했어요.`);
        if (r.more) parts.push("남은 건 곧 이어서 가져와요.");
      }
      toast(parts.join(" "));
      dashboardStale = true;
      await loadCalls();
    } catch (err) {
      if (err.status === 502) {
        // 서버가 못 부름 = 회사 계정 전용 배포 → 새 탭 방식으로 전환하고 기억
        setMode("tab");
        syncInTab(pre);
      } else {
        if (pre) pre.close();
        toast(err.message);
      }
    } finally {
      btn.disabled = false; btn.classList.remove("spinning"); btn.querySelector("span").textContent = "동기화";
    }
  });
  $("#webapp-save").addEventListener("click", async () => {
    const msg = $("#webapp-msg");
    try {
      const s = await api("/api/drive/webapp", { method: "POST", body: JSON.stringify({ url: $("#webapp-url").value.trim() }) });
      state.webapp = !!s.drive_webapp_url; state.webappUrl = s.drive_webapp_url; setMode(null); updateSyncButton();
      msg.className = "form-msg ok";
      msg.textContent = state.webapp ? "저장했어요. 녹취 화면에서 [동기화]를 눌러 보세요." : "웹 앱 주소를 지웠어요.";
    } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
  });

  $("#lookup-reset").addEventListener("click", () => { resetLookup(); $("#lookup-err").textContent = ""; loadCalls(); });

  $("#lookup").addEventListener("submit", (e) => {
    e.preventDefault();
    const err = $("#lookup-err"); err.textContent = "";
    let s = $("#lk-start").value, en = $("#lk-end").value;
    if ((s && !en) || (!s && en)) { err.textContent = "날짜를 둘 다 고르거나 '전체'를 눌러 주세요."; return; }
    if (s && en && s > en) [s, en] = [en, s];
    const hf = Number($("#lk-hfrom").value), ht = Number($("#lk-hto").value);
    if (hf >= ht) { err.textContent = "끝 시각을 시작 시각보다 늦게 골라 주세요."; return; }
    const num = $("#lk-num").value.trim(), digits = num.replace(/\D/g, "");
    if (num && /^[\d\s\-+().]+$/.test(num) && digits.length !== 4 && digits.length < 8) {
      err.textContent = "고객번호는 전체 번호나 뒤 4자리로 넣어 주세요."; return;
    }
    Object.assign(cs, { start: s, end: en, hfrom: hf, hto: ht, agentId: $("#lk-agent").value, num,
      hideShort: $("#lk-short").checked, sameAs: null });
    $("#lk-num").blur();
    loadCalls().then(() => {
      // 휴대폰: 조회 후 결과가 보이게 스크롤
      if (innerWidth <= 720) $("#calls-summary").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  function callsQuery(cursor) {
    const p = new URLSearchParams();
    if (cs.start && cs.end) { p.set("preset", "custom"); p.set("start", cs.start); p.set("end", cs.end); }
    else p.set("preset", "all");
    if (cs.hfrom > 0) p.set("hour_from", cs.hfrom);
    if (cs.hto < 24) p.set("hour_to", cs.hto);
    if (cs.agentId) p.set("agent_id", cs.agentId);
    if (cs.sameAs) p.set("same_as", cs.sameAs);
    else if (cs.num) p.set("q", cs.num);
    if (cs.hideShort && state.minSec) p.set("min_sec", state.minSec);
    if (cursor) p.set("cursor", cursor);
    return p;
  }
  /** 조회 조건을 사람이 읽는 한 줄로 */
  function conditionText() {
    if (cs.sameAs) return `${cs.sameLabel} 고객 · 모든 날짜`;
    const parts = [];
    if (!cs.start) parts.push("모든 날짜");
    else if (cs.start === cs.end) parts.push(`${md(cs.start)} (${dow(cs.start)})`);
    else parts.push(`${md(cs.start)} ~ ${md(cs.end)}`);
    if (cs.hfrom > 0 || cs.hto < 24) parts.push(`${cs.hfrom}시~${cs.hto}시`);
    if (cs.agentId) parts.push($(`#lk-agent option[value="${cs.agentId}"]`)?.textContent || "에이전트");
    if (cs.num) parts.push(`‘${cs.num}’`);
    if (cs.hideShort) parts.push("짧은 통화 제외");
    return parts.join(" · ");
  }

  async function loadCalls(more = false) {
    const seq = ++cs.seq;
    const btn = $("#calls-more");
    if (more) { btn.disabled = true; btn.textContent = "불러오는 중…"; }
    try {
      const r = await api(`/api/calls?${callsQuery(more ? cs.cursor : null)}`);
      if (seq !== cs.seq) return;
      cs.items = more ? cs.items.concat(r.items) : r.items;
      cs.cursor = r.next_cursor; cs.total = r.total; cs.byCustomer = r.by_customer;
      cs.error = null;
      renderCalls();
      loadDriveStatus();
    } catch (err) {
      if (seq !== cs.seq) return;
      cs.error = err.message; cs.items = []; cs.cursor = null; cs.total = 0;
      renderCalls();
    } finally {
      btn.disabled = false; btn.textContent = "더 보기";
    }
  }

  function renderCalls() {
    const sum = $("#calls-summary");
    if (cs.error) {
      sum.replaceChildren(el("span", { class: "warn" }, cs.error));
    } else {
      sum.replaceChildren(...[el("span", {}, conditionText(), " → ", el("b", {}, `${fmtInt(cs.total)}건`)),
        cs.sameAs ? el("button", { class: "clear", onclick: () => { cs.sameAs = null; loadCalls(); } }, "해제") : null,
      ].filter(Boolean));
    }

    const list = $("#calls-list");
    if (!cs.items.length) {
      list.replaceChildren(el("div", { class: "empty" }, cs.error ? "조회 조건을 확인해 주세요" : "조건에 맞는 통화가 없어요"));
      $("#calls-more").hidden = true;
      return;
    }
    const minSec = state.minSec || 0;
    const nodes = [];
    let lastDay = null;
    const today = todayIso();
    for (const c of cs.items) {
      const day = c.started_at.slice(0, 10);
      if (day !== lastDay) {
        lastDay = day;
        const d = toDate(day);
        nodes.push(el("div", { class: "day-head" }, day === today ? "오늘" : `${d.getMonth() + 1}월 ${d.getDate()}일`,
          el("span", {}, `${dow(day)}요일`)));
      }
      nodes.push(el("button", {
        class: `call-row${cs.current === c.id ? " current" : ""}`, "data-id": c.id,
        onclick: () => openCall(c.id),
      },
      el("span", { class: "call-time" }, c.started_at.slice(11, 16)),
      el("span", { class: "call-main" },
        el("div", { class: `call-num${c.customer_masked ? "" : " none"}` }, c.customer_masked || "번호 없음"),
        el("div", { class: "call-agent" }, `${c.agent_name} 에이전트`)),
      el("span", { class: `call-dur${c.duration_sec < minSec ? " short" : ""}` }, fmtDur(c.duration_sec)),
      el("span", { class: `play-ic${c.playable ? "" : " off"}`, "aria-label": c.playable ? "재생 가능" : "재생 불가" },
        playIcon())));
    }
    list.replaceChildren(...nodes);
    $("#calls-more").hidden = !cs.cursor;
  }
  $("#calls-more").addEventListener("click", () => loadCalls(true));
  const playIcon = () => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "16"); s.setAttribute("height", "16");
    s.innerHTML = '<path fill="currentColor" d="M8 5.5v13a1 1 0 0 0 1.5.9l10.4-6.5a1 1 0 0 0 0-1.8L9.5 4.6A1 1 0 0 0 8 5.5Z"/>';
    return s;
  };

  // ---------- 녹취 재생 시트 ----------
  async function openCall(id) {
    cs.current = id;
    $$(".call-row").forEach((r) => r.classList.toggle("current", Number(r.dataset.id) === id));
    const sheet = $("#call-sheet");
    $("#call-player").replaceChildren(el("div", { class: "player-none" }, "불러오는 중…"));
    $("#call-facts").replaceChildren();
    $("#call-same").hidden = true;
    sheet.classList.add("open"); sheet.setAttribute("aria-hidden", "false");
    $("#call-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    try {
      const d = await api(`/api/calls/${id}`);
      if (cs.current !== id) return;
      renderCall(d);
      $("#call-close").focus();
    } catch (err) {
      $("#call-player").replaceChildren(el("div", { class: "player-none" }, err.message));
    }
  }
  function closeCall() {
    const sheet = $("#call-sheet");
    if (!sheet.classList.contains("open")) return;
    sheet.classList.remove("open"); sheet.setAttribute("aria-hidden", "true");
    $("#call-backdrop").hidden = true;
    document.body.style.overflow = "";
    $("#call-player").replaceChildren(); // 재생 멈춤
  }
  $("#call-close").addEventListener("click", closeCall);
  $("#call-backdrop").addEventListener("click", closeCall);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCall(); });

  function renderCall(d) {
    $("#call-avatar").textContent = d.agent_name.slice(0, 1);
    $("#call-title").textContent = d.customer_masked || "번호 없는 통화";
    $("#call-sub").textContent = `${d.agent_name} 에이전트 · ${dateTimeKo(d.started_at)}`;

    const player = $("#call-player");
    if (!d.play) {
      player.replaceChildren(el("div", { class: "player-none" }, "이 녹취는 바로 들을 수 없어요 (드라이브 연결 전에 들어온 기록)"));
    } else if (d.play.type === "demo") {
      player.replaceChildren(
        el("audio", { controls: true, autoplay: true, preload: "auto", src: d.play.url }),
        el("p", { class: "player-note" }, "데모용 소리예요. 드라이브를 연결하면 실제 녹취가 재생돼요."));
    } else {
      player.replaceChildren(
        el("iframe", { src: d.play.preview_url, allow: "autoplay", title: "녹취 재생", loading: "lazy" }),
        el("div", { class: "player-links" },
          el("a", { class: "btn btn-secondary", href: d.play.open_url, target: "_blank", rel: "noopener" }, "드라이브에서 열기")),
        el("p", { class: "player-note" }, "회사 구글 계정으로 로그인돼 있어야 들을 수 있어요."));
    }

    const row = (k, v) => el("div", { class: "status-row" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v));
    $("#call-facts").replaceChildren(
      row("통화 시작", dateTimeKo(d.started_at)),
      row("통화 시간", `${fmtDur(d.duration_sec)}${d.duration_estimated ? " (추정)" : ""}`),
      row("에이전트", `${d.agent_name}${d.extension ? ` · 내선 ${d.extension}` : ""}`),
      row("고객번호", d.customer_masked || "없음"));

    const same = $("#call-same");
    same.hidden = !d.customer_masked || d.same_customer_calls < 2;
    same.textContent = `이 고객 통화 모두 보기 · ${fmtInt(d.same_customer_calls)}건`;
    same.onclick = () => {
      cs.sameAs = d.id; cs.sameLabel = d.customer_masked;
      closeCall();
      if (location.hash !== "#calls") location.hash = "#calls";
      loadCalls();
    };
  }

  // ---------- 설정 ----------
  const SETTING_FIELDS = [
    { key: "min_call_sec", input: "#min-call-sec", min: 0, max: 600, label: "짧은 통화 기준" },
    { key: "fallback_kbps", input: "#fallback-kbps", min: 8, max: 512, label: "비트레이트" },
  ];
  function fillSettings(s) {
    for (const f of SETTING_FIELDS) $(f.input).value = s[f.key];
    renderIngestStatus(s.last_ingest);
    const drive = s.source === "drive";
    $("#drive-card").hidden = !drive || state.user.role !== "admin";
    try { if (!$("#drive-folder").value) $("#drive-folder").value = localStorage.getItem("cti.driveFolder") || ""; } catch { /* 무시 */ }
    $("#webapp-url").value = s.drive_webapp_url || "";
    state.webapp = !!s.drive_webapp_url; state.webappUrl = s.drive_webapp_url; state.source = s.source; updateSyncButton();
    $("#backup-card").hidden = !drive || state.user.role !== "admin";
    if (drive && state.user.role === "admin") loadDriveBackup();
    $("#ingest-now").hidden = drive || state.user.role !== "admin";
    $("#ingest-card h2").textContent = drive ? "드라이브에서 받은 녹취" : "녹취 가져오기";
    loadUploads();
  }
  function renderDriveConn(st) {
    const box = $("#drive-status");
    box.hidden = !st;
    if (!st) return;
    const [cls, text] = driveStatusText(st);
    const run = st.last_run;
    const subs = [];
    if (st.state === "ok" && run?.ok) {
      subs.push(run.added ? `지난번에 새 녹취 ${fmtInt(run.added)}건을 가져왔어요${run.more ? " · 남은 건 이어서 가져와요" : ""}`
        : "지난번에는 새 녹취가 없었어요");
    }
    if (st.state === "stale") subs.push(`${st.stale_min}분 넘게 소식이 없어요. script.google.com 에서 '설치'를 다시 실행해 주세요.`);
    if (st.state === "error") {
      subs.push(run?.error || "알 수 없는 오류");
      if (st.last_ok) subs.push(`마지막으로 성공한 동기화 · ${fmtAgo(st.last_ok)}`);
    }
    box.replaceChildren(el("div", { class: `conn-status ${cls}` }, text),
      ...subs.map((s, i) => el("div", { class: `conn-sub${st.state === "error" && i === 0 ? " err" : ""}` }, s)));
  }
  // ---------- 에이전트별 업로드 · 슬랙 알림 ----------
  async function loadUploads() {
    try { renderUploads(await api("/api/uploads")); } catch (err) { toast(err.message); }
  }
  function renderUploads(u) {
    state.uploads = u;
    $("#uploads-card").hidden = false;
    if (u.drive) {
      state.driveStatus = u.drive; renderSyncLine();
      if (!$("#drive-card").hidden) renderDriveConn(u.drive);
    }
    $("#uploads-list").replaceChildren(...(u.agents.length ? u.agents.map((a) => el("li", {},
      el("span", { class: "avatar" }, a.name.slice(0, 1)),
      el("div", { class: "main" },
        el("div", { class: "t" }, a.name),
        el("div", { class: "s" }, a.last_upload ? `마지막 업로드 ${fmtAgo(a.last_upload)}` : "아직 올린 녹취가 없어요")),
      el("span", { class: `tag ${a.missing ? "tag-fail" : "tag-done"}` }, a.missing ? "오늘 없음" : `오늘 ${fmtInt(a.today)}개`)))
      : [el("li", { class: "empty" }, "에이전트가 없어요")]));
    const s = u.slack;
    $("#slack-box").hidden = !s;
    if (!s) return;
    if (document.activeElement !== $("#slack-target")) $("#slack-target").value = s.target;
    $("#slack-token").value = "";
    $("#slack-token").placeholder = s.token_set ? "저장돼 있어요 · 바꿀 때만 새로 넣으세요" : "xoxb-…";
    $("#slack-token").disabled = s.token_source === "env";
    $("#slack-token-help").textContent = s.token_source === "env"
      ? "서버 환경 변수(SLACK_BOT_TOKEN)의 토큰을 쓰고 있어요."
      : "슬랙 앱의 Bot User OAuth Token 이에요. 권한: chat:write, users:read, users:read.email";
    const n = u.agents.filter((a) => a.missing).length;
    $("#slack-notify").textContent = n ? `오늘 안 올린 ${fmtInt(n)}명 슬랙으로 알리기` : "모두 올렸어요 · 슬랙으로 알리기";
  }
  $("#slack-save").addEventListener("click", async () => {
    const msg = $("#slack-msg"), body = { target: $("#slack-target").value.trim() };
    const tok = $("#slack-token").value.trim();
    if (tok) body.token = tok;
    try {
      const s = await api("/api/slack/config", { method: "POST", body: JSON.stringify(body) });
      renderUploads({ ...state.uploads, slack: s });
      msg.className = "form-msg ok"; msg.textContent = "저장했어요.";
    } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
  });
  $("#slack-notify").addEventListener("click", async () => {
    const msg = $("#slack-msg"), btn = $("#slack-notify");
    const s = state.uploads?.slack;
    if (!s?.token_set || !s.target) { msg.className = "form-msg err"; msg.textContent = "받는 곳과 봇 토큰을 먼저 저장해 주세요."; return; }
    const missing = (state.uploads?.agents || []).filter((a) => a.missing);
    const list = missing.length
      ? missing.map((a) => `• ${a.name} — ${a.last_upload ? `마지막 ${fmtAgo(a.last_upload)}` : "올린 적 없음"}`).join("\n")
      : "오늘 모든 에이전트가 녹취를 올렸어요.";
    const ok = await ask(missing.length ? `${fmtInt(missing.length)}명을 슬랙으로 알릴까요?` : "모두 올렸다고 알릴까요?",
      `받는 곳: ${s.target}\n\n${list}`, "보내기");
    if (!ok) return;
    btn.disabled = true; msg.className = "form-msg"; msg.textContent = "보내는 중이에요…";
    try {
      const r = await api("/api/slack/notify-missing", { method: "POST", body: "{}" });
      msg.className = "form-msg ok"; msg.textContent = `${r.sent_to} 로 보냈어요.`;
    } catch (err) { msg.className = "form-msg err"; msg.textContent = err.message; }
    finally { btn.disabled = false; }
  });
  // ---------- 드라이브 녹취 백업 ----------
  async function loadDriveBackup() {
    try { renderDriveBackup(await api("/api/drive/backup")); } catch (err) { toast(err.message); }
  }
  const picked = new Set();
  const pickable = (a) => a.waiting > 0 && !a.pending;
  function renderDriveBackup(o) {
    state.backupOverview = o;
    for (const id of [...picked]) if (!o.agents.some((a) => a.agent_id === id && pickable(a))) picked.delete(id);
    $("#backup-agents").replaceChildren(...(o.agents.length ? o.agents.map((a) => {
      const can = pickable(a);
      return el("li", {},
        el("label", { class: `pick-row${can ? "" : " off"}` },
          el("input", {
            type: "checkbox", disabled: !can, checked: picked.has(a.agent_id),
            onchange: (e) => { e.target.checked ? picked.add(a.agent_id) : picked.delete(a.agent_id); updatePickUI(); },
          }),
          el("span", { class: "avatar" }, a.name.slice(0, 1)),
          el("div", { class: "main" },
            el("div", { class: "t" }, a.name),
            el("div", { class: "s" }, a.pending ? "백업 대기 중"
              : a.waiting ? `백업 안 한 녹취 ${fmtInt(a.waiting)}개` : "모두 백업했어요")),
          a.pending ? el("span", { class: "tag tag-wait" }, "대기 중") : null));
    }) : [el("li", { class: "empty" }, "에이전트가 없어요")]));
    const label = { pending: ["대기 중", "tag-wait"], done: ["완료", "tag-done"], failed: ["실패", "tag-fail"] };
    $("#backup-history").replaceChildren(...(o.history.length ? o.history.map((h) => el("li", {},
      el("div", { class: "main" },
        el("div", { class: "t" }, h.status === "pending" && !h.moved ? `${h.agent_name} · 처리 기다리는 중`
          : `${h.agent_name} · ${fmtInt(h.moved)}개 옮김${h.failed ? ` · ${fmtInt(h.failed)}개 실패` : ""}`),
        el("div", { class: "s" }, `${dateTimeKo(h.requested_at)} · ${h.requested_by}${h.message ? ` · ${h.message}` : ""}`)),
      el("span", { class: `tag ${label[h.status]?.[1] || "tag-done"}` }, label[h.status]?.[0] || h.status))) : [el("li", { class: "empty" }, "아직 백업한 적이 없어요")]));
    updatePickUI();
  }
  function updatePickUI() {
    const agents = state.backupOverview?.agents || [];
    const can = agents.filter(pickable);
    const sel = can.filter((a) => picked.has(a.agent_id));
    const all = $("#backup-select-all");
    all.disabled = !can.length;
    all.checked = can.length > 0 && sel.length === can.length;
    all.indeterminate = sel.length > 0 && sel.length < can.length;
    const btn = $("#backup-selected");
    btn.disabled = !sel.length;
    btn.textContent = sel.length
      ? `선택한 ${fmtInt(sel.length)}명 백업 · 녹취 ${fmtInt(sel.reduce((n, a) => n + a.waiting, 0))}개`
      : can.length ? "백업할 에이전트를 골라 주세요" : "백업할 녹취가 없어요";
  }
  $("#backup-select-all").addEventListener("change", (e) => {
    const can = (state.backupOverview?.agents || []).filter(pickable);
    picked.clear();
    if (e.target.checked) can.forEach((a) => picked.add(a.agent_id));
    renderDriveBackup(state.backupOverview);
  });
  $("#backup-selected").addEventListener("click", async () => {
    const sel = (state.backupOverview?.agents || []).filter((a) => pickable(a) && picked.has(a.agent_id));
    if (!sel.length) return;
    const n = sel.reduce((s, a) => s + a.waiting, 0);
    const names = sel.length <= 3 ? sel.map((a) => a.name).join(", ") : `${sel[0].name} 외 ${sel.length - 1}명`;
    const lines = sel.map((a) => `• ${a.name} ${fmtInt(a.waiting)}개 → _백업/${a.name}`);
    const ok = await ask(`${names} · 녹취 ${fmtInt(n)}개를 옮길까요?`,
      [...lines, "",
        "에이전트 폴더에서 같은 드라이브의 _백업 폴더로 옮겨 폴더를 비워요. 지우지 않으니 계속 들을 수 있어요.",
        "폴더에 대시보드에 아직 안 들어온 파일이 있으면 먼저 목록에 넣은 뒤 함께 옮겨요."].join("\n"),
      `${fmtInt(n)}개 옮기기`);
    if (!ok) return;
    try {
      const r = await api("/api/drive/backup", { method: "POST", body: JSON.stringify({ agent_ids: sel.map((a) => a.agent_id) }) });
      picked.clear();
      renderDriveBackup(r);
      toast(`${fmtInt(r.requested)}명 백업을 요청했어요`);
    } catch (err) { toast(err.message); }
  });

  $("#drive-copy").addEventListener("click", async () => {
    const msg = $("#drive-msg");
    const raw = $("#drive-folder").value.trim();
    const m = /folders\/([A-Za-z0-9_-]{10,})/.exec(raw) || /^([A-Za-z0-9_-]{10,})$/.exec(raw);
    if (!m) {
      msg.className = "form-msg err"; msg.textContent = "녹취 폴더 주소를 먼저 붙여넣어 주세요."; $("#drive-folder").focus(); return;
    }
    try { localStorage.setItem("cti.driveFolder", raw); } catch { /* 무시 */ }
    try {
      const { script } = await api("/api/drive/script");
      await navigator.clipboard.writeText(script.replace("'여기에_녹취_폴더_ID'", `'${m[1]}'`));
      msg.className = "form-msg ok"; msg.textContent = "복사했어요. script.google.com 에 붙여넣어 주세요.";
    } catch (err) {
      msg.className = "form-msg err";
      msg.textContent = err.message.includes("clipboard") || err.name === "NotAllowedError"
        ? "복사를 못 했어요. 브라우저의 클립보드 권한을 확인해 주세요." : err.message;
    }
  });
  // ---------- 비밀번호 바꾸기 ----------
  $("#password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target)), msg = $("#password-msg");
    msg.className = "form-msg err";
    if (!f.current || !f.new) { msg.textContent = "비밀번호를 모두 입력해 주세요."; return; }
    if (f.new !== f.new2) { msg.textContent = "새 비밀번호가 서로 달라요."; return; }
    try {
      await api("/api/me/password", { method: "POST", body: JSON.stringify({ current: f.current, new: f.new }) });
      e.target.reset();
      msg.className = "form-msg ok"; msg.textContent = "비밀번호를 바꿨어요.";
    } catch (err) { msg.textContent = err.message; }
  });

  // ---------- 관리: 회원 관리 ----------
  const tempPw = {};  // 방금 초기화한 회원의 임시 비밀번호(이 화면에만 보임)
  async function loadUsers() {
    let users;
    try { users = await api("/api/users"); } catch (err) { toast(err.message); return; }
    const me = state.user.id;
    $("#users-list").replaceChildren(...users.map((u) => {
      const locked = u.owner || u.id === me;
      const isAdmin = u.role === "admin";
      return el("li", {},
        el("span", { class: "avatar" }, u.username.slice(0, 1).toUpperCase()),
        el("div", { class: "main" },
          el("div", { class: "t" }, u.username, u.id === me ? " (나)" : ""),
          el("div", { class: "s" }, ...(tempPw[u.id]
            ? ["임시 비밀번호 ", el("span", { class: "temp-pw" }, tempPw[u.id])]
            : [isAdmin ? "관리자" : "회원"]))),
        locked ? el("span", { class: `tag ${isAdmin ? "tag-wait" : "tag-done"}` }, isAdmin ? "관리자" : "회원")
          : el("div", { class: "user-actions" },
            el("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => setRole(u, isAdmin ? "viewer" : "admin") },
              isAdmin ? "회원으로" : "관리자로"),
            el("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: () => resetUser(u) }, "비밀번호 초기화"),
            el("button", { type: "button", class: "btn btn-danger btn-sm", onclick: () => deleteUser(u) }, "탈퇴")));
    }));
  }
  async function setRole(u, role) {
    const label = role === "admin" ? "관리자" : "회원";
    if (!await ask(`${label}로 바꿀까요?`, `${u.username} 님의 등급을 ${label}로 바꿔요.`, "바꾸기")) return;
    try {
      await api(`/api/users/${u.id}/role`, { method: "POST", body: JSON.stringify({ role }) });
      toast(`${label}로 바꿨어요`);
      loadUsers();
    } catch (err) { toast(err.message); }
  }
  async function resetUser(u) {
    if (!await ask("비밀번호를 초기화할까요?", `${u.username} 님은 임시 비밀번호로 다시 로그인해야 해요.`, "초기화하기")) return;
    try {
      const r = await api(`/api/users/${u.id}/reset`, { method: "POST", body: "{}" });
      tempPw[u.id] = r.temp_password;
      try { await navigator.clipboard.writeText(r.temp_password); toast("임시 비밀번호를 복사했어요"); } catch { toast("임시 비밀번호를 만들었어요"); }
      loadUsers();
    } catch (err) { toast(err.message); }
  }
  async function deleteUser(u) {
    if (!await ask("탈퇴시킬까요?", `${u.username} 계정을 바로 지워요.`, "탈퇴시키기", true)) return;
    try {
      await api(`/api/users/${u.id}/delete`, { method: "POST", body: "{}" });
      delete tempPw[u.id];
      toast("탈퇴시켰어요");
      loadUsers();
    } catch (err) { toast(err.message); }
  }

  async function loadSettings() {
    const isAdmin = state.user.role === "admin";
    $("#settings-msg").textContent = "";
    $("#ingest-msg").textContent = "";
    for (const f of SETTING_FIELDS) $(f.input).disabled = !isAdmin;
    $("#settings-save").hidden = !isAdmin;
    $("#ingest-now").hidden = !isAdmin;
    $("#settings-readonly").hidden = isAdmin;
    try {
      fillSettings(await api("/api/settings"));
    } catch (err) {
      const msg = $("#settings-msg"); msg.className = "form-msg err"; msg.textContent = err.message;
    }
  }
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#settings-msg"), btn = $("#settings-save");
    const body = {};
    for (const f of SETTING_FIELDS) {
      const raw = $(f.input).value, v = Number(raw);
      if (raw === "" || !Number.isInteger(v) || v < f.min || v > f.max) {
        msg.className = "form-msg err"; msg.textContent = `${f.label}: ${f.min}~${f.max} 사이 숫자로 입력해 주세요.`; return;
      }
      body[f.key] = v;
    }
    btn.disabled = true;
    try {
      const s = await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
      fillSettings(s);
      msg.className = "form-msg ok"; msg.textContent = `저장했어요. 이제 ${s.min_call_sec}초 미만 통화를 짧은 통화로 분류해요.`;
      state.minSec = s.min_call_sec;
      dashboardStale = true;
    } catch (err) {
      msg.className = "form-msg err"; msg.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  function renderIngestStatus(r) {
    const box = $("#ingest-status");
    if (!r) { box.replaceChildren(el("div", { class: "status-row" }, el("span", { class: "k" }, "아직 가져온 적이 없어요"))); return; }
    const row = (k, v) => el("div", { class: "status-row" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v));
    box.replaceChildren(...[
      row("마지막으로 가져온 시각", dateTimeKo(r.at)),
      row("새로 추가", `${fmtInt(r.added)}건`),
      row("이미 있던 녹취", `${fmtInt(r.already)}건`),
      row("에이전트 폴더", `${fmtInt(r.agents)}개`),
      r.recent ? row("올라오는 중(다음에 가져옴)", `${fmtInt(r.recent)}개`) : null,
      r.failed_count ? el("div", { class: "fail-box" },
        `파일 이름에서 날짜·시간을 못 읽은 파일이 ${fmtInt(r.failed_count)}개 있어요`,
        el("ul", {}, r.failed.map((f) => el("li", {}, f)))) : null,
    ].filter(Boolean));
  }
  $("#ingest-now").addEventListener("click", async () => {
    const btn = $("#ingest-now"), msg = $("#ingest-msg");
    btn.disabled = true; msg.className = "form-msg"; msg.textContent = "가져오는 중이에요…";
    try {
      const s = await api("/api/ingest", { method: "POST", body: "{}" });
      fillSettings(s);
      msg.className = "form-msg ok"; msg.textContent = `${fmtInt(s.last_ingest.added)}건을 새로 가져왔어요.`;
      if (s.last_ingest.added) dashboardStale = true;
    } catch (err) {
      msg.className = "form-msg err"; msg.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });


  // ---------- 시작 ----------
  api("/api/me").then((u) => { state.user = u; return startApp(); }).catch(() => showLogin());
})();
