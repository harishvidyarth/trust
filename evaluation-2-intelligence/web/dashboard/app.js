const API_BASE = "http://localhost:8000";
const POLL_MS = 2000;
const STORAGE_KEYS = {
  verification: "firewallDesk.verification.v1",
  overrides: "firewallDesk.overrides.v1",
};

const ROUTES = {
  PASS_TO_ATS: { label: "Pass to ATS", className: "pass" },
  ADDITIONAL_VERIFICATION: { label: "Verification", className: "verify" },
  MANUAL_REVIEW: { label: "Manual review", className: "review" },
};

const REASON_EXPLANATIONS = {
  DUP_EMAIL: "This email address appears on another recorded application.",
  DUP_PHONE: "This phone number appears on another recorded application.",
  DUP_RESUME_NEAR: "The application content is unusually similar to a previous submission.",
  DUP_SAME_JOB: "The candidate appears to have submitted more than once for this job.",
  VELOCITY_HIGH: "This device or network submitted an unusually high number of applications in a short period.",
  FAST_SUBMIT: "The form was completed much faster than the configured review threshold.",
  PASTE_BULK: "Most of the application text appears to have been pasted rather than entered gradually.",
  TEMPLATE_REUSE: "The same project wording appears across several applications.",
  QUAL_MISSING_MUST_HAVE: "One or more skills marked as required for this role were not listed.",
  QUAL_UNDER_EXPERIENCE: "The stated experience is below the role's configured minimum.",
  TIMELINE_INVALID: "At least one employment date range needs a recruiter to confirm it.",
  TIMELINE_OVERLAP: "Two employment periods overlap beyond the configured allowance.",
};

const state = {
  decisions: [],
  source: "loading",
  selectedId: null,
  polling: false,
  verification: readStore(STORAGE_KEYS.verification),
  overrides: readStore(STORAGE_KEYS.overrides),
};

const elements = {
  banner: document.querySelector("#demo-banner"),
  connection: document.querySelector(".header-status"),
  connectionLabel: document.querySelector("#connection-label"),
  lastUpdated: document.querySelector("#last-updated"),
  total: document.querySelector("#total-count"),
  filtered: document.querySelector("#filtered-count"),
  funnel: document.querySelector("#funnel"),
  chart: document.querySelector("#reason-chart"),
  feedBody: document.querySelector("#feed-body"),
  emptyFeed: document.querySelector("#empty-feed"),
  refresh: document.querySelector("#refresh-button"),
  detail: document.querySelector("#application-detail"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
};

elements.refresh.addEventListener("click", () => refreshData());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshData();
});

refreshData();
window.setInterval(() => refreshData(), POLL_MS);

