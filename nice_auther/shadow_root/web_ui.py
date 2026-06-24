"""Browser UI served by shadow_root."""

INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>nice_auther shadow_root</title>
  <style>
    html, body { width: 100%; height: 100%; overflow: hidden; overscroll-behavior: none; }
    body { margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; touch-action: none; }
    header { height: 48px; display: flex; gap: 8px; align-items: center; padding: 0 12px; background: #1f2933; }
    button { height: 32px; padding: 0 12px; border: 0; border-radius: 4px; background: #e5e7eb; color: #111; }
    #screenVideo, #screenImage { display: none; width: 100vw; height: calc(100vh - 48px); object-fit: contain; margin: 0 auto; touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; background: #000; }
    #screenVideo.active, #screenImage.active { display: block; }
    body.debug-on #screenVideo, body.debug-on #screenImage { height: calc(100vh - 188px); }
    #state { margin-left: auto; font-size: 13px; color: #cbd5e1; }
    #debugLog { display: none; position: fixed; left: 0; right: 0; bottom: 0; height: 140px; overflow: auto; box-sizing: border-box; padding: 6px 8px; background: rgba(0,0,0,.86); color: #d1fae5; font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; border-top: 1px solid #374151; z-index: 10; }
    body.debug-on #debugLog { display: block; }
    .log-warn { color: #fde68a; }
    .log-error { color: #fecaca; }
  </style>
</head>
<body>
  <header>
    <button id="wake">唤醒</button>
    <button id="debugToggle">Debug</button>
    <span id="state">idle</span>
  </header>
  <video id="screenVideo" autoplay playsinline muted></video>
  <img id="screenImage" draggable="false">
  <div id="debugLog" aria-live="polite"></div>
  <script>
    const params = new URLSearchParams(location.search);
    const token = params.get("token") || "";
    const screenVideo = document.getElementById("screenVideo");
    const screenImage = document.getElementById("screenImage");
    const state = document.getElementById("state");
    const debugLog = document.getElementById("debugLog");
    const debugToggle = document.getElementById("debugToggle");
    const pendingEvents = [];
    const activePointers = new Set();
    const debugLines = [];
    const debugStorageKey = "nice_auther_debug";
    const gestureFlushTimeoutMs = 2500;
    let activeScreen = screenVideo;
    let debugEnabled = params.get("debug") === "1" || params.get("debug") === "true" || localStorage.getItem(debugStorageKey) === "1";
    let flushScheduled = false;
    let flushing = false;
    let flushTimer = 0;
    let peerConnection = null;
    let controlChannel = null;
    let wakeInFlight = false;

    function log(message, data = null, level = "info") {
      if (!debugEnabled) return;
      const time = new Date().toLocaleTimeString();
      const suffix = data === null ? "" : " " + safeJson(data);
      const line = `[${time}] ${message}${suffix}`;
      debugLines.push({line, level});
      while (debugLines.length > 80) debugLines.shift();
      debugLog.innerHTML = debugLines.map(item => `<div class="log-${item.level}">${escapeHtml(item.line)}</div>`).join("");
      debugLog.scrollTop = debugLog.scrollHeight;
      if (level === "error") console.error(message, data);
      else if (level === "warn") console.warn(message, data);
      else console.log(message, data);
    }

    function setDebugEnabled(enabled) {
      debugEnabled = !!enabled;
      document.body.classList.toggle("debug-on", debugEnabled);
      debugToggle.textContent = debugEnabled ? "Debug On" : "Debug";
      localStorage.setItem(debugStorageKey, debugEnabled ? "1" : "0");
      if (debugEnabled) log("debug enabled");
    }

    function safeJson(value) {
      try { return JSON.stringify(value); } catch (err) { return String(value); }
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
    }

    function sdpCandidates(sdp) {
      return String(sdp || "").split(/\\r?\\n/).filter(line => line.startsWith("a=candidate:")).slice(0, 8);
    }

    async function post(path, payload = {}) {
      const body = JSON.stringify(payload);
      log(`POST ${path}`, {bytes: body.length, events: Array.isArray(payload.events) ? payload.events.length : undefined});
      try {
        const res = await fetch(path + (token ? "?token=" + encodeURIComponent(token) : ""), {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-Shadow-Token": token},
          body
        });
        const text = await res.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (err) { data = {raw: text}; }
        log(`POST ${path} -> ${res.status}`, data, res.ok ? "info" : "warn");
        return data;
      } catch (err) {
        log(`POST ${path} failed`, {name: err.name, message: err.message}, "error");
        throw err;
      }
    }

    async function refreshStatus() {
      try {
        const res = await fetch("/status" + (token ? "?token=" + encodeURIComponent(token) : ""), {headers: {"X-Shadow-Token": token}});
        const data = await res.json();
        state.textContent = data.recording ? "recording" : "idle";
        log("status", data);
        return data;
      } catch (err) {
        log("status failed", {name: err.name, message: err.message}, "error");
        return {ok: false, video: {backend: "webrtc_h264"}};
      }
    }

    function payloadFromPoint(point, type, pointerId, timeStamp, pressure = 0.5) {
      const rect = activeScreen.getBoundingClientRect();
      return {
        type,
        pointer_id: pointerId || 1,
        x: point.clientX - rect.left,
        y: point.clientY - rect.top,
        width: rect.width,
        height: rect.height,
        pressure,
        client_time_ms: timeStamp || performance.now()
      };
    }

    function pointerPayload(ev, type) {
      return payloadFromPoint(ev, type, ev.pointerId || 1, ev.timeStamp, ev.pressure || 0.5);
    }

    function updatePointerState(pointerId, type) {
      const key = String(pointerId || 1);
      if (type === "pointerdown") activePointers.add(key);
      else if (type === "pointerup" || type === "pointercancel") activePointers.delete(key);
    }

    function queuePointerEvent(ev, type) {
      ev.preventDefault();
      const coalesced = typeof ev.getCoalescedEvents === "function" ? ev.getCoalescedEvents() : [];
      const source = coalesced.length ? coalesced : [ev];
      for (const item of source) pendingEvents.push(pointerPayload(item, type));
      updatePointerState(ev.pointerId, type);
      log(`pointer ${type}`, {pointerId: ev.pointerId, pointerType: ev.pointerType, count: source.length, pending: pendingEvents.length});
      scheduleGestureFlush(type === "pointerup" || type === "pointercancel");
    }

    function queueTouchEvent(ev, type) {
      ev.preventDefault();
      const touches = ev.changedTouches || [];
      for (const touch of touches) {
        pendingEvents.push(payloadFromPoint(touch, type, touch.identifier + 1, ev.timeStamp, type === "pointerup" ? 0 : 0.5));
        updatePointerState(touch.identifier + 1, type);
      }
      log(`touch ${type}`, {changed: touches.length, pending: pendingEvents.length});
      scheduleGestureFlush(type === "pointerup" || type === "pointercancel");
    }

    function scheduleGestureFlush(gestureEnded = false) {
      if (gestureEnded || activePointers.size === 0) {
        clearFlushTimer();
        scheduleFlush(true);
        return;
      }
      scheduleFlushTimeout();
    }

    function scheduleFlushTimeout() {
      if (flushTimer) return;
      flushTimer = window.setTimeout(() => {
        flushTimer = 0;
        log("flush scheduled timeout", {pending: pendingEvents.length, active: activePointers.size});
        void flushEvents();
      }, gestureFlushTimeoutMs);
    }

    function clearFlushTimer() {
      if (!flushTimer) return;
      window.clearTimeout(flushTimer);
      flushTimer = 0;
    }

    function scheduleFlush(immediate = false) {
      if (immediate) {
        log("flush scheduled immediate", {pending: pendingEvents.length});
        void flushEvents();
        return;
      }
      if (!flushScheduled) {
        flushScheduled = true;
        log("flush scheduled raf", {pending: pendingEvents.length});
        requestAnimationFrame(flushEvents);
      }
    }

    async function sendEvents(events) {
      if (controlChannel && controlChannel.readyState === "open") {
        controlChannel.send(JSON.stringify({events}));
        return {ok: true, transport: "datachannel", events: events.length};
      }
      return await post("/events", {events});
    }

    async function wakeDisplay(reason = "manual") {
      if (wakeInFlight) return {ok: true, skipped: true};
      wakeInFlight = true;
      log("wake", {reason});
      try {
        if (screenVideo.srcObject) {
          try { await screenVideo.play(); } catch (err) { log("video play failed", {name: err.name, message: err.message}, "warn"); }
        }
        const result = await post("/wake", {reason});
        log("wake complete", result);
        return result;
      } finally {
        window.setTimeout(() => { wakeInFlight = false; }, 500);
      }
    }

    async function flushEvents() {
      if (flushing) return;
      flushScheduled = false;
      if (!pendingEvents.length) return;
      flushing = true;
      const events = pendingEvents.splice(0, pendingEvents.length);
      log("flush start", {events: events.length, first: events[0], last: events[events.length - 1]});
      try {
        const result = await sendEvents(events);
        log("flush complete", result);
      } finally {
        flushing = false;
        if (pendingEvents.length) scheduleGestureFlush(activePointers.size === 0);
      }
    }

    function bindScreenEvents(target) {
      target.addEventListener("contextmenu", ev => ev.preventDefault());
      if (window.PointerEvent) {
        log("binding pointer events");
        target.addEventListener("pointerdown", ev => { target.setPointerCapture(ev.pointerId); queuePointerEvent(ev, "pointerdown"); }, {passive: false});
        target.addEventListener("pointermove", ev => queuePointerEvent(ev, "pointermove"), {passive: false});
        target.addEventListener("pointerup", ev => queuePointerEvent(ev, "pointerup"), {passive: false});
        target.addEventListener("pointercancel", ev => queuePointerEvent(ev, "pointercancel"), {passive: false});
      } else {
        log("binding touch events");
        target.addEventListener("touchstart", ev => queueTouchEvent(ev, "pointerdown"), {passive: false});
        target.addEventListener("touchmove", ev => queueTouchEvent(ev, "pointermove"), {passive: false});
        target.addEventListener("touchend", ev => queueTouchEvent(ev, "pointerup"), {passive: false});
        target.addEventListener("touchcancel", ev => queueTouchEvent(ev, "pointercancel"), {passive: false});
      }
    }

    function waitForIceGatheringComplete(pc) {
      if (pc.iceGatheringState === "complete") return Promise.resolve();
      return new Promise(resolve => {
        const done = () => {
          if (pc.iceGatheringState === "complete") {
            pc.removeEventListener("icegatheringstatechange", done);
            resolve();
          }
        };
        pc.addEventListener("icegatheringstatechange", done);
      });
    }

    async function startWebRtc() {
      activeScreen = screenVideo;
      screenVideo.classList.add("active");
      screenImage.classList.remove("active");
      peerConnection = new RTCPeerConnection();
      controlChannel = peerConnection.createDataChannel("control", {ordered: true});
      controlChannel.onopen = () => { log("DataChannel open"); void wakeDisplay("datachannel-open"); };
      controlChannel.onclose = () => log("DataChannel closed", null, "warn");
      peerConnection.onconnectionstatechange = () => {
        log("WebRTC connection state", {state: peerConnection.connectionState});
        if (peerConnection.connectionState === "connected") void wakeDisplay("webrtc-connected");
      };
      peerConnection.oniceconnectionstatechange = () => log("WebRTC ice state", {state: peerConnection.iceConnectionState});
      peerConnection.ontrack = ev => {
        screenVideo.srcObject = ev.streams[0];
        void screenVideo.play().catch(err => log("video play failed", {name: err.name, message: err.message}, "warn"));
        log("WebRTC track", {kind: ev.track.kind});
        void wakeDisplay("track");
      };
      screenVideo.onwaiting = () => { log("video waiting", null, "warn"); void wakeDisplay("video-waiting"); };
      screenVideo.onplaying = () => log("video playing", {width: screenVideo.videoWidth, height: screenVideo.videoHeight});
      screenVideo.onloadedmetadata = () => log("video metadata", {width: screenVideo.videoWidth, height: screenVideo.videoHeight, readyState: screenVideo.readyState});
      screenVideo.onresize = () => log("video resize", {width: screenVideo.videoWidth, height: screenVideo.videoHeight});
      screenVideo.onerror = () => log("video error", {code: screenVideo.error && screenVideo.error.code, message: screenVideo.error && screenVideo.error.message}, "error");
      peerConnection.addTransceiver("video", {direction: "recvonly"});
      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      await waitForIceGatheringComplete(peerConnection);
      log("local ICE candidates", sdpCandidates(peerConnection.localDescription && peerConnection.localDescription.sdp));
      const answer = await post("/webrtc/offer", peerConnection.localDescription);
      if (!answer.sdp) throw new Error(answer.error || "WebRTC answer missing sdp");
      log("remote ICE candidates", sdpCandidates(answer.sdp));
      await peerConnection.setRemoteDescription(answer);
      log("WebRTC connected", {type: answer.type});
      void wakeDisplay("answer");
    }

    function startMjpeg() {
      activeScreen = screenImage;
      screenImage.classList.add("active");
      screenVideo.classList.remove("active");
      screenImage.src = "/stream.mjpg" + (token ? "?token=" + encodeURIComponent(token) : "");
      log("stream started", {src: screenImage.src});
    }

    document.getElementById("wake").onclick = async () => { await wakeDisplay("button"); };
    debugToggle.onclick = () => setDebugEnabled(!debugEnabled);
    setDebugEnabled(debugEnabled);
    log("page loaded", {pointerEvent: !!window.PointerEvent, userAgent: navigator.userAgent, maxTouchPoints: navigator.maxTouchPoints || 0});
    bindScreenEvents(screenVideo);
    bindScreenEvents(screenImage);
    refreshStatus().then(data => {
      if (data.video && data.video.backend === "mjpeg_screencap") startMjpeg();
      else if (data.webrtc_error) {
        state.textContent = "webrtc config error";
        log("WebRTC unavailable", {error: data.webrtc_error}, "error");
      }
      else startWebRtc().catch(err => {
        state.textContent = "webrtc error";
        log("WebRTC failed", {name: err.name, message: err.message}, "error");
      });
    });
  </script>
</body>
</html>
"""
