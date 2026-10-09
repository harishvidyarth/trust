(function () {
  var C = window.C;
  var h = C.h;

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

  function isLinked(row) {
    return String(row.outcome_by || "").toLowerCase().indexOf("linked") === 0;
  }

  function badgesFor(row) {
    var open = Number(row.follow_up_open) || 0;
    var list = [];
    if (row.outcome === "rejected") list.push(C.tag("Rejected", "dark", true));
    if (row.outcome === "rejected" && isLinked(row)) list.push(C.tag("Linked to an earlier rejection", "", true));
    if (row.applicant_form_present) list.push(C.tag("Form", "info", true));
    if (open > 0) list.push(C.tag(open + (open === 1 ? " open request" : " open requests"), "", true));
    if (row.identity_check) list.push(C.tag("Identity", "info", true));
    if (row.identity_advisory === "ask_for_live_check") list.push(C.tag("Live check suggested", "warn", true));
    return list;
  }

  var FORM_LABELS = [
    ["applicant_name", "Full name"], ["applicant_email", "Email"], ["applicant_phone", "Phone"], ["role_title", "Role"],
    ["years_experience", "Years of experience"], ["current_employer", "Current or latest employer"], ["education", "Education"],
    ["skills", "Skills"], ["extra_skills", "Other skills"], ["github_url", "GitHub"], ["linkedin_url", "LinkedIn"], ["portfolio_url", "Portfolio"],
    ["papers", "Research papers or DOIs"], ["certificate_ids", "Certificate IDs"]
  ];

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
    var S = { outcomes: {}, rows: [], stats: null, dest: null, destError: "", destLoading: false, tab: C.params && (C.params.tab === "queue" || C.params.tab === "destinations") ? C.params.tab : "overview", route: "ALL", query: "", sort: "score_asc", selected: null, opener: null, nextId: null, loaded: false };
    C.status("Loading applications...");

    var head = h("div", { class: "page-head" },
      h("h1", { text: "Applications" }),
      h("p", { class: "muted", text: "Risk signals help route each application. A person always makes the hiring decision." })
    );
    var tabBar = h("div", { class: "tabs main-tabs", role: "tablist", "aria-label": "Recruiter views" });
    var panelOverview = h("section", { id: "panelOverview", role: "tabpanel", "aria-labelledby": "tabOverview" });
    var panelQueue = h("section", { id: "panelQueue", role: "tabpanel", "aria-labelledby": "tabQueue" });

    var panelDest = h("section", { id: "panelDest", role: "tabpanel", "aria-labelledby": "tabDest" });

    var notice = h("div", { class: "notice-line", id: "outcomeNotice", role: "status", "aria-live": "polite", hidden: true });
    var scrim = h("div", { class: "drawer-scrim", hidden: true });
    var drawer = h("aside", { class: "drawer", id: "detail", role: "dialog", "aria-modal": "true", "aria-labelledby": "detailTitle", tabindex: "-1", hidden: true });
    root.append(head, notice, tabBar, panelOverview, panelQueue, panelDest, scrim, drawer);

    function isRej(row) {
      return row.outcome === "rejected";
    }
    function activeRows() {
      return S.rows.filter(function (row) { return !isRej(row); });
    }
    function showNotice(text, linkLabel, onLink) {
      C.clear(notice);
      if (!text) { notice.hidden = true; return; }
      notice.append(h("span", { text: text }));
      if (linkLabel) {
        notice.append(" ", h("button", { type: "button", class: "link-btn", id: "noticeLink", text: linkLabel, onclick: onLink }));
      }
      notice.append(" ", h("button", { type: "button", class: "link-btn", "aria-label": "Dismiss this message", text: "Dismiss", onclick: function () { showNotice(""); } }));
      notice.hidden = false;
    }
    function showRejectedTab() {
      showNotice("");
      setTab("queue", false);
      S.route = "REJECTED";
      drawRouteTabs();
      drawList();
    }
    function sameApplicantElsewhere(row) {
      var name = String(row.candidate_name || "").trim().toLowerCase();
      return S.rows.some(function (other) {
        return other.application_id !== row.application_id && isRej(other) && String(other.candidate_name || "").trim().toLowerCase() === name && other.job_id === row.job_id;
      });
    }

    function rowById(id) {
      return S.rows.find(function (row) { return row.application_id === id; });
    }

    var TAB_DEFS = [["overview", "Overview", "tabOverview", "panelOverview"], ["queue", "Queue", "tabQueue", "panelQueue"], ["destinations", "Destinations", "tabDest", "panelDest"]];

    function drawMainTabs() {
      C.clear(tabBar);
      TAB_DEFS.forEach(function (t, i) {
        var button = h("button", { type: "button", role: "tab", id: t[2], "aria-controls": t[3], "aria-selected": S.tab === t[0] ? "true" : "false", tabindex: S.tab === t[0] ? "0" : "-1", text: t[1] });
        button.addEventListener("click", function () { setTab(t[0], false); });
        button.addEventListener("keydown", function (event) {
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            var step = event.key === "ArrowRight" ? 1 : -1;
            setTab(TAB_DEFS[(i + step + TAB_DEFS.length) % TAB_DEFS.length][0], true);
          }
        });
        tabBar.appendChild(button);
      });
      panelOverview.hidden = S.tab !== "overview";
      panelQueue.hidden = S.tab !== "queue";
      panelDest.hidden = S.tab !== "destinations";
    }

    function setTab(name, focus) {
      S.tab = name;
      C.keys = name === "queue" ? queueKeys : globalKeys;
      drawMainTabs();
      if (name === "destinations" && !S.dest && !S.destLoading) loadDest();
      if (focus) {
        var def = TAB_DEFS.filter(function (t) { return t[0] === name; })[0];
        C.$(def[2]).focus();
      }
    }

    var DEST_NAMES = { ats: "Mock ATS", mock_ats: "Mock ATS", verification_inbox: "Verification inbox", review_inbox: "Review inbox", slack: "Slack message", email: "Email message" };
    function destName(name) {
      if (DEST_NAMES[name]) return DEST_NAMES[name];
      var text = String(name || "Unnamed place").replace(/[_-]+/g, " ");
      return text.charAt(0).toUpperCase() + text.slice(1);
    }
    var DEST_SECTIONS = [["PASS_TO_ATS", "Passed to the ATS", "These applications were sent on to the hiring system."], ["ADDITIONAL_VERIFICATION", "Waiting for verification", "These applications wait until the candidate or a person confirms the details."], ["MANUAL_REVIEW", "Waiting for a person to review", "These applications wait for a recruiter to look at them."]];

    async function loadDest() {
      S.destLoading = true;
      S.destError = "";
      drawDest();
      var results = await Promise.allSettled([C.api("GET", "/v1/delivery/inbox"), C.api("GET", "/v1/delivery/status")]);
      S.destLoading = false;
      var failed = results.filter(function (r) { return r.status === "rejected"; });
      for (var i = 0; i < failed.length; i++) if (C.authError(failed[i].reason)) return;
      var inbox = results[0].status === "fulfilled" ? results[0].value : null;
      var status = results[1].status === "fulfilled" ? results[1].value : null;
      if (!inbox && !status) {
        var err = failed[0].reason;
        S.destError = err.status === 403 ? "Your account does not have permission to see destinations." : err.status === 404 ? "Destinations are not available on this service yet." : C.friendly(err);
        S.dest = null;
      } else {
        S.dest = { inbox: inbox, status: status, inboxMissing: !inbox };
      }
      drawDest();
    }

    function inboxItems(name) {
      var list = S.dest && S.dest.inbox && Array.isArray(S.dest.inbox.inboxes) ? S.dest.inbox.inboxes : [];
      var found = list.filter(function (b) { return b.name === name; })[0];
      return found && Array.isArray(found.items) ? found.items : [];
    }

    function inboxNameFor(route) {
      var routes = S.dest && S.dest.status && S.dest.status.routes ? S.dest.status.routes : {};
      var names = routes[route] || [];
      var list = S.dest && S.dest.inbox && Array.isArray(S.dest.inbox.inboxes) ? S.dest.inbox.inboxes : [];
      var hit = names.filter(function (n) { return list.some(function (b) { return b.name === n; }); })[0];
      if (hit) return hit;
      return route === "ADDITIONAL_VERIFICATION" ? "verification_inbox" : route === "MANUAL_REVIEW" ? "review_inbox" : null;
    }

    function destItems(route) {
      if (route === "PASS_TO_ATS") {
        return activeRows().filter(function (r) { return routeOf(r) === "PASS_TO_ATS"; }).map(function (r) { return { application_id: r.application_id, job_id: r.job_id, candidate_name: r.candidate_name, at: r.submitted_at, score: scoreOf(r) }; });
      }
      return inboxItems(inboxNameFor(route)).filter(function (item) {
        var known = rowById(item.application_id);
        return !(known && known.outcome === "rejected");
      }).map(function (item) {
        var row = rowById(item.application_id);
        return { application_id: item.application_id, job_id: item.job_id || (row && row.job_id), candidate_name: item.candidate_name || (row && row.candidate_name), at: row && row.submitted_at, score: row ? scoreOf(row) : null };
      });
    }

    async function replayDead(button, message) {
      button.disabled = true;
      message.className = "small";
      message.textContent = "Sending again...";
      try {
        var result = await C.api("POST", "/v1/delivery/replay-dead-letters");
        var n = result && Number.isFinite(Number(result.replayed)) ? Number(result.replayed) : null;
        S.replayNote = n === null ? "The failed deliveries were sent again." : n + (n === 1 ? " failed delivery was sent again." : " failed deliveries were sent again.");
        C.status(S.replayNote);
        await loadDest();
      } catch (error) {
        button.disabled = false;
        if (C.authError(error)) return;
        message.textContent = error.status === 403 ? "Only an admin can send failed deliveries again." : error.status === 404 ? "This service cannot send failed deliveries again yet." : C.friendly(error);
        message.className = "small err";
      }
    }

    function drawDest() {
      C.clear(panelDest);
      var refresh = h("button", { type: "button", class: "btn small", id: "destRefresh", text: "Refresh now", onclick: function () { loadDest(); } });
      panelDest.append(h("div", { class: "row between dest-head" },
        h("div", null, h("p", { class: "eyebrow", text: "One final place for every outcome" }), h("h3", { style: "margin:0", text: "Destinations" })),
        refresh
      ));
      if (S.destLoading && !S.dest) {
        panelDest.append(h("div", { class: "card" }, h("p", { class: "muted", text: "Loading destinations..." })));
        return;
      }
      if (S.destError) {
        panelDest.append(h("div", { class: "card" }, h("p", { class: "muted", text: S.destError }), h("button", { type: "button", class: "btn primary", text: "Try again", onclick: function () { loadDest(); } })));
        return;
      }
      if (!S.dest) return;
      var st = S.dest.status;
      var pending = st && Number.isFinite(Number(st.pending)) ? Number(st.pending) : null;
      var dead = st && Number.isFinite(Number(st.dead_letters)) ? Number(st.dead_letters) : null;
      var isAdmin = C.state.me && C.state.me.role === "admin";
      var line = h("div", { class: "card dest-status" });
      if (st) {
        line.append(h("p", { class: "row", style: "margin:0" },
          h("span", { text: (pending === null ? "Waiting deliveries are not known." : pending + (pending === 1 ? " delivery is waiting to send." : " deliveries are waiting to send.")) }),
          h("span", { text: (dead === null ? "" : dead + (dead === 1 ? " delivery has failed." : " deliveries have failed.")) }),
          dead ? C.tag("Needs attention", "danger") : C.tag("All clear", "ok")
        ));
      } else {
        line.append(h("p", { class: "muted", style: "margin:0", text: "Delivery status is not available for your account." }));
      }
      if (isAdmin && st) {
        var message = h("p", { class: "small", role: "status", "aria-live": "polite", style: "margin:8px 0 0", text: S.replayNote || "" });
        S.replayNote = "";
        var replay = h("button", { type: "button", class: "btn", id: "replayBtn", text: "Send failed deliveries again" });
        if (!dead) replay.disabled = true;
        replay.addEventListener("click", function () { replayDead(replay, message); });
        line.append(h("div", { class: "row", style: "margin-top:12px" }, replay), message);
      }
      panelDest.append(line);
      if (S.dest.inboxMissing) panelDest.append(h("p", { class: "small muted", text: "The waiting lists could not be loaded, so only the passed applications are shown." }));

      var cols = h("div", { class: "grid-3 dest-grid" });
      DEST_SECTIONS.forEach(function (sec, index) {
        var route = sec[0];
        var items = destItems(route).sort(function (a, b) { return seconds(b.at) - seconds(a.at); });
        var names = st && st.routes && st.routes[route] ? st.routes[route] : [];
        var nameText = names.length ? names.map(destName).join(" and ") : (route === "PASS_TO_ATS" ? "Mock ATS" : destName(inboxNameFor(route)));
        var failedHere = st && Array.isArray(st.destinations) ? st.destinations.filter(function (d) { return names.indexOf(d.name) >= 0; }).reduce(function (sum, d) { return sum + (Number(d.dead_letters) || 0); }, 0) : 0;
        var body = items.length
          ? h("ul", { class: "dest-list" }, items.map(function (item) {
              var view = h("button", { type: "button", class: "detail-link", "data-dest-view": item.application_id, "aria-label": "View details for " + (item.candidate_name || item.application_id), text: "View" });
              view.addEventListener("click", function () {
                if (!rowById(item.application_id)) { C.status("The details for this application are not in the loaded list. Refresh the page and try again.", true); return; }
                openDetail(item.application_id, view);
              });
              return h("li", { class: "dest-item" },
                h("div", { class: "dest-who" },
                  h("strong", { text: item.candidate_name || "Unknown candidate" }),
                  h("small", { class: "muted", text: (item.job_id || "No job set") + (Number.isFinite(item.score) ? ", score " + item.score : "") }),
                  h("small", { class: "muted", text: when(item.at) })
                ),
                view
              );
            }))
          : h("p", { class: "empty", text: "Nothing is here right now." });
        cols.append(h("section", { class: "card dest-card", "aria-labelledby": "destT" + index },
          h("p", { class: "eyebrow", text: nameText }),
          h("div", { class: "row between" }, h("h3", { id: "destT" + index, style: "margin:0", text: sec[1] }), h("strong", { class: "dest-count", text: String(items.length) })),
          h("p", { class: "small muted", text: sec[2] }),
          failedHere ? h("p", { class: "small err", text: failedHere + (failedHere === 1 ? " delivery to this place has failed." : " deliveries to this place have failed.") }) : null,
          body
        ));
      });
      panelDest.append(cols);
    }

    function counts() {
      var s = S.stats && S.stats.counts_per_route;
      var out = { PASS_TO_ATS: 0, ADDITIONAL_VERIFICATION: 0, MANUAL_REVIEW: 0 };
      if (s && typeof s === "object") {
        ROUTE_LIST.forEach(function (r) { out[r] = Number(s[r]) || 0; });
        S.rows.forEach(function (row) {
          var rr = routeOf(row);
          if (isRej(row) && out[rr] !== undefined) out[rr] = Math.max(0, out[rr] - 1);
        });
        out.total = out.PASS_TO_ATS + out.ADDITIONAL_VERIFICATION + out.MANUAL_REVIEW;
        return out;
      }
      activeRows().forEach(function (row) {
        var r = row.decision && row.decision.route;
        if (out[r] !== undefined) out[r] += 1;
      });
      out.total = activeRows().length;
      return out;
    }

    function reasonMix() {
      var list = S.stats && Array.isArray(S.stats.top_reason_codes) && !S.rows.some(isRej) ? S.stats.top_reason_codes.map(function (x) { return [x.code, Number(x.count) || 0]; }) : null;
      if (!list) {
        var map = {};
        activeRows().forEach(function (row) {
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
      var rejectedCount = S.rows.filter(isRej).length;
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

      var rows = activeRows().sort(function (a, b) { return seconds(b.submitted_at) - seconds(a.submitted_at); });
      var tbody = h("tbody", null, rows.map(function (row) {
        var view = h("button", { type: "button", class: "detail-link", "data-view-id": row.application_id, "aria-label": "View details for " + (row.candidate_name || row.application_id), text: "View" });
        view.addEventListener("click", function () { openDetail(row.application_id, view); });
        var o = overrideRoute(row);
        return h("tr", { "aria-current": S.selected === row.application_id ? "true" : false },
          h("td", { class: "candidate-cell" }, h("strong", { text: row.candidate_name || "Unknown candidate" }), h("small", { text: row.candidate_email_masked || "Email not shown" }), badgesFor(row).length ? h("span", { class: "badge-line" }, badgesFor(row)) : null),
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
        rejectedCount ? h("p", { class: "small muted rejected-line" }, rejectedCount + " rejected ", h("button", { type: "button", class: "link-btn", id: "overviewRejected", text: "Show rejected", onclick: showRejectedTab })) : null,
        h("div", { class: "grid-2 overview-grid" },
          h("div", { class: "card" }, h("p", { class: "eyebrow", text: "Signal mix" }), h("h3", { text: "Top reason codes" }), mixBody),
          policyCard
        ),
        feed
      );
    }

    var TABS = [["ALL", "All"], ["PASS_TO_ATS", "Pass"], ["ADDITIONAL_VERIFICATION", "Verify"], ["MANUAL_REVIEW", "Manual"], ["REJECTED", "Rejected"]];
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
        h("p", { class: "kbd-hint" }, h("kbd", { text: "j" }), " ", h("kbd", { text: "k" }), " move, ", h("kbd", { text: "Enter" }), " open, ", h("kbd", { text: "o" }), " override, ", h("kbd", { text: "x" }), " reject, ", h("kbd", { text: "Esc" }), " close, ", h("kbd", { text: "?" }), " help")
      )
    );

    function filtered() {
      var q = S.query.trim().toLowerCase();
      var rows = S.rows.filter(function (row) {
        if (S.route === "REJECTED") { if (!isRej(row)) return false; }
        else if (isRej(row) || (S.route !== "ALL" && routeOf(row) !== S.route)) return false;
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
        var count = S.rows.filter(function (row) { return tab[0] === "REJECTED" ? isRej(row) : !isRej(row) && (tab[0] === "ALL" || routeOf(row) === tab[0]); }).length;
        var button = h("button", { type: "button", "aria-pressed": S.route === tab[0] ? "true" : "false", "data-route": tab[0] }, tab[1], h("span", { class: "count", text: String(count) }));
        button.addEventListener("click", function () { S.route = tab[0]; drawRouteTabs(); drawList(); });
        routeTabs.appendChild(button);
      });
    }

    function drawList() {
      C.clear(list);
      var rows = filtered();
      if (!rows.length) {
        list.appendChild(h("li", { class: "empty", text: S.route === "REJECTED" ? "No applications are rejected." : S.rows.length ? "No applications match this filter." : "The queue is empty." }));
        return;
      }
      rows.forEach(function (row) {
        var route = routeOf(row);
        var button = h("button", { type: "button", class: "queue-item", "data-id": row.application_id, "aria-current": S.selected === row.application_id ? "true" : false },
          h("span", { class: "name", text: row.candidate_name || row.application_id }),
          h("span", { class: "score", "aria-label": "Trust score " + scoreOf(row), text: String(scoreOf(row)) }),
          h("span", { class: "meta" }, C.tag(C.ROUTES[route] || route, C.ROUTE_TONE[route] || ""), h("span", { text: row.job_id }), h("span", { text: when(row.submitted_at) }), badgesFor(row), isRej(row) ? h("span", { class: "rejected-by", text: "Rejected by " + (isLinked(row) ? "a linked rejection" : (row.outcome_by || "a reviewer")) + (row.outcome_at ? " on " + when(row.outcome_at) : "") }) : null)
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
      if (S.nextId) {
        var nb = root.querySelector('.queue-item[data-id="' + S.nextId + '"]');
        if (nb) back = nb;
        S.nextId = null;
      }
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

      var formSec = section("Applicant form", "dsForm");
      formSec.body.append(h("p", { class: "muted", text: "Loading the form..." }));

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

      var claimSec = section("Candidate answer check", "dsClaim");
      claimSec.body.append(h("p", { class: "muted", text: "Loading..." }));

      var checksSec = section("Claim checks", "dsChecks");
      checksSec.body.append(C.checks.panel(id, { who: "recruiter", bare: true, isStale: function () { return stale(id); } }));

      var identitySec = section("Identity check", "dsIdentity");
      identitySec.body.append(h("p", { class: "muted", text: "Loading..." }));

      var delivery = section("Delivery", "dsDelivery");
      delivery.body.append(h("p", { class: "muted", text: "Loading delivery status..." }));

      var history = section("Override history", "dsHistory");
      history.body.append(h("p", { class: "muted", text: "Loading history..." }));

      var form = overrideForm(row, history);
      var bannerBox = h("div", { class: "outcome-slot" });
      drawBanner(row, bannerBox);
      var decisionCard = outcomeCard(row);

      drawer.append(header, h("div", { class: "drawer-scroll" }, bannerBox, summary.card, formSec.card, evidence.card, intent.card, agree.card, fixCard.card, cf.card, intake.card, claimSec.card, checksSec.card, identitySec.card, delivery.card, history.card, decisionCard, form));

      loadForm(id, formSec.body);
      C.claim.recruiterLoad(id, claimSec.body, function () { return stale(id); });
      if (C.identity) C.identity.recruiterLoad(id, identitySec.body, function () { return stale(id); });

      if (consentId) loadIntake(id, consentId, intakeBody);
      loadDelivery(id, row, delivery.body);
      loadHistory(id, history.body);
      if (row.outcome === "rejected" && !S.outcomes[id]) loadOutcome(row, bannerBox);
    }

    function drawBanner(row, box) {
      C.clear(box);
      if (row.outcome !== "rejected") return;
      var info = S.outcomes[row.application_id] || {};
      var by = row.outcome_by || info.by;
      var at = row.outcome_at || info.at;
      box.append(h("section", { class: "outcome-banner", id: "rejectedBanner", "aria-labelledby": "rejectedTitle" },
        h("h3", { id: "rejectedTitle", text: "Rejected" }),
        h("p", { class: "small", style: "margin:0", text: (isLinked(row) ? "Closed because of an earlier rejection of the same applicant for the same job" : "Rejected by " + (by || "a reviewer")) + " on " + (at ? when(at) : "an unknown date") }),
        info.reason ? h("blockquote", { class: "evidence", text: info.reason }) : null
      ));
    }

    async function loadOutcome(row, box) {
      var id = row.application_id;
      try {
        var data = await C.api("GET", "/v1/decisions/" + encodeURIComponent(id) + "/outcome");
        if (stale(id)) return;
        var history = data && Array.isArray(data.history) ? data.history : [];
        var last = history.filter(function (e) { return e && e.outcome === "REJECTED"; }).sort(function (a, b) { return seconds(b.at) - seconds(a.at); })[0];
        if (last) S.outcomes[id] = { reason: last.reason, by: last.by, at: last.at };
        drawBanner(row, box);
      } catch (error) {
        if (stale(id) || C.authError(error)) return;
      }
    }

    function outcomeCard(row) {
      var id = row.application_id;
      var rejected = row.outcome === "rejected";
      var trigger = h("button", { type: "button", class: rejected ? "btn" : "btn danger", id: rejected ? "reopenBtn" : "rejectBtn", text: rejected ? "Reopen application" : "Reject application" });
      trigger.addEventListener("click", function () { promptOutcome(row, rejected ? "reopen" : "reject", trigger); });
      return h("section", { class: "drawer-card", "aria-labelledby": "dsOutcome" },
        h("h3", { id: "dsOutcome", text: rejected ? "Reopen this application" : "Reject this application" }),
        h("p", { class: "muted small", text: rejected ? "This application is closed. Reopen it to review it again." : "Only a person can reject an application. Nothing is rejected automatically." }),
        h("div", { class: "row" }, trigger)
      );
    }

    async function promptOutcome(row, kind, opener) {
      var id = row.application_id;
      var rejecting = kind === "reject";
      if (rejecting && row.outcome === "rejected") { C.status("This application is already rejected.", true); return; }
      if (!rejecting && row.outcome !== "rejected") { C.status("This application is not rejected.", true); return; }
      var reason = h("textarea", { id: "outReason", maxlength: "500", "aria-describedby": "outHint", placeholder: "Write why you are making this decision." });
      var hint = h("span", { id: "outHint", class: "hint", text: "0 of 500 characters. Use at least 10." });
      reason.addEventListener("input", function () { hint.textContent = reason.value.trim().length + " of 500 characters. Use at least 10."; });
      var result = null;
      var answer = await C.dialog({
        title: rejecting ? "Reject this application" : "Reopen this application",
        body: h("div", null,
          h("p", { class: "muted", text: rejecting ? "The candidate will see a polite closed message. They will not see your reason. You can reopen this later." : "The application goes back to your review list and the candidate sees their usual status again. Your reason is kept in the record." }),
          rejecting ? h("p", { class: "small muted", text: "Other applications from the same applicant for the same job will be closed too." }) : null,
          h("label", { class: "field", for: "outReason" }, "Reason", reason, hint)
        ),
        actions: [
          { label: "Cancel", value: null },
          {
            label: rejecting ? "Reject application" : "Reopen application",
            kind: rejecting ? "danger" : "primary",
            value: "go",
            validate: function () {
              var n = reason.value.trim().length;
              return n < 10 || n > 500 ? "The reason needs 10 to 500 characters." : null;
            },
            run: async function () {
              try {
                result = await C.api("POST", "/v1/decisions/" + encodeURIComponent(id) + "/" + kind, { json: { reason: reason.value.trim() } });
                return null;
              } catch (error) {
                if (C.authError(error)) return "Your session has ended. Please sign in again.";
                if (error.status === 409) return rejecting ? "This application is already rejected." : "This application is not rejected.";
                if (error.status === 404) return "This service cannot record that decision yet.";
                if (error.status === 403) return "Your account does not have permission to do that.";
                return C.friendly(error);
              }
            }
          }
        ]
      });
      if (answer !== "go") return;
      var at = result && result.at ? result.at : Math.floor(Date.now() / 1000);
      var by = (result && result.by) || (C.state.me && C.state.me.username);
      var delta = 0;
      var nextId = null;
      if (rejecting) {
        var visible = filtered();
        var at0 = visible.findIndex(function (r) { return r.application_id === id; });
        var near = at0 >= 0 ? (visible[at0 + 1] || visible[at0 - 1]) : null;
        nextId = near ? near.application_id : null;
        row.outcome = "rejected";
        row.outcome_by = by;
        row.outcome_at = at;
        S.outcomes[id] = { reason: (result && result.reason) || reason.value.trim(), by: by, at: at };
        delta = 1;
        var also = result && Array.isArray(result.also_rejected) ? result.also_rejected : [];
        also.forEach(function (otherId) {
          var other = rowById(otherId);
          if (other && !isRej(other)) {
            other.outcome = "rejected";
            other.outcome_by = "linked to an earlier rejection";
            other.outcome_at = at;
            delta += 1;
          }
        });
        if (also.length) {
          var message = "Rejected. " + also.length + (also.length === 1 ? " other application" : " other applications") + " from the same applicant for the same job " + (also.length === 1 ? "was" : "were") + " closed too.";
          showNotice(message, "Show them", showRejectedTab);
          C.status(message);
        } else {
          showNotice("");
          C.status((row.candidate_name || id) + " was rejected.");
        }
      } else {
        row.outcome = null;
        row.outcome_by = null;
        row.outcome_at = null;
        delete S.outcomes[id];
        delta = -1;
        if (sameApplicantElsewhere(row)) {
          var again = "Reopened. Other applications from the same applicant stay closed until you reopen them.";
          showNotice(again);
          C.status(again);
        } else {
          showNotice("");
          C.status((row.candidate_name || id) + " was reopened.");
        }
      }
      if (S.stats && Number.isFinite(Number(S.stats.rejected))) S.stats.rejected = Math.max(0, Number(S.stats.rejected) + delta);
      S.nextId = nextId;
      S.opener = null;
      redraw();
      if (S.selected === id && !drawer.hidden) {
        drawDetail(row);
        var title = C.$("detailTitle");
        if (title) title.focus();
      } else if (nextId) {
        var nextButton = list.querySelector('.queue-item[data-id="' + nextId + '"]');
        if (nextButton) nextButton.focus();
        S.nextId = null;
      }
      refreshQuiet();
    }

    async function refreshQuiet() {
      try {
        var results = await Promise.all([C.api("GET", "/v1/stats"), C.api("GET", "/v1/decisions?limit=200")]);
        S.stats = results[0];
        S.rows = listOf(results[1]);
        redraw();
        if (S.selected && !drawer.hidden && rowById(S.selected)) {
          var focusedId = document.activeElement && document.activeElement.id;
          drawDetail(rowById(S.selected));
          if (focusedId && C.$(focusedId)) C.$(focusedId).focus();
        }
      } catch (error) {
        if (C.authError(error)) return;
      }
    }

    function stale(id) {
      return S.selected !== id || drawer.hidden;
    }

    function httpsLink(value) {
      var url = String(value || "").trim();
      if (/^https:\/\/[^\s]+$/i.test(url)) return h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: url });
      return h("span", { text: url });
    }

    function requestStatusNode(r) {
      var answer = r.answer || r.answer_text || r.text || "";
      var waiting = !(r.answered || r.status === "answered" || answer);
      return waiting ? C.tag("Waiting", "", true) : h("blockquote", { class: "evidence", style: "white-space:pre-wrap", text: answer || "Answered" });
    }

    async function loadForm(id, body) {
      try {
        var data = await C.api("GET", "/v1/applications/" + encodeURIComponent(id) + "/form");
        if (stale(id)) return;
        C.clear(body);
        var f = (data && data.fields && typeof data.fields === "object") ? data.fields : (data || {});
        var kv = h("dl", { class: "kv" });
        FORM_LABELS.forEach(function (pair) {
          var value = f[pair[0]];
          if (Array.isArray(value)) value = value.join(pair[0] === "skills" ? ", " : "\n");
          if (value === undefined || value === null || String(value).trim() === "") return;
          var cell;
          if (/_url$/.test(pair[0])) cell = httpsLink(value);
          else if (pair[0] === "papers" || pair[0] === "certificate_ids") cell = h("span", { style: "white-space:pre-line", text: String(value) });
          else cell = h("span", { text: String(value) });
          kv.append(h("dt", { text: pair[1] }), h("dd", null, cell));
        });
        body.append(kv.children.length ? kv : h("p", { class: "muted", text: "The form has no typed details." }));
        var about = f.about_project || data.about_project;
        body.append(h("h4", { text: "About the work" }), about ? h("p", { style: "white-space:pre-wrap", text: about }) : h("p", { class: "muted", text: "Nothing was written." }));
        var notes = data.extra_detail_notes || data.extra_details || data.notes || [];
        notes = Array.isArray(notes) ? notes : (notes ? [notes] : []);
        body.append(h("h4", { text: "Extra detail notes from the candidate" }));
        if (!notes.length) body.append(h("p", { class: "muted", text: "The candidate has not added any notes." }));
        notes.forEach(function (note) {
          var textValue = typeof note === "string" ? note : (note.text || note.answer || "");
          var label = typeof note === "string" ? "" : (note.label || "");
          body.append(h("div", { class: "history-item" }, label ? h("p", { class: "small muted", style: "margin:0 0 4px", text: label }) : null, h("p", { style: "margin:0;white-space:pre-wrap", text: textValue })));
        });
        var requests = data.requests || data.follow_up || data.request_items || [];
        requests = Array.isArray(requests) ? requests.filter(function (r) { return !r.kind || r.kind === "request"; }) : [];
        body.append(askBlock(id, requests));
      } catch (error) {
        if (stale(id) || C.authError(error)) return;
        C.clear(body);
        body.append(h("p", { class: "muted", text: error.status === 404 ? "This applicant did not use the form." : C.friendly(error) }));
      }
    }

    function askBlock(id, requests) {
      var wrap = h("div", { class: "ask-block" });
      var input = h("input", { type: "text", id: "askLabel", maxlength: "200", placeholder: "For example Which part of the project did you build yourself", "aria-describedby": "askHint askMsg" });
      var hint = h("span", { class: "hint", id: "askHint", text: "Write between 3 and 200 characters." });
      var message = h("p", { class: "small", id: "askMsg", role: "status", "aria-live": "polite" });
      var send = h("button", { type: "button", class: "btn", id: "askSend", text: "Send request" });
      var list = h("ul", { class: "req-list", "aria-label": "Requests sent to the candidate" });
      function drawList() {
        C.clear(list);
        if (!requests.length) {
          list.append(h("li", { class: "muted", text: "No requests have been sent." }));
          return;
        }
        requests.forEach(function (r) {
          list.append(h("li", null, h("p", { style: "margin:0 0 4px;font-weight:600", text: r.label || "" }), requestStatusNode(r)));
        });
      }
      send.addEventListener("click", async function () {
        var label = input.value.trim();
        message.className = "small";
        if (label.length < 3 || label.length > 200) {
          message.textContent = "The request needs 3 to 200 characters.";
          message.className = "small err";
          input.focus();
          return;
        }
        send.disabled = true;
        message.textContent = "Sending...";
        try {
          var res = await C.api("POST", "/v1/applications/" + encodeURIComponent(id) + "/requests", { json: { label: label } });
          requests.push({ id: res && res.id, label: label, status: "waiting" });
          input.value = "";
          message.textContent = (res && res.message) || "The request was sent to the candidate.";
          drawList();
          var row = rowById(id);
          if (row) { row.follow_up_open = (Number(row.follow_up_open) || 0) + 1; redraw(); }
        } catch (error) {
          if (C.authError(error)) return;
          message.textContent = error.status === 400 || error.status === 404 || error.status === 422 || error.status === 429 ? error.message : C.friendly(error);
          message.className = "small err";
        } finally {
          send.disabled = false;
        }
      });
      drawList();
      wrap.append(h("h4", { text: "Ask the candidate for details" }), h("label", { class: "field", for: "askLabel" }, "What would you like to know", input, hint), h("div", { class: "row" }, send), message, list);
      return wrap;
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
              h("div", { class: "row" }, h("strong", { text: finding.source }), C.tag(finding.status === "verified" ? "Matches the resume" : String(finding.status || "").replace(/_/g, " "), FINDING_TONE[finding.status] || "info")),
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
      var closedRow = row.outcome === "rejected";
      var form = h("form", { class: "drawer-card override-form", id: "overrideForm", novalidate: true, "aria-labelledby": "dsOverride" },
        h("h3", { id: "dsOverride", text: "Override this decision" }),
        h("p", { class: "muted small", text: "The product never rejects anyone on its own. Your change is saved with your name in the audit log." }),
        h("label", { class: "field", for: "ovRoute" }, "New route", select),
        h("label", { class: "field", for: "ovReason" }, "Reason", reason, hint),
        message,
        h("div", { class: "row" }, submit, manual)
      );
      if (closedRow) {
        select.disabled = true;
        reason.disabled = true;
        submit.disabled = true;
        manual.disabled = true;
        message.textContent = "Reopen the application first.";
      }
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
      } else if (event.key === "x") {
        var pick = index >= 0 ? rowById(all[index].dataset.id) : rowById(S.selected);
        if (pick) {
          event.preventDefault();
          if (isRej(pick)) { C.status("This application is already rejected.", true); return; }
          promptOutcome(pick, "reject", index >= 0 ? all[index] : null);
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
      if (S.tab === "destinations") loadDest();
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
