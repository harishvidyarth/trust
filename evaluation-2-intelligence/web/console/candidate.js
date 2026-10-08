(function () {
  var C = window.C;
  var h = C.h;

  function splitList(value) {
    return String(value || "").split(/[\n,]+/).map(function (item) { return item.trim(); }).filter(Boolean);
  }

  C.meter = function (config) {
    var value = Math.max(0, Math.min(100, Number(config.value) || 0));
    var fill = h("div", { class: "meter-fill " + (config.tone || "") });
    fill.style.width = value + "%";
    return h("div", { class: "meter" },
      h("div", { class: "meter-head" }, h("span", { id: config.id + "Label", text: config.label }), h("span", { class: "meter-value", text: value.toFixed(config.decimals === undefined ? 0 : config.decimals) })),
      h("div", { class: "meter-track", role: "meter", "aria-labelledby": config.id + "Label", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(value), "aria-valuetext": config.valuetext || value + " out of 100" }, fill),
      config.sub ? h("div", { class: "meter-sub", text: config.sub }) : null
    );
  };

  function toneFor(score) {
    return score >= 80 ? "ok" : score >= 50 ? "warn" : "danger";
  }

  var SVGNS = "http://www.w3.org/2000/svg";
  C.cand = C.cand || { replaces: null, replacesNote: "" };
  C.gauge = function (score, small) {
    var value = Math.max(0, Math.min(100, Number(score) || 0));
    var size = small ? 64 : 132;
    var stroke = small ? 7 : 12;
    var radius = (size - stroke) / 2;
    var circumference = 2 * Math.PI * radius;
    var svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size);
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Trust score " + value + " out of 100");
    var track = document.createElementNS(SVGNS, "circle");
    var fill = document.createElementNS(SVGNS, "circle");
    [track, fill].forEach(function (circle) {
      circle.setAttribute("cx", String(size / 2));
      circle.setAttribute("cy", String(size / 2));
      circle.setAttribute("r", String(radius));
      circle.setAttribute("fill", "none");
      circle.setAttribute("stroke-width", String(stroke));
    });
    track.setAttribute("class", "gauge-track");
    fill.setAttribute("class", "gauge-fill " + toneFor(value));
    fill.setAttribute("stroke-dasharray", String((circumference * value) / 100) + " " + String(circumference));
    fill.setAttribute("transform", "rotate(-90 " + size / 2 + " " + size / 2 + ")");
    svg.append(track, fill);
    var label = document.createElementNS(SVGNS, "text");
    label.setAttribute("x", String(size / 2));
    label.setAttribute("y", String(size / 2));
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("dominant-baseline", "central");
    label.setAttribute("class", "gauge-num");
    label.setAttribute("font-size", small ? "20" : "38");
    label.textContent = String(Math.round(value));
    svg.appendChild(label);
    return h("div", { class: "gauge" + (small ? " small" : "") }, svg);
  };

  var ROUTE_MEANING = {
    PASS_TO_ATS: "Your resume can move on to the hiring system.",
    ADDITIONAL_VERIFICATION: "A person will ask you for a little more proof before your resume moves on.",
    MANUAL_REVIEW: "A person will read your resume before it moves on. Nothing is rejected automatically."
  };
  var ROUTE_MEAN_LONG = {
    PASS_TO_ATS: "Your resume reads the same to a person and to a computer, so it can go on to the hiring team. You do not need to change anything, though the tips below can still help.",
    ADDITIONAL_VERIFICATION: "Something in your resume needs a closer look. A person will check it and may ask you to confirm a few details. This is a normal step and it is not a rejection.",
    MANUAL_REVIEW: "A person will read your resume with care before the hiring team sees it. Fixing the items under What you can fix and sending it again often gives a better result."
  };

  var BUILTIN_PRESETS = [
    { name: "Backend engineer", must_have: ["Python", "PostgreSQL", "Kubernetes"], nice_to_have: ["Terraform", "AWS"], min_years: 3 },
    { name: "Data analyst", must_have: ["SQL", "Python", "Excel"], nice_to_have: ["Tableau", "Statistics"], min_years: 2 },
    { name: "Frontend developer", must_have: ["JavaScript", "React", "CSS"], nice_to_have: ["TypeScript", "Testing"], min_years: 2 },
    { name: "DevOps engineer", must_have: ["Linux", "Docker", "CI/CD"], nice_to_have: ["Kubernetes", "Terraform"], min_years: 3 },
    { name: "Machine learning engineer", must_have: ["Python", "PyTorch", "SQL"], nice_to_have: ["MLOps", "Spark"], min_years: 3 }
  ];

  function toSeconds(value) {
    if (typeof value === "number") return value > 1e12 ? Math.floor(value / 1000) : value;
    var parsed = Date.parse(value);
    return isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
  }

  function plural(count, one, many) {
    return count + " " + (count === 1 ? one : many);
  }

  function fixText(fix) {
    return typeof fix === "string" ? fix : (fix.fix || fix.detail || fix.text || fix.title || "");
  }

  function progressCard(delta) {
    var change = Number(delta.score_change) || 0;
    var changeText = change > 0 ? "Up by " + change : change < 0 ? "Down by " + Math.abs(change) : "No change";
    var cleared = delta.cleared || [];
    var added = delta.new || [];
    function list(items, empty) {
      return items.length
        ? h("ul", null, items.map(function (item) { return h("li", null, h("strong", { text: item.title || "Concern" }), item.explanation ? h("span", { class: "muted", text: " " + item.explanation }) : null); }))
        : h("p", { class: "muted", text: empty });
    }
    return h("section", { class: "card progress-card", id: "progressCard", tabindex: "-1", "aria-labelledby": "progressT" },
      h("h3", { id: "progressT", text: "Your progress" }),
      h("div", { class: "progress-scores" },
        h("div", null, h("div", { class: "muted small", text: "Score before" }), h("div", { class: "score-mid", text: String(delta.score_before) })),
        h("div", { class: "progress-arrow", "aria-hidden": "true", text: "to" }),
        h("div", null, h("div", { class: "muted small", text: "Score now" }), h("div", { class: "score-mid", text: String(delta.score_after) })),
        h("div", null, C.tag(changeText, change > 0 ? "ok" : change < 0 ? "danger" : "info"))
      ),
      delta.message ? h("p", { text: delta.message }) : null,
      h("div", { class: "grid-2" },
        h("div", null, h("h4", { text: "Concerns cleared" }), list(cleared, "None cleared this time.")),
        h("div", null, h("h4", { text: "New concerns" }), list(added, "No new concerns."))
      ),
      delta.unchanged_count ? h("p", { class: "small muted", text: plural(delta.unchanged_count, "concern is", "concerns are") + " still the same." }) : null
    );
  }

  async function loadDelta(applicationId) {
    try {
      var delta = await C.api("GET", "/v1/me/applications/" + encodeURIComponent(applicationId) + "/delta");
      return delta && delta.score_before !== undefined ? delta : null;
    } catch (error) {
      return null;
    }
  }

  function decisionNodes(d, opts) {
    var fixes = d.candidate_fixes || [];
    var tone = C.ROUTE_TONE[d.route] || "";
    var practice = d.dry_run && !opts.readOnly;
    var buttons = h("div", { class: "row" });
    if (opts.onBackStep) buttons.append(h("button", { type: "button", class: "btn", text: "Back to X-ray", onclick: opts.onBackStep }));
    buttons.append(
      h("button", { type: "button", class: "btn primary", id: "fixBtn", text: "Fix and resubmit", onclick: opts.onFix }),
      h("button", { type: "button", class: "btn", id: "backAppsBtn", text: "Back to my applications", onclick: opts.onBack })
    );
    if (opts.onAnother) buttons.append(h("button", { type: "button", class: "btn quiet", text: "Check another resume", onclick: opts.onAnother }));
    return [
      h("div", { class: "card" },
        h("div", { class: "decision-top" },
          C.gauge(d.score, false),
          h("div", { class: "decision-side" },
            h("div", { class: "muted small", text: "Trust score out of 100" }),
            h("div", { style: "margin-top:6px" }, C.routeTag(d.route)),
            h("p", { style: "margin:8px 0 0", text: ROUTE_MEANING[d.route] || "" }),
            practice ? h("p", { class: "small muted", style: "margin:6px 0 0", text: "This was a practice run. Nothing was sent and it is not saved in My applications." }) : null
          )
        ),
        h("h3", { text: "What this means" }),
        h("p", { text: ROUTE_MEAN_LONG[d.route] || "" }),
        d.summary ? h("p", { class: "muted", text: d.summary }) : null
      ),
      h("div", { class: "card" }, h("h3", { text: "Reasons" }), d.reasons && d.reasons.length ? d.reasons.map(C.reasonCard) : h("p", { class: "muted", text: "No concerns were found." })),
      h("div", { class: "card" }, h("h3", { text: "What you can fix" }),
        fixes.length ? h("ol", null, fixes.map(function (fix) { return h("li", { text: fixText(fix) }); })) : h("p", { class: "muted", text: "Nothing to fix right now." })),
      opts.progress || null,
      h("div", { class: "card" }, buttons)
    ].filter(Boolean);
  }

  C.views.myapps = function (root) {
    var listBox = h("div", { id: "appList" });
    var detailBox = h("div", { id: "appDetail", hidden: true });
    var title = h("h1", { id: "myAppsTitle", tabindex: "-1", text: "My applications" });
    root.append(h("div", { class: "myapps" }, title, h("p", { class: "muted", text: "Every application you have sent is saved here. Open one to see the decision again or to fix it and send it again. Practice runs are not listed." }), listBox, detailBox));

    function dateText(row) {
      var seconds = toSeconds(row.submitted_at);
      return seconds ? C.fmtTime(seconds) : "Date not recorded";
    }

    function drawList(rows, focusId) {
      C.clear(listBox);
      if (!rows.length) {
        listBox.append(h("div", { class: "card" }, h("h2", { text: "No applications yet" }), h("p", { class: "muted", text: "When you send a resume that is not a practice run it will appear here." }), h("button", { type: "button", class: "btn primary", text: "Start a new application", onclick: function () { C.go("candidate"); } })));
        return;
      }
      var list = h("ul", { class: "app-list", "aria-label": "Your applications" });
      rows.forEach(function (row) {
        var concerns = Number(row.concern_count) || 0;
        var view = h("button", { type: "button", class: "detail-link", "data-view-id": row.application_id, "aria-label": "View application from " + dateText(row), text: "View" });
        view.addEventListener("click", function () { openApp(row, rows); });
        list.append(h("li", { class: "app-item" },
          C.gauge(row.score, true),
          h("div", { class: "app-main" },
            h("div", { class: "row", style: "gap:8px" }, C.routeTag(row.route), row.replaces ? C.tag("Fixed and resent", "info", true) : null),
            h("div", { class: "app-meta", text: dateText(row) + (row.job_id ? " for " + row.job_id : "") }),
            h("div", { class: "app-meta muted", text: concerns === 0 ? "No concerns" : plural(concerns, "concern", "concerns") })
          ),
          view
        ));
      });
      listBox.append(list);
      if (focusId) {
        var target = listBox.querySelector('[data-view-id="' + focusId + '"]');
        if (target) target.focus();
      }
    }

    async function openApp(row, rows) {
      C.status("Opening your application...");
      listBox.hidden = true;
      detailBox.hidden = false;
      C.clear(detailBox);
      var d;
      try {
        d = await C.api("GET", "/v1/me/applications/" + encodeURIComponent(row.application_id));
      } catch (error) {
        if (C.authError(error)) return;
        listBox.hidden = false;
        detailBox.hidden = true;
        C.status(C.friendly(error), true);
        return;
      }
      d = Object.assign({ score: row.score, route: row.route, summary: row.summary, reasons: [], candidate_fixes: [] }, d);
      d.dry_run = false;
      var progress = null;
      if (row.replaces) {
        var delta = await loadDelta(row.application_id);
        if (delta) progress = progressCard(delta);
      }
      var heading = h("h2", { id: "detailTitle", tabindex: "-1", text: "Application from " + dateText(row) });
      C.clear(detailBox);
      detailBox.append(
        h("button", { type: "button", class: "btn small", id: "backListTop", text: "Back to my applications", onclick: back }),
        heading,
        ...decisionNodes(d, {
          readOnly: true,
          progress: progress,
          onFix: function () {
            C.cand.replaces = row.application_id;
            C.go("candidate");
          },
          onBack: back
        })
      );
      function back() {
        detailBox.hidden = true;
        C.clear(detailBox);
        listBox.hidden = false;
        drawList(rows, row.application_id);
        C.status("Back to your applications.");
      }
      heading.focus();
      window.scrollTo(0, 0);
      C.status("Application opened.");
    }

    (async function () {
      C.status("Loading your applications...");
      try {
        var rows = await C.api("GET", "/v1/me/applications");
        rows = Array.isArray(rows) ? rows : (rows && rows.applications) || [];
        rows = rows.slice().sort(function (a, b) { return toSeconds(b.submitted_at) - toSeconds(a.submitted_at); });
        drawList(rows);
        C.status(rows.length ? plural(rows.length, "application", "applications") + " loaded." : "You have no applications yet.");
        title.focus({ preventScroll: true });
      } catch (error) {
        if (C.authError(error)) return;
        C.clear(listBox);
        if (error.status === 404) {
          listBox.append(h("div", { class: "card" }, h("p", { class: "muted", text: "The list of your applications is not available from this server yet." }), h("button", { type: "button", class: "btn primary", text: "Start a new application", onclick: function () { C.go("candidate"); } })));
          C.status("");
        } else {
          listBox.append(h("p", { class: "alert error", role: "alert", text: C.friendly(error) }));
          C.status(C.friendly(error), true);
        }
      }
    })();
  };

  function highlight(container, text, spans) {
    C.clear(container);
    var ranges = [];
    (spans || []).forEach(function (span) {
      if (!span.text) return;
      var from = 0;
      for (;;) {
        var index = text.indexOf(span.text, from);
        if (index < 0) break;
        ranges.push([index, index + span.text.length]);
        from = index + span.text.length;
      }
    });
    ranges.sort(function (a, b) { return a[0] - b[0]; });
    var merged = [];
    ranges.forEach(function (range) {
      var last = merged[merged.length - 1];
      if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
      else merged.push([range[0], range[1]]);
    });
    var position = 0;
    merged.forEach(function (range) {
      if (range[0] > position) container.append(document.createTextNode(text.slice(position, range[0])));
      container.append(h("mark", { class: "hidden-text", text: text.slice(range[0], range[1]) }));
      position = range[1];
    });
    if (position < text.length) container.append(document.createTextNode(text.slice(position)));
    return merged.length;
  }

  function intentList(entries) {
    if (!entries || !entries.length) return null;
    return h("div", { class: "stack" },
      h("h3", { text: "Hidden text and what it says" }),
      entries.map(function (entry) {
        return h("div", { class: "reason sev-high" },
          h("div", { class: "reason-head" }, C.tag(entry.label || "Hidden content", "danger")),
          h("p", { class: "small muted", style: "margin-bottom:4px", text: "Exact text found" }),
          h("blockquote", { class: "evidence", text: entry.evidence || "" })
        );
      })
    );
  }

  function writingCard(style) {
    var card = h("section", { class: "card muted-card", "aria-labelledby": "styleTitle" });
    card.append(h("h3", { id: "styleTitle" }, "Writing style ", C.tag("Not part of the decision", "info", true)));
    if (!style || style.score === null || style.score === undefined) {
      card.append(h("p", { class: "muted", text: "Not enough text to estimate. This never affects the decision." }));
      return card;
    }
    card.append(
      h("dl", { class: "kv" },
        h("dt", { text: "Estimate" }), h("dd", { text: style.label + " (" + style.score + " out of 100)" }),
        h("dt", { text: "Method" }), h("dd", { text: style.mode || "not stated" }),
        h("dt", { text: "Confidence" }), h("dd", { text: (style.confidence || "unknown") + ", " + (style.word_count || 0) + " words" }),
        h("dt", { text: "Effect on decision" }), h("dd", { text: "None. Using AI to polish a resume is fine." })
      )
    );
    var patterns = style.patterns_found || [];
    if (patterns.length) {
      card.append(h("h4", { style: "margin-top:12px", text: "Patterns noticed" }), h("ul", null, patterns.map(function (item) { return h("li", { text: item.name + ": " + item.detail }); })));
    }
    var human = style.human_like_signals || [];
    if (human.length) {
      card.append(h("h4", { text: "Signals that read as human" }), h("ul", null, human.map(function (item) { return h("li", { text: item }); })));
    }
    var quotes = style.verified_quotes || style.quotes || [];
    if (quotes.length) {
      card.append(h("h4", { text: "Quoted from your resume" }));
      quotes.forEach(function (quote) {
        var text = typeof quote === "string" ? quote : quote.text;
        var verified = typeof quote === "string" ? true : quote.verified !== false;
        card.append(h("blockquote", { class: "evidence" }, text, " ", C.tag(verified ? "Found in your resume" : "Not found verbatim", verified ? "ok" : "warn", true)));
      });
    }
    card.append(h("p", { class: "small muted", style: "margin-top:12px", text: style.disclaimer || "This is an estimate, not proof of authorship. It is shown for context only." }));
    return card;
  }

  C.views.candidate = function (root) {
    var S = { file: null, inspect: null, decision: null, step: 1, applicationId: "app-" + Date.now(), consentId: null };

    var stepper = h("ol", { class: "stepper", "aria-label": "Progress" });
    var panels = { 1: h("section", { id: "p1", tabindex: "-1" }), 2: h("section", { id: "p2", tabindex: "-1" }), 3: h("section", { id: "p3", tabindex: "-1" }) };
    root.append(
      h("h1", { text: "Check your resume before you apply" }),
      h("p", { class: "muted", text: "See what a keyword parser reads compared with what a person reads, then see the decision and how to improve it." }),
      stepper, panels[1], panels[2], panels[3]
    );

    function drawStepper() {
      C.clear(stepper);
      [["Upload resume", 1, true], ["X-ray", 2, Boolean(S.inspect)], ["Decision", 3, Boolean(S.decision)]].forEach(function (item) {
        var button = h("button", { type: "button", class: S.step > item[1] ? "done" : "", "aria-current": S.step === item[1] ? "step" : false, disabled: !item[2] },
          h("span", { class: "num", "aria-hidden": "true", text: S.step > item[1] ? "✓" : String(item[1]) }),
          item[0]
        );
        button.addEventListener("click", function () { show(item[1]); });
        stepper.appendChild(h("li", null, button));
      });
    }

    function show(step, noFocus) {
      S.step = step;
      [1, 2, 3].forEach(function (n) { panels[n].hidden = n !== step; });
      drawStepper();
      if (!noFocus) {
        panels[step].focus({ preventScroll: true });
        window.scrollTo(0, 0);
      }
    }

    var MAX_BYTES = 5 * 1024 * 1024;
    var JOB_KEY = "trust-job-fields";
    var fileInput = h("input", { type: "file", id: "resumeFile", accept: ".pdf,.docx,.txt", "aria-describedby": "fileHelp fileError" });
    var fileName = h("div", { class: "file-name", id: "fileName" });
    var fileError = h("p", { class: "field-error", id: "fileError", role: "alert", hidden: true });
    var chipBox = h("div", { id: "samples" });
    var saved = null;
    try { saved = JSON.parse(C.safeStore.get(JOB_KEY) || "null"); } catch (error) { saved = null; }
    saved = saved || {};
    var must = h("input", { type: "text", id: "must", value: saved.must !== undefined ? saved.must : "Python, PostgreSQL, Kubernetes" });
    var nice = h("input", { type: "text", id: "nice", value: saved.nice !== undefined ? saved.nice : "Terraform, AWS" });
    var years = h("input", { type: "number", id: "years", value: saved.years !== undefined ? saved.years : "3", min: "0", max: "40" });
    var preset = h("select", { id: "preset" }, h("option", { value: "", text: "Custom, I will type my own" }));
    var presets = BUILTIN_PRESETS;
    var readBtn = h("button", { type: "button", class: "btn primary", id: "readBtn", text: "Read my resume", disabled: true });
    var readNote = h("p", { class: "small muted", id: "readNote", role: "status", "aria-live": "polite", style: "margin:8px 0 0" });
    var drop = h("div", { class: "dropzone", id: "drop" },
      h("label", { for: "resumeFile", class: "field", style: "margin:0" }, "Choose a resume", fileInput),
      h("p", { class: "small muted", id: "fileHelp", style: "margin:8px 0 0", text: "or drop a file here. PDF, DOCX or TXT, up to 5 MB." }),
      fileName,
      fileError
    );

    function saveJob() {
      C.safeStore.set(JOB_KEY, JSON.stringify({ must: must.value, nice: nice.value, years: years.value, preset: preset.value }));
    }
    [must, nice, years].forEach(function (input) {
      input.addEventListener("input", function () { preset.value = ""; saveJob(); });
    });

    function drawPresets() {
      presets.forEach(function (item, index) {
        preset.append(h("option", { value: String(index), text: item.name }));
      });
      if (saved.preset !== undefined && saved.preset !== "" && presets[Number(saved.preset)]) preset.value = String(saved.preset);
    }
    preset.addEventListener("change", function () {
      var item = presets[Number(preset.value)];
      if (!item) { saveJob(); return; }
      must.value = (item.must_have || []).join(", ");
      nice.value = (item.nice_to_have || []).join(", ");
      years.value = String(item.min_years || 0);
      saveJob();
      C.status("Skills and years filled in for " + item.name + ". You can still change them.");
    });
    drawPresets();
    (async function () {
      try {
        var remote = await C.api("GET", "/v1/job-presets");
        remote = Array.isArray(remote) ? remote : (remote && remote.presets) || [];
        if (remote.length) {
          var current = preset.value ? presets[Number(preset.value)] : null;
          presets = remote;
          while (preset.options.length > 1) preset.remove(1);
          drawPresets();
          if (current) {
            var again = presets.findIndex(function (item) { return item.name === current.name; });
            preset.value = again >= 0 ? String(again) : "";
          }
        }
      } catch (error) {
        return;
      }
    })();

    var tips = [
      "Keep all of your text visible. Never hide words in white or tiny text.",
      "Describe your own work with a project and a result.",
      "List the skills you really have and no others.",
      "Check that your dates are in order and that jobs do not overlap by mistake.",
      "Write for a person. Do not add instructions meant for a screening tool.",
      "Use a PDF, DOCX or TXT file up to 5 MB."
    ];

    var resubmitNote = h("div", { id: "resubmitNote" });

    panels[1].append(
      resubmitNote,
      h("section", { class: "card tips-card", "aria-labelledby": "tipsT", style: "margin-bottom:16px" },
        h("h2", { id: "tipsT", text: "Before you submit" }),
        h("ul", { class: "tips" }, tips.map(function (tip) { return h("li", { text: tip }); }))
      ),
      h("div", { class: "grid-2" },
        h("div", { class: "card" }, h("h2", { text: "1. Upload your resume" }), drop,
          h("details", { class: "group", style: "margin-top:16px" }, h("summary", { text: "Try a sample resume" }), chipBox)),
        h("div", { class: "card" }, h("h2", { text: "The job you are applying to" }),
          h("label", { class: "field", for: "preset" }, "Job role", preset, h("span", { class: "hint", text: "Pick a role to fill in the skills and years below." })),
          h("label", { class: "field", for: "must" }, "Skills the job must have", must, h("span", { class: "hint", text: "Separate with commas." })),
          h("label", { class: "field", for: "nice" }, "Skills that would be a bonus", nice),
          h("label", { class: "field", for: "years" }, "Minimum years of experience", years),
          readBtn, readNote)
      )
    );

    function drawResubmit() {
      C.clear(resubmitNote);
      if (!C.cand.replaces) return;
      resubmitNote.append(h("div", { class: "alert info" },
        h("strong", { text: "You are fixing an earlier application. " }),
        "Choose your updated resume and send it again. We will show how your result changed. ",
        h("button", { type: "button", class: "btn small", text: "Start a fresh application instead", onclick: function () { C.cand.replaces = null; drawResubmit(); C.status("This will be saved as a new application."); } })
      ));
    }
    drawResubmit();

    function formatSize(bytes) {
      if (bytes < 1024) return bytes + " bytes";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
      return (bytes / (1024 * 1024)).toFixed(2) + " MB";
    }

    function checkFile(file, isSample) {
      var name = String(file.name || "");
      if (!/\.(pdf|docx|txt)$/i.test(name)) return "That file type is not accepted. Please choose a PDF, DOCX or TXT file.";
      if (file.size > MAX_BYTES) return "That file is " + formatSize(file.size) + ", which is more than the 5 MB limit. Please save a smaller copy.";
      if (!isSample && file.size === 0) return "That file is empty. Please choose a file that has your resume in it.";
      return null;
    }

    function setFile(file, isSample) {
      var problem = file ? checkFile(file, isSample) : null;
      fileError.hidden = !problem;
      fileError.textContent = problem || "";
      fileInput.setAttribute("aria-invalid", problem ? "true" : "false");
      if (problem) {
        S.file = null;
        fileName.textContent = "";
        readBtn.disabled = true;
        fileInput.value = "";
        chipBox.querySelectorAll("button").forEach(function (chip) { chip.setAttribute("aria-pressed", "false"); });
        C.status(problem, true);
        drawStepper();
        return;
      }
      S.file = file;
      S.inspect = null;
      S.decision = null;
      fileName.textContent = file ? file.name + (isSample ? "" : ", " + formatSize(file.size)) : "";
      readBtn.disabled = !file;
      if (file) C.status("Selected " + file.name + (isSample ? "." : ", " + formatSize(file.size) + "."));
      chipBox.querySelectorAll("button").forEach(function (chip) { chip.setAttribute("aria-pressed", file && chip.dataset.file === file.name ? "true" : "false"); });
      drawStepper();
    }

    fileInput.addEventListener("change", function () { if (fileInput.files[0]) setFile(fileInput.files[0]); });
    drop.addEventListener("dragover", function (event) { event.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("over"); });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("over");
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length > 1) { setFile(files[0]); return; }
      if (files && files[0]) setFile(files[0]);
    });

    async function loadSample(name) {
      if (C.state.demo) {
        setFile(new File(["sample"], name), true);
        return;
      }
      C.status("Loading sample...");
      try {
        var response = await fetch("../resume-xray/samples/" + encodeURIComponent(name));
        if (!response.ok) throw new Error("missing");
        var blob = await response.blob();
        setFile(new File([blob], name), true);
        C.status("Sample loaded.");
      } catch (error) {
        C.status("That sample could not be loaded.", true);
      }
    }

    async function drawSamples() {
      var groups = C.demo.samples;
      try {
        var response = await fetch("../resume-xray/samples/index.json");
        if (response.ok) groups = await response.json();
      } catch (error) {
        groups = C.demo.samples;
      }
      C.clear(chipBox);
      groups.forEach(function (group) {
        chipBox.append(
          h("p", { class: "small muted", style: "margin:12px 0 6px", text: group.group }),
          h("div", { class: "chips" }, group.samples.map(function (sample) {
            var chip = h("button", { type: "button", class: "chip", "data-file": sample.file, "aria-pressed": "false", title: sample.note, text: sample.file.replace(/\.[a-z]+$/, "").replace(/_/g, " ") });
            chip.addEventListener("click", function () { loadSample(sample.file); });
            return chip;
          }))
        );
      });
    }

    function skillList(value) {
      return value.split(",").map(function (item) { return item.trim(); }).filter(Boolean);
    }
    function jobJson() {
      return JSON.stringify({ must_have_skills: skillList(must.value), nice_to_have: skillList(nice.value), min_years: Number(years.value) || 0 });
    }

    var testMode = h("input", { type: "checkbox", id: "dryRun", checked: !C.cand.replaces });

    function drawInspect() {
      var r = S.inspect;
      var panel = panels[2];
      C.clear(panel);
      var humanBox = h("pre", { class: "textview", tabindex: "0", "aria-label": "Text a person reads" });
      var atsBox = h("pre", { class: "textview", tabindex: "0", "aria-label": "Text a parser reads, hidden text marked" });
      highlight(humanBox, r.human_view || "", []);
      var hiddenCount = highlight(atsBox, r.ats_view || "", r.hidden_spans || []);
      var gap = r.naive_ats_score - r.human_view_ats_score;
      var note = hiddenCount > 0 || gap > 0
        ? h("div", { class: "alert warn", role: "status" }, h("strong", { text: "The two views differ. " }), "A keyword parser would score this resume " + r.naive_ats_score.toFixed(1) + ", while a person would score it " + r.human_view_ats_score.toFixed(1) + ". Some content is hidden from people.")
        : h("div", { class: "alert ok", role: "status" }, h("strong", { text: "The two views match. " }), "Nothing is hidden from a person.");
      var agreement = r.agreement || { score: 0, label: "Unavailable" };
      panel.append(
        h("h2", { text: "2. X-ray, parser versus person" }),
        note,
        h("div", { class: "grid-3" },
          h("div", { class: "card" }, C.meter({ id: "mAts", label: "What an ATS parser reads", value: r.naive_ats_score, decimals: 1, sub: "Keyword match score on the full text layer." })),
          h("div", { class: "card" }, C.meter({ id: "mHuman", label: "What a person reads", value: r.human_view_ats_score, decimals: 1, sub: "Same score using only visible text." })),
          h("div", { class: "card" }, C.meter({ id: "mAgree", label: "Agreement", value: agreement.score, tone: toneFor(agreement.score), valuetext: agreement.score + " out of 100, " + agreement.label, sub: agreement.label }))
        ),
        h("div", { class: "grid-2", style: "margin-top:16px" },
          h("div", { class: "card" }, h("h3", { id: "hvT", text: "What a person sees" }), humanBox),
          h("div", { class: "card" }, h("h3", { id: "avT" }, "What a parser reads ", C.tag(hiddenCount > 0 ? "Hidden text found" : "Full text layer", hiddenCount > 0 ? "warn" : "ok")), atsBox)
        ),
        h("div", { style: "margin-top:16px" }, intentList(r.hidden_intent)),
        h("div", { style: "margin-top:16px" }, writingCard(r.ai_writing)),
        h("div", { class: "card", style: "margin-top:16px" },
          h("label", { class: "check", for: "dryRun" }, testMode, h("span", null, "Practice mode lets you try this resume without sending it or saving it to My applications.")),
          h("div", { class: "row" },
            h("button", { type: "button", class: "btn primary", id: "sendBtn", text: "See the decision", onclick: submit }),
            h("button", { type: "button", class: "btn", text: "Choose another resume", onclick: function () { show(1); } })
          )
        )
      );
    }

    async function runInspect() {
      if (!S.file) { C.status("Choose a file or a sample first.", true); return; }
      readBtn.disabled = true;
      readBtn.setAttribute("aria-busy", "true");
      readBtn.textContent = "Reading your resume...";
      readNote.textContent = "Uploading your file.";
      var stage = setTimeout(function () { readNote.textContent = "Comparing what a parser reads with what a person reads."; }, 900);
      C.status("Reading the resume both ways...");
      var form = new FormData();
      form.append("file", S.file);
      form.append("job_json", jobJson());
      try {
        S.inspect = await C.api("POST", "/v1/resume/inspect", { form: form });
        S.decision = null;
        drawInspect();
        show(2);
        C.status("Compare the two views, then see the decision.");
      } catch (error) {
        if (C.authError(error)) return;
        C.status(C.friendly(error), true);
      } finally {
        clearTimeout(stage);
        readBtn.disabled = !S.file;
        readBtn.removeAttribute("aria-busy");
        readBtn.textContent = "Read my resume";
        readNote.textContent = "";
      }
    }
    readBtn.addEventListener("click", runInspect);

    function drawDecision(progress) {
      var d = S.decision;
      var panel = panels[3];
      C.clear(panel);
      panel.append(
        h("h2", { text: "3. Decision" }),
        ...decisionNodes(d, {
          progress: progress,
          onBackStep: function () { show(2); },
          onFix: function () {
            C.cand.replaces = d.dry_run ? C.cand.replaces : d.application_id;
            S.fromFix = true;
            setFile(null);
            fileInput.value = "";
            testMode.checked = false;
            drawResubmit();
            show(1);
            C.status("Change your resume, then choose it again and send it. The job details are kept.");
          },
          onBack: function () { C.go("myapps"); },
          onAnother: function () { setFile(null); fileInput.value = ""; show(1); }
        }),
        intakeCard()
      );
    }

    async function submit() {
      var button = C.$("sendBtn");
      button.disabled = true;
      S.applicationId = "app-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
      C.status("Sending through the firewall...");
      var form = new FormData();
      form.append("file", S.file);
      form.append("job_json", jobJson());
      form.append("device_id", "console-" + Math.random().toString(36).slice(2, 10));
      form.append("application_id", S.applicationId);
      form.append("session_seconds", "120");
      form.append("dry_run", testMode.checked ? "true" : "false");
      var replaced = !testMode.checked && C.cand.replaces ? C.cand.replaces : null;
      if (replaced) form.append("replaces", replaced);
      try {
        S.decision = await C.api("POST", "/v1/applications/upload", { form: form });
        S.consentId = null;
        var progress = null;
        if (replaced) {
          var delta = await loadDelta(S.decision.application_id || S.applicationId);
          if (delta) progress = progressCard(delta);
          C.cand.replaces = null;
        }
        drawDecision(progress);
        show(3);
        C.status((S.decision.route === "PASS_TO_ATS" ? "Your resume can move on." : "Your resume needs a person to look at it.") + (progress ? " Your progress is shown below the result." : ""));
        if (!testMode.checked && C.refreshSummary) C.refreshSummary();
      } catch (error) {
        if (C.authError(error)) return;
        C.status(C.friendly(error), true);
        button.disabled = false;
      }
    }

    function intakeCard() {
      var card = h("section", { class: "card", "aria-labelledby": "intakeT" });
      var results = h("div", { id: "intakeResults", "aria-live": "polite" });
      var github = h("input", { type: "url", id: "inGithub", placeholder: "https://github.com/your-name", autocomplete: "off" });
      var linkedin = h("input", { type: "file", id: "inLinkedin", accept: ".pdf" });
      var portfolio = h("input", { type: "url", id: "inPortfolio", placeholder: "https://your-site.example", autocomplete: "off" });
      var dois = h("textarea", { id: "inDois", placeholder: "10.1000/example.123\nhttps://doi.org/10.1000/example.456" });
      var certs = h("textarea", { id: "inCerts", placeholder: "One certificate ID per line" });
      var consent = h("input", { type: "checkbox", id: "inConsent" });
      var errorBox = h("p", { class: "alert error", role: "alert", hidden: true });
      var send = h("button", { type: "button", class: "btn primary", text: "Check what I added" });

      function drawFindings(data) {
        C.clear(results);
        S.consentId = data.consent_id;
        results.append(h("h3", { style: "margin-top:16px", text: "What we found" }));
        (data.findings || []).forEach(function (finding, index) {
          var tone = finding.status === "verified" ? "ok" : finding.status === "needs_review" ? "warn" : "info";
          var dispute = h("button", { type: "button", class: "btn small", text: "Dispute this finding", "aria-label": "Dispute finding " + (index + 1) + " from " + finding.source });
          dispute.addEventListener("click", function () { disputeFinding(index, finding); });
          results.append(h("div", { class: "finding" }, h("div", null, h("div", { class: "row" }, h("strong", { text: finding.source }), C.tag(finding.status.replace(/_/g, " "), tone)), h("p", { style: "margin:4px 0 0", text: finding.fact })), dispute));
        });
        results.append(h("p", { class: "small muted", text: "Reference: " + data.consent_id + ". You can ask for any finding to be reviewed." }));
      }

      async function disputeFinding(index, finding) {
        var note = h("textarea", { id: "disputeNote", "aria-label": "Why is this finding wrong?", placeholder: "What is wrong or missing?" });
        var answer = await C.dialog({
          title: "Dispute a finding",
          body: h("div", null, h("p", { class: "muted", text: finding.source + ": " + finding.fact }), h("label", { class: "field", for: "disputeNote" }, "What is wrong?", note)),
          actions: [
            { label: "Cancel", value: null },
            { label: "Send dispute", kind: "primary", value: "send", validate: function () { return note.value.trim().length < 5 ? "Add a short note, at least 5 characters." : null; } }
          ]
        });
        if (answer !== "send") return;
        try {
          await C.api("POST", "/v1/intake/dispute", { json: { consent_id: S.consentId, finding_index: index, note: note.value.trim() } });
          C.status("Your dispute was sent. A person will review this finding.");
        } catch (error) {
          if (C.authError(error)) return;
          C.status(C.friendly(error), true);
        }
      }

      send.addEventListener("click", async function () {
        errorBox.hidden = true;
        if (!consent.checked) {
          errorBox.textContent = "Please tick the consent box so we know you agree to these checks.";
          errorBox.hidden = false;
          consent.focus();
          return;
        }
        var form = new FormData();
        form.append("application_id", S.applicationId);
        form.append("github_url", github.value.trim());
        form.append("portfolio_url", portfolio.value.trim());
        splitList(dois.value).forEach(function (item) { form.append("doi_links", item); });
        splitList(certs.value).forEach(function (item) { form.append("certificate_ids", item); });
        form.append("consent", "true");
        if (linkedin.files[0]) form.append("linkedin_pdf", linkedin.files[0]);
        send.disabled = true;
        C.status("Checking what you added...");
        try {
          drawFindings(await C.api("POST", "/v1/intake/profile", { form: form }));
          C.status("Done. Review the findings below.");
        } catch (error) {
          if (C.authError(error)) return;
          errorBox.textContent = C.friendly(error);
          errorBox.hidden = false;
        } finally {
          send.disabled = false;
        }
      });

      card.append(
        h("h3", { id: "intakeT", text: "Add more about you (optional)" }),
        h("p", { class: "muted", text: "Sharing these can help a reviewer see your work. Leaving them out never counts against you." }),
        h("div", { class: "alert info" }, h("strong", { text: "Your consent. " }), "If you tick the box, we look only at the links and files you provide here, compare them with your resume, and show you every finding. A person can see the findings when reviewing your application. You can dispute any of them."),
        h("div", { class: "grid-2" },
          h("div", null,
            h("label", { class: "field", for: "inGithub" }, "GitHub profile URL", github),
            h("label", { class: "field", for: "inPortfolio" }, "Portfolio URL", portfolio),
            h("label", { class: "field", for: "inLinkedin" }, "LinkedIn profile saved as PDF", linkedin, h("span", { class: "hint", text: "On LinkedIn choose More, then Save to PDF." }))
          ),
          h("div", null,
            h("label", { class: "field", for: "inDois" }, "Papers (DOI or link, one per line)", dois),
            h("label", { class: "field", for: "inCerts" }, "Certificate IDs", certs)
          )
        ),
        h("label", { class: "check", for: "inConsent" }, consent, h("span", { text: "I agree to these checks on the links and files above." })),
        errorBox,
        send,
        results
      );
      return card;
    }

    drawStepper();
    show(1, true);
    drawSamples();

    var auto = C.params && C.params.sample;
    if (auto) {
      C.params.sample = null;
      setFile(new File(["sample"], auto), true);
      runInspect().then(function () {
        if (C.params.step === "3") {
          C.params.step = null;
          return submit();
        }
      });
    }
  };
})();