async function refreshData() {
  if (state.polling) return;
  state.polling = true;
  elements.refresh.disabled = true;

  try {
    const response = await fetch(`${API_BASE}/v1/decisions?limit=200`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const decisions = await response.json();
    if (!Array.isArray(decisions)) throw new Error("Unexpected decision response");
    state.decisions = decisions;
    state.source = "api";
  } catch (error) {
    if (state.source !== "demo") {
      try {
        const sampleResponse = await fetch("./sample-data.json", { cache: "no-store" });
        if (!sampleResponse.ok) throw new Error(`Sample data returned ${sampleResponse.status}`);
        state.decisions = await sampleResponse.json();
      } catch (sampleError) {
        state.decisions = [];
        console.error("Could not load API or sample data", error, sampleError);
      }
    }
    state.source = "demo";
  } finally {
    state.polling = false;
    elements.refresh.disabled = false;
    render();
  }
}

function render() {
  const now = new Date();
  const isDemo = state.source === "demo";
  elements.banner.hidden = !isDemo;
  elements.connection.classList.toggle("online", !isDemo);
  elements.connection.classList.toggle("offline", isDemo);
  elements.connectionLabel.textContent = isDemo ? "Demo mode" : "API live";
  elements.lastUpdated.textContent = `updated ${now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;

  renderOverview();
  renderFunnel();
  renderReasonChart();
  renderFeed();
  renderDetail();
}

function renderOverview() {
  const filtered = state.decisions.filter((item) => item.decision?.route !== "PASS_TO_ATS").length;
  elements.total.textContent = formatNumber(state.decisions.length);
  elements.filtered.textContent = formatNumber(filtered);
}

function renderFunnel() {
  const total = state.decisions.length;
  const definitions = [
    { label: "Received", count: total, className: "received" },
    { label: "Passed to ATS", count: routeCount("PASS_TO_ATS"), className: "pass" },
    { label: "Needs verification", count: routeCount("ADDITIONAL_VERIFICATION"), className: "verify" },
    { label: "Manual review", count: routeCount("MANUAL_REVIEW"), className: "review" },
  ];

  const cards = definitions.map((definition) => {
    const article = node("article", `funnel-card ${definition.className}`);
    const label = node("span", "", definition.label);
    const count = node("strong", "", formatNumber(definition.count));
    const percent = total ? Math.round((definition.count / total) * 100) : 0;
    const share = node("span", "", `${percent}% of received`);
    article.append(label, count, share);
    return article;
  });
  elements.funnel.replaceChildren(...cards);
}

function renderReasonChart() {
  const counts = new Map();
  for (const item of state.decisions) {
    for (const reason of item.decision?.reasons ?? []) {
      counts.set(reason.code, (counts.get(reason.code) ?? 0) + 1);
    }
  }
  const ranked = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 7);

  if (!ranked.length) {
    elements.chart.replaceChildren(node("p", "no-signals", "No risk or qualification signals recorded."));
    return;
  }

  const svg = svgNode("svg");
  const width = 700;
  const rowHeight = 38;
  const height = ranked.length * rowHeight + 18;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Top reason codes: ${ranked.map(([code, count]) => `${code}, ${count}`).join("; ")}`);
  const max = ranked[0][1];

  ranked.forEach(([code, count], index) => {
    const y = index * rowHeight + 6;
    const label = svgNode("text");
    label.setAttribute("x", "0");
    label.setAttribute("y", String(y + 14));
    label.setAttribute("class", "chart-label");
    label.textContent = code;

    const track = svgNode("rect");
    track.setAttribute("x", "210");
    track.setAttribute("y", String(y));
    track.setAttribute("width", "430");
    track.setAttribute("height", "20");
    track.setAttribute("rx", "10");
    track.setAttribute("class", "chart-track");

    const bar = svgNode("rect");
    bar.setAttribute("x", "210");
    bar.setAttribute("y", String(y));
    bar.setAttribute("width", String(Math.max(8, (count / max) * 430)));
    bar.setAttribute("height", "20");
    bar.setAttribute("rx", "10");
    bar.setAttribute("class", "chart-bar");

    const value = svgNode("text");
    value.setAttribute("x", "654");
    value.setAttribute("y", String(y + 14));
    value.setAttribute("class", "chart-value");
    value.textContent = String(count);
    svg.append(label, track, bar, value);
  });
  elements.chart.replaceChildren(svg);
}

