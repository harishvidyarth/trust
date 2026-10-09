(function () {
  var C = window.C;
  var h = C.h;

  var DRAFT_KEY = "trust-apply-draft";
  var FORMS_KEY = "trust-apply-sent";
  var DEVICE_KEY = "trust-device-id";
  var MAX_BYTES = 5 * 1024 * 1024;
  var ABOUT_MAX = 2000;
  var ANSWER_MAX = 4000;
  var FALLBACK_ROLES = ["Backend engineer", "Data analyst", "Frontend developer", "DevOps engineer", "Machine learning engineer"];
  var OTHER = "__other__";

  var STATUS_TEXT = { sent: "Sent", more_details: "More details needed", in_review: "In review" };

  C.cand = C.cand || { replaces: null, replacesRole: "" };

  function readJson(key, fallback) {
    try {
      var value = JSON.parse(C.safeStore.get(key) || "null");
      return value && typeof value === "object" ? value : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function deviceId() {
    var id = C.safeStore.get(DEVICE_KEY);
    if (!id) {
      id = "console-" + Math.random().toString(36).slice(2, 12);
      C.safeStore.set(DEVICE_KEY, id);
    }
    return id;
  }

  function toSeconds(value) {
    if (typeof value === "number") return value > 1e12 ? Math.floor(value / 1000) : value;
    var parsed = Date.parse(value);
    return isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
  }

  function dateText(value) {
    var seconds = toSeconds(value);
    if (!seconds) return "Date not recorded";
    return new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function plural(count, one, many) {
    return count + " " + (count === 1 ? one : many);
  }

  function shortId(id) {
    return String(id || "").replace(/^app-/, "").slice(0, 8).toUpperCase();
  }

  function splitLines(value) {
    return String(value || "").split(/[\n,]+/).map(function (item) { return item.trim(); }).filter(Boolean);
  }

  function errorText(error) {
    if (error && (error.status === 400 || error.status === 404 || error.status === 422 || error.status === 429) && error.message) return error.message;
    return C.friendly(error);
  }

  function statusChip(status) {
    return C.tag(STATUS_TEXT[status] || "Sent", "info", true);
  }

  function normaliseItems(list) {
    return (Array.isArray(list) ? list : []).filter(Boolean).map(function (item, index) {
      return {
        id: item.id !== undefined && item.id !== null ? String(item.id) : "item-" + index,
        kind: item.kind || "details",
        label: item.label || "",
        help: item.help || "",
        required: Boolean(item.required),
        answered: Boolean(item.answered),
        attempts_left: typeof item.attempts_left === "number" ? item.attempts_left : null
      };
    });
  }

  C.cand.followPanel = function (applicationId, rawItems, hooks) {
    var items = normaliseItems(rawItems);
    var uid = "fu" + Math.random().toString(36).slice(2, 7);
    var section = h("section", { class: "card follow-card", id: uid, "aria-labelledby": uid + "T" });

    function attemptsNote(item) {
      if (item.attempts_left === null) return null;
      if (item.attempts_left <= 0) return h("p", { class: "small muted", text: "You cannot send more for this one right now." });
      return h("p", { class: "small muted", text: "You can send " + plural(item.attempts_left, "more answer", "more answers") + " for this one." });
    }

    function itemBox(item, note, composing) {
      var box = h("article", { class: "follow-item", "data-item-id": item.id, "aria-labelledby": uid + "L" + item.id });
      var title = h("h3", { id: uid + "L" + item.id, class: "follow-label" + (item.kind === "question" ? " pre" : ""), text: item.label });
      var head = h("div", { class: "row between follow-head" }, title, item.answered ? C.tag("Sent", "info", true) : (item.required ? C.tag("Needed", "info", true) : C.tag("Optional", "info", true)));
      box.append(head);
      if (item.help) box.append(h("p", { class: "muted small", text: item.help }));
      if (note) box.append(h("p", { class: "follow-note", role: "status", text: note }));
      if (item.answered && !composing) {
        box.append(h("p", { class: "small", text: "Your answer was sent to the hiring team." }));
        if (item.kind === "details" && (item.attempts_left === null || item.attempts_left > 0)) {
          var more = h("button", { type: "button", class: "btn small", text: "Add more", "aria-label": "Add more to " + item.label });
          more.addEventListener("click", function () {
            var next = itemBox(item, "", true);
            box.replaceWith(next);
            var area = next.querySelector("textarea");
            if (area) area.focus();
          });
          box.append(h("div", { class: "row" }, more));
        }
        return box;
      }
      if (item.attempts_left !== null && item.attempts_left <= 0) {
        box.append(attemptsNote(item));
        return box;
      }
      var areaId = uid + "A" + item.id;
      var area = h("textarea", { id: areaId, rows: "5", maxlength: String(ANSWER_MAX), "aria-labelledby": uid + "L" + item.id, "aria-describedby": areaId + "C " + areaId + "E" });
      var count = h("span", { id: areaId + "C", class: "small muted", text: "0 of " + ANSWER_MAX + " characters" });
      var err = h("p", { class: "err-text", id: areaId + "E", role: "alert", hidden: true });
      var send = h("button", { type: "button", class: "btn primary", text: "Send" });
      area.addEventListener("input", function () {
        count.textContent = area.value.length + " of " + ANSWER_MAX + " characters";
      });
      send.addEventListener("click", async function () {
        var text = area.value.trim();
        err.hidden = true;
        if (!text) {
          err.textContent = "Please write your answer before you send it.";
          err.hidden = false;
          area.setAttribute("aria-invalid", "true");
          area.focus();
          return;
        }
        if (text.length > ANSWER_MAX) {
          err.textContent = "Your answer is too long. Please keep it under " + ANSWER_MAX + " characters.";
          err.hidden = false;
          area.focus();
          return;
        }
        area.removeAttribute("aria-invalid");
        send.disabled = true;
        send.setAttribute("aria-busy", "true");
        send.textContent = "Sending...";
        try {
          var res = await C.api("POST", "/v1/me/applications/" + encodeURIComponent(applicationId) + "/answers", { json: { item_id: item.id, text: text } });
          item.answered = true;
          if (res && typeof res.attempts_left === "number") item.attempts_left = res.attempts_left;
          var message = (res && res.message) || "Thank you. Your answer was sent to the hiring team.";
          var next = itemBox(item, message, false);
          box.replaceWith(next);
          C.status(message);
          if (hooks && hooks.onAnswered) hooks.onAnswered(items);
          var focusTarget = next.querySelector("button") || next.querySelector(".follow-note");
          if (focusTarget) focusTarget.focus && focusTarget.focus();
        } catch (error) {
          if (C.authError(error)) return;
          err.textContent = errorText(error);
          err.hidden = false;
          send.disabled = false;
          send.removeAttribute("aria-busy");
          send.textContent = "Send";
        }
      });
      box.append(h("label", { class: "sr-only", for: areaId, text: "Your answer to " + item.label }), area, count, err, attemptsNote(item), h("div", { class: "row", style: "margin-top:8px" }, send));
      return box;
    }

    section.append(h("h2", { id: uid + "T", text: "A few more details" }), h("p", { class: "muted", text: "The hiring team would like to know a little more. Please write your answers in your own words. You can send them now or come back later." }));
    var open = items.filter(function (item) { return !item.answered; });
    if (!items.length) {
      section.append(h("p", { class: "muted", text: "Nothing more is needed from you right now." }));
    }
    items.forEach(function (item) { section.append(itemBox(item, "", false)); });
    section.openCount = open.length;
    return section;
  };

  C.views.candidate = function (root) {
    var replaces = C.cand.replaces;
    var memory = readJson(FORMS_KEY, {});
    var draft = replaces && memory[replaces] ? memory[replaces] : readJson(DRAFT_KEY, {});
    if (replaces && !memory[replaces] && C.cand.replacesRole) draft = Object.assign({}, draft, { role: C.cand.replacesRole });
    var S = { file: null, sending: false };
    var controls = {};
    var errors = {};

    var page = h("div", { class: "apply-page" });
    root.append(page);

    function field(name, label, control, o) {
      var opts = o || {};
      var id = "f" + name;
      control.id = id;
      control.name = name;
      var described = [];
      var hint = null;
      if (opts.hint) {
        hint = h("span", { class: "hint", id: id + "H", text: opts.hint });
        described.push(id + "H");
      }
      var err = h("p", { class: "err-text", id: id + "E", hidden: true });
      described.push(id + "E");
      control.setAttribute("aria-describedby", described.join(" "));
      if (opts.required) control.setAttribute("aria-required", "true");
      if (draft[name] !== undefined && control.tagName !== "SELECT") control.value = draft[name];
      controls[name] = control;
      errors[name] = err;
      control.addEventListener("input", function () {
        clearError(name);
        saveDraft();
      });
      control.addEventListener("change", function () {
        clearError(name);
        saveDraft();
      });
      var labelNode = h("label", { for: id, class: "form-label" }, label, opts.required ? h("span", { class: "req", text: " Required" }) : h("span", { class: "req", text: " Optional" }));
      var wrap = h("div", { class: "form-field" }, labelNode, hint, control, err);
      if (opts.extra) wrap.append(opts.extra);
      return wrap;
    }

    function setError(name, message) {
      errors[name].textContent = message;
      errors[name].hidden = false;
      controls[name].setAttribute("aria-invalid", "true");
    }
    function clearError(name) {
      if (!errors[name]) return;
      errors[name].hidden = true;
      errors[name].textContent = "";
      controls[name].removeAttribute("aria-invalid");
    }

    function text(type, attrs) {
      return h("input", Object.assign({ type: type, autocomplete: "off", spellcheck: "false" }, attrs || {}));
    }

    var roleSelect = h("select", {}, h("option", { value: "", text: "Choose a role" }));
    var roleOther = text("text", { placeholder: "For example Site reliability engineer", maxlength: "120" });
    var roleOtherWrap = h("div", { class: "form-field", hidden: true }, roleOtherLabel(), roleOther);
    function roleOtherLabel() { return h("label", { for: "froleOther", class: "form-label" }, "Role you want to apply for", h("span", { class: "req", text: " Required" })); }
    roleOther.id = "froleOther";
    roleOther.name = "roleOther";
    var roleOtherErr = h("p", { class: "err-text", id: "froleOtherE", hidden: true });
    roleOther.setAttribute("aria-describedby", "froleOtherE");
    roleOtherWrap.append(roleOtherErr);
    controls.roleOther = roleOther;
    errors.roleOther = roleOtherErr;
    roleOther.value = draft.roleOther || "";
    roleOther.addEventListener("input", function () { clearError("roleOther"); saveDraft(); });

    function addRoleOptions(names) {
      while (roleSelect.options.length > 1) roleSelect.remove(1);
      names.forEach(function (name) { roleSelect.append(h("option", { value: name, text: name })); });
      roleSelect.append(h("option", { value: OTHER, text: "Other" }));
      var want = draft.role || "";
      if (want && Array.prototype.some.call(roleSelect.options, function (o) { return o.value === want; })) roleSelect.value = want;
      syncOther();
    }
    function syncOther() {
      roleOtherWrap.hidden = roleSelect.value !== OTHER;
    }
    roleSelect.addEventListener("change", syncOther);
    addRoleOptions(FALLBACK_ROLES);
    (async function () {
      try {
        var remote = await C.api("GET", "/v1/job-presets");
        remote = Array.isArray(remote) ? remote : (remote && remote.presets) || [];
        var names = remote.map(function (p) { return p && (p.name || p.title); }).filter(Boolean);
        if (names.length) {
          draft.role = roleSelect.value || draft.role;
          addRoleOptions(names);
        }
      } catch (error) {
        return;
      }
    })();

    var years = text("number", { min: "0", max: "60", step: "1", inputmode: "numeric" });
    var skills = text("text", { placeholder: "For example Python, SQL, Docker", maxlength: "400" });
    var about = h("textarea", { rows: "7", maxlength: String(ABOUT_MAX) });
    var aboutCount = h("span", { class: "small muted", id: "faboutC", text: "0 of " + ABOUT_MAX + " characters" });

    var fileInput = h("input", { type: "file", id: "resumeFile", accept: ".pdf,.docx,.txt", "aria-describedby": "fileHelp fileError" });
    var fileName = h("div", { class: "file-name", id: "fileName" });
    var fileError = h("p", { class: "err-text", id: "fileError", role: "alert", hidden: true });
    var drop = h("div", { class: "dropzone", id: "drop" },
      h("label", { for: "resumeFile", class: "form-label" }, "Choose a file"),
      fileInput,
      h("p", { class: "small muted", id: "fileHelp", style: "margin:8px 0 0", text: "Or drop a file here. PDF, DOCX or TXT, up to 5 MB." }),
      fileName,
      fileError
    );
    var removeFile = h("button", { type: "button", class: "btn small", text: "Remove file", hidden: true });

    function formatSize(bytes) {
      if (bytes < 1024) return bytes + " bytes";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
      return (bytes / (1024 * 1024)).toFixed(2) + " MB";
    }
    function checkFile(file) {
      var name = String(file.name || "");
      if (!/\.(pdf|docx|txt)$/i.test(name)) return "That file type is not accepted. Please choose a PDF, DOCX or TXT file.";
      if (file.size > MAX_BYTES) return "That file is " + formatSize(file.size) + ", which is more than the 5 MB limit. Please save a smaller copy.";
      if (file.size === 0) return "That file is empty. Please choose a file that has your resume in it.";
      return null;
    }
    function setFile(file) {
      var problem = file ? checkFile(file) : null;
      fileError.hidden = !problem;
      fileError.textContent = problem || "";
      fileInput.setAttribute("aria-invalid", problem ? "true" : "false");
      if (problem || !file) {
        S.file = null;
        fileName.textContent = "";
        removeFile.hidden = true;
        fileInput.value = "";
        if (problem) C.status(problem, true);
        return;
      }
      S.file = file;
      fileName.textContent = file.name + ", " + formatSize(file.size);
      removeFile.hidden = false;
      C.status("Selected " + file.name + ".");
    }
    fileInput.addEventListener("change", function () { if (fileInput.files[0]) setFile(fileInput.files[0]); });
    removeFile.addEventListener("click", function () { setFile(null); fileInput.focus(); });
    drop.addEventListener("dragover", function (event) { event.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("over"); });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("over");
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files[0]) setFile(files[0]);
    });

    var consent = h("input", { type: "checkbox", id: "fconsent", "aria-describedby": "fconsentE", "aria-required": "true" });
    var consentErr = h("p", { class: "err-text", id: "fconsentE", hidden: true });
    var sendBtn = h("button", { type: "submit", class: "btn primary", id: "sendApp", text: "Send application" });
    var progress = h("p", { class: "small muted", id: "sendNote", role: "status", "aria-live": "polite" });
    var formError = h("p", { class: "alert error", id: "formError", role: "alert", hidden: true });

    about.addEventListener("input", function () {
      aboutCount.textContent = about.value.length + " of " + ABOUT_MAX + " characters";
    });

    function sectionCard(title, id, help) {
      var card = h("fieldset", { class: "card form-section", "aria-labelledby": id });
      card.append(h("legend", { id: id, class: "section-title", text: title }));
      if (help) card.append(h("p", { class: "muted small", text: help }));
      return card;
    }

    var banner = h("div", { id: "updateNote" });
    function drawBanner() {
      C.clear(banner);
      if (!C.cand.replaces) return;
      banner.append(h("div", { class: "alert info" },
        h("strong", { text: "You are updating an application you sent before. " }),
        "Change anything you like and send it again. Your earlier details are filled in for you. ",
        h("button", { type: "button", class: "btn small", text: "Start a fresh application instead", onclick: function () { C.cand.replaces = null; drawBanner(); C.status("This will be sent as a new application."); } })
      ));
    }
    drawBanner();

    var secDetails = sectionCard("Your details", "secDetails", "We use these to reach you about this application.");
    secDetails.append(
      field("applicant_name", "Full name", text("text", { autocomplete: "name", maxlength: "120" }), { required: true }),
      field("applicant_email", "Email address", text("email", { autocomplete: "email", maxlength: "200", inputmode: "email" }), { required: true, hint: "The hiring team will use this to write to you." }),
      field("applicant_phone", "Phone number", text("tel", { autocomplete: "tel", maxlength: "30", inputmode: "tel" }), { required: true, hint: "Include your country code if you can." })
    );

    var secRole = sectionCard("Role", "secRole", "Tell us which job you are applying for.");
    secRole.append(field("role", "Role", roleSelect, { required: true }), roleOtherWrap);

    var secExp = sectionCard("Your experience", "secExp", "Share what you have done so far. Short answers are fine.");
    secExp.append(
      field("years", "Years of experience", years, { hint: "A whole number. Use 0 if you are just starting out." }),
      field("current_employer", "Current or latest employer", text("text", { autocomplete: "organization", maxlength: "160" })),
      field("education", "Education", text("text", { maxlength: "240" }), { hint: "For example your degree and where you studied." }),
      field("skills", "Skills", skills, { hint: "Separate each skill with a comma." }),
      field("extra_skills", "Anything else you are good at", h("textarea", { rows: "3", maxlength: "600" }), { hint: "Other tools, languages or strengths that were not listed above." })
    );

    var secLinks = sectionCard("Your links", "secLinks", "Links help the hiring team see your work. Leave out any you do not have.");
    secLinks.append(
      field("github_url", "GitHub profile link", text("url", { placeholder: "https://github.com/yourname", maxlength: "300", inputmode: "url" })),
      field("linkedin_url", "LinkedIn profile link", text("url", { placeholder: "https://www.linkedin.com/in/yourname", maxlength: "300", inputmode: "url" })),
      field("portfolio_url", "Portfolio link", text("url", { placeholder: "https://yourname.example", maxlength: "300", inputmode: "url" })),
      field("papers", "Research papers or DOIs", h("textarea", { rows: "3", placeholder: "10.1000/example.123" }), { hint: "Put one on each line." }),
      field("certificate_ids", "Certificate IDs", h("textarea", { rows: "3" }), { hint: "Put one on each line." })
    );

    var secAbout = sectionCard("About your work", "secAbout", null);
    secAbout.append(field("about_project", "Tell us about one project you are proud of and what you did yourself", about, { hint: "Write in your own words. Plain and honest is best.", extra: aboutCount }));

    var secResume = sectionCard("Resume", "secResume", "You can add your resume if you have one. It is optional.");
    secResume.append(drop, h("div", { class: "row", style: "margin-top:8px" }, removeFile));

    var secSend = h("div", { class: "card form-section" },
      h("label", { class: "check", for: "fconsent" }, consent, h("span", { text: "I agree that the hiring team can read what I send and check the links I give." })),
      consentErr,
      formError,
      h("div", { class: "row" }, sendBtn, progress)
    );

    var form = h("form", { id: "applyForm", novalidate: true, "aria-labelledby": "applyTitle" },
      banner, secDetails, secRole, secExp, secLinks, secAbout, secResume, secSend
    );
    page.append(
      h("h1", { id: "applyTitle", tabindex: "-1", text: "Apply for a role" }),
      h("p", { class: "muted", text: "Fill in the form below and send it. It goes straight to the hiring team. Fields marked Required must be filled in." }),
      form
    );
    aboutCount.textContent = about.value.length + " of " + ABOUT_MAX + " characters";

    consent.addEventListener("change", function () {
      consentErr.hidden = true;
      consent.removeAttribute("aria-invalid");
    });

    function values() {
      var v = {};
      Object.keys(controls).forEach(function (name) { v[name] = controls[name].value; });
      v.role = roleSelect.value;
      return v;
    }

    function saveDraft() {
      var v = values();
      C.safeStore.set(DRAFT_KEY, JSON.stringify(v));
    }

    function normaliseUrl(value) {
      var trimmed = String(value || "").trim();
      if (!trimmed) return "";
      var full = /^[a-z][a-z0-9+.]*:\/\//i.test(trimmed) ? trimmed : "https://" + trimmed;
      try {
        var parsed = new URL(full);
        if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
        if (parsed.hostname.indexOf(".") < 1) return null;
        return parsed.toString();
      } catch (error) {
        return null;
      }
    }

    function validate() {
      Object.keys(errors).forEach(clearError);
      consentErr.hidden = true;
      var problems = [];
      function bad(name, message) { setError(name, message); problems.push(name); }
      var v = values();
      if (v.applicant_name.trim().length < 2) bad("applicant_name", "Please enter your full name.");
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v.applicant_email.trim())) bad("applicant_email", "Please enter an email address like name@example.com.");
      if (!/^\+?[0-9 ()\-.]{7,20}$/.test(v.applicant_phone.trim()) || v.applicant_phone.replace(/\D/g, "").length < 7) bad("applicant_phone", "Please enter a phone number with at least 7 digits.");
      if (!roleSelect.value) bad("role", "Please choose the role you are applying for.");
      else if (roleSelect.value === OTHER && v.roleOther.trim().length < 2) bad("roleOther", "Please type the role you are applying for.");
      if (v.years.trim() !== "") {
        var n = Number(v.years);
        if (!isFinite(n) || n < 0 || n > 60) bad("years", "Please enter a number from 0 to 60.");
      }
      ["github_url", "linkedin_url", "portfolio_url"].forEach(function (name) {
        if (v[name].trim() && normaliseUrl(v[name]) === null) bad(name, "Please enter a full web address such as https://example.com.");
      });
      if (v.about_project.length > ABOUT_MAX) bad("about_project", "Please keep this under " + ABOUT_MAX + " characters.");
      if (!consent.checked) {
        consentErr.textContent = "Please tick the box so we know you agree.";
        consentErr.hidden = false;
        consent.setAttribute("aria-invalid", "true");
        problems.push("consent");
      }
      return problems;
    }

    function focusProblem(name) {
      var node = name === "consent" ? consent : name === "role" ? roleSelect : controls[name];
      if (node) node.focus();
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (S.sending) return;
      formError.hidden = true;
      var problems = validate();
      if (problems.length) {
        var message = problems.length === 1 ? "One thing needs your attention before you can send." : plural(problems.length, "thing needs", "things need").replace("things need", "things need") + " your attention before you can send.";
        C.status(message, true);
        focusProblem(problems[0]);
        return;
      }
      var v = values();
      var role = roleSelect.value === OTHER ? v.roleOther.trim() : roleSelect.value;
      var skillList = splitLines(v.skills);
      var yearsValue = v.years.trim() === "" ? "" : String(Math.max(0, Math.floor(Number(v.years))));
      var fd = new FormData();
      if (S.file) fd.append("file", S.file);
      fd.append("job_json", JSON.stringify({ must_have_skills: skillList, nice_to_have: [], min_years: yearsValue === "" ? 0 : Number(yearsValue) }));
      fd.append("device_id", deviceId());
      fd.append("applicant_name", v.applicant_name.trim());
      fd.append("applicant_email", v.applicant_email.trim());
      fd.append("applicant_phone", v.applicant_phone.trim());
      fd.append("role_title", role);
      function maybe(key, value) { if (value !== "" && value !== null && value !== undefined) fd.append(key, value); }
      maybe("current_employer", v.current_employer.trim());
      maybe("education", v.education.trim());
      maybe("github_url", normaliseUrl(v.github_url) || "");
      maybe("linkedin_url", normaliseUrl(v.linkedin_url) || "");
      maybe("portfolio_url", normaliseUrl(v.portfolio_url) || "");
      maybe("papers", splitLines(v.papers.replace(/,/g, "\n")).join("\n"));
      maybe("certificate_ids", splitLines(v.certificate_ids.replace(/,/g, "\n")).join("\n"));
      maybe("years_experience", yearsValue);
      maybe("extra_skills", v.extra_skills.trim());
      maybe("about_project", v.about_project.trim());
      fd.append("consent", "true");
      if (C.cand.replaces) fd.append("replaces", C.cand.replaces);

      S.sending = true;
      sendBtn.disabled = true;
      sendBtn.setAttribute("aria-busy", "true");
      sendBtn.textContent = "Sending...";
      progress.textContent = S.file ? "Sending your application and your file. Please keep this page open." : "Sending your application. Please keep this page open.";
      C.status("Sending your application...");
      try {
        var res = await C.api("POST", "/v1/applications/upload", { form: fd });
        var saved = values();
        var memory2 = readJson(FORMS_KEY, {});
        if (res && res.application_id) {
          memory2[res.application_id] = saved;
          var keys = Object.keys(memory2);
          while (keys.length > 20) delete memory2[keys.shift()];
          C.safeStore.set(FORMS_KEY, JSON.stringify(memory2));
        }
        C.safeStore.set(DRAFT_KEY, "");
        C.cand.replaces = null;
        if (C.refreshSummary) C.refreshSummary();
        thankYou(res || {}, role);
      } catch (error) {
        if (C.authError(error)) return;
        formError.textContent = errorText(error);
        formError.hidden = false;
        C.status("Your application was not sent. Please read the message and try again.", true);
        sendBtn.disabled = false;
        sendBtn.removeAttribute("aria-busy");
        sendBtn.textContent = "Send application";
        progress.textContent = "";
        S.sending = false;
        formError.scrollIntoView({ block: "nearest" });
      }
    });

    function thankYou(res, role) {
      C.clear(root);
      var id = res.application_id || "";
      var title = h("h1", { id: "thanksTitle", tabindex: "-1", class: "thanks-title", text: res.title || "Thank you. We have your application." });
      var copyNote = h("span", { class: "small muted", role: "status", "aria-live": "polite" });
      var copy = h("button", { type: "button", class: "btn small", text: "Copy", "aria-label": "Copy reference number" });
      copy.addEventListener("click", async function () {
        try {
          await navigator.clipboard.writeText(id);
          copyNote.textContent = "Copied.";
        } catch (error) {
          copyNote.textContent = "Copy did not work. Please select the number and copy it.";
        }
      });
      var wrap = h("div", { class: "myapps thanks" },
        h("div", { class: "card thanks-card" },
          title,
          res.message ? h("p", { class: "thanks-message", text: res.message }) : null,
          role ? h("p", { class: "muted", text: "Role " + role }) : null,
          id ? h("div", { class: "ref-row" }, h("span", { class: "muted small", text: "Your reference" }), h("strong", { class: "mono ref-id", id: "refId", text: shortId(id) }), copy, copyNote) : null,
          h("h2", { text: "What happens next" }),
          h("p", { text: "The hiring team reads every application. You can come back here any time to see if they need anything more from you." }),
          h("div", { class: "row" },
            h("button", { type: "button", class: "btn primary", id: "toMine", text: "See my applications", onclick: function () { C.go("myapps"); } }),
            h("button", { type: "button", class: "btn", id: "another", text: "Send another application", onclick: function () { C.go("candidate"); } })
          )
        )
      );
      var items = normaliseItems(res.follow_up);
      var idItem = items.filter(function (item) { return item.kind === "identity"; })[0];
      var plainItems = items.filter(function (item) { return item.kind !== "identity"; });
      if (id && C.identity && !(idItem && idItem.answered)) wrap.append(C.identity.card(id, { answered: false }));
      if (plainItems.some(function (item) { return !item.answered; })) {
        wrap.append(C.cand.followPanel(id, plainItems, {}));
      }
      root.append(wrap);
      title.focus();
      window.scrollTo(0, 0);
      C.status("Your application was sent.");
    }

    page.querySelector("#applyTitle").focus({ preventScroll: true });
  };

  C.views.myapps = function (root) {
    var listBox = h("div", { id: "appList" });
    var detailBox = h("div", { id: "appDetail", hidden: true });
    var title = h("h1", { id: "myAppsTitle", tabindex: "-1", text: "My applications" });
    root.append(h("div", { class: "myapps" }, title, h("p", { class: "muted", text: "Every application you have sent is saved here. Open one to see how it is going or to answer anything the hiring team asked." }), listBox, detailBox));

    function drawList(rows, focusId) {
      C.clear(listBox);
      if (!rows.length) {
        listBox.append(h("div", { class: "card" }, h("h2", { text: "No applications yet" }), h("p", { class: "muted", text: "When you send an application it will appear here." }), h("button", { type: "button", class: "btn primary", text: "Start an application", onclick: function () { C.go("candidate"); } })));
        return;
      }
      var list = h("ul", { class: "app-list", "aria-label": "Your applications" });
      rows.forEach(function (row) {
        var needs = Number(row.follow_up_count) > 0;
        var role = row.role_title || row.job_id || "Application";
        var view = h("button", { type: "button", class: "detail-link", "data-view-id": row.application_id, "aria-label": "View application for " + role + " sent on " + dateText(row.submitted_at), text: "View" });
        view.addEventListener("click", function () { openApp(row, rows); });
        list.append(h("li", { class: "app-item" },
          h("div", { class: "app-main" },
            h("h2", { class: "app-role", text: role }),
            h("div", { class: "row", style: "gap:8px" }, statusChip(row.status), needs ? C.tag("Details needed", "", true) : null, row.replaces ? C.tag("Updated", "", true) : null),
            h("div", { class: "app-meta muted", text: "Sent on " + dateText(row.submitted_at) })
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
        C.status(errorText(error), true);
        return;
      }
      d = Object.assign({}, row, d || {});
      var role = d.role_title || d.job_id || "Application";
      var heading = h("h2", { id: "detailTitle", tabindex: "-1", text: role });
      function back() {
        detailBox.hidden = true;
        C.clear(detailBox);
        listBox.hidden = false;
        refresh(row.application_id);
        C.status("Back to your applications.");
      }
      var statusLine = h("div", { class: "row", style: "gap:8px" }, statusChip(d.status), h("span", { class: "muted small", text: "Sent on " + dateText(d.submitted_at) }));
      var summary = h("div", { class: "card" },
        statusLine,
        d.title ? h("h3", { style: "margin-top:12px", text: d.title }) : null,
        d.message ? h("p", { text: d.message }) : null,
        h("p", { class: "muted small", text: "Your reference is " + shortId(d.application_id) + "." })
      );
      var allItems = normaliseItems(d.follow_up);
      var idItem = allItems.filter(function (item) { return item.kind === "identity"; })[0];
      var items = allItems.filter(function (item) { return item.kind !== "identity"; });
      var idCard = idItem && C.identity ? C.identity.card(d.application_id, { answered: idItem.answered, later: false }) : null;
      var follow = items.length ? C.cand.followPanel(d.application_id, items, {}) : h("div", { class: "card" }, h("h3", { text: "A few more details" }), h("p", { class: "muted", text: "Nothing more is needed from you right now. The hiring team will write to you if that changes." }));
      var update = h("button", { type: "button", class: "btn primary", id: "updateApp", text: "Update this application", onclick: function () {
        C.cand.replaces = d.application_id;
        C.cand.replacesRole = role;
        C.go("candidate");
      } });
      C.clear(detailBox);
      detailBox.append(
        h("button", { type: "button", class: "btn small", id: "backListTop", text: "Back to my applications", onclick: back }),
        heading,
        summary,
        follow,
        idCard,
        h("div", { class: "card" }, h("h3", { text: "Want to change something" }), h("p", { class: "muted", text: "You can open the form again with your details filled in, make your changes and send it again." }), update)
      );
      heading.focus();
      window.scrollTo(0, 0);
      C.status("Application opened.");
    }

    async function refresh(focusId) {
      C.status("Loading your applications...");
      try {
        var rows = await C.api("GET", "/v1/me/applications");
        rows = Array.isArray(rows) ? rows : (rows && rows.applications) || [];
        rows = rows.slice().sort(function (a, b) { return toSeconds(b.submitted_at) - toSeconds(a.submitted_at); });
        drawList(rows, focusId);
        C.status(rows.length ? plural(rows.length, "application", "applications") + " loaded." : "You have no applications yet.");
        if (!focusId) title.focus({ preventScroll: true });
      } catch (error) {
        if (C.authError(error)) return;
        C.clear(listBox);
        if (error.status === 404) {
          listBox.append(h("div", { class: "card" }, h("p", { class: "muted", text: "The list of your applications is not available from this service yet." }), h("button", { type: "button", class: "btn primary", text: "Start an application", onclick: function () { C.go("candidate"); } })));
          C.status("");
        } else {
          listBox.append(h("p", { class: "alert error", role: "alert", text: C.friendly(error) }));
          C.status(C.friendly(error), true);
        }
      }
    }
    refresh();
  };
})();
