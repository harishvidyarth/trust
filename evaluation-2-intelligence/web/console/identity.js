(function () {
  var C = (window.C = window.C || {});
  var h = C.h;

  var LEFT_EYE = [33, 160, 158, 133, 153, 144];
  var RIGHT_EYE = [362, 387, 385, 263, 373, 380];
  var TURN_THRESHOLD = 0.2;
  var SMILE_BASE_THRESHOLD = 3.0;
  var SMILE_INCREASE = 1.3;
  var SMILE_CALIBRATION_MS = 1000;
  var MOUTH_OPEN_THRESHOLD = 0.32;
  var EYE_CLOSED_EAR = 0.2;
  var EYE_OPEN_EAR = 0.25;
  var BLINK_MIN_CLOSED_MS = 70;
  var BLINK_MAX_CLOSED_MS = 600;
  var HOLD_FRAMES = 3;
  var PROMPT_MS = 7000;
  var MAX_TRIES = 3;
  var WAV_RATE = 16000;
  var WAV_MAX_SECONDS = 12;
  var FACE_BASE = "vendor/mediapipe/face_mesh/";
  var ID_MAX_BYTES = 1.5 * 1024 * 1024;
  var ID_MAX_SIDE = 1600;
  var FRAME_SIDE = 640;
  var FRAME_MAX_BYTES = 700 * 1024;

  function dist(a, b) {
    var dx = a.x - b.x;
    var dy = a.y - b.y;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function eyeAspectRatio(landmarks, points) {
    var p = points.map(function (i) { return landmarks[i]; });
    var vertical = dist(p[1], p[5]) + dist(p[2], p[4]);
    return vertical / (2 * Math.max(dist(p[0], p[3]), 0.000001));
  }

  function analyzeFace(landmarks) {
    if (!landmarks || landmarks.length < 292) return null;
    var nose = landmarks[1];
    var leftEye = landmarks[33];
    var rightEye = landmarks[263];
    var eyeDistance = Math.abs(rightEye.x - leftEye.x);
    if (eyeDistance < 0.0001) return null;
    var noseOffset = (nose.x - (leftEye.x + rightEye.x) / 2) / eyeDistance;
    var mouthWidth = Math.abs(landmarks[291].x - landmarks[61].x);
    var mouthHeight = Math.abs(landmarks[14].y - landmarks[13].y);
    return {
      noseOffset: noseOffset,
      smileRatio: mouthWidth / Math.max(mouthHeight, 0.001),
      mouthOpen: mouthHeight / Math.max(mouthWidth, 0.001),
      ear: (eyeAspectRatio(landmarks, LEFT_EYE) + eyeAspectRatio(landmarks, RIGHT_EYE)) / 2
    };
  }

  function createBlinkDetector() {
    var ready = false;
    var open = true;
    var closedAt = 0;
    var validated = false;
    return function feed(ear, now) {
      if (!ready) {
        ready = true;
        open = ear >= EYE_OPEN_EAR;
        return false;
      }
      if (ear <= EYE_CLOSED_EAR) {
        if (open) {
          open = false;
          closedAt = now;
          validated = false;
        } else if (!validated && now - closedAt >= BLINK_MIN_CLOSED_MS) {
          validated = true;
        }
        return false;
      }
      if (ear >= EYE_OPEN_EAR && !open) {
        open = true;
        var good = validated && now - closedAt <= BLINK_MAX_CLOSED_MS;
        validated = false;
        return good;
      }
      return false;
    };
  }

  function createSmoother(alpha) {
    var last = null;
    return function (value) {
      last = last === null ? value : last + alpha * (value - last);
      return last;
    };
  }

  function createEvaluator(id) {
    var run = 0;
    var blink = createBlinkDetector();
    var smoothNose = createSmoother(0.5);
    var smoothSmile = createSmoother(0.5);
    var smoothMouth = createSmoother(0.5);
    var startedAt = null;
    var baselineSum = 0;
    var baselineCount = 0;
    var baseline = null;
    function hold(ok) {
      run = ok ? run + 1 : 0;
      return run >= HOLD_FRAMES;
    }
    return function feed(m, now) {
      if (!m) {
        run = 0;
        return false;
      }
      var nose = smoothNose(m.noseOffset);
      var smile = smoothSmile(m.smileRatio);
      var mouth = smoothMouth(m.mouthOpen);
      if (id === "blink") return blink(m.ear, now);
      if (id === "turn_left") return hold(nose > TURN_THRESHOLD);
      if (id === "turn_right") return hold(nose < -TURN_THRESHOLD);
      if (id === "open_mouth") return hold(mouth >= MOUTH_OPEN_THRESHOLD);
      if (id === "smile") {
        if (startedAt === null) startedAt = now;
        if (baseline === null) {
          baselineSum += smile;
          baselineCount += 1;
          if (now - startedAt >= SMILE_CALIBRATION_MS) baseline = baselineSum / Math.max(1, baselineCount);
          return false;
        }
        return hold(smile >= Math.max(SMILE_BASE_THRESHOLD, baseline * SMILE_INCREASE));
      }
      return false;
    };
  }

  function writeText(view, offset, text) {
    for (var i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  }

  function encodeWav(samples, rate) {
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);
    writeText(view, 0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeText(view, 8, "WAVE");
    writeText(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeText(view, 36, "data");
    view.setUint32(40, samples.length * 2, true);
    for (var i = 0; i < samples.length; i++) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  async function toWav16k(blob, maxSeconds) {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    var ctx = new Ctx();
    var decoded;
    try {
      decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    } finally {
      ctx.close();
    }
    var seconds = Math.min(decoded.duration, maxSeconds || WAV_MAX_SECONDS, WAV_MAX_SECONDS);
    var length = Math.max(1, Math.floor(seconds * WAV_RATE));
    var off = new OfflineAudioContext(1, length, WAV_RATE);
    var source = off.createBufferSource();
    source.buffer = decoded;
    source.connect(off.destination);
    source.start(0);
    var rendered = await off.startRendering();
    return encodeWav(rendered.getChannelData(0), WAV_RATE);
  }

  function practiceWav(seconds) {
    var length = Math.floor(WAV_RATE * seconds);
    var data = new Float32Array(length);
    for (var i = 0; i < length; i++) {
      var t = i / WAV_RATE;
      data[i] = 0.08 * Math.sin(2 * Math.PI * 180 * t) * (0.6 + 0.4 * Math.sin(2 * Math.PI * 3 * t));
    }
    return encodeWav(data, WAV_RATE);
  }

  function pickRecorderMimeType() {
    var list = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    for (var i = 0; i < list.length; i++) if (MediaRecorder.isTypeSupported(list[i])) return list[i];
    return "";
  }

  var meshScript = null;
  function loadFaceScript() {
    if (window.FaceMesh) return Promise.resolve();
    if (meshScript) return meshScript;
    meshScript = new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = new URL(FACE_BASE + "face_mesh.js", document.baseURI).href;
      tag.onload = function () { resolve(); };
      tag.onerror = function () { meshScript = null; reject(new Error("load")); };
      document.head.appendChild(tag);
    });
    return meshScript;
  }

  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new Error("timeout")); }, ms);
      promise.then(function (v) { clearTimeout(timer); resolve(v); }, function (e) { clearTimeout(timer); reject(e); });
    });
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  var PHOTO_CONSENT_TEXT = "An ID photo and two pictures from your camera are compared on the server. Nothing is kept after that. A person reads the result. A computer comparison can be wrong, so it is only a hint.";
  var PHOTO_RECEIVED = "Thank you. Your photo check was received.";

  function toBlob(canvas, quality) {
    return new Promise(function (resolve) {
      try { canvas.toBlob(function (b) { resolve(b || null); }, "image/jpeg", quality); } catch (e) { resolve(null); }
    });
  }

  async function loadImage(blob) {
    if (window.createImageBitmap) {
      try {
        var bmp = await createImageBitmap(blob);
        return { source: bmp, width: bmp.width, height: bmp.height, release: function () { try { bmp.close(); } catch (e) { return null; } } };
      } catch (e) {
        return null;
      }
    }
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        resolve({ source: img, width: img.naturalWidth, height: img.naturalHeight, release: function () { img.src = ""; URL.revokeObjectURL(url); } });
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(null); };
      img.src = url;
    });
  }

  async function drawToJpeg(source, width, height, longest, maxBytes, qualities) {
    var scale = Math.min(1, longest / Math.max(width, height));
    var canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    var g = canvas.getContext("2d");
    g.drawImage(source, 0, 0, canvas.width, canvas.height);
    var out = null;
    for (var i = 0; i < qualities.length; i++) {
      out = await toBlob(canvas, qualities[i]);
      if (out && out.size <= maxBytes) break;
    }
    canvas.width = 0;
    canvas.height = 0;
    return out && out.size <= maxBytes ? out : null;
  }

  async function prepareIdPhoto(file) {
    if (!file || (file.type !== "image/jpeg" && file.type !== "image/png")) return { error: "Please choose a JPEG or PNG photo." };
    if (file.size <= ID_MAX_BYTES) return { blob: file };
    var img = await loadImage(file);
    if (!img) return { error: "We could not read that photo. Please choose another one." };
    var out = null;
    try {
      out = await drawToJpeg(img.source, img.width, img.height, ID_MAX_SIDE, ID_MAX_BYTES, [0.85, 0.7, 0.55]);
      if (!out) out = await drawToJpeg(img.source, img.width, img.height, 1200, ID_MAX_BYTES, [0.6, 0.45]);
    } finally {
      img.release();
    }
    return out ? { blob: out } : { error: "That photo is too large. Please choose a smaller one." };
  }

  function message(error) {
    if (error && error.message && (error.status === 404 || error.status === 409 || error.status === 429 || error.status === 400 || error.status === 413 || error.status === 422)) return error.message;
    return C.friendly(error);
  }

  var CONSENT_LOCAL = [
    "This is a short check that you are a real person. It has two parts. First you follow a few simple prompts in front of your camera, like blinking or turning your head. Then you read one sentence aloud.",
    "Your camera and microphone are only used during the check. The video and the sound stay on your device while they are measured. Nothing is recorded or kept. Only a few measurements are sent to the hiring team, and a person reads them."
  ];

  function openFlow(applicationId, hooks) {
    var onDone = (hooks && hooks.onDone) || function () {};
    var opener = document.activeElement;
    var dlg = h("dialog", { class: "idflow", "aria-labelledby": "idTitle" });
    var live = h("p", { class: "sr-only", role: "status", "aria-live": "polite", id: "idLive" });
    var body = h("div", { class: "id-body" });
    var closeBtn = h("button", { type: "button", class: "btn small", id: "idClose", text: "Close" });
    dlg.append(h("div", { class: "row between id-top" }, h("h2", { id: "idTitle", text: "Quick identity check" }), closeBtn), live, body);
    document.body.append(dlg);

    var F = {
      closed: false, session: null, stream: null, ctx: null, analyser: null, mesh: null,
      practice: false, hasCam: false, hasMic: false, faceReady: false, skipFace: false, faceSent: false,
      timers: [], urls: [], latest: null, listeners: [], resolvers: [], meterTimer: null, detect: false,
      framesTotal: 0, framesWith: 0, faceStart: 0, onFrame: null, video: null, sim: null, audio: null, recorder: null,
      photoAvail: false, photoOn: false, idPhoto: null, photoUrl: null, frames: [], photoSent: false, notice: "", simCanvas: null
    };

    function totalSteps() {
      return F.photoOn ? 5 : 4;
    }
    function stepNo(base) {
      return "Step " + (F.photoOn && base >= 3 ? base + 1 : base) + " of " + totalSteps();
    }

    function dropPhotos() {
      F.idPhoto = null;
      F.frames = [];
      if (F.photoUrl) {
        URL.revokeObjectURL(F.photoUrl);
        F.photoUrl = null;
      }
    }

    async function grabFrame() {
      var source = null;
      var w = 0;
      var hgt = 0;
      if (F.practice && F.simCanvas) {
        source = F.simCanvas;
        w = source.width;
        hgt = source.height;
      } else if (F.video && F.video.readyState >= 2 && F.video.videoWidth > 0) {
        source = F.video;
        w = source.videoWidth;
        hgt = source.videoHeight;
      }
      if (!source) return null;
      try {
        return await drawToJpeg(source, w, hgt, FRAME_SIDE, FRAME_MAX_BYTES, [0.85, 0.7, 0.55]);
      } catch (e) {
        return null;
      }
    }

    function say(text) {
      live.textContent = "";
      setTimeout(function () { live.textContent = text; }, 30);
    }

    function later(fn, ms) {
      var t = setTimeout(fn, ms);
      F.timers.push(t);
      return t;
    }

    function every(fn, ms) {
      var t = setInterval(fn, ms);
      F.timers.push(t);
      return t;
    }

    function stopMedia() {
      F.detect = false;
      if (F.stream) F.stream.getTracks().forEach(function (t) { t.stop(); });
      F.stream = null;
      if (F.video) {
        F.video.srcObject = null;
        F.video = null;
      }
      if (F.mesh) {
        try { F.mesh.close(); } catch (e) { F.mesh = null; }
        F.mesh = null;
      }
      if (F.ctx) {
        try { F.ctx.close(); } catch (e) { F.ctx = null; }
        F.ctx = null;
      }
      F.analyser = null;
    }

    function cleanup() {
      F.closed = true;
      F.timers.forEach(function (t) { clearTimeout(t); clearInterval(t); });
      F.timers = [];
      if (F.recorder && F.recorder.state !== "inactive") {
        try { F.recorder.stop(); } catch (e) { F.recorder = null; }
      }
      if (F.audio) {
        F.audio.pause();
        F.audio = null;
      }
      F.urls.forEach(function (u) { URL.revokeObjectURL(u); });
      F.urls = [];
      dropPhotos();
      F.simCanvas = null;
      stopMedia();
      F.listeners.forEach(function (l) { l[0].removeEventListener(l[1], l[2]); });
      F.listeners = [];
      F.resolvers.forEach(function (r) { r("closed"); });
      F.resolvers = [];
    }

    function listen(target, name, fn) {
      target.addEventListener(name, fn);
      F.listeners.push([target, name, fn]);
    }

    function finishDialog() {
      cleanup();
      if (dlg.open) dlg.close();
      dlg.remove();
      if (opener && opener.focus && document.contains(opener)) opener.focus();
    }

    closeBtn.addEventListener("click", function () { finishDialog(); });
    dlg.addEventListener("close", function () {
      if (!F.closed) {
        cleanup();
        dlg.remove();
        if (opener && opener.focus && document.contains(opener)) opener.focus();
      }
    });
    listen(document, "visibilitychange", function () {
      if (document.hidden && !F.closed && F.stream) {
        finishDialog();
        C.status("The check was closed because you left this page. You can start it again any time.");
      }
    });

    function ask() {
      return new Promise(function (resolve) { F.resolvers.push(resolve); });
    }
    function answer(value) {
      var list = F.resolvers;
      F.resolvers = [];
      list.forEach(function (r) { r(value); });
    }

    function render(heading, nodes, spoken) {
      if (F.closed) return;
      C.clear(body);
      var title = h("h3", { id: "idStep", tabindex: "-1", class: "id-step-title", text: heading });
      body.append(title);
      nodes.flat().forEach(function (n) { if (n) body.append(n); });
      title.focus({ preventScroll: true });
      if (spoken) say(spoken);
    }

    function errorBox() {
      return h("p", { class: "alert error", role: "alert", hidden: true });
    }
    function showError(box, text) {
      box.textContent = text;
      box.hidden = false;
    }

    async function stepConsent() {
      try {
        var caps = await C.api("GET", "/v1/verify/capabilities");
        F.photoAvail = Boolean(caps && caps.face_match_available === true);
      } catch (error) {
        F.photoAvail = false;
      }
      if (F.closed) return;
      var box = h("input", { type: "checkbox", id: "idConsent" });
      var photoBox = F.photoAvail ? h("input", { type: "checkbox", id: "idPhotoConsent" }) : null;
      var stepLine = h("p", { class: "muted small", text: "Step 1 of 4" });
      if (photoBox) photoBox.addEventListener("change", function () { stepLine.textContent = "Step 1 of " + (photoBox.checked ? 5 : 4); });
      var err = errorBox();
      var go = h("button", { type: "button", class: "btn primary", id: "idContinue", text: "Continue" });
      var no = h("button", { type: "button", class: "btn", text: "Not now" });
      no.addEventListener("click", finishDialog);
      go.addEventListener("click", async function () {
        err.hidden = true;
        if (!box.checked) {
          showError(err, "Please tick the box if you are happy to go on.");
          box.focus();
          return;
        }
        go.disabled = true;
        go.setAttribute("aria-busy", "true");
        try {
          F.session = await C.api("POST", "/v1/verify/session", { json: { application_id: applicationId, consent: true, photo_consent: Boolean(photoBox && photoBox.checked) } });
        } catch (error) {
          go.disabled = false;
          go.removeAttribute("aria-busy");
          if (F.closed) return;
          showError(err, message(error));
          return;
        }
        if (F.closed) return;
        F.photoOn = Boolean(photoBox && photoBox.checked && F.session && F.session.photo && F.session.photo.enabled === true);
        stepDevices();
      });
      render("Before we start", [
        stepLine,
        CONSENT_LOCAL.map(function (t) { return h("p", { text: t }); }),
        h("label", { class: "check", for: "idConsent" }, box, h("span", { text: "I understand and I agree to take this check." })),
        photoBox ? h("div", { class: "id-photo-consent" },
          h("label", { class: "check", for: "idPhotoConsent" }, photoBox, h("span", { text: "I also agree to a photo comparison. This part is optional." })),
          h("p", { class: "small muted", id: "idPhotoConsentText", text: PHOTO_CONSENT_TEXT })
        ) : null,
        err,
        h("div", { class: "actions" }, no, go)
      ], "Before we start. Please read and tick the box.");
    }

    async function initFace() {
      try {
        await withTimeout(loadFaceScript(), 20000);
        var mesh = new window.FaceMesh({ locateFile: function (file) { return new URL(FACE_BASE + file, document.baseURI).href; } });
        mesh.setOptions({ maxNumFaces: 1, refineLandmarks: false, minDetectionConfidence: 0.5, minTrackingConfidence: 0.5 });
        mesh.onResults(function (r) {
          var face = r && r.multiFaceLandmarks && r.multiFaceLandmarks[0];
          if (F.onFrame) F.onFrame(face || null);
        });
        await withTimeout(mesh.initialize(), 40000);
        if (F.closed) {
          try { mesh.close(); } catch (e) { return false; }
          return false;
        }
        F.mesh = mesh;
        return true;
      } catch (error) {
        return false;
      }
    }

    function levelMeter() {
      var fill = h("div", { class: "id-level-fill" });
      var track = h("div", { class: "id-level", role: "meter", "aria-label": "Microphone level", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, fill);
      track.set = function (v) {
        var pct = Math.max(0, Math.min(100, Math.round(v * 100)));
        fill.style.width = pct + "%";
        track.setAttribute("aria-valuenow", String(pct));
      };
      return track;
    }

    function readLevel() {
      if (F.practice) {
        var t = Date.now() / 1000;
        return 0.35 + 0.25 * Math.sin(t * 5) + 0.15 * Math.sin(t * 11);
      }
      if (!F.analyser) return 0;
      var data = new Uint8Array(F.analyser.fftSize);
      F.analyser.getByteTimeDomainData(data);
      var sum = 0;
      for (var i = 0; i < data.length; i++) {
        var v = (data[i] - 128) / 128;
        sum += v * v;
      }
      return Math.min(1, Math.sqrt(sum / data.length) * 5);
    }

    function runMeter(meter) {
      if (F.meterTimer) clearInterval(F.meterTimer);
      F.meterTimer = every(function () { meter.set(readLevel()); }, 80);
    }

    function drawSim(canvas, getState) {
      var g = canvas.getContext("2d");
      var ink = cssVar("--accent", "#1f3556");
      var paper = cssVar("--surface-3", "#ebe8dd");
      var line = cssVar("--border-strong", "#7a766a");
      every(function () {
        var s = getState();
        g.fillStyle = paper;
        g.fillRect(0, 0, canvas.width, canvas.height);
        var cx = canvas.width / 2 + s.turn * 40;
        var cy = canvas.height / 2;
        g.strokeStyle = ink;
        g.lineWidth = 2;
        g.beginPath();
        g.ellipse(cx, cy, 70, 90, 0, 0, Math.PI * 2);
        g.stroke();
        g.fillStyle = ink;
        [-26, 26].forEach(function (dx) {
          g.beginPath();
          g.ellipse(cx + dx + s.turn * 8, cy - 22, 9, Math.max(1.5, 7 * (1 - s.blink)), 0, 0, Math.PI * 2);
          g.fill();
        });
        g.beginPath();
        g.moveTo(cx + s.turn * 12, cy - 8);
        g.lineTo(cx + s.turn * 12 - 4, cy + 14);
        g.stroke();
        g.beginPath();
        if (s.open > 0.3) {
          g.ellipse(cx + s.turn * 10, cy + 40, 18, 6 + 14 * s.open, 0, 0, Math.PI * 2);
        } else {
          g.moveTo(cx + s.turn * 10 - 20, cy + 38);
          g.quadraticCurveTo(cx + s.turn * 10, cy + 38 + 22 * s.smile, cx + s.turn * 10 + 20, cy + 38);
        }
        g.stroke();
        g.fillStyle = line;
        g.font = "13px sans-serif";
        g.fillText("Practice mode", 10, 20);
      }, 66);
    }

    function previewFrame() {
      var frame = h("div", { class: "id-frame" });
      if (F.practice) {
        F.sim = { turn: 0, blink: 0, smile: 0, open: 0 };
        var canvas = h("canvas", { width: "320", height: "240", role: "img", "aria-label": "A simple drawing that stands in for the camera in practice mode" });
        frame.append(canvas);
        F.simCanvas = canvas;
        drawSim(canvas, function () { return F.sim; });
      } else if (F.hasCam && F.stream) {
        var video = h("video", { autoplay: true, muted: true, playsinline: true, "aria-label": "Your camera preview" });
        video.muted = true;
        video.srcObject = new MediaStream(F.stream.getVideoTracks());
        video.play().catch(function () { return null; });
        F.video = video;
        frame.append(video);
      } else {
        frame.append(h("p", { class: "muted small id-frame-note", text: "No camera preview" }));
      }
      return frame;
    }

    async function getTrackStream(constraints) {
      var md = navigator.mediaDevices;
      if (!md || !md.getUserMedia) return { error: "nodevice" };
      try {
        return { stream: await md.getUserMedia(constraints) };
      } catch (e) {
        if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) return { error: "denied" };
        if (e && (e.name === "NotReadableError" || e.name === "AbortError")) return { error: "busy" };
        return { error: "nodevice" };
      }
    }

    function stepDevices() {
      var err = errorBox();
      var note = h("p", { class: "small", role: "status" });
      var allow = h("button", { type: "button", class: "btn primary", id: "idAllow", text: "Allow camera and microphone" });
      var later = h("button", { type: "button", class: "btn", id: "idLater", text: "Do this later" });
      var practiceBox = h("input", { type: "checkbox", id: "idPractice" });
      var practiceRow = h("label", { class: "check small muted id-practice", for: "idPractice" }, practiceBox, h("span", { text: "Practice mode without a camera" }));
      F.facePromise = initFace();
      later.addEventListener("click", finishDialog);
      var holder = h("div", { class: "id-preview" });
      var actions = h("div", { class: "actions" }, later, allow);
      var consentLine = F.session && F.session.consent_text ? h("details", { class: "id-consent" }, h("summary", { class: "small", text: "What you agreed to" }), h("p", { class: "small", text: F.session.consent_text })) : null;

      allow.addEventListener("click", async function () {
        err.hidden = true;
        allow.disabled = true;
        allow.setAttribute("aria-busy", "true");
        F.practice = Boolean(practiceBox.checked);
        var earlyFrame = null;
        if (!F.practice) {
          note.textContent = "Opening your camera. Your browser may ask for permission. Please choose Allow.";
          var waitTimer = setTimeout(function () {
            if (F.closed) return;
            note.textContent = "Still waiting for your browser. Look for a small box near the top left of the page, or a camera icon at the right end of the address bar, and choose Allow. If you see nothing, open the Apple menu, then System Settings, then Privacy and Security, then Camera, and turn on your browser. Then close the browser fully and open it again.";
          }, 8000);
          var vid = await getTrackStream({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } } });
          clearTimeout(waitTimer);
          if (F.closed) {
            if (vid.stream) vid.stream.getTracks().forEach(function (t) { t.stop(); });
            return;
          }
          var failure = vid.error || "";
          if (vid.stream) {
            F.stream = vid.stream;
            F.hasCam = true;
            C.clear(holder);
            earlyFrame = previewFrame();
            holder.append(earlyFrame);
            note.textContent = "Opening your microphone.";
          }
          var aud = await getTrackStream({ audio: true });
          if (F.closed) {
            if (aud.stream) aud.stream.getTracks().forEach(function (t) { t.stop(); });
            return;
          }
          allow.disabled = false;
          allow.removeAttribute("aria-busy");
          note.textContent = "";
          if (aud.stream) {
            if (!F.stream) F.stream = new MediaStream();
            aud.stream.getAudioTracks().forEach(function (t) { F.stream.addTrack(t); });
            F.hasMic = true;
          } else if (!failure) {
            failure = aud.error;
          }
          if (!F.hasCam && !F.hasMic) {
            allow.textContent = "Try again";
            C.clear(holder);
            showError(err, failure === "denied"
              ? "We could not use your camera or microphone because permission was not given. Click the icon at the left of the address bar, set Camera and Microphone to Allow, then reload the page. On a Mac also check System Settings, then Privacy and Security, then Camera. You can also do the check later."
              : (failure === "busy"
                ? "Your camera is being used by another app or is blocked by the computer. Close apps such as Zoom, FaceTime, Photo Booth or another browser tab that uses the camera, then press Try again."
                : "We could not find a camera or microphone that works here. A secure page is needed, and localhost counts as secure. You can do the check later on another device, or tick the practice mode below."));
            err.append(" ", h("a", { href: "camera-test.html", target: "_blank", rel: "noopener", text: "Open the camera test page" }));
            return;
          }
          if (F.hasMic) {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            try {
              F.ctx = new Ctx();
              var src = F.ctx.createMediaStreamSource(new MediaStream(F.stream.getAudioTracks()));
              F.analyser = F.ctx.createAnalyser();
              F.analyser.fftSize = 1024;
              src.connect(F.analyser);
            } catch (e) {
              F.analyser = null;
            }
          }
        } else {
          F.hasCam = true;
          F.hasMic = true;
          allow.disabled = false;
          allow.removeAttribute("aria-busy");
        }
        C.clear(holder);
        var meter = levelMeter();
        holder.append(earlyFrame || previewFrame(), h("p", { class: "small muted", id: "idMicLabel", text: F.hasMic ? "Say a few words to see the bar move." : "No microphone was found." }), F.hasMic ? meter : null);
        if (F.hasMic) runMeter(meter);
        var go = h("button", { type: "button", class: "btn primary", id: "idToPrompts", text: "Continue", disabled: true });
        actions.replaceChildren(later, go);
        var messages = [];
        if (!F.hasCam) messages.push("We could not use your camera, so you can do the voice part only.");
        if (!F.hasMic) messages.push("We could not use your microphone, so you can do the face part only.");
        if (F.hasCam && !F.practice) {
          note.textContent = "Getting the face check ready. This can take a few seconds.";
          F.facePromise.then(function (ok) {
            if (F.closed) return;
            F.faceReady = ok;
            if (!ok) {
              messages.push("The face check could not start on this device, so you can do the voice part only.");
              F.hasCam = false;
              if (F.stream) F.stream.getVideoTracks().forEach(function (t) { t.stop(); });
            }
            note.textContent = messages.join(" ");
            go.disabled = false;
            say(note.textContent || "Ready.");
            if (!ok && !F.hasMic) go.textContent = "Close";
          });
        } else {
          F.faceReady = F.practice;
          note.textContent = messages.join(" ");
          go.disabled = false;
        }
        go.addEventListener("click", function () {
          if (!F.hasCam && !F.hasMic) return finishDialog();
          if (F.photoOn && F.hasCam && F.faceReady) return stepPhoto("");
          proceedFromDevices();
        });
        go.focus();
        say("Camera and microphone are ready.");
      });

      render("Camera and microphone", [
        h("p", { class: "muted small", text: stepNo(2) }),
        h("p", { text: "Please allow your camera and your microphone when your browser asks. You will see yourself in the box below so you know it works." }),
        consentLine,
        holder,
        note,
        err,
        actions,
        practiceRow
      ], "Camera and microphone. Press the button to allow them.");
    }

    function proceedFromDevices() {
      if (F.hasCam && F.faceReady) stepFace();
      else stepVoice();
    }

    function stepPhoto(retryMessage) {
      var retry = Boolean(retryMessage);
      var err = errorBox();
      var input = h("input", { type: "file", id: "idPhotoFile", accept: "image/jpeg,image/png" });
      var thumb = h("div", { class: "id-thumb", id: "idPhotoThumb", hidden: true });
      var info = h("p", { class: "small", role: "status" });
      var use = h("button", { type: "button", class: "btn primary", id: "idPhotoUse", text: retry ? "Send this photo" : "Continue", disabled: true });
      var skip = h("button", { type: "button", class: "btn quiet small", id: "idSkipPhoto", text: retry ? "Go on to the voice part" : "Skip the photo part" });
      if (retry) showError(err, retryMessage);
      function clearThumb() {
        C.clear(thumb);
        thumb.hidden = true;
        if (F.photoUrl) {
          URL.revokeObjectURL(F.photoUrl);
          F.photoUrl = null;
        }
      }
      input.addEventListener("change", async function () {
        err.hidden = true;
        info.textContent = "";
        use.disabled = true;
        F.idPhoto = null;
        clearThumb();
        var file = input.files && input.files[0];
        if (!file) return;
        info.textContent = "Getting your photo ready.";
        var made = await prepareIdPhoto(file);
        input.value = "";
        if (F.closed) return;
        info.textContent = "";
        if (made.error) {
          showError(err, made.error);
          return;
        }
        F.idPhoto = made.blob;
        F.photoUrl = URL.createObjectURL(made.blob);
        var img = h("img", { src: F.photoUrl, alt: "A small preview of the photo you chose" });
        thumb.append(img);
        thumb.hidden = false;
        use.disabled = false;
        info.textContent = "Photo ready.";
        say("Photo ready.");
      });
      use.addEventListener("click", async function () {
        if (!F.idPhoto) return;
        if (!retry) {
          proceedFromDevices();
          return;
        }
        use.disabled = true;
        use.setAttribute("aria-busy", "true");
        var res = await sendPhotos();
        if (F.closed) return;
        if (res.retry) return stepPhoto(res.retry);
        stepVoice();
      });
      skip.addEventListener("click", function () {
        dropPhotos();
        if (retry) stepVoice();
        else proceedFromDevices();
      });
      render("Photo of your ID", [
        retry ? null : h("p", { class: "muted small", text: "Step 3 of 5" }),
        h("p", { text: "Please add a photo of the face side of your ID card or passport page. You can skip this part." }),
        h("ul", { class: "id-notes" },
          h("li", { text: "Use a clear photo of the face side of your ID card or passport page." }),
          h("li", { text: "Use good light and avoid glare." }),
          h("li", { text: "Make sure your face in the photo is not covered." })
        ),
        h("label", { class: "small", for: "idPhotoFile", text: "Choose a photo" }),
        input,
        thumb,
        info,
        err,
        h("div", { class: "actions" }, use),
        h("div", { class: "row" }, skip)
      ], retry ? "Please choose another photo." : "Photo of your ID. Choose a photo or skip this part.");
      if (retry) input.focus();
    }

    async function sendPhotos() {
      if (!F.idPhoto || !F.frames.length || !F.session) {
        dropPhotos();
        return {};
      }
      var fd = new FormData();
      fd.append("id_photo", F.idPhoto, F.idPhoto.type === "image/png" ? "id.png" : "id.jpg");
      fd.append("live_1", F.frames[0], "live_1.jpg");
      if (F.frames[1]) fd.append("live_2", F.frames[1], "live_2.jpg");
      try {
        await C.api("POST", "/v1/verify/" + encodeURIComponent(F.session.session_id) + "/photos", { form: fd });
        F.photoSent = true;
        dropPhotos();
        return {};
      } catch (error) {
        if (error && error.status === 400 && error.message) {
          F.idPhoto = null;
          if (F.photoUrl) {
            URL.revokeObjectURL(F.photoUrl);
            F.photoUrl = null;
          }
          return { retry: error.message };
        }
        dropPhotos();
        F.notice = "We could not send your photo, so that part was left out. The rest of the check can go on.";
        return {};
      }
    }

    async function afterFace() {
      if (F.closed) return;
      if (!F.photoOn || !F.idPhoto || !F.frames.length) {
        dropPhotos();
        return stepVoice();
      }
      render("Sending your photo", [h("p", { text: "Please wait a moment." })], "Sending your photo.");
      var res = await sendPhotos();
      if (F.closed) return;
      if (res.retry) return stepPhoto(res.retry);
      stepVoice();
    }

    async function noteFrame(first) {
      if (!F.photoOn || !F.idPhoto) return;
      var blob = await grabFrame();
      if (!blob) return;
      if (first && !F.frames.length) F.frames[0] = blob;
      else F.frames[1] = blob;
    }

    function analysisForPractice(id, t) {
      var base = { noseOffset: 0, smileRatio: 6, mouthOpen: 0.1, ear: 0.3 };
      F.sim = { turn: 0, blink: 0, smile: 0, open: 0 };
      if (t < 1600) return base;
      if (id === "blink") {
        if (t < 1900) {
          F.sim.blink = 1;
          return Object.assign(base, { ear: 0.12 });
        }
        return base;
      }
      if (id === "turn_left") {
        F.sim.turn = 1;
        return Object.assign(base, { noseOffset: 0.35 });
      }
      if (id === "turn_right") {
        F.sim.turn = -1;
        return Object.assign(base, { noseOffset: -0.35 });
      }
      if (id === "smile") {
        F.sim.smile = 1;
        return Object.assign(base, { smileRatio: 11 });
      }
      if (id === "open_mouth") {
        F.sim.open = 1;
        return Object.assign(base, { mouthOpen: 0.55 });
      }
      return base;
    }

    async function detectLoop() {
      F.detect = true;
      var video = F.video;
      while (F.detect && !F.closed && F.mesh && video) {
        if (video.readyState >= 2) {
          try {
            await F.mesh.send({ image: video });
          } catch (e) {
            F.detect = false;
            F.onFrame = null;
            return;
          }
        }
        await wait(50);
      }
    }

    function dotsRow(steps, results, current) {
      var list = h("ol", { class: "id-dots", "aria-label": "Progress" });
      steps.forEach(function (s, i) {
        var state = i < results.length ? (results[i].passed ? "done" : "missed") : (i === current ? "current" : "pending");
        list.append(h("li", { class: "id-dot " + state, "aria-label": "Prompt " + (i + 1) + " of " + steps.length + (state === "done" ? ", done" : state === "current" ? ", now" : "") }));
      });
      return list;
    }

    async function stepFace() {
      var steps = (F.session && F.session.face && F.session.face.steps) || [];
      if (!steps.length) return stepVoice();
      var results = [];
      var sizeNote = h("p", { class: "small muted", text: stepNo(3) });
      var promptTitle = h("h3", { id: "idPrompt", tabindex: "-1", class: "id-prompt" });
      var hint = h("p", { class: "id-hint" });
      var count = h("p", { class: "small muted id-count", "aria-hidden": "true" });
      var info = h("p", { class: "small", role: "status" });
      var dotsBox = h("div");
      var retryBox = h("div", { class: "row id-retry", hidden: true });
      var skip = h("button", { type: "button", class: "btn quiet small", id: "idSkipFace", text: "Skip the face part" });
      var frame = previewFrame();
      skip.addEventListener("click", function () {
        F.skipFace = true;
        answer("skip");
      });
      C.clear(body);
      body.append(sizeNote, dotsBox, promptTitle, hint, frame, count, info, retryBox, h("div", { class: "row" }, skip));
      F.faceStart = Date.now();
      F.framesTotal = 0;
      F.framesWith = 0;
      var evaluator = null;
      var attemptStart = 0;
      var seen = 0;
      var passedFlag = false;
      F.onFrame = function (landmarks) {
        F.framesTotal += 1;
        var m = landmarks ? (landmarks.practice ? landmarks.practice : analyzeFace(landmarks)) : null;
        if (m) {
          F.framesWith += 1;
          seen += 1;
        }
        if (evaluator && !passedFlag && evaluator(m, Date.now())) {
          passedFlag = true;
          answer("passed");
        }
      };
      if (F.practice) {
        var current = null;
        every(function () {
          if (!evaluator || !current) return;
          var m = analysisForPractice(current, Date.now() - attemptStart);
          F.onFrame({ practice: m });
        }, 66);
        F.setCurrent = function (id) { current = id; };
      } else {
        detectLoop();
      }

      for (var i = 0; i < steps.length && !F.closed; i++) {
        var step = steps[i];
        C.clear(dotsBox);
        dotsBox.append(dotsRow(steps, results, i));
        promptTitle.textContent = step.label;
        hint.textContent = step.hint || "";
        say(step.label + ". " + (step.hint || ""));
        promptTitle.focus({ preventScroll: true });
        var outcome = null;
        var ms = 0;
        for (var attempt = 0; attempt < MAX_TRIES && !F.closed; attempt++) {
          retryBox.hidden = true;
          C.clear(retryBox);
          info.textContent = "";
          evaluator = createEvaluator(step.id);
          if (F.setCurrent) F.setCurrent(step.id);
          passedFlag = false;
          seen = 0;
          attemptStart = Date.now();
          var timer = every(function () {
            var left = Math.max(0, Math.ceil((PROMPT_MS - (Date.now() - attemptStart)) / 1000));
            count.textContent = plural(left, "second", "seconds") + " left";
            if (Date.now() - attemptStart >= PROMPT_MS) answer("timeout");
          }, 250);
          count.textContent = plural(Math.ceil(PROMPT_MS / 1000), "second", "seconds") + " left";
          var result = await ask();
          clearInterval(timer);
          evaluator = null;
          if (F.setCurrent) F.setCurrent(null);
          if (result === "closed") return;
          if (result === "skip") {
            outcome = "skip";
            break;
          }
          if (result === "passed") {
            outcome = "passed";
            ms = Date.now() - attemptStart;
            await noteFrame(!F.frames.length);
            info.textContent = "Thank you. That worked.";
            say("That worked.");
            await wait(700);
            break;
          }
          if (attempt >= MAX_TRIES - 1) {
            outcome = "missed";
            ms = PROMPT_MS;
            break;
          }
          var again = h("button", { type: "button", class: "btn primary", text: "Try again" });
          info.textContent = seen === 0
            ? "We could not see your face. Please sit in front of the camera with a little light on your face, then try again."
            : "We did not catch that one. You can try again, and there is no hurry.";
          say(info.textContent);
          retryBox.hidden = false;
          retryBox.append(again);
          again.addEventListener("click", function () { answer("again"); });
          again.focus();
          var next = await ask();
          if (next === "closed") return;
          if (next === "skip") {
            outcome = "skip";
            break;
          }
        }
        if (F.closed) return;
        if (outcome === "skip") break;
        results.push({ id: step.id, passed: outcome === "passed", ms: Math.round(ms) });
        C.clear(dotsBox);
        dotsBox.append(dotsRow(steps, results, i + 1));
      }
      F.onFrame = null;
      F.detect = false;
      if (F.closed) return;
      if (!F.frames.length) await noteFrame(true);
      if (F.closed) return;
      if (F.stream) F.stream.getVideoTracks().forEach(function (t) { t.stop(); });
      if (F.mesh) {
        try { F.mesh.close(); } catch (e) { F.mesh = null; }
        F.mesh = null;
      }
      if (F.skipFace || results.length < steps.length) {
        F.skipFace = true;
        return afterFace();
      }
      info.textContent = "Sending your face part.";
      var payload = { steps: results, frames_with_face: F.framesWith, total_frames: F.framesTotal, duration_ms: Date.now() - F.faceStart, model: "mediapipe-facemesh" };
      sendFace(payload);
    }

    async function sendFace(payload) {
      try {
        await C.api("POST", "/v1/verify/" + encodeURIComponent(F.session.session_id) + "/face", { json: payload });
        F.faceSent = true;
        if (F.closed) return;
        afterFace();
      } catch (error) {
        if (F.closed) return;
        var err = errorBox();
        showError(err, message(error));
        var retry = h("button", { type: "button", class: "btn primary", text: "Try sending again" });
        var skipBtn = h("button", { type: "button", class: "btn", text: "Go on to the voice part" });
        retry.addEventListener("click", function () { sendFace(payload); });
        skipBtn.addEventListener("click", function () { afterFace(); });
        render("We could not send that", [err, h("div", { class: "actions" }, skipBtn, retry)], "We could not send the face part.");
      }
    }

    function stepVoice() {
      if (F.closed) return;
      if (!F.hasMic || !F.session || !F.session.voice) {
        if (F.faceSent || F.photoSent) return stepDone();
        return finishDialog();
      }
      var maxSeconds = Math.min(Number(F.session.voice.max_seconds) || 10, WAV_MAX_SECONDS);
      var sentence = F.session.voice.sentence || "";
      var state = "idle";
      var recorded = null;
      var tookAgain = false;
      var startedAt = 0;
      var meter = levelMeter();
      var info = h("p", { class: "small", role: "status" });
      var clock = h("p", { class: "small muted", "aria-hidden": "true" });
      var err = errorBox();
      var rec = h("button", { type: "button", class: "btn primary", id: "idRecord", text: "Record" });
      var stop = h("button", { type: "button", class: "btn", id: "idStop", text: "Stop", hidden: true });
      var listenBtn = h("button", { type: "button", class: "btn", id: "idListen", text: "Listen back", hidden: true });
      var again = h("button", { type: "button", class: "btn", id: "idAgain", text: "Record again", hidden: true });
      var send = h("button", { type: "button", class: "btn primary", id: "idSend", text: "Send", hidden: true });
      var skip = h("button", { type: "button", class: "btn quiet small", id: "idSkipVoice", text: "Skip the voice part" });
      var chunks = [];
      var mime = "";
      var stream = F.stream ? new MediaStream(F.stream.getAudioTracks()) : null;

      function setState(next) {
        state = next;
        rec.hidden = next !== "idle";
        stop.hidden = next !== "recording";
        listenBtn.hidden = next !== "recorded";
        again.hidden = next !== "recorded" || tookAgain;
        send.hidden = next !== "recorded";
      }

      function finishRecording(blob, seconds) {
        recorded = { blob: blob, seconds: seconds };
        setState("recorded");
        clock.textContent = "";
        if (seconds < 1.5) {
          info.textContent = "That was very short. Please record again and read the whole sentence.";
          send.disabled = true;
        } else {
          info.textContent = "Recording done. You can listen back, record again once, or send it.";
          send.disabled = false;
        }
        say(info.textContent);
        (listenBtn.hidden ? rec : listenBtn).focus();
      }

      function stopRecording() {
        if (state !== "recording") return;
        var seconds = (Date.now() - startedAt) / 1000;
        if (clockTimer) clearInterval(clockTimer);
        if (F.practice) {
          finishRecording(practiceWav(Math.max(2, Math.min(seconds, maxSeconds))), seconds);
        } else if (F.recorder && F.recorder.state !== "inactive") {
          F.recorder.onstop = function () {
            finishRecording(new Blob(chunks, { type: mime || "audio/webm" }), seconds);
          };
          F.recorder.stop();
        }
      }

      var clockTimer = null;
      rec.addEventListener("click", function () {
        err.hidden = true;
        if (F.audio) {
          F.audio.pause();
          F.audio = null;
        }
        chunks = [];
        if (!F.practice) {
          if (!window.MediaRecorder || !stream) {
            showError(err, "This browser cannot record sound here. You can do the check later on another device.");
            return;
          }
          mime = pickRecorderMimeType();
          try {
            F.recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
          } catch (e) {
            showError(err, "We could not start the recording on this device.");
            return;
          }
          F.recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
          F.recorder.start(250);
        }
        startedAt = Date.now();
        setState("recording");
        info.textContent = "Recording now. Please read the sentence aloud.";
        say("Recording now.");
        stop.focus();
        clockTimer = every(function () {
          var left = Math.max(0, Math.ceil(maxSeconds - (Date.now() - startedAt) / 1000));
          clock.textContent = plural(left, "second", "seconds") + " left";
          if ((Date.now() - startedAt) / 1000 >= maxSeconds) stopRecording();
        }, 200);
        clock.textContent = plural(maxSeconds, "second", "seconds") + " left";
      });
      stop.addEventListener("click", stopRecording);
      again.addEventListener("click", function () {
        tookAgain = true;
        recorded = null;
        if (F.audio) {
          F.audio.pause();
          F.audio = null;
        }
        setState("idle");
        info.textContent = "Press Record when you are ready.";
        rec.focus();
      });
      listenBtn.addEventListener("click", function () {
        if (!recorded) return;
        if (F.audio) {
          F.audio.pause();
          F.audio = null;
          listenBtn.textContent = "Listen back";
          return;
        }
        var url = URL.createObjectURL(recorded.blob);
        F.urls.push(url);
        F.audio = new Audio(url);
        listenBtn.textContent = "Stop listening";
        F.audio.onended = function () {
          F.audio = null;
          listenBtn.textContent = "Listen back";
        };
        F.audio.play().catch(function () {
          F.audio = null;
          listenBtn.textContent = "Listen back";
          showError(err, "Your browser would not play the recording. You can still send it.");
        });
      });
      send.addEventListener("click", async function () {
        if (!recorded) return;
        err.hidden = true;
        send.disabled = true;
        send.setAttribute("aria-busy", "true");
        info.textContent = "Sending your voice part.";
        try {
          var wav = F.practice ? recorded.blob : await toWav16k(recorded.blob, maxSeconds);
          var fd = new FormData();
          fd.append("audio", wav, "voice.wav");
          await C.api("POST", "/v1/verify/" + encodeURIComponent(F.session.session_id) + "/voice", { form: fd });
          if (F.closed) return;
          stepDone();
        } catch (error) {
          if (F.closed) return;
          send.disabled = false;
          send.removeAttribute("aria-busy");
          info.textContent = "";
          showError(err, error && error.status ? message(error) : "We could not prepare that recording. Please record it again.");
        }
      });
      skip.addEventListener("click", function () {
        if (F.faceSent || F.photoSent) stepDone();
        else finishDialog();
      });

      render("Read one sentence", [
        h("p", { class: "muted small", text: stepNo(4) }),
        F.photoSent ? h("p", { class: "small", id: "idPhotoThanks", role: "status", text: PHOTO_RECEIVED }) : null,
        F.notice ? h("p", { class: "small", id: "idPhotoNotice", role: "status", text: F.notice }) : null,
        h("p", { class: "muted", text: "Press Record, then read this sentence aloud in your normal voice." }),
        h("blockquote", { class: "id-sentence", text: sentence }),
        F.hasMic ? meter : null,
        clock,
        info,
        err,
        h("div", { class: "row id-actions" }, rec, stop, listenBtn, again, send),
        h("div", { class: "row" }, skip)
      ], "Read one sentence. Press Record when you are ready.");
      setState("idle");
      runMeter(meter);
      rec.focus();
    }

    function stepDone() {
      stopMedia();
      if (F.meterTimer) clearInterval(F.meterTimer);
      var closeNow = h("button", { type: "button", class: "btn primary", id: "idDone", text: "Close" });
      closeNow.addEventListener("click", finishDialog);
      render("Thank you", [
        h("p", { text: "Thank you. Your check was received. The hiring team will read it." }),
        F.photoSent ? h("p", { id: "idPhotoDone", text: PHOTO_RECEIVED }) : null,
        F.notice ? h("p", { class: "small", text: F.notice }) : null,
        h("div", { class: "actions" }, closeNow)
      ], "Thank you. Your check was received.");
      closeNow.focus();
      onDone();
    }

    dlg.showModal();
    stepConsent();
  }

  function card(applicationId, options) {
    var opts = options || {};
    var uid = "idc" + Math.random().toString(36).slice(2, 7);
    var box = h("section", { class: "card id-card", id: uid, "aria-labelledby": uid + "T" });
    var state = opts.answered ? "answered" : "open";
    function draw() {
      C.clear(box);
      box.append(h("div", { class: "row between" }, h("h2", { id: uid + "T", text: "Quick identity check" }), state === "answered" ? C.tag("Sent", "info", true) : C.tag("Optional", "info", true)));
      if (state === "answered") {
        box.append(h("p", { class: "id-sent", text: "Sent. Thank you." }));
        return;
      }
      if (state === "later") {
        box.append(h("p", { text: "No problem. You can do this later from My applications. It is optional." }));
        return;
      }
      box.append(
        h("p", { text: "This takes about one minute. It uses your camera and your microphone. Nothing is recorded or kept. A person at the hiring team reads the result." }),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn primary", "data-id-start": "1", text: "Start the check", onclick: function (event) {
            openFlow(applicationId, { onDone: function () {
              state = "answered";
              draw();
              if (opts.onDone) opts.onDone();
            } });
            event.currentTarget.blur();
          } }),
          opts.later === false ? null : h("button", { type: "button", class: "btn", "data-id-later": "1", text: "Do this later", onclick: function () {
            state = "later";
            draw();
            C.status("You can do the identity check later from My applications.");
          } })
        )
      );
    }
    draw();
    return box;
  }

  var FACE_TEXT = {
    passed: "Prompts were completed",
    not_seen: "A face was not seen clearly",
    steps_incomplete: "Some prompts were not completed",
    implausible: "The measurements look unlikely",
    missing: "No face part was sent"
  };
  var VOICE_TEXT = {
    human_like: "No machine like signs found",
    synthetic_suspected: "Looked very even",
    replay_suspected: "Started abruptly",
    unclear: "Unclear",
    too_short: "Too short to judge",
    missing: "No voice part was sent"
  };
  var VOICE_TONE = { human_like: "closed", synthetic_suspected: "warn", replay_suspected: "warn" };
  var PHOTO_STATE = {
    match: { tag: "Looked alike", tone: "closed", text: "The live face looked like the ID photo." },
    no_match: { tag: "Did not look alike", tone: "warn", text: "The live face did not look like the ID photo. This can happen with poor light, glasses or an old photo. Consider a short live video call." },
    no_face_in_id: { tag: "Could not compare", tone: "closed", text: "No face could be found in the ID photo." },
    no_face_live: { tag: "Could not compare", tone: "closed", text: "No face could be found in the pictures from the camera." },
    several_faces: { tag: "Could not compare", tone: "closed", text: "More than one face was found in a picture." },
    unreadable: { tag: "Could not compare", tone: "closed", text: "One of the pictures could not be read." },
    not_available: { tag: "Not available", tone: "closed", text: "Photo comparison was not available." },
    missing: { tag: "Not done", tone: "closed", text: "Not done. This is not held against the candidate." }
  };
  var PHOTO_HONEST = "Face matching can be wrong. It can miss the same person in poor light and can pass someone who looks similar. It works differently for different groups of people and we have not measured that here. Use it only as a hint, never as proof.";
  var STATUS_TEXT = { complete: "Complete", partial: "Partly done", none: "Not done" };

  function labelOf(key) {
    var text = String(key).replace(/_/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function valueOf(v) {
    if (v === true) return "Yes";
    if (v === false) return "No";
    if (v === null || v === undefined) return "Not available";
    if (typeof v === "number") return String(Math.round(v * 100) / 100);
    if (typeof v === "object") return Object.keys(v).map(function (k) { return labelOf(k) + " " + valueOf(v[k]); }).join(", ");
    return String(v);
  }

  async function recruiterLoad(id, body, isStale) {
    C.clear(body);
    body.append(h("p", { class: "muted", text: "Loading..." }));
    var data;
    try {
      data = await C.api("GET", "/v1/verify/application/" + encodeURIComponent(id));
    } catch (error) {
      if (isStale && isStale()) return;
      C.clear(body);
      if (error && (error.status === 404 || error.status === 501)) {
        body.append(h("p", { class: "muted", id: "idNone", text: "No identity check has been done yet." }));
        return;
      }
      if (C.authError && C.authError(error)) return;
      body.append(h("p", { class: "alert error", role: "alert", text: C.friendly(error) }));
      return;
    }
    if (isStale && isStale()) return;
    C.clear(body);
    data = data || {};
    var face = data.face || {};
    var voice = data.voice || {};
    var kv = h("dl", { class: "kv", id: "idResult" });
    if (data.photo && typeof data.photo === "object") {
      var ph = data.photo;
      var pinfo = PHOTO_STATE[ph.state] || PHOTO_STATE.not_available;
      var pdetail = [];
      if (typeof ph.similarity === "number" && typeof ph.threshold === "number") pdetail.push("Closeness " + (Math.round(ph.similarity * 100) / 100).toFixed(2) + " where " + (Math.round(ph.threshold * 100) / 100).toFixed(2) + " or more counts as alike.");
      if (typeof ph.frames_checked === "number" && ph.frames_checked > 0) pdetail.push(plural(ph.frames_checked, "camera picture was", "camera pictures were") + " checked.");
      kv.append(h("dt", { text: "Photo match" }), h("dd", { id: "idPhoto" },
        C.tag(pinfo.tag, pinfo.tone, true),
        h("p", { id: "idPhotoText", style: "margin:4px 0 0", text: pinfo.text }),
        pdetail.length ? h("p", { class: "small muted", id: "idPhotoDetail", style: "margin:4px 0 0", text: pdetail.join(" ") }) : null,
        h("p", { class: "small muted", id: "idPhotoHonest", style: "margin:4px 0 0", text: PHOTO_HONEST })
      ));
    }
    kv.append(h("dt", { text: "Status" }), h("dd", null, C.tag(STATUS_TEXT[data.status] || "Not done", "closed", true)));
    var codeText = voice.code_matched === true ? "The spoken code matched" : voice.code_matched === false ? "The spoken code did not match. Speech to text makes mistakes, so this is not proof." : "The spoken code was not checked";
    kv.append(h("dt", { text: "Spoken code" }), h("dd", { id: "idCode", text: codeText }));
    kv.append(h("dt", { text: "Voice part" }), h("dd", null, C.tag(VOICE_TEXT[voice.state] || "No voice part was sent", VOICE_TONE[voice.state] || "closed", true), voice.state && voice.state !== "missing" ? h("p", { class: "small muted", id: "idVoiceNote", style: "margin:4px 0 0", text: "This rule based check cannot tell a machine voice from a person. A clean result proves nothing." }) : null));
    kv.append(h("dt", { text: "Sentence match" }), h("dd", { text: typeof voice.sentence_match === "number" ? Math.round(voice.sentence_match * 100) + " percent" : "Not available" }));
    kv.append(h("dt", { text: "Transcript made by" }), h("dd", { text: voice.transcript_source === "server" ? "The server" : voice.transcript_source === "browser" ? "The browser" : "Not available" }));
    var faceLine = [C.tag(FACE_TEXT[face.state] || "No face part was sent", "closed", true)];
    if (typeof face.steps_total === "number") faceLine.push(" ", face.steps_done + " of " + face.steps_total + " prompts done");
    kv.append(h("dt", { text: "Face part" }), h("dd", null, faceLine));
    if (data.completed_at) kv.append(h("dt", { text: "Finished" }), h("dd", { text: C.fmtTime(typeof data.completed_at === "number" ? data.completed_at : Math.floor(Date.parse(data.completed_at) / 1000)) }));
    body.append(kv);
    if (data.summary) body.append(h("p", { style: "margin-top:12px", text: data.summary }));
    var indicators = voice.indicators && typeof voice.indicators === "object" ? Object.keys(voice.indicators) : [];
    if (indicators.length) {
      var ind = h("dl", { class: "kv", id: "idIndicators" });
      indicators.forEach(function (k) { ind.append(h("dt", { text: labelOf(k) }), h("dd", { text: valueOf(voice.indicators[k]) })); });
      body.append(h("h4", { text: "Voice indicators" }), ind);
    }
    var notes = Array.isArray(data.notes) ? data.notes.filter(Boolean) : [];
    if (notes.length) body.append(h("h4", { text: "Notes" }), h("ul", { class: "id-notes" }, notes.map(function (n) { return h("li", { text: String(n) }); })));
    var ask = data.advisory === "ask_for_live_check";
    body.append(h("p", { class: "alert " + (ask ? "warn" : "info"), id: "idAdvisory", style: "margin-top:12px", text: ask ? "A person may want to ask for a short live video call." : "Nothing here needs follow up." }));
    body.append(h("p", { class: "small muted", id: "idHonest", text: "The face check is measured in the candidate's browser and the voice check is a rule based estimate, not a trained model. Both can be fooled. Use them only as a hint, never as proof." }));
  }

  C.identity = {
    start: openFlow,
    card: card,
    recruiterLoad: recruiterLoad,
    geometry: { eyeAspectRatio: eyeAspectRatio, analyzeFace: analyzeFace, createBlinkDetector: createBlinkDetector, createEvaluator: createEvaluator },
    audio: { encodeWav: encodeWav, toWav16k: toWav16k }
  };
})();
