(function () {
  var C = window.C;
  var h = C.h;

  function label(key) {
    return key.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }
  function valueNode(key, value) {
    if (value === null || value === undefined) return "none";
    if (typeof value === "number" && /(_at|ts|time)$/.test(key) && value > 1000000000) return C.fmtTime(value);
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) return value.length ? value.map(function (v) { return typeof v === "object" && v ? plain(v) : String(v); }).join(", ") : "none";
    if (typeof value === "object") return plain(value);
    return String(value);
  }
  function plain(obj) {
    var keys = Object.keys(obj);
    if (!keys.length) return "none";
    return keys.map(function (k) {
      var v = obj[k];
      var name = C.ROUTES[k] || label(k);
      if (typeof v === "boolean") v = v ? "yes" : "no";
      else if (Array.isArray(v)) v = v.length ? v.join(", ") : "none";
      else if (v && typeof v === "object") v = plain(v);
      return name + ": " + v;
    }).join("; ");
  }
  function kv(data) {
    var dl = h("dl", { class: "kv" });
    Object.keys(data || {}).forEach(function (key) {
      var value = data[key];
      if (Array.isArray(value) && value.length && typeof value[0] === "object") {
        dl.append(h("dt", { text: label(key) }), h("dd", null, value.map(function (item) {
          return h("div", null, Object.keys(item).map(function (k) { return k + ": " + valueNode(k, item[k]); }).join(", "));
        })));
      } else {
        var text = valueNode(key, value);
        var tone = key === "status" ? (text === "ok" || text === "healthy" ? "ok" : "warn") : null;
        dl.append(h("dt", { text: label(key) }), h("dd", null, tone ? C.tag(text, tone) : text));
      }
    });
    return dl;
  }

  C.views.admin = function (root) {
    var S = { tab: C.params && C.params.tab ? C.params.tab : "overview", users: [], audit: [] };
    if (C.params) C.params.tab = null;
    var tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Admin sections" });
    var panel = h("section", { id: "adminPanel", role: "tabpanel", tabindex: "-1" });
    root.append(h("h1", { text: "Administration" }), tabs, panel);

    var TABS = [["overview", "Overview"], ["users", "Users"], ["audit", "Audit log"]];
    function drawTabs() {
      C.clear(tabs);
      TABS.forEach(function (tab, i) {
        var button = h("button", { type: "button", role: "tab", id: "tab-" + tab[0], "aria-selected": S.tab === tab[0] ? "true" : "false", "aria-controls": "adminPanel", tabindex: S.tab === tab[0] ? "0" : "-1", text: tab[1] });
        button.addEventListener("click", function () { select(tab[0]); });
        button.addEventListener("keydown", function (event) {
          var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          if (!step) return;
          event.preventDefault();
          select(TABS[(i + step + TABS.length) % TABS.length][0], true);
        });
        tabs.appendChild(button);
      });
    }
    async function select(name, focusTab) {
      S.tab = name;
      drawTabs();
      if (focusTab) C.$("tab-" + name).focus();
      C.clear(panel);
      panel.setAttribute("aria-labelledby", "tab-" + name);
      C.status("Loading...");
      try {
        if (name === "overview") await overview();
        else if (name === "users") await users();
        else await audit();
        C.status("");
      } catch (error) {
        if (C.authError(error)) return;
        C.status("", false);
        panel.append(h("div", { class: "alert error", role: "alert", text: C.friendly(error) }));
      }
    }

    async function overview() {
      var results = await Promise.allSettled([C.api("GET", "/healthz"), C.api("GET", "/v1/stats"), C.api("GET", "/v1/delivery/status")]);
      results.forEach(function (res) {
        if (res.status === "rejected" && res.reason && res.reason.status === 401) throw res.reason;
      });
      function card(title, res) {
        var body;
        if (res.status === "fulfilled") body = kv(res.value);
        else body = h("p", { class: "alert error", text: C.friendly(res.reason) });
        return h("div", { class: "card", style: "margin:0" }, h("h2", { text: title }), body);
      }
      panel.append(h("div", { class: "grid-3" }, card("System health", results[0]), card("Statistics", results[1]), card("Routes and delivery", results[2])));
    }

    async function users() {
      var data = await C.api("GET", "/v1/admin/users");
      S.users = Array.isArray(data) ? data : data.users || [];
      drawUsers();
    }

    function drawUsers() {
      C.clear(panel);
      var body = h("tbody");
      S.users.forEach(function (user) {
        var role = h("select", { "aria-label": "Role for " + user.username }, ["candidate", "recruiter", "admin"].map(function (r) { return h("option", { value: r, selected: r === user.role, text: r }); }));
        var isDisabled = user.disabled === true || user.active === false;
        var isSelf = C.state.me && C.state.me.username === user.username;
        var active = h("input", { type: "checkbox", checked: !isDisabled, disabled: isSelf, "aria-label": user.username + " is active" });
        if (isSelf) role.disabled = true;
        var save = h("button", { type: "button", class: "btn small", text: "Save", "aria-label": "Save " + user.username });
        save.addEventListener("click", async function () {
          save.disabled = true;
          try {
            await C.api("PATCH", "/v1/admin/users/" + encodeURIComponent(user.username), { json: { role: role.value, disabled: !active.checked } });
            user.role = role.value;
            user.disabled = !active.checked;
            user.active = active.checked;
            C.status("Saved changes for " + user.username + ".");
          } catch (error) {
            if (C.authError(error)) return;
            C.status(C.friendly(error), true);
          } finally {
            save.disabled = false;
          }
        });
        body.appendChild(h("tr", null, h("td", null, h("strong", { text: user.username })), h("td", null, role), h("td", null, h("label", { class: "check", style: "margin:0" }, active, isDisabled ? "Disabled" : "Active")), h("td", { text: C.fmtTime(user.created_at || user.last_login) }), h("td", null, isSelf ? h("span", { class: "muted small", text: "You" }) : save)));
      });
      var add = h("button", { type: "button", class: "btn primary", text: "Add user", onclick: addUser });
      panel.append(
        h("div", { class: "row", style: "justify-content:space-between;margin-bottom:12px" }, h("h2", { style: "margin:0", text: "Users (" + S.users.length + ")" }), add),
        h("div", { class: "table-wrap" }, h("table", null, h("caption", { class: "sr-only", text: "Users" }), h("thead", null, h("tr", null, ["User", "Role", "Status", "Created", "Actions"].map(function (t) { return h("th", { scope: "col", text: t }); }))), body))
      );
    }

    async function addUser() {
      var name = h("input", { type: "text", id: "nuName", autocomplete: "off", spellcheck: "false" });
      var pass = h("input", { type: "password", id: "nuPass", autocomplete: "new-password" });
      var role = h("select", { id: "nuRole" }, ["recruiter", "admin", "candidate"].map(function (r) { return h("option", { value: r, text: r }); }));
      var answer = await C.dialog({
        title: "Add a user",
        body: h("div", null,
          h("label", { class: "field", for: "nuName" }, "Username", name),
          h("label", { class: "field", for: "nuPass" }, "Temporary password", pass, h("span", { class: "hint", text: "At least 8 characters." })),
          h("label", { class: "field", for: "nuRole" }, "Role", role)),
        actions: [
          { label: "Cancel", value: null },
          { label: "Create user", kind: "primary", value: "go", validate: function () { return !name.value.trim() ? "Enter a username." : pass.value.length < 8 ? "The password needs at least 8 characters." : null; } }
        ]
      });
      if (answer !== "go") return;
      try {
        await C.api("POST", "/v1/admin/users", { json: { username: name.value.trim(), password: pass.value, role: role.value } });
        C.status("Created " + name.value.trim() + ".");
        await users();
      } catch (error) {
        if (C.authError(error)) return;
        C.status(C.friendly(error), true);
      }
    }

    async function audit() {
      var data = await C.api("GET", "/v1/admin/audit");
      S.audit = Array.isArray(data) ? data : data.entries || data.events || [];
      var body = h("tbody");
      S.audit.forEach(function (entry) {
        var d = entry.detail;
        var detailText = d && typeof d === "object" ? Object.keys(d).map(function (k) { return k + ": " + d[k]; }).join(", ") : d || "";
        if (entry.ip) detailText = (detailText ? detailText + ", " : "") + "from " + entry.ip;
        body.appendChild(h("tr", null, h("td", { text: C.fmtTime(entry.at || entry.ts || entry.timestamp) }), h("td", { text: entry.actor || "" }), h("td", null, h("code", { text: entry.event || entry.action || "" })), h("td", { text: entry.target || "" }), h("td", { text: detailText })));
      });
      panel.append(
        h("h2", { text: "Audit log (" + S.audit.length + ")" }),
        S.audit.length
          ? h("div", { class: "table-wrap" }, h("table", null, h("caption", { class: "sr-only", text: "Audit log" }), h("thead", null, h("tr", null, ["When", "Who", "Action", "Target", "Detail"].map(function (t) { return h("th", { scope: "col", text: t }); }))), body))
          : h("p", { class: "muted", text: "No audit entries yet." })
      );
    }

    drawTabs();
    select(S.tab);
  };
})();
