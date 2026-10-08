const DEFAULT_API_BASE = "http://localhost:8000";
const DEVICE_KEY = "applicationFirewall.deviceUuid.v1";
const CANARY_TOKEN = "MERIDIAN-7";

export class FirewallSDK {
  constructor({ form, apiBase = DEFAULT_API_BASE, job }) {
    if (!(form instanceof HTMLFormElement)) throw new TypeError("FirewallSDK requires a form element.");
    this.form = form;
    this.apiBase = apiBase.replace(/\/$/, "");
    this.job = job;
    this.startedAt = performance.now();
    this.typedChars = 0;
    this.pastedChars = 0;
    this.lastKeyAt = null;
    this.interKeyIntervals = [];
    this.modalityCounts = { keyboard: 0, mouse: 0, touch: 0, dictation: 0 };
    this.handleBeforeInput = this.handleBeforeInput.bind(this);
    this.handlePaste = this.handlePaste.bind(this);
    this.handleKeydown = this.handleKeydown.bind(this);
    this.handlePointerdown = this.handlePointerdown.bind(this);
    this.attachSignalCollection();
  }

  attachSignalCollection() {
    this.form.addEventListener("beforeinput", this.handleBeforeInput);
    this.form.addEventListener("paste", this.handlePaste);
    this.form.addEventListener("keydown", this.handleKeydown);
    this.form.addEventListener("pointerdown", this.handlePointerdown);
  }

  destroy() {
    this.form.removeEventListener("beforeinput", this.handleBeforeInput);
    this.form.removeEventListener("paste", this.handlePaste);
    this.form.removeEventListener("keydown", this.handleKeydown);
    this.form.removeEventListener("pointerdown", this.handlePointerdown);
  }

  handleBeforeInput(event) {
    const inputType = event.inputType || "";
    if (inputType.startsWith("insertFromDictation")) {
      this.modalityCounts.dictation += 1;
      this.typedChars += event.data?.length || 1;
      return;
    }
    if (inputType === "insertText" || inputType === "insertCompositionText") {
      this.modalityCounts.keyboard += 1;
      this.typedChars += event.data?.length || 1;
    }
  }

  handlePaste(event) {
    this.pastedChars += event.clipboardData?.getData("text")?.length || 0;
  }

  handleKeydown(event) {
    if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) return;
    const now = performance.now();
    if (this.lastKeyAt !== null) {
      const interval = now - this.lastKeyAt;
      if (interval < 5000) this.interKeyIntervals.push(interval);
    }
    this.lastKeyAt = now;
  }

  handlePointerdown(event) {
    if (event.pointerType === "touch") this.modalityCounts.touch += 1;
    else if (event.pointerType === "mouse") this.modalityCounts.mouse += 1;
  }

  markSimulatedPaste(characterCount) {
    const count = Math.max(0, Number(characterCount) || 0);
    this.pastedChars += count;
  }

  buildPayload() {
    const data = new FormData(this.form);
    const typedAndPasted = this.typedChars + this.pastedChars;
    const applicationId = createUuid();
    const freeText = [...this.form.querySelectorAll('textarea, input[type="text"]')]
      .filter((field) => field.name !== "company_website")
      .map((field) => field.value)
      .join("\n");

    return {
      application: {
        application_id: applicationId,
        job_id: String(data.get("job_id")),
        candidate: {
          name: String(data.get("name")).trim(),
          email: String(data.get("email")).trim(),
          phone: String(data.get("phone")).trim(),
          skills: splitCommaList(data.get("skills")),
          experience: collectRows(this.form, ".experience-row", ["company", "title", "start", "end"]),
          projects: collectRows(this.form, ".project-row", ["name", "description"]),
          claimed_experience_years: numberOrNull(data.get("claimed_experience_years")),
        },
        signals: {
          device_id: getDeviceId(),
          ip: "0.0.0.0",
          session_seconds: Math.max(0, (performance.now() - this.startedAt) / 1000),
          paste_char_ratio: typedAndPasted ? this.pastedChars / typedAndPasted : 0,
          submitted_at: Date.now() / 1000,
        },
      },
      job: {
        must_have_skills: [...this.job.must_have_skills],
        nice_to_have: [...this.job.nice_to_have],
        min_years: this.job.min_years,
      },
      extra_signals: {
        keystroke_inter_key_ms: summarizeTimings(this.interKeyIntervals),
        input_modality_counts: { ...this.modalityCounts },
        honeypot_filled: String(data.get("company_website") || "").trim().length > 0,
        canary_hit: freeText.toUpperCase().includes(CANARY_TOKEN),
      },
    };
  }

  async submit() {
    const payload = this.buildPayload();
    const response = await fetch(`${this.apiBase}/v1/applications/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      let detail = `The application service returned ${response.status}.`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
      }
      throw new Error(detail);
    }

    return { decision: await response.json(), payload };
  }
}

function collectRows(form, selector, keys) {
  return [...form.querySelectorAll(selector)]
    .map((row) => Object.fromEntries(keys.map((key) => [key, row.querySelector(`[data-field="${key}"]`)?.value.trim() || ""])))
    .filter((row) => Object.values(row).some(Boolean));
}

function splitCommaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberOrNull(value) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return null;
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function summarizeTimings(values) {
  if (!values.length) return { count: 0, mean: null, median: null, p95: null };
  const sorted = [...values].sort((a, b) => a - b);
  const mean = sorted.reduce((sum, value) => sum + value, 0) / sorted.length;
  return {
    count: sorted.length,
    mean: round(mean),
    median: round(percentile(sorted, 0.5)),
    p95: round(percentile(sorted, 0.95)),
  };
}

function percentile(sorted, proportion) {
  const index = Math.min(sorted.length - 1, Math.ceil(sorted.length * proportion) - 1);
  return sorted[index];
}

function round(value) {
  return Math.round(value * 10) / 10;
}

function getDeviceId() {
  let uuid;
  try {
    uuid = localStorage.getItem(DEVICE_KEY);
    if (!uuid) {
      uuid = createUuid();
      localStorage.setItem(DEVICE_KEY, uuid);
    }
  } catch {
    uuid = createUuid();
  }
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown";
  const fingerprint = [navigator.userAgent, screen.width, screen.height, screen.colorDepth, timezone].join("|");
  return `${uuid}.${lightHash(fingerprint)}`;
}

function lightHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function createUuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}
