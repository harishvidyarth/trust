(function () {
  var C = (window.C = window.C || {});
  C.views = {};
  C.keys = null;

  C.$ = function (id) {
    return document.getElementById(id);
  };

  C.h = function (tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === undefined || value === null || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.indexOf("on") === 0 && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (value === true) node.setAttribute(key, "");
        else node.setAttribute(key, value);
      });
    }
    for (var i = 2; i < arguments.length; i++) C.append(node, arguments[i]);
    return node;
  };

  C.append = function (node, child) {
    if (child === undefined || child === null || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(function (item) {
        C.append(node, item);
      });
    } else if (typeof child === "string" || typeof child === "number") {
      node.appendChild(document.createTextNode(String(child)));
    } else {
      node.appendChild(child);
    }
  };

  C.clear = function (node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  };

  var h = C.h;

  C.status = function (message, isError) {
    var node = C.$("status");
    node.textContent = message || "";
    node.className = isError ? "status error" : "status";
  };

  C.ROUTES = { PASS_TO_ATS: "Pass to ATS", ADDITIONAL_VERIFICATION: "Additional verification", MANUAL_REVIEW: "Manual review" };
  C.ROUTE_TONE = { PASS_TO_ATS: "ok", ADDITIONAL_VERIFICATION: "warn", MANUAL_REVIEW: "danger" };
  C.SEV_TONE = { critical: "danger", high: "danger", medium: "warn", low: "info", info: "info" };

  C.tag = function (text, tone, plain) {
    return h("span", { class: "tag " + (tone || "") + (plain ? " plain" : ""), text: text });
  };
  C.SEV_TITLE = { critical: "Serious concern", high: "Serious concern", medium: "Concern worth checking", low: "Small concern", info: "Good to know" };

  C.reasonCard = function (reason) {
    var evidence = reason.evidence || reason.snippet || "";
    var text = reason.explanation || C.WHY[reason.code] || reason.detail || "";
    var title = C.SEV_TITLE[reason.severity] || "Concern";
    return h("div", { class: "reason sev-" + reason.severity },
      h("div", { class: "reason-head" }, h("strong", { text: title }), C.tag(reason.severity, C.SEV_TONE[reason.severity] || "")),
      h("p", { class: "reason-text", text: text }),
      h("details", { class: "reason-more" },
        h("summary", { class: "small", text: "Evidence" }),
        evidence ? h("p", { class: "small label-line", text: "Exact words from the resume" }) : null,
        evidence ? h("blockquote", { class: "evidence", text: evidence }) : null,
        reason.detail ? h("p", { class: "small label-line", text: "Technical detail" }) : null,
        reason.detail ? h("p", { class: "small mono tech-detail", text: reason.detail }) : null,
        h("p", { class: "small muted meta-line" }, h("code", { text: reason.code }), " with weight " + reason.weight)
      )
    );
  };

  C.routeTag = function (route) {
    return C.tag(C.ROUTES[route] || route, C.ROUTE_TONE[route] || "");
  };

  C.fmtTime = function (seconds) {
    if (!seconds) return "none";
    var date = new Date(seconds * 1000);
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  C.safeStore = {
    get: function (key) {
      try {
        return localStorage.getItem(key);
      } catch (error) {
        return null;
      }
    },
    set: function (key, value) {
      try {
        localStorage.setItem(key, value);
      } catch (error) {
        return;
      }
    }
  };

  function effectiveTheme() {
    var attr = document.documentElement.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeButton() {
    var dark = effectiveTheme() === "dark";
    var button = C.$("themeBtn");
    if (!button) return;
    button.textContent = dark ? "Dark" : "Light";
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.setAttribute("aria-label", "Dark theme, currently " + (dark ? "on" : "off"));
  }
  C.toggleTheme = function () {
    var next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    C.safeStore.set("console-theme", next);
    syncThemeButton();
  };
  C.syncThemeButton = syncThemeButton;

  var lastFocus = null;
  C.dialog = function (config) {
    var dialog = C.$("dialog");
    lastFocus = document.activeElement;
    C.clear(dialog);
    var errorBox = h("p", { class: "alert error", role: "alert", hidden: true });
    var settled = false;
    return new Promise(function (resolve) {
      function finish(value) {
        if (settled) return;
        settled = true;
        dialog.close();
        dialog.onclose = null;
        if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
        resolve(value);
      }
      var actions = h("div", { class: "actions" });
      config.actions.forEach(function (action) {
        actions.appendChild(
          h("button", {
            type: "button",
            class: "btn " + (action.kind || ""),
            text: action.label,
            onclick: function () {
              if (action.validate) {
                var problem = action.validate();
                if (problem) {
                  errorBox.textContent = problem;
                  errorBox.hidden = false;
                  return;
                }
              }
              finish(action.value === undefined ? true : action.value);
            }
          })
        );
      });
      dialog.append(h("h2", { id: "dialogTitle", text: config.title }), config.body, errorBox, actions);
      dialog.onclose = function () {
        if (!settled) {
          settled = true;
          if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
          resolve(null);
        }
      };
      dialog.showModal();
      var first = dialog.querySelector("[autofocus], input, select, textarea");
      (first || dialog.querySelector("button")).focus();
    });
  };

  C.showHelp = function () {
    var rows = [
      ["?", "Open this help"],
      ["j / k", "Next or previous application in the queue"],
      ["Enter", "Open the highlighted application"],
      ["o", "Override the open decision"],
      ["/", "Search the queue"],
      ["Esc", "Close the detail panel or a dialog"]
    ];
    var table = h("table", null, h("tbody", null, rows.map(function (row) {
      return h("tr", null, h("td", null, h("kbd", { text: row[0] })), h("td", { text: row[1] }));
    })));
    C.dialog({ title: "Keyboard shortcuts", body: table, actions: [{ label: "Close", kind: "primary", value: true }] });
  };

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    var target = event.target;
    var typing = target && target.matches && target.matches("input, textarea, select, [contenteditable]");
    if (typing) return;
    if (event.key === "?") {
      event.preventDefault();
      C.showHelp();
    } else if (C.keys) {
      C.keys(event);
    }
  });

  C.renderBanner = function () {
    var banner = C.$("demoBanner");
    C.clear(banner);
    if (!C.state.demo) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    banner.append(h("span", { text: "Demo data" }), h("span", { class: "muted", text: C.state.demoReason || "Sample data only. No server is contacted." }));
    if (C.state.me) {
      var select = h("select", { id: "demoRole", "aria-label": "View as role" }, C.demo.users.map(function (role) {
        return h("option", { value: role, selected: role === C.state.me.username, text: role });
      }));
      select.addEventListener("change", function () {
        C.login(select.value, C.demo.password);
      });
      banner.append(h("label", null, "View as", select));
    }
    if (C.state.demoReason) banner.append(h("button", { type: "button", class: "btn small", text: "Retry", onclick: function () { location.reload(); } }));
    banner.append(h("button", { type: "button", class: "btn small", text: "Exit demo", onclick: C.exitDemo }));
  };

  C.exitDemo = function () {
    C.state.demo = false;
    C.state.demoReason = "";
    C.state.me = null;
    C.state.csrf = null;
    var url = new URL(location.href);
    url.searchParams.delete("demo");
    url.searchParams.delete("as");
    history.replaceState(null, "", url.toString());
    C.renderBanner();
    C.renderShell();
    C.showLogin("");
  };

  C.enterDemo = function (reason) {
    C.state.demo = true;
    C.state.demoReason = reason || "";
    C.renderBanner();
  };

  var NAV = {
    candidate: [["candidate", "New application"], ["myapps", "My applications"]],
    recruiter: [["recruiter", "Applications"]],
    admin: [["admin", "Admin"], ["recruiter", "Applications"]]
  };

  C.renderShell = function () {
    var nav = C.$("nav");
    var who = C.$("who");
    C.clear(nav);
    C.clear(who);
    C.syncThemeButton();
    if (!C.state.me) {
      nav.hidden = true;
      if (C.state.demo) who.append(h("button", { type: "button", class: "btn small", id: "exitDemoTop", text: "Exit demo", onclick: C.exitDemo }));
      return;
    }
    nav.hidden = false;
    (NAV[C.state.me.role] || []).forEach(function (item) {
      var button = h("button", { type: "button", text: item[1], "data-view": item[0] });
      if (C.state.view === item[0]) button.setAttribute("aria-current", "page");
      button.addEventListener("click", function () {
        C.go(item[0]);
      });
      nav.appendChild(button);
    });
    if (C.state.me.role === "candidate") who.append(h("span", { class: "summary-chip", id: "summaryChip", hidden: true }));
    who.append(
      h("span", { class: "who-name", text: "Signed in as " + C.state.me.username + " (" + C.state.me.role + ")" }),
      C.state.demo
        ? h("button", { type: "button", class: "btn small", id: "exitDemoTop", text: "Exit demo", onclick: C.exitDemo })
        : h("button", { type: "button", class: "btn small", id: "signOutBtn", text: "Sign out", onclick: C.logout })
    );
  };

  C.refreshSummary = async function () {
    var chip = C.$("summaryChip");
    if (!chip || !C.state.me || C.state.me.role !== "candidate") return;
    try {
      var data = await C.api("GET", "/v1/me/summary");
      var node = C.$("summaryChip");
      if (!node) return;
      if (data && data.best_score !== null && data.best_score !== undefined && Number(data.applications) > 0) {
        node.textContent = "Best score " + Math.round(Number(data.best_score));
        node.title = Number(data.applications) + (Number(data.applications) === 1 ? " saved application" : " saved applications");
        node.hidden = false;
      } else {
        node.hidden = true;
      }
    } catch (error) {
      var gone = C.$("summaryChip");
      if (gone) gone.hidden = true;
    }
  };

  C.go = function (view) {
    C.state.view = view;
    C.keys = null;
    var root = C.$("view");
    C.clear(root);
    C.status("");
    C.renderShell();
    C.refreshSummary();
    C.views[view](root);
    document.title = "TR∩ST";
  };

  C.homeFor = function (role) {
    if (location.hash === "#recruiter" && (role === "recruiter" || role === "admin")) return "recruiter";
    return role === "admin" ? "admin" : role === "recruiter" ? "recruiter" : "candidate";
  };

  C.authError = function (error) {
    if (error && error.status === 401 && C.state.me) {
      C.state.lastUser = C.state.me.username;
      C.state.me = null;
      C.state.csrf = null;
      C.renderShell();
      C.showLogin("Your session has ended. Please sign in again.");
      return true;
    }
    return false;
  };

  C.accessDenied = function (root, error) {
    C.clear(root);
    root.append(
      h("div", { class: "card login-wrap" },
        h("h1", { text: "No access to this page" }),
        h("p", { class: "muted", text: C.friendly(error) }),
        h("button", { type: "button", class: "btn primary", text: "Back to my home", onclick: function () { C.go(C.homeFor(C.state.me.role)); } })
      )
    );
  };

  C.login = async function (username, password) {
    C.state.lastUser = username;
    C.status("Signing in...");
    try {
      var data = await C.api("POST", "/v1/auth/login", { json: { username: username, password: password } });
      C.state.me = { username: data.username, role: data.role };
      C.state.csrf = data.csrf_token || data.csrf || null;
      C.renderBanner();
      C.go(C.homeFor(data.role));
      C.status("Signed in as " + data.username + ".");
      return true;
    } catch (error) {
      C.status("", false);
      if (error.network && !C.state.demo) {
        C.showLogin(C.friendly(error), true);
      } else {
        C.showLogin(C.friendly(error));
      }
      return false;
    }
  };

  C.logout = async function () {
    try {
      await C.api("POST", "/v1/auth/logout", { json: {} });
    } catch (error) {
      C.status("Signed out locally; the server could not be reached.", false);
    }
    C.state.me = null;
    C.state.csrf = null;
    C.renderBanner();
    C.renderShell();
    C.showLogin("");
    C.status("You have been signed out.");
  };

  C.showLogin = function (message, offerDemo) {
    var root = C.$("view");
    C.clear(root);
    C.keys = null;
    var mode = "signin";
    var card = h("div", { class: "card" });
    var wrap = h("div", { class: "login-wrap" }, card);
    root.appendChild(wrap);

    function draw() {
      C.clear(card);
      var tabs = h("div", { class: "tabs-inline", role: "tablist", "aria-label": "Account" },
        h("button", { type: "button", role: "tab", id: "tabSignin", "aria-selected": mode === "signin" ? "true" : "false", text: "Sign in" }),
        h("button", { type: "button", role: "tab", id: "tabSignup", "aria-selected": mode === "signup" ? "true" : "false", text: "Create candidate account" })
      );
      tabs.querySelector("#tabSignin").addEventListener("click", function () { mode = "signin"; draw(); });
      tabs.querySelector("#tabSignup").addEventListener("click", function () { mode = "signup"; draw(); });
      tabs.addEventListener("keydown", function (event) {
        if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
          mode = mode === "signin" ? "signup" : "signin";
          draw();
          C.$(mode === "signin" ? "tabSignin" : "tabSignup").focus();
        }
      });
      var alertBox = h("div", { id: "loginAlert", role: "alert" });
      if (message) {
        alertBox.appendChild(h("p", { class: "alert error", text: message }));
        if (offerDemo) {
          alertBox.appendChild(
            h("p", null, h("button", { type: "button", class: "btn", text: "Use demo data instead", onclick: function () {
              C.enterDemo("The service could not be reached, so this is sample data.");
              message = "";
              draw();
            } }))
          );
        }
      }
      var user = h("input", { type: "text", id: "lgUser", value: C.state.lastUser || "", autocomplete: "username", required: true, spellcheck: "false", autocapitalize: "none" });
      var pass = h("input", { type: "password", id: "lgPass", autocomplete: mode === "signin" ? "current-password" : "new-password", required: true });
      var form = h("form", { novalidate: true },
        h("h1", { text: mode === "signin" ? "Sign in" : "Create a candidate account" }),
        h("p", { class: "muted", text: mode === "signin" ? "Candidates, recruiters and admins sign in here." : "Candidate accounts let you check your resume and see why a decision was made." }),
        alertBox,
        h("label", { class: "field", for: "lgUser" }, "Username", user),
        h("label", { class: "field", for: "lgPass" }, "Password", pass, mode === "signup" ? h("span", { class: "hint", text: "At least 8 characters." }) : null),
        h("button", { type: "submit", class: "btn primary", style: "width:100%", text: mode === "signin" ? "Sign in" : "Create account" })
      );
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (!user.value.trim() || !pass.value) {
          alertBox.textContent = "";
          alertBox.appendChild(h("p", { class: "alert error", text: "Enter a username and a password." }));
          (user.value.trim() ? pass : user).focus();
          return;
        }
        if (mode === "signup") {
          try {
            await C.api("POST", "/v1/auth/register", { json: { username: user.value.trim(), password: pass.value } });
            await C.login(user.value.trim(), pass.value);
          } catch (error) {
            alertBox.textContent = "";
            alertBox.appendChild(h("p", { class: "alert error", text: C.friendly(error) }));
          }
          return;
        }
        C.login(user.value.trim(), pass.value);
      });
      var demoToggle = h("label", { class: "check" },
        h("input", { type: "checkbox", id: "demoToggle", checked: C.state.demo }),
        h("span", null, "Use demo data. Everything on the screen is sample data and no server is contacted.")
      );
      demoToggle.querySelector("input").addEventListener("change", function (event) {
        if (event.target.checked) C.enterDemo("");
        else {
          C.state.demo = false;
          C.state.demoReason = "";
          C.renderBanner();
        }
        message = "";
        draw();
      });
      card.append(tabs, form, h("div", { style: "margin-top:16px" }, demoToggle));
      if (C.state.demo) {
        card.append(
          h("div", { class: "demo-creds" },
            h("strong", { text: "Demo sign-ins" }),
            h("p", { class: "small", style: "margin:4px 0 8px" }, "Password for all: ", h("code", { text: C.demo.password })),
            h("div", { class: "row" }, C.demo.users.map(function (role) {
              return h("button", { type: "button", class: "btn small", text: "Sign in as " + role, onclick: function () { C.login(role, C.demo.password); } });
            }))
          )
        );
      }
    }
    draw();
    C.$("main").focus();
  };
})();
