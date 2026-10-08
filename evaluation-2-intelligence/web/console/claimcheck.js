(function () {
  var C = (window.C = window.C || {});
  var h = C.h;

  var VERDICT = {
    "SUPPORTED": { text: "Supported", tone: "ok" },
    "PARTIALLY SUPPORTED": { text: "Partly supported", tone: "warn" },
    "NOT SUPPORTED": { text: "Not supported", tone: "danger" }
  };
  var LEVEL_TONE = { HIGH: "ok", MEDIUM: "warn", LOW: "danger" };
  var LEVEL_TEXT = { HIGH: "high", MEDIUM: "medium", LOW: "low" };
  var SPEC_TONE = { HIGH: "ok", MEDIUM: "warn", LOW: "danger" };

  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  async function call(method, path, json) {
    var tries = 0;
    while (true) {
      try {
        return await C.api(method, path, json === undefined ? undefined : { json: json });
      } catch (error) {
        tries += 1;
        if (!(error && error.network) || tries >= 4) throw error;
        await wait(1500);
      }
    }
  }

  function chip(label, level, tones) {
    var key = String(level || "").toUpperCase();
    return C.tag(label + " " + (LEVEL_TEXT[key] || "not rated"), (tones || LEVEL_TONE)[key] || "info", true);
  }

  function chipRow(r) {
    return h("div", { class: "row claim-chips" },
      chip("Relevance", r.relevance),
      chip("Consistency", r.consistency, { HIGH: "ok", LOW: "danger" }),
      chip("Specificity", r.specificity, SPEC_TONE)
    );
  }

  function reasonList(reasons) {
    var list = Array.isArray(reasons) ? reasons : [];
    if (!list.length) return null;
    return h("ul", { class: "claim-reasons" }, list.map(function (item) {
      return h("li", { class: item.ok ? "ok" : "warn" },
        h("span", { class: "claim-mark", "aria-hidden": "true", text: item.ok ? "✓" : "!" }),
        h("div", null,
          h("strong", { text: item.title || "" }),
          h("span", { class: "sr-only", text: item.ok ? " Looks good." : " Worth a look." }),
          item.explanation ? h("p", { class: "small muted", text: item.explanation }) : null
        )
      );
    }));
  }

  function verdictTag(r) {
    var v = VERDICT[String(r.verification_result || "").toUpperCase()];
    return v ? C.tag(v.text, v.tone) : null;
  }

  function questionBlock(claim, question) {
    return [
      claim ? h("p", { class: "claim-name" }, h("span", { class: "small muted", text: "Skill or claim" }), h("strong", { text: claim })) : null,
      question ? h("div", { class: "claim-question", text: question }) : null
    ];
  }

  C.claim = {};


  C.claim.recruiterLoad = async function (applicationId, body, isStale) {
    try {
      var r = await call("GET", "/v1/claims/application/" + encodeURIComponent(applicationId));
      if (isStale && isStale()) return;
      C.clear(body);
      var scores = h("dl", { class: "claim-scores" });
      [["Answer score", r.answer_score], ["Base score", r.base_score], ["Combined score", r.trust_score]].forEach(function (pair) {
        if (pair[1] === undefined || pair[1] === null) return;
        scores.append(h("div", null, h("dt", { class: "small muted", text: pair[0] }), h("dd", { text: String(pair[1]) })));
      });
      var terms = Array.isArray(r.evidence_terms_found) ? r.evidence_terms_found : [];
      C.append(body, [
        questionBlock(r.claim, r.question),
        h("p", { class: "small muted", text: "The candidate's answer is not stored. Only the result is kept." }),
        h("div", { class: "row" }, h("strong", { text: "Result" }), verdictTag(r)),
        chipRow(r),
        scores.children.length ? scores : null,
        r.summary ? h("p", { text: r.summary }) : null,
        reasonList(r.reasons),
        h("h4", { text: "Evidence terms found" }),
        terms.length
          ? h("div", { class: "row claim-terms" }, terms.map(function (t) { return h("span", { class: "claim-term", text: t }); }))
          : h("p", { class: "small muted", text: "No evidence terms were found in the answer." }),
        h("p", { class: "small muted claim-advice", text: "This is advice for a person. It never changes the decision by itself." })
      ]);
    } catch (error) {
      if (isStale && isStale()) return;
      C.clear(body);
      body.append(
        h("p", { class: "muted", text: error && error.status === 404 ? "The candidate has not answered yet." : "The answer check could not be loaded." }),
        h("p", { class: "small muted claim-advice", text: "This is advice for a person. It never changes the decision by itself." })
      );
    }
  };

  var SRC_ORDER = ["GitHub", "Research papers", "Employer website", "Name and email", "Role specific checks", "Other check"];
  var STATUS = {
    confirmed: { text: "Confirmed", cls: "ok", mark: "✓", tone: "ok" },
    problem: { text: "Needs a closer look", cls: "warn", mark: "!", tone: "warn" },
    not_checked: { text: "Could not be checked", cls: "idle", mark: "?", tone: "info" }
  };

  function httpsOnly(url) {
    return typeof url === "string" && /^https:\/\//i.test(url);
  }


  function plainDate(value) {
    if (!value) return "Not known";
    var d = new Date(typeof value === "number" ? value * 1000 : value);
    if (isNaN(d.getTime())) return "Not known";
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function ageText(value) {
    if (!value) return "Not known";
    var d = new Date(typeof value === "number" ? value * 1000 : value);
    if (isNaN(d.getTime())) return "Not known";
    var months = Math.max(0, Math.floor((Date.now() - d.getTime()) / (30.44 * 86400000)));
    var years = Math.floor(months / 12);
    var rest = months % 12;
    if (years < 1) return rest <= 1 ? "Less than a month old" : rest + " months old";
    return years + (years === 1 ? " year" : " years") + (rest ? " and " + rest + (rest === 1 ? " month" : " months") : "") + " old";
  }

  function githubCard(g) {
    var card = h("section", { class: "gh-card", id: "ghEvidence", "aria-labelledby": "ghTitle" });
    var name = g.username ? String(g.username) : "GitHub";
    var link = httpsOnly(g.profile_url) ? h("a", { href: g.profile_url, target: "_blank", rel: "noopener noreferrer", text: name }) : h("span", { text: name });
    card.append(h("h4", { id: "ghTitle", class: "gh-title" }, "GitHub evidence for ", link));
    if (g.rate_limited) {
      card.append(h("p", { class: "gh-note", text: "GitHub asked us to slow down. Ask an admin to add a GitHub token." }));
      return card;
    }
    if (g.found === false) {
      card.append(h("p", { class: "gh-note", text: "The GitHub account in the resume was not found." }));
      return card;
    }
    var active = g.active_last_year;
    var original = Number(g.original_repos);
    var copies = Number(g.fork_repos);
    card.append(
      h("div", { class: "row gh-facts" },
        h("span", { text: "Account " + ageText(g.account_created) }),
        h("span", { text: (g.public_repos === undefined || g.public_repos === null ? "Unknown" : g.public_repos) + " public repositories" + (isFinite(original) && isFinite(copies) ? ", " + original + " original and " + copies + " copied" : "") }),
        h("span", { text: (g.followers === undefined || g.followers === null ? "Unknown" : g.followers) + (Number(g.followers) === 1 ? " follower" : " followers") }),
        active === undefined || active === null ? null : C.tag(active ? "Active in the last year" : "No activity in the last year", "info", true)
      ),
      g.last_pushed ? h("p", { class: "small muted", text: "Last public activity " + plainDate(g.last_pushed) }) : null
    );
    var langs = Array.isArray(g.top_languages) ? g.top_languages : [];
    if (langs.length) {
      card.append(h("h5", { class: "gh-sub", text: "Languages" }), h("ul", { class: "lang-bars" }, langs.map(function (l) {
        var pct = Math.max(0, Math.min(100, Number(l.share_percent) || 0));
        var fill = h("span", { class: "lang-fill" });
        fill.style.width = Math.max(2, pct) + "%";
        return h("li", null,
          h("span", { class: "lang-name", text: l.language }),
          h("span", { class: "lang-track", role: "img", "aria-label": l.language + " " + Math.round(pct) + " percent" }, fill),
          h("span", { class: "lang-pct", text: Math.round(pct) + "%" })
        );
      })));
    }
    var repos = Array.isArray(g.repositories) ? g.repositories : [];
    card.append(h("h5", { class: "gh-sub", text: "Repositories" }));
    if (!repos.length) {
      card.append(h("p", { class: "small muted", text: "No repositories were listed." }));
    } else {
      var rows = repos.map(function (r) {
        var nameCell = h("td", null,
          httpsOnly(r.url) ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.name }) : h("strong", { text: r.name }),
          h("div", { class: "row gh-tags" },
            r.claimed ? C.tag("Claimed in resume", "info", true) : null,
            r.fork ? C.tag("Copy of another project" + (r.parent ? " " + r.parent : ""), "", true) : null
          )
        );
        var langCell = h("td", null, h("div", { class: "row gh-langs" }, (Array.isArray(r.languages) ? r.languages : []).map(function (l) {
          return h("span", { class: "claim-term", text: l.language + " " + Math.round(Number(l.percent) || 0) + "%" });
        })));
        return h("tr", null,
          nameCell,
          h("td", { text: r.commits_by_you === null || r.commits_by_you === undefined ? "Could not be counted" : String(r.commits_by_you) }),
          h("td", { text: plainDate(r.first_commit) }),
          h("td", { text: plainDate(r.last_commit) }),
          langCell,
          h("td", { class: "num", text: String(r.stars === undefined || r.stars === null ? 0 : r.stars) })
        );
      });
      card.append(h("div", { class: "table-wrap" }, h("table", { class: "gh-table" },
        h("caption", { class: "sr-only", text: "Repositories on the GitHub account" }),
        h("thead", null, h("tr", null, ["Repository", "Commits by this account", "First commit", "Last commit", "Languages", "Stars"].map(function (t) { return h("th", { scope: "col", text: t }); }))),
        h("tbody", null, rows)
      )));
    }
    var skills = Array.isArray(g.skills) ? g.skills : [];
    if (skills.length) {
      card.append(h("h5", { class: "gh-sub", text: "Skills against code" }), h("ul", { class: "skill-chips" }, skills.map(function (s) {
        return h("li", { class: s.found ? "seen" : "unseen" },
          h("strong", { text: s.skill }),
          h("span", { class: "small", text: s.found ? "Seen in public code" : "Not seen in public code" }),
          s.found && s.repos ? h("span", { class: "small muted", text: s.repos + (Number(s.repos) === 1 ? " repository" : " repositories") }) : null
        );
      })), h("p", { class: "small muted", text: "Work may be private. This is never held against anyone alone." }));
    }
    if (g.checked_at) card.append(h("p", { class: "small muted", text: "Looked up on " + plainDate(g.checked_at) }));
    return card;
  }

  function checkRow(c) {
    var st = STATUS[c.status] || STATUS.not_checked;
    return h("li", { class: st.cls },
      h("span", { class: "claim-mark", "aria-hidden": "true", text: st.mark }),
      h("div", null,
        h("div", { class: "row check-head" },
          h("strong", { text: st.text }),
          c.title && c.title !== st.text ? h("span", { class: "small muted", text: c.title }) : null
        ),
        c.claim ? h("p", { class: "small muted", text: "Claim in your resume " + c.claim }) : null,
        c.explanation ? h("p", { class: "small", text: c.explanation }) : null,
        httpsOnly(c.evidence_url) ? h("p", { class: "small" }, h("a", { href: c.evidence_url, target: "_blank", rel: "noopener noreferrer", text: "See the source" })) : null
      )
    );
  }

  function checkResult(r, who) {
    var counts = r.counts || {};
    var list = Array.isArray(r.checks) ? r.checks : [];
    var groups = {};
    list.forEach(function (c) {
      var key = c.source || "Other check";
      (groups[key] = groups[key] || []).push(c);
    });
    var keys = SRC_ORDER.filter(function (k) { return groups[k]; }).concat(Object.keys(groups).filter(function (k) { return SRC_ORDER.indexOf(k) < 0; }));
    var ranAt = r.ran_at ? new Date(Number(r.ran_at) * 1000).toLocaleString() : "";
    var out = [
      r.github && typeof r.github === "object" ? githubCard(r.github) : null,
      h("div", { class: "row claim-chips" },
        C.tag((counts.confirmed || 0) + " confirmed", "ok", true),
        C.tag((counts.problem || 0) + " needs a closer look", "warn", true),
        C.tag((counts.not_checked || 0) + " could not be checked", "info", true),
        r.role_label ? C.tag(r.role_label, "info") : null
      ),
      ranAt ? h("p", { class: "small muted", text: "Checked on " + ranAt }) : null
    ];
    if (r.github_found === false) {
      out.push(h("p", { class: "check-tip", text: "No GitHub link was found in this resume." }));
    }
    if (!list.length) out.push(h("p", { class: "muted", text: "There was nothing to check." }));
    keys.forEach(function (k) {
      out.push(h("h4", { class: "check-group", text: k }));
      out.push(h("ul", { class: "claim-reasons" }, groups[k].map(checkRow)));
    });
    if (Array.isArray(r.slow_sources) && r.slow_sources.length) {
      out.push(h("p", { class: "small muted", text: "These sources were slow to answer and may be missing " + r.slow_sources.join(" and ") + "." }));
    }
    if (r.note) out.push(h("p", { class: "small muted", text: r.note }));
    return out;
  }

  C.checks = {};

  C.checks.panel = function (applicationId, opts) {
    var o = opts || {};
    var who = "recruiter";
    var title = "Claim checks";
    var heading = title;
    var uid = "rc" + (o.readOnly ? "ro" : "");
    var box = o.bare ? h("div", { class: "check-panel" }) : h("div", { class: "card check-card", "aria-labelledby": uid + "Title" });
    var resultBox = h("div", { class: "check-result", "aria-live": "polite" });
    var errorBox = h("p", { class: "field-error", role: "alert", hidden: true });
    var progress = h("p", { class: "small muted", role: "status", hidden: true });
    var button = null;
    var hasResult = false;
    var busy = false;

    if (!o.readOnly) {
      button = h("button", { type: "button", class: "btn", id: uid + "Run", text: "Run checks" });
      button.addEventListener("click", run);
    }

    function setBusy(on) {
      busy = on;
      if (button) {
        button.disabled = on;
        if (on) button.setAttribute("aria-busy", "true"); else button.removeAttribute("aria-busy");
        button.textContent = on ? "Running..." : (hasResult ? "Run again" : "Run checks");
      }
      progress.hidden = !on;
      progress.textContent = on ? "Running the checks. This can take up to 15 seconds." : "";
    }

    function show(r) {
      hasResult = true;
      C.clear(resultBox);
      C.append(resultBox, checkResult(r, who));
      if (button && !busy) button.textContent = "Run again";
    }

    async function run() {
      if (busy) return;
      errorBox.hidden = true;
      setBusy(true);
      try {
        var r = await call("POST", "/v1/checks/run", { application_id: applicationId });
        if (isStale()) return;
        setBusy(false);
        show(r);
        C.status("The claim checks are ready.");
      } catch (error) {
        if (C.authError && C.authError(error)) return;
        setBusy(false);
        errorBox.textContent = error && (error.status === 429 || error.status === 404) && error.message ? error.message : C.friendly(error);
        errorBox.hidden = false;
      }
    }

    function isStale() {
      return typeof o.isStale === "function" && o.isStale();
    }

    var head = [];
    if (!o.bare) head.push(h("h3", { id: uid + "Title", text: heading }));
    head.push(h("p", { class: "small muted", text: "This looks at the links and facts in the resume and compares them with public sources." }));
    C.append(box, head);
    if (button) box.append(h("div", { class: "row" }, button));
    box.append(progress, errorBox, resultBox);
    box.append(h("p", { class: "small muted claim-advice", text: "This is advice for a person. It never changes the decision by itself." }));

    (async function () {
      try {
        var r = await call("GET", "/v1/checks/application/" + encodeURIComponent(applicationId));
        if (isStale()) return;
        show(r);
      } catch (error) {
        if (error && error.status === 404) {
          if (o.readOnly) resultBox.append(h("p", { class: "muted", text: "These checks have not been run yet." }));
          return;
        }
        if (C.authError && C.authError(error)) return;
        if (o.readOnly) resultBox.append(h("p", { class: "muted", text: "The saved checks could not be loaded." }));
      }
    })();

    return box;
  };
})();
