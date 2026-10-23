"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
  const icons = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    tasks: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M9 5V3h6v2M8 11l1 1 2-2m2 1h3M8 16l1 1 2-2m2 1h3"/>',
    agents: '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 7V3m-2 0h4M1 12v4m22-4v4M9 16h6"/><circle cx="8.5" cy="12" r=".7"/><circle cx="15.5" cy="12" r=".7"/>',
    finance: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M16 10h5v5h-5a2.5 2.5 0 0 1 0-5Z"/><path d="M7 5V3h11"/>',
    settings: '<path d="m9.5 3-.7 2.3-2 .9-2.2-.5-2 3.4 1.5 1.8v2.2l-1.5 1.8 2 3.4 2.2-.5 2 .9.7 2.3h4.9l.8-2.3 2-.9 2.2.5 1.9-3.4-1.5-1.8v-2.2l1.5-1.8-1.9-3.4-2.2.5-2-.9-.8-2.3Z"/><circle cx="12" cy="12" r="3"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    up: '<path d="m5 16 6-6 4 4 5-8m-5 0h5v5"/>',
    play: '<path d="m8 5 11 7-11 7V5Z"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    leaf: '<path d="M20 4c-9-2-16 2-16 8a7 7 0 0 0 7 7c6 0 9-7 9-15Z"/><path d="M4 21 15 10m-5 9v-4m-4-4h6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    link: '<path d="m10 13 4-4m-5-1 3-3a5 5 0 0 1 7 7l-3 3m-1 1-3 3a5 5 0 0 1-7-7l3-3"/>',
    spark: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
    review: '<path d="M3 12s3-6 9-6 9 6 9 6-3 6-9 6-9-6-9-6Z"/><circle cx="12" cy="12" r="2.5"/>',
    send: '<path d="m3 3 19 9-19 9 4-9-4-9Zm4 9h15"/>',
    github: '<path d="M9 19c-4 1-4-2-6-2m12 5v-3.5c0-1 .1-1.5-.5-2 3-.3 6.5-1.5 6.5-7A5.5 5.5 0 0 0 19.5 6 5 5 0 0 0 19.4 2S18.2 1.7 15.5 3a13 13 0 0 0-7 0C5.8 1.7 4.6 2 4.6 2A5 5 0 0 0 4.5 6 5.5 5.5 0 0 0 3 9.5c0 5.5 3.5 6.7 6.5 7-.6.5-.6 1.2-.5 2V22"/>',
    refresh: '<path d="M20 7V2l-3 3a8 8 0 0 0-13 6m0 6v5l3-3a8 8 0 0 0 13-6M20 7h-5M4 17h5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"/><path d="M14 2v6h6M8 13h8m-8 4h6"/>',
  };
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.spark}</svg>`;
  const navigation = [{ key: "overview", label: "Översikt", icon: "grid" }, { key: "tasks", label: "Uppdrag", icon: "tasks" }, { key: "agents", label: "Agenter", icon: "agents" }, { key: "finance", label: "Ekonomi", icon: "finance" }, { key: "settings", label: "Inställningar", icon: "settings" }];
  const taskStatuses = {
    discovered: ["Hittat", "blue"], found: ["Hittat", "blue"], new: ["Hittat", "blue"], queued: ["I kö", "blue"], researching: ["Undersöks", "blue"], evaluating: ["Bedöms", "blue"], qualified: ["Utvalt", "green"], eligible: ["Utvalt", "green"], preparing: ["Granskar underlag", "blue"], working: ["Pågår", "green"], testing: ["Testas", "blue"], in_progress: ["Pågår", "green"], executing: ["Pågår", "green"], solving: ["Pågår", "green"], reviewing: ["Granskas", "amber"], review: ["Granskas", "amber"], ready: ["Klart", "green"], ready_to_submit: ["Klart", "green"], completed: ["Klart", "green"], submitted: ["Inskickat", "blue"], paid: ["Betalt", "green"], blocked: ["Blockerat", "amber"], skipped: ["Bortvalt", "gray"], rejected: ["Avvisat", "gray"], failed: ["Fel", "red"], error: ["Fel", "red"], needs_review: ["Behöver ses över", "amber"], awaiting_payment: ["Väntar betalning", "amber"], submitting: ["Skickas in", "blue"], draft: ["Utkast", "gray"], reserved: ["Reserverat", "amber"], closed: ["Stängt", "gray"], expired: ["Utgånget", "gray"]
  };
  const agentStatuses = { idle: "Redo", waiting: "Väntar", paused: "Pausad", stopped: "Pausad", running: "Arbetar", preparing: "Granskar underlag", working: "Arbetar", testing: "Testar", queued: "I kö", busy: "Arbetar", scanning: "Söker", scouting: "Söker", searching: "Söker", reviewing: "Granskar", blocked: "Blockerad", error: "Fel", offline: "Offline", disabled: "Avstängd", ready: "Redo" };
  const roleNames = { bountyhub_scout: "Hittar och granskar BountyHub-uppdrag", bountyhub_docs: "BountyHub · dokumentation", bountyhub_code: "BountyHub · små Python-ändringar", scout: "Hittar nya uppdrag", discovery: "Hittar nya uppdrag", researcher: "Hittar nya uppdrag", evaluator: "Bedömer möjligheter", qualifier: "Bedömer möjligheter", worker: "Utför uppdrag", solver: "Utför uppdrag", reviewer: "Granskar leveranser", executor: "Utför uppdrag", submission: "Skickar leveranser" };
  let state = null;
  let currentView = navigation.some(item => item.key === location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  let taskFilter = "all";
  let searchQuery = "";
  let settingsDraft = null;
  let settingsDirty = false;
  let polling = false;
  let operationBusy = false;
  let drawerTaskId = null;
  let drawerTaskSnapshot = null;
  let drawerArtifactPath = null;
  let previousFocus = null;

  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const money = (value, decimals = 0) => new Intl.NumberFormat("sv-SE", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(number(value));
  const safeURL = value => { try { const url = new URL(value); return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password ? url.href : null; } catch { return null; } };
  const safePullRequestURL = value => {
    const match = typeof value === "string" && /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]*\/([A-Za-z0-9_.-]+)\/pull\/[1-9][0-9]*$/.exec(value);
    return match && ![".", ".."].includes(match[1]) ? value : null;
  };
  const safeBountyHubURL = value => typeof value === "string" && /^https:\/\/www\.bountyhub\.dev\/bounty\/view\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : null;
  const formatDate = value => { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("sv-SE", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Stockholm" }).format(date); };
  const relativeTime = value => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return "Nyss";
    if (minutes < 60) return `För ${minutes} min sedan`;
    if (minutes < 1440) return `För ${Math.floor(minutes / 60)} tim sedan`;
    return formatDate(value);
  };
  const tasks = () => Array.isArray(state?.tasks) ? state.tasks : [];
  const agents = () => Array.isArray(state?.agents) ? state.agents : [];
  const settings = () => state?.settings || {};
  const metrics = () => state?.metrics || {};
  const effectiveWorkers = () => settings().bountyhub_enabled !== false ? 1 : number(settings().max_agents) || 1;
  const isRunning = () => Boolean(settings().running);
  const searchEnabled = () => settings().search_enabled !== false;
  const agentEnabled = agent => {
    if (agent.role === "scout") return searchEnabled();
    if (agent.role === "bountyhub_scout") return searchEnabled() && settings().bountyhub_enabled !== false;
    if (agent.role === "bountyhub_docs") return isRunning() && settings().bountyhub_enabled !== false && settings().bountyhub_docs_enabled !== false;
    if (agent.role === "bountyhub_code") return isRunning() && settings().bountyhub_enabled !== false && settings().bountyhub_code_enabled !== false;
    return isRunning();
  };
  const specialistName = task => task.specialist === "code" ? "Små Python-ändringar" : task.specialist === "documentation" ? "Dokumentation" : task.status === "discovered" ? "Väntar på granskning" : "Ingen specialist vald";
  const aiReady = () => ["ready", "connected", "ok", "available", "healthy"].includes(String(state?.connections?.ai?.status || "").toLowerCase());
  const workingStatuses = new Set(["preparing", "working", "testing", "in_progress", "executing", "solving", "researching", "evaluating", "reviewing", "review"]);
  const availableTasks = () => tasks().filter(task => !["paid", "rejected", "skipped", "closed", "expired"].includes(task.status));
  const agentWorking = agent => ["preparing", "working", "testing", "running", "busy", "scanning", "scouting", "searching", "reviewing"].includes(agent.status);
  const statusBadge = status => { const known = taskStatuses[status] || [String(status || "Okänt"), "gray"]; return `<span class="badge ${known[1]}">${escapeHTML(known[0])}</span>`; };
  const reward = (task, compact = true) => {
    if (task.reward_amount == null) return escapeHTML(compact ? "Se villkor" : task.advertised_reward_description || "Ej bekräftad");
    const currency = String(task.reward_currency || "valuta saknas");
    const known = /^[A-Z]{3}$/.test(currency);
    const label = !known && currency.includes("$") ? (compact ? "$ · valuta okänd" : "$ (dollarvaluta ospecificerad)") : compact && !known ? "· obekr." : currency;
    return `${money(task.reward_amount, number(task.reward_amount) % 1 ? 2 : 0)} ${escapeHTML(label)}`;
  };
  const displayAgentStatus = agent => !agentEnabled(agent) && ["idle", "ready", "queued"].includes(agent.status) ? "Pausad" : agentStatuses[agent.status] || String(agent.status || "Väntar");
  const roleName = agent => roleNames[agent.role] || String(agent.role || "Agent");
  const agentTaskText = agent => {
    if (["scout", "bountyhub_scout"].includes(agent.role) || agent.id === "scout") {
      if (["scouting", "scanning", "searching"].includes(agent.status)) return "Söker efter uppdrag och kontrollerar vilka som går att utföra.";
      if (!agentEnabled(agent)) return "Automatiska sökningar är pausade. Aktivera sökningen och källan för att fortsätta.";
      const due = Date.parse(agent.next_scan_at || "");
      const seconds = Number.isFinite(due) ? Math.max(0, Math.ceil((due - Date.now()) / 1000)) : 0;
      const next = seconds ? `Nästa automatiska sökning om ${Math.floor(seconds / 60)} min ${seconds % 60} s.` : "Nästa automatiska sökning startar strax.";
      return next + (agent.detail ? " " + agent.detail : "");
    }
    if (!agentEnabled(agent) && ["idle", "ready", "queued"].includes(agent.status)) return "Arbetet är pausat. Du kan aktivera specialisten i inställningarna och starta arbetsagenterna.";
    const assignedTask = tasks().find(task => String(task.id) === String(agent.task));
    return assignedTask?.title || agent.task || agent.detail || (isRunning() ? "Väntar på nästa uppgift." : "Arbetet startar när du aktiverar agenterna.");
  };

  function friendlyError(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(item => typeof item === "object" ? item.msg || item.message || "Kontrollera dina uppgifter." : String(item)).join(" ");
    if (detail && typeof detail === "object") return detail.message || detail.detail || "Begäran kunde inte genomföras.";
    return "Något gick fel. Försök igen om en stund.";
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) }, signal: controller.signal });
      const contentType = response.headers.get("content-type") || "";
      const result = contentType.includes("application/json") ? await response.json() : await response.text();
      if (!response.ok) throw new Error(friendlyError(typeof result === "object" ? result.detail || result.error || result : result));
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Servern tar längre tid än väntat. Status uppdateras automatiskt.");
      if (error instanceof TypeError) throw new Error("Ingen kontakt med servern. Kontrollera att BountyOS körs.");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = `toast${error ? " error" : ""}`;
    item.setAttribute("role", error ? "alert" : "status");
    item.innerHTML = `${icon(error ? "info" : "check")}<span>${escapeHTML(message)}</span><button class="icon-button" aria-label="Stäng meddelandet">${icon("close")}</button>`;
    $("button", item).addEventListener("click", () => item.remove());
    $("#toast-container").append(item);
    setTimeout(() => item.remove(), error ? 10000 : 6000);
  }

  function renderNavigation() {
    $("#main-nav").innerHTML = navigation.map(item => `<a href="#${item.key}" class="nav-item${currentView === item.key ? " active" : ""}"${currentView === item.key ? ' aria-current="page"' : ""}>${icon(item.icon)}<span>${item.label}</span>${item.key === "tasks" && tasks().length ? `<span class="nav-count">${tasks().length}</span>` : ""}</a>`).join("");
    $("#breadcrumb-current").textContent = navigation.find(item => item.key === currentView).label;
    document.title = `${navigation.find(item => item.key === currentView).label} — BountyOS`;
  }

  function renderSystemStatus() {
    const running = isRunning();
    const text = running ? "Arbetsagenterna är igång" : searchEnabled() ? "Automatisk sökning på" : "Automatik pausad";
    $("#system-indicator").innerHTML = `<span class="status-dot"></span>${text}`;
    $("#system-indicator").className = `system-indicator${running ? " running" : ""}`;
    $("#sidebar-status").textContent = aiReady() ? "Lokal AI ansluten" : "Lokal AI behöver anslutas";
    $("#sidebar-dot").className = `connection-dot${aiReady() ? " ready" : ""}`;
  }

  function controls(includeScan = true) {
    return `<div class="heading-actions">${includeScan ? `<button class="button button-secondary" data-action="scan"${operationBusy ? " disabled" : ""}>${icon("search")}Sök nu</button><button class="button button-secondary" data-action="search-control"${operationBusy ? " disabled" : ""}>${icon(searchEnabled() ? "pause" : "play")}${searchEnabled() ? "Pausa sökning" : "Starta sökning"}</button>` : ""}<button class="button ${isRunning() ? "button-secondary" : "button-primary"}" data-action="control"${operationBusy ? " disabled" : ""}>${icon(isRunning() ? "pause" : "play")}${isRunning() ? "Pausa arbete" : "Starta arbete"}</button></div>`;
  }

  function heading(title, description, actions = "", eyebrow = "DIN ARBETSPLATS") {
    return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${description}</p></div>${actions}</div>`;
  }

  function operatingStrip() {
    return `<div class="operating-strip"><div class="operating-strip-main">${icon("leaf")}<span><strong>0 kr i driftbudget.</strong> Lokal AI med Ollama.</span><span class="operating-strip-separator"></span><span class="operating-strip-extra">${aiReady() ? "Din modell är ansluten och redo." : "Anslut din modell för att utföra uppdrag."}</span></div><a href="#settings">${aiReady() ? "Inställningar" : "Kom igång"}${icon("arrow")}</a></div>`;
  }

  function metricCards() {
    const data = metrics();
    const active = number(data.active_agents);
    return `<div class="metrics-grid"><section class="metric-card primary"><div class="metric-label">${icon("finance")}Bekräftad intäkt</div><span class="metric-top-icon">${icon("up")}</span><div class="metric-value">${money(data.earned_sek)}<span class="metric-unit">kr</span></div><div class="metric-note">${icon("check")}Registrerade inbetalningar</div></section><section class="metric-card"><div class="metric-label">${icon("clock")}Avvaktar betalning</div><div class="metric-value">${money(data.pending_sek)}<span class="metric-unit">kr</span></div><div class="metric-note">Endast kända belopp i SEK</div></section><section class="metric-card"><div class="metric-label">${icon("agents")}Aktiva agentroller</div><div class="metric-value">${active}</div><div class="metric-note">Högst ${effectiveWorkers()} arbetsuppdrag samtidigt</div>${agents().length ? `<div class="metric-agent-mini">${agents().slice(0, 3).map((agent, i) => `<span title="${escapeHTML(agent.name)}">${String(i + 1).padStart(2, "0")}</span>`).join("")}</div>` : ""}</section><section class="metric-card"><div class="metric-label">${icon("up")}Nettoresultat</div><div class="metric-value">${money(data.net_sek)}<span class="metric-unit">kr</span></div><div class="metric-note">Intäkter minus registrerad drift</div></section></div>`;
  }

  function chartPanel() {
    const supplied = Array.isArray(state.chart) ? state.chart : [];
    const points = [];
    const stockholmDate = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const anchor = new Date(`${stockholmDate}T12:00:00Z`);
    for (let day = 6; day >= 0; day -= 1) {
      const date = new Date(anchor); date.setUTCDate(anchor.getUTCDate() - day);
      const key = date.toISOString().slice(0, 10);
      const found = supplied.find(point => String(point.date).slice(0, 10) === key);
      points.push({ date, earned: number(found?.earned), cost: number(found?.cost) });
    }
    const max = Math.max(1, ...points.flatMap(point => [point.earned, point.cost]));
    const any = points.some(point => point.earned || point.cost);
    const width = 600, left = 39, right = 21, top = 24, bottom = 165;
    const x = index => left + index * ((width - left - right) / 6);
    const y = value => bottom - (value / max) * (bottom - top);
    const line = key => points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point[key])}`).join(" ");
    const lines = [0, .33, .66, 1].map((fraction, index) => `<line x1="${left}" y1="${y(max * fraction)}" x2="${width - right}" y2="${y(max * fraction)}" stroke="#edf1e7" stroke-dasharray="3 5"/><text x="${left - 10}" y="${y(max * fraction) + 3}" text-anchor="end" class="chart-label">${any ? escapeHTML(money(max * fraction)) : index === 0 ? "0" : ""}</text>`).join("");
    const weekdays = new Intl.DateTimeFormat("sv-SE", { weekday: "short", timeZone: "Europe/Stockholm" });
    return `<section class="panel"><div class="panel-heading"><div><h2 class="panel-title">Dina intäkter</h2><p class="panel-subtitle">Från utfört arbete till pengar på kontot.</p></div><span class="period-chip">${icon("calendar")}Senaste 7 dagarna</span></div><div class="chart-legend"><span><i class="legend-dot"></i>Inbetalningar</span><span><i class="legend-dot cost"></i>Driftkostnad</span></div><div class="chart-wrap"><svg class="earnings-chart" viewBox="0 0 600 195" role="img" aria-label="${any ? "Registrerade intäkter och driftkostnader i kronor under de senaste sju dagarna" : "Inga intäkter eller driftkostnader registrerade de senaste sju dagarna"}" preserveAspectRatio="none">${lines}${any ? `<path d="${line("earned")} L${x(6)},${bottom} L${x(0)},${bottom}Z" fill="#edf5e9"/><path d="${line("cost")}" fill="none" stroke="#b8c6ac" stroke-width="1.5" stroke-dasharray="4 4"/>` : ""}<path d="${line("earned")}" fill="none" stroke="${any ? "#739f61" : "#c5d8b9"}" stroke-width="1.8"/>${points.map((point, index) => `<text x="${x(index)}" y="188" text-anchor="middle" class="chart-label">${escapeHTML(weekdays.format(point.date).replace(".", ""))}</text>${any ? `<circle cx="${x(index)}" cy="${y(point.earned)}" r="3" fill="#fff" stroke="#739f61" stroke-width="1.5"><title>${escapeHTML(point.date.toISOString().slice(0, 10))}: ${money(point.earned)} kr</title></circle>` : ""}`).join("")}</svg>${!any ? '<div class="chart-empty"><strong>Här tar din första intäkt form.</strong><p>Grafen fylls när du registrerar en inbetalning.</p></div>' : ""}</div><div class="chart-footer"><span>${icon("shield")}Visar registrerade belopp i SEK</span><span>Uppdateras automatiskt</span></div></section>`;
  }

  function pipelinePanel() {
    const counts = [tasks().filter(task => ["discovered", "found", "new", "queued", "qualified", "eligible"].includes(task.status)).length, tasks().filter(task => task.status === "preparing").length, tasks().filter(task => ["working", "testing", "in_progress", "executing", "solving", "researching", "evaluating"].includes(task.status)).length, tasks().filter(task => ["reviewing", "review", "needs_review", "ready", "ready_to_submit", "completed"].includes(task.status)).length, tasks().filter(task => ["submitted", "paid", "awaiting_payment"].includes(task.status)).length];
    const stages = [{ name: "Hittade uppdrag", icon: "search" }, { name: "Granskar underlag", icon: "file" }, { name: "Arbete pågår", icon: "spark" }, { name: "Granskning & leverans", icon: "review" }, { name: "Inskickade", icon: "send" }];
    const total = Math.max(1, ...counts);
    return `<section class="panel"><div class="panel-heading"><div><h2 class="panel-title">Från möjlighet till leverans</h2><p class="panel-subtitle">Varje uppdrag, ett steg i taget.</p></div>${icon("link")}</div><div class="pipeline-content">${stages.map((stage, i) => `<div class="pipeline-stage"><span class="stage-icon">${icon(stage.icon)}</span><span class="stage-label">${stage.name}</span><span class="stage-number">${counts[i]}</span><span class="stage-track"><span style="width:${counts[i] / total * 100}%"></span></span></div>`).join("")}</div><p class="pipeline-footnote">${counts.some(Boolean) ? "Agenterna följer samma flöde för varje uppdrag." : "Flödet är redo. Din första sökning är nästa steg."}</p></section>`;
  }

  function filteredTasks() {
    return tasks().filter(task => {
      const matchesSearch = `${task.title || ""} ${task.repo || ""} ${task.kind || ""}`.toLocaleLowerCase("sv-SE").includes(searchQuery.toLocaleLowerCase("sv-SE"));
      const matchesFilter = taskFilter === "all" || (taskFilter === "active" ? workingStatuses.has(task.status) : taskFilter === "delivered" ? ["submitted", "paid", "awaiting_payment"].includes(task.status) : taskFilter === "available" ? ["discovered", "found", "new", "queued", "qualified", "eligible"].includes(task.status) : taskFilter === "blocked" ? ["blocked", "failed", "error", "rejected", "skipped"].includes(task.status) : task.status === taskFilter);
      return matchesSearch && matchesFilter;
    }).sort((a, b) => String(b.updated_at || b.created_at).localeCompare(String(a.updated_at || a.created_at)));
  }

  function taskRows(limit = 0) {
    const selected = filteredTasks();
    const shown = limit ? selected.slice(0, limit) : selected;
    if (!shown.length) return `<div class="task-empty"><span class="empty-illustration">${icon(searchQuery || taskFilter !== "all" ? "search" : "folder")}</span><strong>${searchQuery || taskFilter !== "all" ? "Inga uppdrag matchar din sökning" : "Nästa möjlighet väntar där ute"}</strong><p>${searchQuery || taskFilter !== "all" ? "Prova en annan sökning eller visa alla uppdrag." : "Låt dina agenter hitta små uppdrag med potential. Allt samlas här."}</p>${!tasks().length ? `<button class="button button-secondary button-small" data-action="scan"${operationBusy ? " disabled" : ""}>${icon("search")}Hitta första uppdraget</button>` : '<button class="button button-quiet" data-action="clear-filter">Visa alla uppdrag</button>'}</div>`;
    return `<table class="tasks-table${limit ? "" : " full-tasks-table"}"><thead><tr><th class="title-col" scope="col">Uppdrag</th><th class="reward-col" scope="col">Belöning</th><th scope="col">Status</th></tr></thead><tbody>${shown.map(task => `<tr><td><button class="task-title" data-task-id="${escapeHTML(task.id)}" title="${escapeHTML(task.title)}">${escapeHTML(task.title || "Namnlöst uppdrag")}</button><span class="task-repo">${escapeHTML(task.repo || task.kind || "GitHub")}</span>${task.source === "bountyhub" ? `<span class="task-source">BountyHub · ${escapeHTML(specialistName(task))}</span>` : ""}${!limit && task.reason && ["discovered", "preparing", "blocked", "failed", "skipped"].includes(task.status) ? `<span class="task-assessment">${escapeHTML(task.reason)}</span>` : ""}</td><td title="${escapeHTML(task.advertised_reward_description || "Annonserad belöning, ingen garanterad utbetalning")}">${reward(task)}</td><td>${statusBadge(task.status)}</td></tr>`).join("")}</tbody></table>`;
  }

  function tasksPanel(compact = true) {
    return `<section class="panel"><div class="panel-heading"><h2 class="panel-title">${compact ? "Dina uppdrag" : "Alla möjligheter"}<span class="count-chip">${tasks().length}</span></h2>${compact ? `<a class="text-link" href="#tasks">Visa alla${icon("arrow")}</a>` : '<span class="panel-subtitle">Annonserade belöningar i originalvaluta</span>'}</div>${compact ? `<div class="task-toolbar"><div class="task-tabs" role="group" aria-label="Filtrera uppdrag"><button class="task-tab${taskFilter === "all" ? " selected" : ""}" data-filter="all">Alla uppdrag</button><button class="task-tab${taskFilter === "active" ? " selected" : ""}" data-filter="active">Pågående</button><button class="task-tab${taskFilter === "delivered" ? " selected" : ""}" data-filter="delivered">Inskickade</button></div><label class="search-box">${icon("search")}<input id="task-search" type="search" placeholder="Sök uppdrag…" aria-label="Sök uppdrag" value="${escapeHTML(searchQuery)}" /></label></div>` : `<div class="full-task-toolbar"><label class="search-box">${icon("search")}<input id="task-search" type="search" placeholder="Sök efter uppdrag, projekt eller typ…" aria-label="Sök uppdrag" value="${escapeHTML(searchQuery)}" /></label><select class="filter-select" id="task-filter" aria-label="Filtrera efter status">${[["all", "Alla statusar"], ["available", "Hittade & i kö"], ["active", "Pågående"], ["delivered", "Inskickade & betalda"], ["blocked", "Blockerade & bortvalda"]].map(([value, label]) => `<option value="${value}"${taskFilter === value ? " selected" : ""}>${label}</option>`).join("")}</select></div>`}<div id="task-results">${taskRows(compact ? 5 : 0)}</div><div class="panel-bottom"><span>${icon("github")}GitHub · publika uppdrag och BountyHub-annonser</span><span id="task-count">${filteredTasks().length} uppdrag${compact && filteredTasks().length > 5 ? " · visar de 5 senaste" : ""}</span></div></section>`;
  }

  function agentsPanel() {
    return `<section class="panel"><div class="panel-heading"><h2 class="panel-title">Din agentstyrka<span class="count-chip">${agents().length}</span></h2><a class="text-link" href="#agents">Visa alla${icon("arrow")}</a></div>${agents().length ? `<div class="agent-list">${agents().slice(0, 4).map((agent, index) => `<div class="agent-row"><div class="agent-avatar variant-${index % 4}">${icon(["search", "review", "spark", "shield"][index % 4])}</div><div class="agent-info"><div class="agent-name">${escapeHTML(agent.name || "Agent")}</div><div class="agent-role">${escapeHTML(roleName(agent))}</div></div><span class="agent-status${agentWorking(agent) ? " working" : agent.status === "blocked" ? " blocked" : ""}">${escapeHTML(displayAgentStatus(agent))}</span></div>`).join("")}</div>` : '<div class="agent-panel-empty">Agenterna visas här när arbetsplatsen är redo. Du styr antalet i inställningarna.</div>'}<div class="panel-bottom"><span>${icon("leaf")}Lokal AI · ingen API-kostnad</span><a href="#settings" class="text-link">Hantera${icon("arrow")}</a></div></section>`;
  }

  function activityPanel() {
    const activity = Array.isArray(state.activity) ? state.activity : [];
    return `<section class="panel"><div class="panel-heading"><h2 class="panel-title">Senaste aktivitet</h2>${icon("clock")}</div><div class="activity-list">${activity.length ? activity.slice(0, 5).map(item => `<div class="activity-item"><span class="activity-bullet ${["error", "warning"].includes(item.level) ? item.level : ""}"></span><div><p class="activity-message">${escapeHTML(item.message)}</p><time class="activity-time" datetime="${escapeHTML(item.timestamp)}" title="${escapeHTML(formatDate(item.timestamp))}">${escapeHTML(relativeTime(item.timestamp))}</time></div></div>`).join("") : '<p class="activity-empty">Lugnt just nu. Sökningar, utfört arbete och leveranser visas här löpande.</p>'}</div></section>`;
  }

  function overviewView() {
    return `${heading('Din agentstyrka. <em>Full överblick.</em>', "Hitta möjligheter, följ arbetet och håll koll på varje krona.", controls())}${operatingStrip()}${metricCards()}<div class="dashboard-middle">${chartPanel()}${pipelinePanel()}</div><div class="dashboard-lower">${tasksPanel()}<div class="right-column">${agentsPanel()}${activityPanel()}</div></div>`;
  }

  function tasksView() {
    return `${heading('En plats för <em>nästa möjlighet.</em>', "Agenten granskar underlaget och gör en arbetsplan innan ett utvalt uppdrag får status Pågår.", controls(), "UPPDRAG")}${operatingStrip()}${tasksPanel(false)}<div class="hint-card section-gap"><h3>En annonserad belöning är en möjlighet</h3>Belöningar visas i den valuta uppdragsgivaren anger. Intäkter räknas först när du registrerar en faktisk inbetalning i Ekonomi.</div>`;
  }

  function agentsView() {
    return `${heading('Möt din <em>agentstyrka.</em>', "Ett gemensamt mål. Tydliga roller. Du bestämmer takten.", controls(false), "AGENTER")}${operatingStrip()}<div class="view-intro"><span>${agents().length} agentroller · högst ${effectiveWorkers()} arbetsuppdrag samtidigt${settings().bountyhub_enabled !== false ? " · BountyHub-specialisterna delar arbetskön" : settings().autoscale ? " · automatisk skalning på" : ""}</span><a class="button button-secondary button-small" href="#settings">${icon("settings")}Hantera kapacitet</a></div><div class="agents-grid">${agents().length ? agents().map((agent, index) => `<article class="panel agent-card"><div class="agent-card-top"><div class="agent-avatar variant-${index % 4}">${icon(["search", "review", "spark", "shield"][index % 4])}</div><span class="agent-status${agentWorking(agent) ? " working" : agent.status === "blocked" ? " blocked" : ""}">${escapeHTML(displayAgentStatus(agent))}</span></div><h3>${escapeHTML(agent.name || "Agent")}</h3><p class="agent-card-role">${escapeHTML(roleName(agent))}</p><div class="agent-current-task"><strong>JUST NU</strong>${escapeHTML(agentTaskText(agent))}</div></article>`).join("") : '<div class="panel task-empty"><span class="empty-illustration">' + icon("agents") + '</span><strong>Din agentstyrka förbereds</strong><p>Agenternas roller och aktivitet visas här när systemet har laddat dem.</p></div>'}</div><div class="hint-card section-gap"><h3>Två specialister, en lokal modell</h3>BountyHub-specialisterna för dokumentation och små Python-ändringar arbetar en i taget. Anslutningen visar vilken sökkälla som används. Kodändringar testas i Docker och granskas före inlämning. Med automatisk inlämning aktiverad kan både dokumentation och testad kod skickas till GitHub.</div>`;
  }

  function financeView() {
    const submitted = tasks().filter(task => ["submitted", "awaiting_payment"].includes(task.status));
    return `${heading('Resultatet, <em>krona för krona.</em>', "Bekräftade pengar in. Tydlig koll på vad som återstår.", `<div class="heading-actions"><button class="button button-primary" data-action="payment"${submitted.length ? "" : ' disabled title="En betalning kan registreras efter att ett uppdrag skickats in"'}>${icon("finance")}Registrera inbetalning</button></div>`, "EKONOMI")}${metricCards()}<div class="content-grid"><div>${chartPanel()}<div class="hint-card section-gap"><h3>Din överblick bygger på faktiska belopp</h3>Inbetalningar registreras manuellt. Annonserade belöningar och färdiga leveranser räknas inte som intäkter. Belopp i andra valutor räknas inte om automatiskt.</div></div><section class="panel"><div class="panel-heading"><div><h2 class="panel-title">Din ekonomi just nu</h2><p class="panel-subtitle">Alla registrerade belopp i kronor.</p></div>${icon("finance")}</div><div class="section-body"><div class="budget-summary"><div><strong>0 kr</strong><small>/ dygn</small></div><span>Daglig driftbudget</span></div></div><div class="info-stat"><span>Registrerad driftkostnad</span><strong>${money(metrics().estimated_cost_sek)} kr</strong></div><div class="info-stat"><span>Färdiga uppdrag</span><strong>${number(metrics().completed_tasks)}</strong></div><div class="info-stat"><span>Inskickade, ej betalda</span><strong>${submitted.length}</strong></div><div class="info-stat"><span>Betalda uppdrag</span><strong>${tasks().filter(task => task.status === "paid").length}</strong></div><div class="finance-note">Nollbudgeten gäller AI-anrop och servrar. El, dator och internet ingår inte i systemets kostnadsuppföljning.</div></section></div>`;
  }

  function connectionCard(name, data, iconName) {
    const connection = data || {};
    const ready = ["ready", "connected", "ok", "available", "healthy"].includes(String(connection.status || "").toLowerCase());
    const statusText = ready ? "Ansluten" : connection.status === "checking" ? "Kontrolleras" : connection.configured ? "Behöver ses över" : "Ej ansluten";
    return `<div class="connection-card"><div class="connection-title"><span class="connection-icon">${icon(iconName)}</span><span class="connection-name">${name}</span><span class="badge ${ready ? "green" : "amber"}">${statusText}</span></div><p class="connection-detail">${escapeHTML(connection.detail || "Kontrollera anslutningen för att se aktuell status.")}</p></div>`;
  }

  function bountyhubSettings(draft) {
    return `<div class="form-section"><h3>BountyHub-specialister</h3><p class="settings-description">Söker och granskar BountyHub-annonser. Anslutningen visar vilken sökkälla som används. Specialisterna delar din lokala modell och arbetar en i taget.</p><div class="form-field"><div><label class="form-label" for="bountyhub-enabled">Aktivera BountyHub</label><p class="form-help">Använd källan för sökning och arbete.</p></div><label class="switch"><input id="bountyhub-enabled" name="bountyhub_enabled" type="checkbox"${draft.bountyhub_enabled !== false ? " checked" : ""}/><span class="switch-track"></span></label></div><div class="form-field"><div><label class="form-label" for="bountyhub-max-sek">Riktvärde för belöning, SEK</label><p class="form-help">Prioriterar mindre uppdrag. Belopp över riktvärdet kan fortfarande tas; uppgiftens svårighet avgör. Uppskattningen är ingen intäkt eller driftbudget.</p></div><input class="input-number" id="bountyhub-max-sek" name="bountyhub_max_sek" type="number" min="1" max="100000" step="1" required value="${number(draft.bountyhub_max_sek) || 1500}" /></div><div class="form-field"><div><label class="form-label" for="bountyhub-assume-usd">Anta USD när bara $ anges</label><p class="form-help">Använd USD för att uppskatta och prioritera belöningen. Originalvalutan visas fortfarande som ospecificerad.</p></div><label class="switch"><input id="bountyhub-assume-usd" name="bountyhub_assume_usd" type="checkbox"${draft.bountyhub_assume_usd !== false ? " checked" : ""}/><span class="switch-track"></span></label></div><div class="form-field"><div><label class="form-label" for="bountyhub-docs-enabled">Specialist på dokumentation</label><p class="form-help">Förbättrar dokument och skriver uttryckligen efterfrågade nya dokument.</p></div><label class="switch"><input id="bountyhub-docs-enabled" name="bountyhub_docs_enabled" type="checkbox"${draft.bountyhub_docs_enabled !== false ? " checked" : ""}/><span class="switch-track"></span></label></div><div class="form-field"><div><label class="form-label" for="bountyhub-code-enabled">Specialist på små Python-ändringar</label><p class="form-help">Ändrar små Python-projekt och kör tester i Docker. Godkända, granskade leveranser kan skickas automatiskt.</p></div><label class="switch"><input id="bountyhub-code-enabled" name="bountyhub_code_enabled" type="checkbox"${draft.bountyhub_code_enabled !== false ? " checked" : ""}/><span class="switch-track"></span></label></div></div>`;
  }

  function settingsView() {
    if (!settingsDraft || !settingsDirty) settingsDraft = { ...settings() };
    const draft = settingsDraft;
    return `${heading('Din arbetsplats. <em>Dina villkor.</em>', "Ställ in kapacitet, anslutningar och hur självständigt agenterna arbetar.", "", "INSTÄLLNINGAR")}<div class="content-grid"><form id="settings-form" class="panel"><div class="panel-heading"><h2 class="panel-title">Arbetsflöde & kapacitet</h2>${icon("settings")}</div><div class="form-section"><h3>En kostnadsfri start</h3><p class="settings-description">Agenterna använder Ollama på din egen dator. Betalda AI-anrop är avstängda med ditt valda kostnadstak.</p><div class="budget-summary"><div><strong>0 kr</strong><small>/ dygn</small></div><span>Fast kostnadstak</span></div></div><div class="form-section"><div class="form-field"><div><label class="form-label" for="max-agents">Samtidiga agenter</label><p class="form-help">Sparat tak för arbetsagenter. Med BountyHub aktiverat körs högst ett arbetsuppdrag samtidigt; sökningen fortsätter separat.</p></div><div class="range-control"><input id="max-agents-range" type="range" min="1" max="10" step="1" value="${number(draft.max_agents) || 1}" aria-label="Antal samtidiga agenter"/><input class="input-number" id="max-agents" name="max_agents" type="number" min="1" max="10" step="1" required value="${number(draft.max_agents) || 1}" /></div></div><div class="form-field"><div><label class="form-label" for="autoscale">Väx med resultaten</label><p class="form-help">Anpassa automatiskt antalet arbetande agenter upp till ditt tak.</p></div><label class="switch"><input id="autoscale" name="autoscale" type="checkbox"${draft.autoscale ? " checked" : ""}/><span class="switch-track"></span></label></div></div><div class="form-section"><div class="form-field"><div><label class="form-label" for="auto-submit">Skicka in leveranser automatiskt</label><p class="form-help">Granskad dokumentation och testad kod kan skickas via ditt GitHub-konto. Kodens testade projektversion måste fortfarande vara oförändrad. BountyHub-anspråket registreras separat på plattformen.</p></div><label class="switch"><input id="auto-submit" name="auto_submit" type="checkbox"${draft.auto_submit ? " checked" : ""}/><span class="switch-track"></span></label></div><div class="form-field"><div><label class="form-label" for="poll-interval">Sökintervall i minuter</label><p class="form-help">Sökningen startar med programmet. Pausen mellan sökomgångar; källans väntetider kan förlänga den.</p></div><input class="input-number" id="poll-interval" name="poll_interval_minutes" type="number" min="1" max="1440" step="1" required value="${number(draft.poll_interval_minutes) || 1}" /></div></div>${bountyhubSettings(draft)}<div class="settings-actions"><span id="settings-save-note">${settingsDirty ? "Du har osparade ändringar." : "Ändringar gäller efter att du sparat."}</span><button class="button button-primary" type="submit"${operationBusy ? " disabled" : ""}>${icon("check")}Spara inställningar</button></div></form><div class="right-column" style="display:flex"><section class="panel"><div class="panel-heading"><h2 class="panel-title">Anslutningar</h2><button class="text-link" data-action="connections"${operationBusy ? " disabled" : ""}>${icon("refresh")}Kontrollera</button></div>${connectionCard("GitHub · sökning", state.connections?.github, "github")}${connectionCard("GitHub · leveranser", state.connections?.submission, "send")}${connectionCard("BountyHub · sökning", state.connections?.bountyhub, "search")}${connectionCard("Kodtester · Docker", state.connections?.code_tests, "shield")}${connectionCard("Ollama · lokal AI", state.connections?.ai, "spark")}</section><div class="hint-card"><h3>Din dator är motorn</h3>Körs när den här datorn är igång. Inbetalningar registreras manuellt. Modell och GitHub-åtkomst ställs in i serverns lokala miljökonfiguration; inga hemligheter visas här.</div></div></div>`;
  }

  function renderView() {
    if (!state) return;
    const viewMap = { overview: overviewView, tasks: tasksView, agents: agentsView, finance: financeView, settings: settingsView };
    $("#main-content").innerHTML = viewMap[currentView]();
    bindViewEvents();
  }

  function renderTaskResults() {
    const container = $("#task-results");
    if (!container) return;
    container.innerHTML = taskRows(currentView === "overview" ? 5 : 0);
    $("#task-count").textContent = `${filteredTasks().length} uppdrag${currentView === "overview" && filteredTasks().length > 5 ? " · visar de 5 senaste" : ""}`;
    document.querySelectorAll("[data-filter]").forEach(button => button.classList.toggle("selected", button.dataset.filter === taskFilter));
  }

  function bindViewEvents() {
    $("#task-search")?.addEventListener("input", event => { searchQuery = event.target.value; renderTaskResults(); });
    $("#task-filter")?.addEventListener("change", event => { taskFilter = event.target.value; renderTaskResults(); });
    const form = $("#settings-form");
    if (!form) return;
    const markDirty = () => { settingsDirty = true; $("#settings-save-note").textContent = "Du har osparade ändringar."; };
    form.addEventListener("input", event => {
      const input = event.target;
      if (input.id === "max-agents-range") { $("#max-agents").value = input.value; settingsDraft.max_agents = Number(input.value); }
      else if (input.name === "max_agents") { $("#max-agents-range").value = input.value; settingsDraft.max_agents = Number(input.value); }
      else if (input.name) settingsDraft[input.name] = input.type === "checkbox" ? input.checked : Number(input.value);
      markDirty();
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const payload = { max_agents: Number($("#max-agents").value), autoscale: $("#autoscale").checked, auto_submit: $("#auto-submit").checked, poll_interval_minutes: Number($("#poll-interval").value), daily_budget_sek: 0, bountyhub_enabled: $("#bountyhub-enabled").checked, bountyhub_max_sek: Number($("#bountyhub-max-sek").value), bountyhub_assume_usd: $("#bountyhub-assume-usd").checked, bountyhub_docs_enabled: $("#bountyhub-docs-enabled").checked, bountyhub_code_enabled: $("#bountyhub-code-enabled").checked };
      await mutate("/api/settings", payload, "Inställningarna är sparade.", "PUT", () => { settingsDirty = false; settingsDraft = null; });
    });
  }

  async function refreshState(forceRender = false) {
    if (polling) return;
    polling = true;
    try {
      const incoming = await api("/api/state");
      if (!incoming || typeof incoming !== "object" || !incoming.settings) throw new Error("Servern skickade ett oväntat svar. Försök att uppdatera sidan.");
      state = incoming;
      $("#connection-error").hidden = true;
      renderNavigation(); renderSystemStatus();
      const focused = document.activeElement;
      const editing = focused && ["INPUT", "SELECT", "TEXTAREA"].includes(focused.tagName);
      if (forceRender || (!editing && !(currentView === "settings" && settingsDirty))) renderView();
      refreshDrawer();
      $("#last-updated").textContent = `Uppdaterat ${new Intl.DateTimeFormat("sv-SE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Stockholm" }).format(new Date())}`;
    } catch (error) {
      $("#connection-error").textContent = error.message;
      $("#connection-error").hidden = false;
      $("#system-indicator").innerHTML = '<span class="status-dot"></span>Ingen kontakt';
      $("#system-indicator").className = "system-indicator";
      $("#last-updated").textContent = "Väntar på servern";
      if (!state) $("#main-content").innerHTML = `<div class="loading-state"><span class="empty-illustration" style="margin:auto">${icon("link")}</span><p>Arbetsplatsen är inte tillgänglig just nu.</p><button class="button button-secondary" data-action="refresh">${icon("refresh")}Försök igen</button></div>`;
    } finally { polling = false; }
  }

  async function mutate(path, payload, successMessage, method = "POST", onSuccess = null) {
    if (operationBusy) return false;
    operationBusy = true;
    document.querySelectorAll('[data-action="scan"], [data-action="search-control"], [data-action="control"], [data-action="connections"], #settings-form button[type="submit"], #payment-form button[type="submit"], [data-action="retry"]').forEach(button => { button.disabled = true; });
    try {
      await api(path, { method, body: JSON.stringify(payload) });
      if (onSuccess) onSuccess();
      toast(successMessage);
      return true;
    } catch (error) { toast(error.message, true); return false; }
    finally { operationBusy = false; await refreshState(true); const retryButton = $('.drawer [data-action="retry"]'); if (retryButton) retryButton.disabled = false; const paymentButton = $('#payment-form button[type="submit"]'); if (paymentButton) paymentButton.disabled = false; }
  }

  function closeDrawer() {
    $("#drawer-root").innerHTML = "";
    drawerTaskId = null;
    drawerTaskSnapshot = null;
    drawerArtifactPath = null;
    if (!$("#modal-root").children.length) document.body.style.overflow = "";
    previousFocus?.focus?.();
  }

  function refreshDrawer() {
    if (!drawerTaskId || operationBusy || $("#modal-root").children.length) return;
    const task = tasks().find(item => String(item.id) === drawerTaskId);
    if (JSON.stringify(task || null) === drawerTaskSnapshot) return;
    const body = $(".drawer-body");
    const scrollTop = body?.scrollTop || 0;
    const focused = $(".drawer")?.contains(document.activeElement) ? document.activeElement : null;
    const focusAction = focused?.dataset.action;
    const focusHref = focused?.getAttribute("href");
    const preserveArtifact = task && (task.artifact_path || null) === drawerArtifactPath;
    const artifact = preserveArtifact ? $("#artifact-container") : null;
    const artifactScrollTop = artifact?.querySelector(".artifact-output")?.scrollTop || 0;
    const artifactButton = preserveArtifact ? $('.drawer [data-action="artifact"]') : null;
    renderDrawer(drawerTaskId);
    if (!drawerTaskId) return;
    if (artifact?.children.length && $("#artifact-container")) {
      $("#artifact-container").replaceChildren(...artifact.childNodes);
      const output = $("#artifact-container .artifact-output");
      if (output) output.scrollTop = artifactScrollTop;
    }
    const button = $('.drawer [data-action="artifact"]');
    if (button && artifactButton) { button.innerHTML = artifactButton.innerHTML; button.disabled = artifactButton.disabled; }
    // Updating live details should keep the reader in place and avoid replaying
    // the opening animation on every task-stage change.
    $(".drawer").style.animation = "none";
    $(".drawer-backdrop").style.animation = "none";
    if (focused) {
      const replacement = [...document.querySelectorAll('.drawer button, .drawer a[href]')].find(item => (focusAction && item.dataset.action === focusAction) || (focusHref && item.getAttribute("href") === focusHref));
      (replacement || $('.drawer [data-action="close-drawer"]'))?.focus({ preventScroll: true });
    }
    $(".drawer-body").scrollTop = scrollTop;
  }

  function workPlanDetails(task) {
    const boundedText = (value, limit) => typeof value === "string" ? value.trim().slice(0, limit) : "";
    const boundedList = (value, limit, length) => Array.isArray(value) ? value.slice(0, limit).map(item => boundedText(item, length)).filter(Boolean) : [];
    const preparation = boundedText(task.preparation_reason, 1200);
    const preparationText = preparation && preparation !== task.reason ? `<div class="detail-label">Förberedelse</div><p class="detail-text">${escapeHTML(preparation)}</p>` : "";
    const plan = task.work_plan;
    if (!plan || typeof plan !== "object" || Array.isArray(plan)) return preparationText;
    const summary = boundedText(plan.summary, 1200);
    const steps = boundedList(plan.steps, 8, 400);
    const sources = boundedList(plan.source_paths, 12, 240);
    const targets = boundedList(plan.target_paths, 12, 240);
    if (!summary && !steps.length && !sources.length && !targets.length) return preparationText;
    return `${preparationText}<section class="work-plan" aria-label="Arbetsplan"><div class="detail-label">Arbetsplan</div>${summary ? `<p class="detail-text">${escapeHTML(summary)}</p>` : ""}${steps.length ? `<ol class="detail-text">${steps.map(step => `<li>${escapeHTML(step)}</li>`).join("")}</ol>` : ""}${sources.length ? `<div class="detail-label">Underlag</div><p class="detail-text">${sources.map(escapeHTML).join(" · ")}</p>` : ""}${targets.length ? `<div class="detail-label">Filer som ska levereras</div><p class="detail-text">${targets.map(escapeHTML).join(" · ")}</p>` : ""}</section>`;
  }

  function sourceDetails(task) {
    const bountyhub = task.source === "bountyhub";
    const specialist = specialistName(task);
    const estimate = task.price_estimate;
    const validEstimate = estimate && typeof estimate === "object" && !Array.isArray(estimate);
    const available = validEstimate && estimate.status === "available" && estimate.selection_only === true && typeof estimate.amount_sek === "number" && Number.isFinite(estimate.amount_sek) && estimate.amount_sek >= 0;
    const date = validEstimate && /^\d{4}-\d{2}-\d{2}$/.test(String(estimate.rate_date || "")) ? estimate.rate_date : null;
    const reason = validEstimate && typeof estimate.reason === "string" ? estimate.reason.slice(0, 800) : "SEK-uppskattning har inte gjorts.";
    return `<div class="detail-grid"><div><div class="detail-label">Källa</div><p class="detail-text">${bountyhub ? "BountyHub" : "GitHub"}</p></div>${task.specialist ? `<div><div class="detail-label">Specialist</div><p class="detail-text">${escapeHTML(specialist)}</p></div>` : ""}</div>${bountyhub ? `<section class="price-estimate" aria-label="Uppskattat värde i SEK"><div class="detail-label">Uppskattat värde i SEK</div><p class="detail-text">${available ? `Cirka ${money(estimate.amount_sek, 2)} kr${estimate.assumed_currency === true && estimate.conversion_currency === "USD" ? " · $ räknad som USD" : ""}${date ? ` · kursdatum ${escapeHTML(date)}` : ""}. Används endast för prioritering; ingen bekräftad intäkt.` : `${escapeHTML(reason)} Saknad SEK-uppskattning stoppar inte uppdraget. Se Senaste bedömning för urvalsorsaken.`}</p></section>` : ""}`;
  }

  function testReportDetails(task) {
    if (task.specialist !== "code") return "";
    const report = task.test_report && typeof task.test_report === "object" && !Array.isArray(task.test_report) ? task.test_report : {};
    const boundedText = (value, limit) => typeof value === "string" ? value.slice(0, limit) : "";
    const count = value => Number.isInteger(value) && value >= 0 ? value : null;
    const stages = [["baseline", "Tester före ändring"], ["regression_before", "Nytt regressionstest före ändring"], ["final", "Tester efter ändring"]];
    const results = stages.map(([key, label]) => {
      const result = report[key];
      if (!result || typeof result !== "object" || Array.isArray(result)) return `<div class="test-stage"><strong>${label}</strong><p class="detail-text">Inget testresultat registrerat.</p></div>`;
      const tests = count(result.tests_run), failures = count(result.failure_count), errors = count(result.error_count), skipped = count(result.skipped_count);
      const expected = count(result.expected_failure_count ?? 0), unexpected = count(result.unexpected_success_count ?? 0);
      const measured = [tests, failures, errors, skipped, expected, unexpected].every(value => value !== null) && tests > 0 && skipped === 0 && expected === 0 && unexpected === 0;
      const passed = measured && result.passed === true && failures === 0 && errors === 0 && result.timed_out !== true;
      const reproduced = key === "regression_before" && measured && result.passed === false && failures > 0 && errors === 0 && result.timed_out !== true;
      const verdict = result.timed_out === true ? "Tidsgränsen nåddes" : reproduced ? "Felet återskapat" : passed ? "Testerna godkändes" : tests === 0 || (tests !== null && tests === skipped) ? "Inga tester kördes" : "Tester inte godkända";
      const counts = [[tests, "testfall"], [failures, "misslyckade"], [errors, "körfel"], [skipped, "överhoppade"]].filter(([value]) => value !== null).map(([value, name]) => `${value} ${name}`).join(" · ");
      const detail = boundedText(result.detail, 1200), output = boundedText(result.output, 8000);
      return `<div class="test-stage"><strong>${label}</strong><p class="detail-text">${verdict}${counts ? `<br>${counts}` : ""}</p>${detail ? `<p class="detail-text">${escapeHTML(detail)}</p>` : ""}${output ? `<details class="test-output"><summary>Visa testutskrift</summary><pre class="artifact-output">${escapeHTML(output)}</pre></details>` : ""}</div>`;
    }).join("");
    const runner = boundedText(report.runner, 200);
    return `<section class="test-report" aria-label="Observerade kodtester"><div class="detail-label">Observerade kodtester</div>${runner ? `<p class="detail-text">${escapeHTML(runner)}</p>` : ""}${results}</section><p class="drawer-info">Kodleveranser kan skickas automatiskt efter godkända tester och separat granskning. Projektversionen måste vara densamma som den testade.</p>`;
  }

  function renderDrawer(id) {
    const task = tasks().find(item => String(item.id) === String(id));
    if (!task) { closeDrawer(); return; }
    drawerTaskId = String(id);
    drawerTaskSnapshot = JSON.stringify(task);
    drawerArtifactPath = task.artifact_path || null;
    const url = safeURL(task.url);
    const bountyhubURL = task.source === "bountyhub" ? safeBountyHubURL(task.bountyhub_url) : null;
    const submissionURL = safePullRequestURL(task.submission_url);
    const claimRequired = bountyhubURL && submissionURL && task.platform_claim_status === "manual_required";
    const retryable = ["blocked", "failed", "skipped"].includes(task.status);
    $("#drawer-root").innerHTML = `<div class="drawer-backdrop" data-action="close-drawer"></div><section class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><header class="drawer-header"><h2 id="drawer-title">Uppdragsdetaljer</h2><button class="icon-button" data-action="close-drawer" aria-label="Stäng detaljer">${icon("close")}</button></header><div class="drawer-body">${statusBadge(task.status)}<h3>${escapeHTML(task.title)}</h3><p class="drawer-repo">${escapeHTML(task.repo || "GitHub")}</p><div class="detail-grid"><div><div class="detail-label">Annonserad belöning</div><p class="detail-text">${reward(task, false)}</p></div><div><div class="detail-label">Uppdragstyp</div><p class="detail-text">${escapeHTML(task.kind || "Ej klassificerat")}</p></div></div>${sourceDetails(task)}<div class="detail-label">Senaste bedömning</div><p class="detail-text">${escapeHTML(task.reason || "Uppdraget har ännu inte bedömts.")}</p>${workPlanDetails(task)}${testReportDetails(task)}<div class="detail-grid"><div><div class="detail-label">Hittat</div><p class="detail-text">${escapeHTML(formatDate(task.created_at))}</p></div><div><div class="detail-label">Senast uppdaterat</div><p class="detail-text">${escapeHTML(formatDate(task.updated_at))}</p></div></div><div class="drawer-actions">${url ? `<a class="button button-secondary" href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${icon("github")}Visa på GitHub</a>` : ""}${bountyhubURL ? `<a class="button button-secondary" href="${escapeHTML(bountyhubURL)}" target="_blank" rel="noopener noreferrer">${icon("link")}Visa på BountyHub</a>` : ""}${submissionURL ? `<a class="button button-secondary" href="${escapeHTML(submissionURL)}" target="_blank" rel="noopener noreferrer">${icon("github")}Visa pull request</a>` : ""}${claimRequired ? `<a class="button button-primary" href="${escapeHTML(bountyhubURL)}" target="_blank" rel="noopener noreferrer">${icon("send")}Registrera PR på BountyHub</a>` : ""}${retryable ? `<button class="button button-secondary" data-action="retry"${operationBusy ? " disabled" : ""}>${icon("refresh")}Försök igen</button>` : ""}${["submitted", "awaiting_payment"].includes(task.status) ? `<button class="button button-primary" data-action="payment" data-payment-task="${escapeHTML(task.id)}">${icon("finance")}Registrera betalning</button>` : ""}</div>${claimRequired ? `<p class="drawer-info" role="note">Din pull request finns på GitHub. Logga in på BountyHub med samma GitHub-konto och registrera PR-länken som ett anspråk på belöningen. Anspråket är ännu inte registrerat av BountyOS.</p>` : ""}${task.artifact_path ? `<div class="detail-label">Arbetsresultat</div><button class="button button-secondary" data-action="artifact">${icon("file")}Läs leveransen</button><div id="artifact-container"></div>` : '<p class="drawer-info">En sparad leverans visas här när agenten har tagit fram ett arbetsresultat.</p>'}<p class="drawer-info">Den annonserade belöningen är ingen bekräftad intäkt. Registrera inbetalningen när du faktiskt har fått betalt.</p></div></section>`;
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    $("#modal-root").innerHTML = "";
    if (!drawerTaskId) document.body.style.overflow = "";
    previousFocus?.focus?.();
  }

  function paymentModal(selectedId = "") {
    const eligible = tasks().filter(task => ["submitted", "awaiting_payment"].includes(task.status));
    if (!eligible.length) { toast("Det finns inget inskickat uppdrag att registrera betalning för.", true); return; }
    previousFocus = document.activeElement;
    $("#modal-root").innerHTML = `<div class="modal-backdrop" data-action="close-modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="payment-title"><div class="modal-header"><h2 id="payment-title">Registrera inbetalning</h2><button class="icon-button" data-action="close-modal" aria-label="Stäng betalningsformulär">${icon("close")}</button></div><p>Registrera pengar som du redan har fått. Ange beloppet i svenska kronor efter eventuell växling.</p><form id="payment-form"><div class="modal-field"><label for="payment-task">Inskickat uppdrag</label><select id="payment-task" name="task_id" required>${eligible.map(task => `<option value="${escapeHTML(task.id)}"${String(task.id) === String(selectedId) ? " selected" : ""}>${escapeHTML(task.title)}</option>`).join("")}</select></div><div class="modal-field"><label for="payment-amount">Mottaget belopp (SEK)</label><input id="payment-amount" name="amount_sek" type="number" min="0.01" max="10000000" step="0.01" required placeholder="0,00" inputmode="decimal" /></div><div class="modal-field"><label for="payment-reference">Betalningsreferens</label><input id="payment-reference" name="reference" type="text" maxlength="200" required placeholder="Till exempel transaktions-ID eller fakturanummer" /></div><div class="modal-actions"><button class="button button-secondary" type="button" data-action="close-modal">Avbryt</button><button class="button button-primary" type="submit">${icon("check")}Registrera inbetalning</button></div></form></section></div>`;
    document.body.style.overflow = "hidden";
    $("#payment-amount").focus();
    $("#payment-form").addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.target;
      if (!form.reportValidity()) return;
      const payload = { task_id: $("#payment-task").value, amount_sek: Number($("#payment-amount").value), reference: $("#payment-reference").value.trim() };
      if (!payload.reference) { toast("Ange en betalningsreferens.", true); return; }
      await mutate("/api/payments", payload, "Inbetalningen är registrerad.", "POST", closeModal);
    });
  }

  document.addEventListener("click", async event => {
    const taskButton = event.target.closest("[data-task-id]");
    if (taskButton) { previousFocus = taskButton; renderDrawer(taskButton.dataset.taskId); $('.drawer [data-action="close-drawer"]')?.focus(); return; }
    const filter = event.target.closest("[data-filter]");
    if (filter) { taskFilter = filter.dataset.filter; renderTaskResults(); return; }
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton || actionButton.disabled) return;
    const action = actionButton.dataset.action;
    if (action === "control") await mutate("/api/control", { action: isRunning() ? "pause" : "start" }, isRunning() ? "Arbetsagenterna har pausats. Sökningen styrs separat." : "Arbetsagenterna har startats. Följ arbetet i aktivitetsflödet.");
    else if (action === "search-control") await mutate("/api/settings", { search_enabled: !searchEnabled() }, searchEnabled() ? "Automatisk sökning pausad. En pågående omgång får slutföras." : "Automatisk sökning startad.", "PUT");
    else if (action === "scan") await mutate("/api/scan", {}, "Sökningen är startad. Nya uppdrag visas automatiskt.");
    else if (action === "connections") await mutate("/api/connections/check", {}, "Anslutningarna har kontrollerats.");
    else if (action === "refresh") await refreshState(true);
    else if (action === "clear-filter") { searchQuery = ""; taskFilter = "all"; renderView(); }
    else if (action === "close-drawer") closeDrawer();
    else if (action === "close-modal") closeModal();
    else if (action === "close-modal-backdrop" && event.target === actionButton) closeModal();
    else if (action === "payment") paymentModal(actionButton.dataset.paymentTask || "");
    else if (action === "retry" && drawerTaskId) await mutate(`/api/tasks/${encodeURIComponent(drawerTaskId)}/retry`, {}, "Uppdraget har lagts tillbaka för ett nytt försök.");
    else if (action === "artifact" && drawerTaskId) {
      const id = drawerTaskId;
      const artifactPath = drawerArtifactPath;
      actionButton.disabled = true;
      try {
        const result = await api(`/api/tasks/${encodeURIComponent(id)}/artifact`);
        if (drawerTaskId !== id || drawerArtifactPath !== artifactPath) return;
        const container = $("#artifact-container");
        const output = document.createElement("pre");
        output.className = "artifact-output";
        output.textContent = typeof result === "string" ? result : result.content || result.text || JSON.stringify(result, null, 2);
        container.replaceChildren(output);
        const currentButton = $('.drawer [data-action="artifact"]');
        if (currentButton) currentButton.innerHTML = `${icon("check")}Leveransen visas nedan`;
      } catch (error) { toast(error.message, true); if (drawerTaskId === id && drawerArtifactPath === artifactPath) { const currentButton = $('.drawer [data-action="artifact"]'); if (currentButton) currentButton.disabled = false; } }
    }
  });

  window.addEventListener("hashchange", () => {
    const nextView = location.hash.slice(1);
    currentView = navigation.some(item => item.key === nextView) ? nextView : "overview";
    closeDrawer(); closeModal();
    $(".sidebar").classList.remove("open");
    $("#mobile-menu").setAttribute("aria-expanded", "false");
    renderNavigation(); renderView();
    window.scrollTo({ top: 0, behavior: "instant" });
  });

  $("#mobile-menu").addEventListener("click", () => {
    const open = $(".sidebar").classList.toggle("open");
    $("#mobile-menu").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", event => {
    if ($(".sidebar").classList.contains("open") && !event.target.closest(".sidebar, #mobile-menu")) {
      $(".sidebar").classList.remove("open");
      $("#mobile-menu").setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if ($("#modal-root").children.length) closeModal(); else if (drawerTaskId) closeDrawer();
      $(".sidebar").classList.remove("open"); $("#mobile-menu").setAttribute("aria-expanded", "false");
    }
    if (event.key !== "Tab") return;
    const dialog = $("#modal-root [role=dialog]") || $("#drawer-root [role=dialog]");
    if (!dialog) return;
    const focusable = [...dialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (!first) return;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshState(); });
  setInterval(() => { if (!document.hidden && !operationBusy) refreshState(); }, 8000);
  renderNavigation();
  refreshState(true);
  api("/api/health").catch(() => {});
})();
