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
          h("p", { class: "small muted", style: "margin-bottom:4px", text: "Exact text found:" }),
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
      card.append(h("h4", { text: "Human-like signals" }), h("ul", null, human.map(function (item) { return h("li", { text: item }); })));
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

    var fileInput = h("input", { type: "file", id: "resumeFile", accept: ".pdf,.docx,.txt" });
    var fileName = h("div", { class: "file-name", id: "fileName", "aria-live": "polite" });
    var chipBox = h("div", { id: "samples" });
    var must = h("input", { type: "text", id: "must", value: "Python, PostgreSQL, Kubernetes" });
    var nice = h("input", { type: "text", id: "nice", value: "Terraform, AWS" });
    var years = h("input", { type: "number", id: "years", value: "3", min: "0", max: "40" });
    var readBtn = h("button", { type: "button", class: "btn primary", id: "readBtn", text: "Read my resume", disabled: true });
    var drop = h("div", { class: "dropzone", id: "drop" },
      h("label", { for: "resumeFile", class: "field", style: "margin:0" }, "Choose a resume (PDF, DOCX or TXT)", fileInput),
      h("p", { class: "small muted", style: "margin:8px 0 0", text: "or drop a file here" }),
      fileName
    );

    panels[1].append(
      h("div", { class: "grid-2" },
        h("div", { class: "card" }, h("h2", { text: "1. Upload your resume" }), drop,
          h("details", { class: "group", style: "margin-top:16px", open: true }, h("summary", { text: "Try a sample resume" }), chipBox)),
        h("div", { class: "card" }, h("h2", { text: "The job you are applying to" }),
          h("label", { class: "field", for: "must" }, "Must-have skills", must, h("span", { class: "hint", text: "Separate with commas." })),
          h("label", { class: "field", for: "nice" }, "Nice-to-have skills", nice),
          h("label", { class: "field", for: "years" }, "Minimum years of experience", years),
          readBtn)
      )
    );

    function setFile(file) {
      S.file = file;
      S.inspect = null;
      S.decision = null;
      fileName.textContent = file ? file.name : "";
      readBtn.disabled = !file;
      chipBox.querySelectorAll("button").forEach(function (chip) { chip.setAttribute("aria-pressed", file && chip.dataset.file === file.name ? "true" : "false"); });
      drawStepper();
    }

    fileInput.addEventListener("change", function () { if (fileInput.files[0]) setFile(fileInput.files[0]); });
    drop.addEventListener("dragover", function (event) { event.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("over"); });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("over");
      if (event.dataTransfer.files[0]) setFile(event.dataTransfer.files[0]);
    });

    async function loadSample(name) {
      if (C.state.demo) {
        setFile(new File([""], name));
        return;
      }
      C.status("Loading sample...");
      try {
        var response = await fetch("../resume-xray/samples/" + encodeURIComponent(name));
        if (!response.ok) throw new Error("missing");
        var blob = await response.blob();
        setFile(new File([blob], name));
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

    var testMode = h("input", { type: "checkbox", id: "dryRun", checked: true });

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
        h("h2", { text: "2. X-ray: parser versus person" }),
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
          h("label", { class: "check", for: "dryRun" }, testMode, h("span", null, "Test mode. Nothing is delivered to a hiring system.")),
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
      readBtn.textContent = "Reading...";
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
        readBtn.disabled = false;
        readBtn.textContent = "Read my resume";
      }
    }
    readBtn.addEventListener("click", runInspect);

    function reasonCard(reason) {
      return C.reasonCard(reason);
    }

    function drawDecision() {
      var d = S.decision;
      var panel = panels[3];
      C.clear(panel);
      var fixes = d.candidate_fixes || [];
      var tone = C.ROUTE_TONE[d.route] || "";
      panel.append(
        h("h2", { text: "3. Decision" }),
        h("div", { class: "card" },
          h("div", { class: "decision-top" },
            h("div", null, h("div", { class: "score-big" }, String(d.score), h("small", { text: " / 100" })), h("div", { class: "muted small", text: "Trust score" })),
            h("div", null, C.routeTag(d.route), h("p", { style: "margin:8px 0 0", text: d.route === "PASS_TO_ATS" ? "Delivered to the ATS." : "Held back from the ATS for a person to review. Nothing is rejected automatically." }), d.dry_run ? h("p", { class: "small muted", style: "margin:4px 0 0", text: "Test mode: nothing was delivered." }) : null)
          ),
          h("div", { class: "meter" }, h("div", { class: "meter-track", role: "meter", "aria-label": "Trust score", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(d.score) }, (function () { var f = h("div", { class: "meter-fill " + tone }); f.style.width = Math.max(0, Math.min(100, d.score)) + "%"; return f; })())),
          h("p", { text: d.summary })
        ),
        h("div", { class: "card" }, h("h3", { text: "Reasons" }), d.reasons && d.reasons.length ? d.reasons.map(reasonCard) : h("p", { class: "muted", text: "No concerns were found." })),
        h("div", { class: "card" }, h("h3", { text: "What you can fix" }),
          fixes.length ? h("ol", null, fixes.map(function (fix) { return h("li", { text: typeof fix === "string" ? fix : (fix.fix || fix.detail || fix.text || JSON.stringify(fix)) }); })) : h("p", { class: "muted", text: "Nothing to fix right now." })),
        h("div", { class: "card" },
          h("div", { class: "row" },
            h("button", { type: "button", class: "btn", text: "Back to X-ray", onclick: function () { show(2); } }),
            h("button", { type: "button", class: "btn", text: "Check another resume", onclick: function () { setFile(null); fileInput.value = ""; show(1); } })
          )
        ),
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
      try {
        S.decision = await C.api("POST", "/v1/applications/upload", { form: form });
        S.consentId = null;
        drawDecision();
        show(3);
        C.status(S.decision.route === "PASS_TO_ATS" ? "Passed to the ATS." : "Held back for review.");
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
      setFile(new File([""], auto));
      runInspect().then(function () {
        if (C.params.step === "3") {
          C.params.step = null;
          return submit();
        }
      });
    }
  };
})();