function renderFeed() {
  const rows = state.decisions.map((item) => {
    const route = getRoute(item.decision?.route);
    const row = document.createElement("tr");
    if (item.application_id === state.selectedId) row.setAttribute("aria-current", "true");

    const candidate = document.createElement("td");
    candidate.className = "candidate-cell";
    candidate.append(
      node("strong", "", item.candidate_name || "Unknown candidate"),
      node("small", "", item.candidate_email_masked || "Email unavailable"),
    );
    const submitted = node("td", "", formatDate(item.submitted_at));
    const score = node("td", "", Number.isFinite(item.decision?.score) ? `${item.decision.score}/100` : "—");
    const routeCell = document.createElement("td");
    routeCell.append(node("span", `route-badge ${route.className}`, route.label));

    const verificationCell = document.createElement("td");
    const verification = getVerification(item);
    verificationCell.append(node("span", `state-pill ${slug(verification)}`, verification));

    const actionCell = document.createElement("td");
    const action = state.overrides[item.application_id] || "No override";
    actionCell.append(node("span", `state-pill ${slug(action)}`, action));

    const detailCell = document.createElement("td");
    const detailButton = node("button", "detail-link", "View");
    detailButton.type = "button";
    detailButton.setAttribute("aria-label", `View details for ${item.candidate_name || "candidate"}`);
    detailButton.addEventListener("click", () => selectApplication(item.application_id));
    detailCell.append(detailButton);

    row.append(candidate, submitted, score, routeCell, verificationCell, actionCell, detailCell);
    return row;
  });

  elements.feedBody.replaceChildren(...rows);
  elements.emptyFeed.hidden = rows.length > 0;
}

