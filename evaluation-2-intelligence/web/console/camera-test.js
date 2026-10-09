(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var stream = null;
  var meterTimer = 0;
  var audioContext = null;

  var HELP = {
    NotAllowedError: "Permission was refused. Click the icon at the left of the address bar, set Camera and Microphone to Allow, then reload this page. On a Mac also open System Settings, then Privacy and Security, then Camera and Microphone, and turn on your browser. Then close the browser fully and open it again.",
    SecurityError: "The browser blocked the camera because this page is not secure. Open the page with https or on localhost.",
    NotFoundError: "No camera or microphone was found. Plug one in or check that it is not switched off.",
    NotReadableError: "The camera is in use by another app or is blocked by the computer. Close Zoom, FaceTime, Photo Booth and other tabs that use the camera, then try again.",
    AbortError: "The camera could not be started. Close other apps that use it and try again.",
    OverconstrainedError: "The camera does not support the settings we asked for."
  };

  function row(name, value) {
    var dl = $("info");
    var dt = document.createElement("dt");
    var dd = document.createElement("dd");
    dt.textContent = name;
    dd.textContent = value;
    dl.append(dt, dd);
  }

  async function describe() {
    $("info").textContent = "";
    $("secure").textContent = window.isSecureContext
      ? "This page is secure, so the browser may allow the camera."
      : "This page is not secure, so the browser will block the camera. Open it with https or on localhost.";
    row("Address", location.origin);
    for (var i = 0; i < 2; i++) {
      var name = i === 0 ? "camera" : "microphone";
      try {
        var status = await navigator.permissions.query({ name: name });
        row("Permission for " + name, status.state === "prompt" ? "not answered yet" : (status.state === "granted" ? "allowed" : "blocked"));
      } catch (e) {
        row("Permission for " + name, "the browser did not say");
      }
    }
    try {
      var devices = await navigator.mediaDevices.enumerateDevices();
      var cams = devices.filter(function (d) { return d.kind === "videoinput"; });
      var mics = devices.filter(function (d) { return d.kind === "audioinput"; });
      row("Cameras found", String(cams.length));
      row("Microphones found", String(mics.length));
      cams.forEach(function (d, n) { if (d.label) row("Camera " + (n + 1), d.label); });
    } catch (e) {
      row("Devices", "could not be listed");
    }
  }

  function stop() {
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null;
    clearInterval(meterTimer);
    if (audioContext) { try { audioContext.close(); } catch (e) { audioContext = null; } }
    audioContext = null;
    $("frame").textContent = "";
    var note = document.createElement("p");
    note.className = "muted small id-frame-note";
    note.textContent = "No camera preview yet";
    $("frame").append(note);
    $("level").style.width = "0";
    $("stop").disabled = true;
    $("start").disabled = false;
  }

  async function start() {
    $("result").textContent = "Your browser may ask for permission. Please choose Allow. The box is near the top left of the page.";
    $("start").disabled = true;
    var tries = [{ video: true, audio: true }, { video: true }, { audio: true }];
    var lastError = null;
    for (var i = 0; i < tries.length && !stream; i++) {
      try {
        stream = await navigator.mediaDevices.getUserMedia(tries[i]);
      } catch (e) {
        lastError = e;
        if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) break;
      }
    }
    if (!stream) {
      $("start").disabled = false;
      var name = lastError && lastError.name;
      $("result").textContent = (HELP[name] || "The camera could not be opened.") + (name ? " The browser reported " + name + "." : "");
      describe();
      return;
    }
    var video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    $("frame").textContent = "";
    $("frame").append(video);
    var hasVideo = stream.getVideoTracks().length > 0;
    var hasAudio = stream.getAudioTracks().length > 0;
    $("result").textContent = (hasVideo ? "The camera is working. " : "No camera was found. ") + (hasAudio ? "The microphone is working. Say a few words to see the bar move." : "No microphone was found.");
    $("stop").disabled = false;
    if (hasAudio) {
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        audioContext = new Ctx();
        var analyser = audioContext.createAnalyser();
        analyser.fftSize = 1024;
        audioContext.createMediaStreamSource(new MediaStream(stream.getAudioTracks())).connect(analyser);
        var data = new Uint8Array(analyser.fftSize);
        meterTimer = setInterval(function () {
          analyser.getByteTimeDomainData(data);
          var peak = 0;
          for (var k = 0; k < data.length; k++) peak = Math.max(peak, Math.abs(data[k] - 128));
          $("level").style.width = Math.min(100, Math.round(peak / 64 * 100)) + "%";
        }, 80);
      } catch (e) {
        audioContext = null;
      }
    }
    describe();
  }

  $("start").addEventListener("click", start);
  $("stop").addEventListener("click", stop);
  window.addEventListener("pagehide", stop);
  describe();
})();
