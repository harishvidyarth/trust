# AI Application Firewall web demo

This directory is a build-free recruiter dashboard and mock career page for the FastAPI application firewall. It uses plain HTML, CSS, and browser-native JavaScript modules.

## Run it

Start the API from the repository root:

```bash
uvicorn firewall.api:app --reload --port 8000
```

In a second terminal, serve this directory:

```bash
cd web
python -m http.server 8080
```

Open:

- Recruiter dashboard: <http://localhost:8080/dashboard/>
- Mock career page: <http://localhost:8080/career-page/>

The API must allow the origin `http://localhost:8080`. The dashboard polls `GET /v1/decisions?limit=200` every two seconds. If that endpoint or the API is unavailable, it displays `dashboard/sample-data.json` and a visible **Demo data** banner. It retries the API on every poll and switches back automatically when the API becomes reachable.

## File map

```text
web/
├── README.md
├── career-page/
│   └── index.html          # Mock job description and accessible application form
├── dashboard/
│   ├── app.js              # Polling, metrics, chart, drill-down, local recruiter state
│   ├── index.html          # Recruiter dashboard structure
│   ├── sample-data.json    # Offline/demo fallback records
│   └── style.css           # Responsive light/dark dashboard presentation
└── sdk/
    └── firewall-sdk.js     # Payload builder, signal collection, and API client
```

Verification status and recruiter overrides are demo-only client state stored in `localStorage` under `firewallDesk.verification.v1` and `firewallDesk.overrides.v1`. They are not sent to the API.

## SDK request

`FirewallSDK.submit()` posts to `http://localhost:8000/v1/applications/evaluate` with `Content-Type: application/json`. Values below show the exact shape sent; UUIDs, timing, timestamps, and interaction summaries are generated at runtime.

```json
{
  "application": {
    "application_id": "3ccf77b1-716c-4698-b8d8-fbf5687f7019",
    "job_id": "SWE-PLATFORM-04",
    "candidate": {
      "name": "Priya Raman",
      "email": "priya@example.com",
      "phone": "+91 98765 43210",
      "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
      "experience": [
        {
          "company": "Example Systems",
          "title": "Platform Engineer",
          "start": "2022-01",
          "end": "2025-08"
        }
      ],
      "projects": [
        {
          "name": "Cloud Control Plane",
          "description": "Built a reliable internal platform with observable deployment workflows."
        }
      ],
      "claimed_experience_years": 3.5
    },
    "signals": {
      "device_id": "c0eb4437-d4b3-4663-864d-56acd34cd599.1u7s0ki",
      "ip": "0.0.0.0",
      "session_seconds": 84.217,
      "paste_char_ratio": 0.13513513513513514,
      "submitted_at": 1791440400.417
    }
  },
  "job": {
    "must_have_skills": ["Python", "FastAPI", "PostgreSQL"],
    "nice_to_have": ["Docker", "AWS"],
    "min_years": 2
  },
  "extra_signals": {
    "keystroke_inter_key_ms": {
      "count": 126,
      "mean": 181.4,
      "median": 147.8,
      "p95": 402.1
    },
    "input_modality_counts": {
      "keyboard": 132,
      "mouse": 8,
      "touch": 0,
      "dictation": 0
    },
    "honeypot_filled": false,
    "canary_hit": false
  }
}
```

The backend contract currently consumes `application` and `job`; Pydantic ignores the sibling `extra_signals` object. The IP value is deliberately the placeholder `0.0.0.0`, ready for the server to replace using the request's client address.

The device ID combines a browser-local random UUID with a small non-cryptographic hash of user agent, screen dimensions, color depth, and timezone. It is a lightweight abuse-correlation identifier, not a claim of unique identity.

### Advisory signals

- Paste ratio is pasted characters divided by typed plus pasted characters observed after page load.
- Inter-key timings are summarized locally; individual key values and full timing sequences are not sent.
- Input-modality counts record keyboard text insertion, mouse/touch pointer activity, and dictation insertion events where the browser exposes them.
- The honeypot is off-screen, `aria-hidden`, excluded from the tab order, and has autocomplete disabled.
- The canary instruction is in an `aria-hidden` job-description element. Detection checks free-text form fields for its token.
- Honeypot and canary outcomes are flags only. The current SDK does not alter route, score, or candidate-facing text based on either flag. A screen-reader or keyboard-only user who simply navigates the form cannot trigger them.

## Accessibility notes

- Pages use native landmarks, headings, fieldsets, labels, buttons, a data table, and form controls before ARIA enhancements.
- Skip links provide direct access to each page's primary task.
- Dynamic connection, saved-state, submission, and result messages use polite live regions.
- The dashboard chart has a complete text alternative; route and state labels never depend on color alone.
- Focus is moved to the application detail after a recruiter opens it and to the result after submission.
- All interactive targets are at least 44 CSS pixels high, with a high-contrast visible focus ring.
- Layouts reflow to one column on small screens. Light and dark palettes follow `prefers-color-scheme`, and motion is reduced under `prefers-reduced-motion`.
- Recruiter actions are explicitly presented as local overrides. The UI states that automated routing supports triage and does not make hiring decisions.

Automated checks cannot establish full WCAG conformance. Complete the manual checks below before a production release.

## Manual keyboard test checklist

### Career page

- [ ] Press `Tab` once: the skip link appears and moves focus to the application form when activated.
- [ ] Continue with `Tab`/`Shift+Tab`: focus follows the visual order and every control has a visible focus ring.
- [ ] Confirm the hidden company-website honeypot never receives focus.
- [ ] Activate **Simulate bot**, **Add another role**, **Remove role**, **Add another project**, and **Remove project** using `Enter` and `Space`.
- [ ] Submit required fields empty: the browser identifies the first invalid labeled field.
- [ ] Complete and submit the form: focus moves to a route-specific, non-accusatory result without displaying raw reason codes.
- [ ] At 400% browser zoom and at 320 CSS pixels wide, confirm no content or action is lost and horizontal scrolling is limited to content that requires it.

### Dashboard

- [ ] Press `Tab` once and activate **Skip to dashboard**.
- [ ] Activate **Refresh now** and every application **View** button from the keyboard.
- [ ] Confirm **View** moves focus to the drill-down and the score is announced as a progress bar from 0 to 100.
- [ ] Change verification status with arrow keys; activate **Approve**, **Reject**, and **Request verification**; confirm the saved-state message updates.
- [ ] Focus the application table scroller and confirm horizontal scrolling works with keyboard controls at narrow widths.
- [ ] Switch the operating system between light and dark appearances and confirm text, badges, charts, and focus remain readable.

## VoiceOver test checklist (macOS)

- [ ] Enable VoiceOver with `Command-F5`; use `VO-U` to open the rotor and confirm Header, Main, Footer, and form/table landmarks are present.
- [ ] On the career page, navigate by headings and form controls. Confirm every input's label, required state, type, hint, and current value are announced.
- [ ] Confirm neither the canary instruction nor the honeypot appears in the VoiceOver rotor or reading order.
- [ ] Add and remove repeated experience/project rows; confirm the newly focused input and remove-button names make sense without visual context.
- [ ] Submit the form and confirm the result is announced once, without raw firewall reason codes.
- [ ] On the dashboard, navigate by headings and tables; verify column headings are announced with each cell.
- [ ] Confirm the reason chart announces its full text summary and does not require inspecting individual SVG shapes.
- [ ] Open an application and verify candidate identity, route, trust score, each reason and weight, verification selector, and override controls are understandable.
- [ ] Leave the dashboard open through a polling cycle and confirm updates do not repeatedly interrupt current reading.
