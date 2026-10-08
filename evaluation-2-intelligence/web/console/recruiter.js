(function () {
  var C = window.C;
  var h = C.h;

  var ROUTE_LIST = ["PASS_TO_ATS", "ADDITIONAL_VERIFICATION", "MANUAL_REVIEW"];
  var INTENT_LABELS = {
    keyword_stuffing: "Keywords hidden from readers",
    screener_instruction: "Instruction aimed at a screening tool",
    harmless: "Hidden but harmless"
  };
  var FINDING_TONE = { verified: "ok", needs_review: "warn", disputed: "danger", unverified: "info" };

  function seconds(value) {
    var n = Number(value);
    if (!n) return 0;
    return n > 100000000000 ? n / 1000 : n;
  }
  function when(value) {
    var s = seconds(value);
    if (!s) return "Unknown time";
    return new Date(s * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function overrideRoute(row) {
    var o = row.override;
    if (!o) return null;
    if (typeof o === "string") return o;
    return o.override_route || o.route || null;
  }
  function routeOf(row) {
    return overrideRoute(row) || (row.decision && row.decision.route);
  }
  function scoreOf(row) {
    return row.decision && Number.isFinite(row.decision.score) ? row.decision.score : 0;
  }
  function toneFor(score) {
    return score >= 70 ? "ok" : score >= 41 ? "warn" : "danger";
  }
  function listOf(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.items)) return data.items;
    if (data && Array.isArray(data.overrides)) return data.overrides;
    if (data && Array.isArray(data.decisions)) return data.decisions;
    return [];
  }
  function pct(part, total) {
    return total ? Math.round((part / total) * 100) : 0;
  }

  C.views.recruiter = async function (root) {
    var S = { rows: [], stats: null, tab: C.params && C.params.tab === "queue" ? "queue" : "overview", route: "ALL", query: "", sort: "score_asc", selected: null, opener: null, loaded: false };
    C.status("Loading applications...");

    var head = h("div", { class: "page-head" },
      h("h1", { text: "Applications" }),
      h("p", { class: "muted", text: "Risk signals help route each application. A person always makes the hiring decision." })
    );
    var tabBar = h("div", { class: "tabs main-tabs", role: "tablist", "aria-label": "Recruiter views" });
    var panelOverview = h("section", { id: "panelOverview", role: "tabpanel", "aria-labelledby": "tabOverview" });
    var panelQueue = h("section", { id: "panelQueue", role: "tabpanel", "aria-labelledby": "tabQueue" });

    var scrim = h("div", { class: "drawer-scrim", hidden: true });
    var drawer = h("aside", { class: "drawer", id: "detail", role: "dialog", "aria-modal": "true", "aria-labelledby": "detailTitle", tabindex: "-1", hidden: true });
    root.append(head, tabBar, panelOverview, panelQueue, scrim, drawer);

    function rowById(id) {
      return S.rows.find(function (row) { return row.application_id === id; });
    }

    function drawMainTabs() {
      C.clear(tabBar);
      [["overview", "Overview", "tabOverview", "panelOverview"], ["queue", "Queue", "tabQueue", "panelQueue"]].forEach(function (t) {
        var button = h("button", { type: "button", role: "tab", id: t[2], "aria-controls": t[3], "aria-selected": S.tab === t[0] ? "true" : "false", tabindex: S.tab === t[0] ? "0" : "-1", text: t[1] });
        button.addEventListener("click", function () { setTab(t[0], false); });
        button.addEventListener("keydown", function (event) {
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            setTab(S.tab === "overview" ? "queue" : "overview", true);
          }
        });
        tabBar.appendChild(button);
      });
      panelOverview.hidden = S.tab !== "overview";
      panelQueue.hidden = S.tab !== "queue";
    }

    function setTab(name, focus) {
      S.tab = name;
      C.keys = name === "queue" ? queueKeys : globalKeys;
      drawMainTabs();
      if (focus) C.$(name === "overview" ? "tabOverview" : "tabQueue").focus();
    }

    function counts() {
      var s = S.stats && S.stats.counts_per_route;
      var out = { PASS_TO_ATS: 0, ADDITIONAL_VERIFICATION: 0, MANUAL_REVIEW: 0 };
      if (s && typeof s === "object") {
        ROUTE_LIST.forEach(function (r) { out[r] = Number(s[r]) || 0; });
        out.total = Number(S.stats.total_received);
        if (!Number.isFinite(out.total)) out.total = out.PASS_TO_ATS + out.ADDITIONAL_VERIFICATION + out.MANUAL_REVIEW;
        return out;
      }
      S.rows.forEach(function (row) {
        var r = row.decision && row.decision.route;
        if (out[r] !== undefined) out[r] += 1;
      });
      out.total = S.rows.length;
      return out;
    }

    function reasonMix() {
      var list = S.stats && Array.isArray(S.stats.top_reason_codes) ? S.stats.top_reason_codes.map(function (x) { return [x.code, Number(x.count) || 0]; }) : null;
      if (!list) {
        var map = {};
        S.rows.forEach(function (row) {
          ((row.decision && row.decision.reasons) || []).forEach(function (reason) { map[reason.code] = (map[reason.code] || 0) + 1; });
        });
        list = Object.keys(map).map(function (k) { return [k, map[k]]; });
      }
      list.sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
      return list.slice(0, 7);
    }

    function drawOverview() {
      C.clear(panelOverview);
      var c = counts();
      var cards = [["Received", c.total, "received"], ["Passed to ATS", c.PASS_TO_ATS, "pass"], ["Needs verification", c.ADDITIONAL_VERIFICATION, "verify"], ["Manual review", c.MANUAL_REVIEW, "review"]];
      var funnel = h("div", { class: "funnel", "aria-label": "Routing funnel" }, cards.map(function (card) {
        return h("article", { class: "funnel-card " + card[2] }, h("span", { class: "funnel-label", text: card[0] }), h("strong", { text: String(card[1]) }), h("span", { class: "funnel-share", text: pct(card[1], c.total) + "% of received" }));
      }));

      var mix = reasonMix();
      var maxCount = mix.length ? mix[0][1] : 1;
      var mixBody = mix.length
        ? h("ul", { class: "mix-list" }, mix.map(function (entry) {
            var fill = h("span", { class: "mix-fill" });
            fill.style.width = Math.max(4, (entry[1] / maxCount) * 100) + "%";
            return h("li", null,
              h("span", { class: "mix-code" }, h("code", { text: entry[0] }), h("small", { class: "muted", text: C.WHY[entry[0]] || "" })),
              h("span", { class: "mix-track", role: "img", "aria-label": entry[0] + " appeared " + entry[1] + " times" }, fill),
              h("span", { class: "mix-count", text: String(entry[1]) })
            );
          }))
        : h("p", { class: "muted", text: "No signals have been recorded yet." });

      var policy = [["Pass to ATS", "Score 70 and above"], ["Additional verification", "Score 41 to 69"], ["Manual review", "Score 40 and below"], ["Fast submission", "Under 30 seconds"], ["Bulk paste", "90% or more"], ["Near duplicate", "75% or more"]];
      var policyCard = h("div", { class: "card" },
        h("div", { class: "row between" }, h("h3", { style: "margin:0", text: "Routing thresholds" }), C.tag("Locked", "info", true)),
        h("dl", { class: "kv policy", style: "margin-top:12px" }, policy.map(function (p) { return [h("dt", { text: p[0] }), h("dd", { text: p[1] })]; })),
        h("p", { class: "small muted", style: "margin:12px 0 0", text: "Some combined signals go straight to manual review. Routes help with triage and never make hiring decisions." })
      );

      var rows = S.rows.slice().sort(function (a, b) { return seconds(b.submitted_at) - seconds(a.submitted_at); });
      var tbody = h("tbody", null, rows.map(function (row) {
        var view = h("button", { type: "button", class: "detail-link", "data-view-id": row.application_id, "aria-label": "View details for " + (row.candidate_name || row.application_id), text: "View" });
        view.addEventListener("click", function () { openDetail(row.application_id, view); });
        var o = overrideRoute(row);
        return h("tr", { "aria-current": S.selected === row.application_id ? "true" : false },
          h("td", { class: "candidate-cell" }, h("strong", { text: row.candidate_name || "Unknown candidate" }), h("small", { text: row.candidate_email_masked || "Email not shown" })),
          h("td", { text: when(row.submitted_at) }),
          h("td", { class: "num", text: scoreOf(row) + " / 100" }),
          h("td", null, C.routeTag(routeOf(row))),
          h("td", null, o ? C.tag("Overridden", "info", true) : h("span", { class: "muted", text: "None" })),
          h("td", { class: "detail-cell" }, view)
        );
      }));
      var table = h("table", null,
        h("caption", { class: "sr-only", text: "Applications and their current routing" }),
        h("thead", null, h("tr", null, ["Candidate", "Submitted", "Score", "Route", "Override", "Details"].map(function (t) { return h("th", { scope: "col", text: t }); }))),
        tbody
      );
      var feed = h("div", { class: "card feed-card" },
        h("div", { class: "row between" },
          h("div", null, h("p", { class: "eyebrow", text: "Newest first" }), h("h3", { style: "margin:0", text: "Application feed" })),
          h("button", { type: "button", class: "btn small", id: "refreshBtn", text: "Refresh now", onclick: function () { load(true); } })
        ),
        rows.length ? h("div", { class: "table-wrap feed-wrap", style: "margin-top:12px" }, table) : h("p", { class: "empty", text: "No applications have arrived yet." })
      );

      panelOverview.append(
        funnel,
        h("div", { class: "grid-2 overview-grid" },
          h("div", { class: "card" }, h("p", { class: "eyebrow", text: "Signal mix" }), h("h3", { text: "Top reason codes" }), mixBody),
          policyCard
        ),
        feed
      );
    }

    var TABS = [["ALL", "All"], ["PASS_TO_ATS", "Pass"], ["ADDITIONAL_VERIFICATION", "Verify"], ["MANUAL_REVIEW", "Manual"]];
    var search = h("input", { type: "search", id: "q", placeholder: "Name, job or application ID", "aria-label": "Search the queue", autocomplete: "off" });
    var sort = h("select", { id: "sort", "aria-label": "Sort queue" },
      h("option", { value: "score_asc", text: "Lowest trust score first" }),
      h("option", { value: "score_desc", text: "Highest trust score first" }),
      h("option", { value: "newest", text: "Newest first" })
    );
    var routeTabs = h("div", { class: "tabs", role: "group", "aria-label": "Filter by route" });
    var list = h("ul", { class: "queue-list", id: "queueList", "aria-label": "Applications" });
    panelQueue.append(
      h("div", { class: "card queue-card" },
        h("div", { class: "queue-tools" }, search, sort),
        routeTabs, list,
        h("p", { class: "kbd-hint" }, h("kbd", { text: "j" }), " ", h("kbd", { text: "k" }), " move, ", h("kbd", { text: "Enter" }), " open, ", h("kbd", { text: "o" }), " override, ", h("kbd", { text: "Esc" }), " close, ", h("kbd", { text: "?" }), " help")
      )
    );

    function filtered() {
      var q = S.query.trim().toLowerCase();
      var rows = S.rows.filter(function (row) {
        if (S.route !== "ALL" && routeOf(row) !== S.route) return false;
        if (!q) return true;
        return [row.candidate_name, row.job_id, row.application_id].join(" ").toLowerCase().indexOf(q) >= 0;
      });
      rows.sort(function (a, b) {
        if (S.sort === "newest") return seconds(b.submitted_at) - seconds(a.submitted_at);
        var diff = scoreOf(a) - scoreOf(b);
        return S.sort === "score_asc" ? diff : -diff;
      });
      return rows;
    }

    function drawRouteTabs() {
      C.clear(routeTabs);
      TABS.forEach(function (tab) {
        var count = S.rows.filter(function (row) { return tab[0] === "ALL" || routeOf(row) === tab[0]; }).length;
        var button = h("button", { type: "button", "aria-pressed": S.route === tab[0] ? "true" : "false", "data-route": tab[0] }, tab[1], h("span", { class: "count", text: String(count) }));
        button.addEventListener("click", function () { S.route = tab[0]; drawRouteTabs(); drawList(); });
        routeTabs.appendChild(button);
      });
    }

    function drawList() {
      C.clear(list);
      var rows = filtered();
      if (!rows.length) {
        list.appendChild(h("li", { class: "empty", text: S.rows.length ? "No applications match this filter." : "The queue is empty." }));
        return;
      }
      rows.forEach(function (row) {
        var route = routeOf(row);
        var button = h("button", { type: "button", class: "queue-item", "data-id": row.application_id, "aria-current": S.selected === row.application_id ? "true" : false },
          h("span", { class: "name", text: row.candidate_name || row.application_id }),
          h("span", { class: "score", "aria-label": "Trust score " + scoreOf(row), text: String(scoreOf(row)) }),
          h("span", { class: "meta" }, C.tag(C.ROUTES[route] || route, C.ROUTE_TONE[route] || ""), h("span", { text: row.job_id }), h("span", { text: when(row.submitted_at) }))
        );
        button.addEventListener("click", function () { openDetail(row.application_id, button); });
        list.appendChild(h("li", null, button));
      });
    }

    function redraw() {
      drawOverview();
      drawRouteTabs();
      drawList();
    }

    var trapHandler = null;

    function focusables() {
      return Array.prototype.slice.call(drawer.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])")).filter(function (el) { return el.offsetParent !== null; });
    }

    function openDetail(id, opener) {
      S.selected = id;
      if (opener) S.opener = opener;
      redraw();
      drawer.hidden = false;
      scrim.hidden = false;
      document.body.classList.add("drawer-open");
      drawDetail(rowById(id));
      drawer.scrollTop = 0;
      var title = C.$("detailTitle");
      if (title) title.focus();
      else drawer.focus();
    }

    function closeDetail() {
      if (drawer.hidden) return;
      var id = S.selected;
      drawer.hidden = true;
      scrim.hidden = true;
      document.body.classList.remove("drawer-open");
      S.selected = null;
      C.clear(drawer);
      redraw();
      var back = null;
      if (id) back = root.querySelector('[data-view-id="' + id + '"]') || root.querySelector('.queue-item[data-id="' + id + '"]');
      if (S.opener && document.contains(S.opener)) back = S.opener;
      if (back) back.focus();
      S.opener = null;
      C.status("");
    }

    scrim.addEventListener("click", closeDetail);
    drawer.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        if (document.querySelector("dialog[open]")) return;
        event.preventDefault();
        event.stopPropagation();
        closeDetail();
      } else if (event.key === "Tab") {
        var items = focusables();
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer || document.activeElement.id === "detailTitle")) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });
    trapHandler = function (event) {
      if (event.key === "Escape" && !drawer.hidden && !document.querySelector("dialog[open]")) closeDetail();
    };
    document.addEventListener("keydown", trapHandler);

    function section(title, titleId) {
      var body = h("div", { class: "drawer-body" });
      var card = h("section", { class: "drawer-card", "aria-labelledby": titleId }, h("h3", { id: titleId, text: title }), body);
      return { card: card, body: body };
    }

    function weightBar(weight) {
      var fill = h("div", { class: "weight-fill" });
      fill.style.width = Math.min(100, Math.max(0, (Number(weight) / 40) * 100)) + "%";
      return h("div", { class: "weight-wrap" }, h("span", { class: "small muted", text: "Weight " + weight + " points" }), h("div", { class: "weight-track", role: "img", "aria-label": "Weight of " + weight + " points" }, fill));
    }

    function drawDetail(row) {
      C.clear(drawer);
      if (!row) return;
      var id = row.application_id;
      var d = row.decision || {};
      var route = routeOf(row);
      var score = scoreOf(row);
      var reasons = d.reasons || [];
      var hidden = d.hidden_intent || row.hidden_intent || [];
      var agreement = d.agreement || row.agreement;
      var fixes = d.candidate_fixes || [];
      var counter = d.counterfactual || row.counterfactual;
      var consentId = row.consent_id || (d && d.consent_id) || null;

      var gauge = h("div", { class: "gauge " + toneFor(score), role: "img", "aria-label": "Trust score " + score + " out of 100" }, h("span", { class: "gauge-num", text: String(score) }), h("small", { text: "of 100" }));
      gauge.style.setProperty("--score", String(score));

      var header = h("div", { class: "drawer-head" },
        h("button", { type: "button", class: "btn small back-link", text: "Back to list", onclick: closeDetail }),
        h("div", { class: "drawer-title-row" },
          h("div", { class: "drawer-id" },
            h("p", { class: "eyebrow", text: "Job " + (row.job_id || "not set") }),
            h("h2", { id: "detailTitle", tabindex: "-1", text: row.candidate_name || id }),
            h("p", { class: "muted small" }, (row.candidate_email_masked || "Email not shown"), " on ", when(row.submitted_at)),
            h("p", { class: "muted small mono", text: id }),
            h("div", { class: "row" }, C.routeTag(route), overrideRoute(row) ? C.tag("Overridden", "info", true) : null)
          ),
          gauge
        )
      );

      var summary = section("Summary", "dsSummary");
      summary.body.append(h("p", { text: d.summary || "No summary is available." }));
      if (d.recruiter_summary) summary.body.append(h("h4", { text: "For the reviewer" }), h("p", { text: d.recruiter_summary }));

      var evidence = section("Reasons (" + reasons.length + ")", "dsReasons");
      if (reasons.length) {
        reasons.forEach(function (reason) {
          var card = C.reasonCard(reason);
          card.appendChild(weightBar(reason.weight));
          evidence.body.appendChild(card);
        });
      } else {
        evidence.body.append(h("p", { class: "muted", text: "No concerns were found for this application." }));
      }

      var intent = section("Hidden content", "dsHidden");
      if (hidden.length) {
        hidden.forEach(function (entry) {
          var label = INTENT_LABELS[entry.label] || String(entry.label || "Hidden content").replace(/_/g, " ");
          intent.body.append(h("div", { class: "reason sev-high" },
            h("div", { class: "reason-head" }, C.tag(label, entry.label === "harmless" ? "info" : "danger")),
            entry.hidden_reason ? h("p", { class: "small muted", text: entry.hidden_reason }) : null,
            h("blockquote", { class: "evidence", text: entry.evidence || entry.text || "" })
          ));
        });
      } else {
        intent.body.append(h("p", { class: "muted", text: "No hidden text was reported." }));
      }

      var agree = section("Parser and person agreement", "dsAgree");
      if (agreement) {
        var av = Number(agreement.score) || 0;
        agree.body.append(C.meter({ id: "rAgree", label: agreement.label || "Agreement", value: av, tone: av >= 80 ? "ok" : av >= 50 ? "warn" : "danger", valuetext: av + " out of 100, " + (agreement.label || "") }));
      } else {
        agree.body.append(h("p", { class: "muted", text: "Agreement was not reported." }));
      }

      var fixCard = section("Fixes suggested to the candidate", "dsFixes");
      if (fixes.length) fixCard.body.append(h("ol", null, fixes.map(function (fix) { return h("li", { text: typeof fix === "string" ? fix : (fix.fix || fix.detail || fix.text || "") }); })));
      else fixCard.body.append(h("p", { class: "muted", text: "No fixes were suggested." }));

      var cf = section("What would change this", "dsCounter");
      cf.body.append(h("p", { id: "counterfactual", text: counter || "The service did not give a hint for this decision. Ask the candidate about the heaviest reason above before you change the route." }));

      var intake = section("Extra details from the candidate", "dsIntake");
      var intakeBody = intake.body;
      intakeBody.append(h("p", { class: "muted", text: consentId ? "Loading what the candidate added..." : "The candidate has not given consent for any extra checks." }));

      var delivery = section("Delivery", "dsDelivery");
      delivery.body.append(h("p", { class: "muted", text: "Loading delivery status..." }));

      var history = section("Override history", "dsHistory");
      history.body.append(h("p", { class: "muted", text: "Loading history..." }));

      var form = overrideForm(row, history);

      drawer.append(header, h("div", { class: "drawer-scroll" }, summary.card, evidence.card, intent.card, agree.card, fixCard.card, cf.card, intake.card, delivery.card, history.card, form));

      if (consentId) loadIntake(id, consentId, intakeBody);
      loadDelivery(id, row, delivery.body);
      loadHistory(id, history.body);
    }

    function stale(id) {
      return S.selected !== id || drawer.hidden;
    }

    async function loadIntake(id, consentId, body) {
      try {
        var data = await C.api("GET", "/v1/intake/" + encodeURIComponent(consentId) + "/recruiter");
        if (stale(id)) return;
        C.clear(body);
        body.append(h("p", { class: "small" }, C.tag("Consent given", "ok"), " ", h("span", { class: "muted mono", text: consentId })));
        var findings = (data && data.findings) || [];
        if (!findings.length) body.append(h("p", { class: "muted", text: "There are no findings to show." }));
        findings.forEach(function (finding, index) {
          var recheck = h("button", { type: "button", class: "btn small", text: "Recheck", "aria-label": "Recheck finding " + (index + 1) + " from " + finding.source });
          recheck.addEventListener("click", function () { recheckFinding(id, consentId, index, finding, body); });
          body.append(h("div", { class: "finding" },
            h("div", null,
              h("div", { class: "row" }, h("strong", { text: finding.source }), C.tag(String(finding.status || "").replace(/_/g, " "), FINDING_TONE[finding.status] || "info")),
              h("p", { style: "margin:4px 0 0", text: finding.fact }),
              finding.evidence ? h("blockquote", { class: "evidence", text: finding.evidence }) : null
            ),
            recheck
          ));
        });
        if (data && data.withheld_count) body.append(h("p", { class: "small muted", text: data.withheld_count + " finding" + (data.withheld_count === 1 ? " is" : "s are") + " hidden because the candidate disputed them." }));
      } catch (error) {
        if (stale(id) || C.authError(error)) return;
        C.clear(body);
        body.append(h("p", { class: "muted", text: error.status === 404 ? "No intake details are available for this application yet." : C.friendly(error) }));
      }
    }

    async function recheckFinding(id, consentId, index, finding, body) {
      var outcome = h("select", { id: "rcOutcome" }, h("option", { value: "upheld", text: "Upheld, the finding stands" }), h("option", { value: "withdrawn", text: "Withdrawn, the finding is wrong" }));
      var note = h("textarea", { id: "rcNote", placeholder: "Add a short note for the record" });
      var answer = await C.dialog({
        title: "Recheck a finding",
        body: h("div", null,
          h("p", { class: "muted", text: finding.source + " said " + finding.fact }),
          h("label", { class: "field", for: "rcOutcome" }, "Outcome", outcome),
          h("label", { class: "field", for: "rcNote" }, "Note", note)
        ),
        actions: [
          { label: "Cancel", value: null },
          { label: "Save recheck", kind: "primary", value: "go", validate: function () { return note.value.trim().length < 3 ? "Add a short note first." : null; } }
        ]
      });
      if (answer !== "go") return;
      try {
        await C.api("POST", "/v1/intake/" + encodeURIComponent(consentId) + "/recheck", { json: { finding_index: index, outcome: outcome.value, note: note.value.trim() } });
        C.status("The recheck was saved.");
        if (!stale(id)) loadIntake(id, consentId, body);
      } catch (error) {
        if (C.authError(error)) return;
        C.status(error.status === 404 ? "Rechecks are not available yet on this service." : C.friendly(error), true);
      }
    }

    async function loadDelivery(id, row, body) {
      var own = row.delivery;
      try {
        var data = await C.api("GET", "/v1/delivery/status");
        if (stale(id)) return;
        C.clear(body);
        var route = routeOf(row);
        body.append(h("p", { text: route === "PASS_TO_ATS" ? "This application is routed to the hiring system." : "This application is held back and is not sent to the hiring system." }));
        var dest = (data && data.destinations) || [];
        var kv = h("dl", { class: "kv" },
          h("dt", { text: "Waiting to send" }), h("dd", { text: String(data && data.pending !== undefined ? data.pending : 0) }),
          h("dt", { text: "Failed deliveries" }), h("dd", { text: String(data && data.dead_letters !== undefined ? data.dead_letters : 0) })
        );
        if (own) kv.append(h("dt", { text: "This application" }), h("dd", null, C.tag(own.status || "unknown", own.status === "delivered" ? "ok" : own.status === "failed" ? "danger" : "info")));
        dest.forEach(function (item) { kv.append(h("dt", { text: "Destination " + item.name }), h("dd", { text: item.target || item.status || "set" })); });
        body.append(kv, h("p", { class: "small muted", style: "margin-top:8px", text: "This is read only." }));
      } catch (error) {
        if (stale(id) || C.authError(error)) return;
        C.clear(body);
        body.append(h("p", { class: "muted", text: error.status === 404 ? "Delivery status is not available." : C.friendly(error) }));
      }
    }

    async function loadHistory(id, body) {
      try {
        var data = await C.api("GET", "/v1/decisions/" + encodeURIComponent(id) + "/overrides");
        if (stale(id)) return;
        var items = listOf(data);
        C.clear(body);
        if (!items.length) {
          body.append(h("p", { class: "muted", text: "No one has overridden this decision." }));
          return;
        }
        items.slice().sort(function (a, b) { return seconds(b.at || b.ts) - seconds(a.at || a.ts); }).forEach(function (item) {
          var to = item.override_route || item.route;
          body.append(h("div", { class: "history-item" },
            h("div", { class: "row" }, h("strong", { text: item.actor || item.username || "A reviewer" }), h("span", { class: "muted small", text: when(item.at || item.ts) })),
            h("p", { class: "small", style: "margin:4px 0" }, (C.ROUTES[item.original_route] || item.original_route || "Earlier route"), " changed to ", (C.ROUTES[to] || to)),
            h("p", { class: "small muted", style: "margin:0", text: item.reason || "" })
          ));
        });
      } catch (error) {
        if (stale(id) || C.authError(error)) return;
        C.clear(body);
        body.append(h("p", { class: "muted", text: error.status === 404 ? "Override history is not available yet." : C.friendly(error) }));
      }
    }

    function overrideForm(row, history) {
      var id = row.application_id;
      var current = routeOf(row);
      var select = h("select", { id: "ovRoute" }, ROUTE_LIST.filter(function (r) { return r !== current; }).map(function (r) { return h("option", { value: r, text: C.ROUTES[r] }); }));
      var reason = h("textarea", { id: "ovReason", placeholder: "Why is this the right outcome? Write at least 10 characters.", "aria-describedby": "ovHint" });
      var hint = h("span", { id: "ovHint", class: "hint", text: "0 of 10 characters" });
      var message = h("p", { class: "small", id: "ovMessage", role: "status", "aria-live": "polite" });
      var submit = h("button", { type: "submit", class: "btn primary", text: "Save override" });
      var manual = h("button", { type: "button", class: "btn", id: "toManual", text: "Send to manual review" });
      if (current === "MANUAL_REVIEW") manual.hidden = true;
      reason.addEventListener("input", function () {
        var n = reason.value.trim().length;
        hint.textContent = n + " of 10 characters";
      });
      manual.addEventListener("click", function () {
        select.value = "MANUAL_REVIEW";
        reason.focus();
      });
      var form = h("form", { class: "drawer-card override-form", id: "overrideForm", novalidate: true, "aria-labelledby": "dsOverride" },
        h("h3", { id: "dsOverride", text: "Override this decision" }),
        h("p", { class: "muted small", text: "The product never rejects anyone on its own. Your change is saved with your name in the audit log." }),
        h("label", { class: "field", for: "ovRoute" }, "New route", select),
        h("label", { class: "field", for: "ovReason" }, "Reason", reason, hint),
        message,
        h("div", { class: "row" }, submit, manual)
      );
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        message.className = "small";
        if (reason.value.trim().length < 10) {
          message.textContent = "The reason needs at least 10 characters.";
          message.className = "small err";
          reason.focus();
          return;
        }
        submit.disabled = true;
        message.textContent = "Saving...";
        try {
          var result = await C.api("POST", "/v1/decisions/" + encodeURIComponent(id) + "/override", { json: { route: select.value, reason: reason.value.trim() } });
          var record = result && result.override ? result.override : { override_route: select.value, reason: reason.value.trim(), actor: C.state.me && C.state.me.username, at: Math.floor(Date.now() / 1000), original_route: current };
          row.override = record;
          C.status("Override saved. " + (row.candidate_name || id) + " is now " + (C.ROUTES[select.value] || select.value) + ".");
          redraw();
          drawDetail(row);
          var title = C.$("detailTitle");
          if (title) title.focus();
        } catch (error) {
          submit.disabled = false;
          if (C.authError(error)) return;
          message.textContent = C.friendly(error);
          message.className = "small err";
        }
      });
      return form;
    }

    function globalKeys(event) {
      if (event.key === "Escape" && !drawer.hidden) closeDetail();
    }

    function queueKeys(event) {
      var all = Array.prototype.slice.call(list.querySelectorAll(".queue-item"));
      var index = all.indexOf(document.activeElement);
      if (event.key === "j" || event.key === "k") {
        if (!all.length) return;
        event.preventDefault();
        var next = index < 0 ? 0 : event.key === "j" ? index + 1 : index - 1;
        next = Math.max(0, Math.min(all.length - 1, next));
        all[next].focus();
        all[next].scrollIntoView({ block: "nearest" });
      } else if (event.key === "o") {
        var target = index >= 0 ? rowById(all[index].dataset.id) : rowById(S.selected);
        if (target) {
          event.preventDefault();
          openDetail(target.application_id, index >= 0 ? all[index] : null);
          var f = C.$("ovReason");
          if (f) f.focus();
        }
      } else if (event.key === "/") {
        event.preventDefault();
        search.focus();
      }
    }

    search.addEventListener("input", function () { S.query = search.value; drawList(); });
    sort.addEventListener("change", function () { S.sort = sort.value; drawList(); });

    var observer = new MutationObserver(function () {
      if (!document.contains(root.firstChild || scrim)) {
        document.removeEventListener("keydown", trapHandler);
        document.body.classList.remove("drawer-open");
        observer.disconnect();
      }
    });
    observer.observe(root, { childList: true });

    async function load(refresh) {
      C.status(refresh ? "Refreshing..." : "Loading applications...");
      var results = await Promise.allSettled([C.api("GET", "/v1/stats"), C.api("GET", "/v1/decisions?limit=200")]);
      var decisions = results[1];
      if (decisions.status === "rejected") {
        var error = decisions.reason;
        if (C.authError(error)) return;
        if (error.status === 403) { C.accessDenied(root, error); return; }
        C.status(C.friendly(error), true);
        C.clear(root);
        root.append(h("div", { class: "card" }, h("h1", { text: "The applications could not be loaded" }), h("p", { class: "muted", text: C.friendly(error) }), h("div", { class: "row" },
          h("button", { type: "button", class: "btn primary", text: "Try again", onclick: function () { C.go("recruiter"); } }),
          error.network && !C.state.demo ? h("button", { type: "button", class: "btn", text: "Use demo data", onclick: function () { C.enterDemo("The service could not be reached, so this is sample data."); C.state.me = null; C.renderShell(); C.showLogin(""); } }) : null)));
        return;
      }
      S.rows = listOf(decisions.value);
      S.stats = results[0].status === "fulfilled" ? results[0].value : null;
      S.loaded = true;
      drawMainTabs();
      redraw();
      C.status(S.rows.length + " applications loaded.");
      if (refresh && S.selected && rowById(S.selected)) drawDetail(rowById(S.selected));
      var want = C.params && C.params.select;
      if (want && rowById(want)) {
        C.params.select = null;
        openDetail(want, null);
      }
    }

    C.keys = S.tab === "queue" ? queueKeys : globalKeys;
    drawMainTabs();
    await load(false);
  };
})();