function renderDetail() {
  const item = state.decisions.find((decision) => decision.application_id === state.selectedId);
  elements.detailEmpty.hidden = Boolean(item);
  elements.detailContent.hidden = !item;
  if (!item) {
    elements.detailContent.replaceChildren();
    return;
  }

  const route = getRoute(item.decision?.route);
  const header = node("div", "detail-header");
  const identity = node("div", "detail-identity");
  const identityText = document.createElement("div");
  identityText.append(
    node("p", "eyebrow", `Job ${item.job_id || "unassigned"}`),
    node("h2", "", item.candidate_name || "Unknown candidate"),
    node("p", "", `${item.candidate_email_masked || "Email unavailable"} · ${formatDate(item.submitted_at, true)}`),
    node("span", `route-badge ${route.className}`, route.label),
  );
  identity.append(identityText);

  const gauge = node("div", "score-gauge");
  const score = Number.isFinite(item.decision?.score) ? item.decision.score : 0;
  gauge.style.setProperty("--score", String(score));
  gauge.setAttribute("role", "progressbar");
  gauge.setAttribute("aria-label", "Trust score");
  gauge.setAttribute("aria-valuemin", "0");
  gauge.setAttribute("aria-valuemax", "100");
  gauge.setAttribute("aria-valuenow", String(score));
  const gaugeLabel = node("span", "gauge-label", String(score));
  gaugeLabel.append(node("small", "", "trust score"));
  gauge.append(gaugeLabel);
  header.append(identity, gauge);

  const layout = node("div", "detail-layout");
  const signalColumn = document.createElement("div");
  signalColumn.append(
    node("h3", "", "Decision summary"),
    node("p", "summary-box", item.decision?.summary || "No summary available."),
    node("h3", "", "Signals and weights"),
  );
  const reasonList = node("div", "reason-list");
  const reasons = item.decision?.reasons ?? [];
  if (!reasons.length) {
    reasonList.append(node("p", "muted", "No configured concerns were detected."));
  } else {
    reasonList.append(...reasons.map(renderReason));
  }
  signalColumn.append(reasonList);

  const controls = node("div", "decision-controls");
  controls.append(node("h3", "", "Recruiter controls"));
  const verificationGroup = node("div", "control-group");
  const verificationLabel = node("label", "", "Verification status");
  verificationLabel.htmlFor = "verification-status";
  const select = document.createElement("select");
  select.id = "verification-status";
  for (const value of ["Not required", "Pending", "Verified", "Failed"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = getVerification(item) === value;
    select.append(option);
  }
  select.addEventListener("change", () => {
    state.verification[item.application_id] = select.value;
    writeStore(STORAGE_KEYS.verification, state.verification);
    announceSaved(`Verification status saved as ${select.value}.`);
    renderFeed();
  });
  verificationGroup.append(verificationLabel, select);

  const actionGroup = node("div", "control-group");
  actionGroup.append(node("label", "", "Recruiter override"));
  const actions = node("div", "detail-actions");
  actions.append(
    actionButton("Approve", "primary", item),
    actionButton("Reject", "danger", item),
    actionButton("Request verification", "", item),
  );
  actionGroup.append(actions);
  const currentAction = state.overrides[item.application_id] || "No override saved";
  const saved = node("p", "saved-status", `Current: ${currentAction}`);
  saved.id = "saved-status";
  saved.setAttribute("role", "status");
  actionGroup.append(saved);
  controls.append(verificationGroup, actionGroup);

  layout.append(signalColumn, controls);
  elements.detailContent.replaceChildren(header, layout);
}

function renderReason(reason) {
  const item = node("article", "reason-item");
  const heading = node("div", "reason-title");
  heading.append(node("strong", "", reason.code), node("span", "", `−${reason.weight} points`));
  const explanation = node("p", "", explainReason(reason.code));
  const detail = node("p", "", reason.detail || "No additional detail supplied.");
  const track = node("div", "weight-track");
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", `${reason.code} has a weight of ${reason.weight} points`);
  const fill = node("div", "weight-fill");
  fill.style.width = `${Math.min(100, Math.max(0, (Number(reason.weight) / 40) * 100))}%`;
  track.append(fill);
  item.append(heading, explanation, detail, track);
  return item;
}

function actionButton(label, className, item) {
  const button = node("button", `action-button ${className}`.trim(), label);
  button.type = "button";
  button.addEventListener("click", () => {
    state.overrides[item.application_id] = label;
    if (label === "Request verification") {
      state.verification[item.application_id] = "Pending";
      writeStore(STORAGE_KEYS.verification, state.verification);
    }
    writeStore(STORAGE_KEYS.overrides, state.overrides);
    renderFeed();
    renderDetail();
    announceSaved(`${label} saved locally for ${item.candidate_name || "this candidate"}.`);
  });
  return button;
}

function selectApplication(applicationId) {
  state.selectedId = applicationId;
  renderFeed();
  renderDetail();
  elements.detail.scrollIntoView({ behavior: "smooth", block: "start" });
  elements.detail.focus({ preventScroll: true });
}

function getVerification(item) {
  return state.verification[item.application_id]
    || (item.decision?.route === "PASS_TO_ATS" ? "Not required" : "Pending");
}

function explainReason(code = "") {
  if (REASON_EXPLANATIONS[code]) return REASON_EXPLANATIONS[code];
  if (code.startsWith("GITHUB_")) return "A public GitHub profile signal could not be fully confirmed and may need a recruiter check.";
  if (code.startsWith("DOI_")) return "A cited publication identifier could not be fully confirmed and may need a recruiter check.";
  if (code.startsWith("DOMAIN_")) return "A domain or website claim could not be fully confirmed and may need a recruiter check.";
  if (code.startsWith("FED_")) return "A federated identity or profile signal could not be fully confirmed and may need a recruiter check.";
  return "This configured signal contributed to the routing score and should be interpreted with the application context.";
}

function getRoute(routeCode) {
  return ROUTES[routeCode] ?? { label: routeCode || "Unknown", className: "" };
}

function routeCount(routeCode) {
  return state.decisions.filter((item) => item.decision?.route === routeCode).length;
}

function formatDate(value, full = false) {
  if (value === undefined || value === null) return "Unknown time";
  const numeric = Number(value);
  const date = new Date(numeric < 100000000000 ? numeric * 1000 : numeric);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, full
    ? { dateStyle: "medium", timeStyle: "short" }
    : { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function slug(value) {
  return String(value).toLowerCase().replaceAll(" ", "-");
}

function node(tagName, className = "", text = "") {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== "") element.textContent = text;
  return element;
}

function svgNode(tagName) {
  return document.createElementNS("http://www.w3.org/2000/svg", tagName);
}

function readStore(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}");
  } catch {
    return {};
  }
}

function writeStore(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn("Local browser storage is unavailable", error);
  }
}

function announceSaved(message) {
  const target = document.querySelector("#saved-status");
  if (target) target.textContent = message;
}
