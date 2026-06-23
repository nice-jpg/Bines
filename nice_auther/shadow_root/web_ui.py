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
    #screen { display: block; max-width: 100vw; max-height: calc(100vh - 188px); margin: 0 auto; touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
    #state { margin-left: auto; font-size: 13px; color: #cbd5e1; }
    #debugLog { position: fixed; left: 0; right: 0; bottom: 0; height: 140px; overflow: auto; box-sizing: border-box; padding: 6px 8px; background: rgba(0,0,0,.86); color: #d1fae5; font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; border-top: 1px solid #374151; z-index: 10; }
    .log-warn { color: #fde68a; }
    .log-error { color: #fecaca; }
  </style>
</head>
<body>
  <header>
    <button id="start">开始</button>
    <button id="stop">结束</button>
    <span id="state">idle</span>
  </header>
  <img id="screen" draggable="false">
  <div id="debugLog" aria-live="polite"></div>
  <script>
    const params = new URLSearchParams(location.search);
    const token = params.get("token") || "";
    const screen = document.getElementById("screen");
    const state = document.getElementById("state");
    const debugLog = document.getElementById("debugLog");
    const pendingEvents = [];
    const debugLines = [];
    let flushScheduled = false;
    let flushing = false;

    function log(message, data = null, level = "info") {
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

    function safeJson(value) {
      try { return JSON.stringify(value); } catch (err) { return String(value); }
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
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
      } catch (err) {
        log("status failed", {name: err.name, message: err.message}, "error");
      }
    }

    function payloadFromPoint(point, type, pointerId, timeStamp, pressure = 0.5) {
      const rect = screen.getBoundingClientRect();
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

    function queuePointerEvent(ev, type) {
      ev.preventDefault();
      const coalesced = typeof ev.getCoalescedEvents === "function" ? ev.getCoalescedEvents() : [];
      const source = coalesced.length ? coalesced : [ev];
      for (const item of source) pendingEvents.push(pointerPayload(item, type));
      log(`pointer ${type}`, {pointerId: ev.pointerId, pointerType: ev.pointerType, count: source.length, pending: pendingEvents.length});
      scheduleFlush(type === "pointerup" || type === "pointercancel");
    }

    function queueTouchEvent(ev, type) {
      ev.preventDefault();
      const touches = ev.changedTouches || [];
      for (const touch of touches) {
        pendingEvents.push(payloadFromPoint(touch, type, touch.identifier + 1, ev.timeStamp, type === "pointerup" ? 0 : 0.5));
      }
      log(`touch ${type}`, {changed: touches.length, pending: pendingEvents.length});
      scheduleFlush(type === "pointerup" || type === "pointercancel");
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

    async function flushEvents() {
      if (flushing) return;
      flushScheduled = false;
      if (!pendingEvents.length) return;
      flushing = true;
      const events = pendingEvents.splice(0, pendingEvents.length);
      log("flush start", {events: events.length, first: events[0], last: events[events.length - 1]});
      try {
        const result = await post("/events", {events});
        log("flush complete", result);
      } finally {
        flushing = false;
        if (pendingEvents.length) scheduleFlush();
      }
    }

    document.getElementById("start").onclick = async () => { log("start clicked"); await post("/recording/start"); await refreshStatus(); };
    document.getElementById("stop").onclick = async () => {
      log("stop clicked");
      const result = await post("/recording/stop");
      await refreshStatus();
      console.log("recording bundle", result.bundle);
      log("recording bundle", {hasBundle: !!result.bundle, operations: result.bundle && result.bundle.operations ? result.bundle.operations.length : 0});
    };
    screen.addEventListener("contextmenu", ev => ev.preventDefault());
    log("page loaded", {pointerEvent: !!window.PointerEvent, userAgent: navigator.userAgent, maxTouchPoints: navigator.maxTouchPoints || 0});
    if (window.PointerEvent) {
      log("binding pointer events");
      screen.addEventListener("pointerdown", ev => { screen.setPointerCapture(ev.pointerId); queuePointerEvent(ev, "pointerdown"); }, {passive: false});
      screen.addEventListener("pointermove", ev => queuePointerEvent(ev, "pointermove"), {passive: false});
      screen.addEventListener("pointerup", ev => queuePointerEvent(ev, "pointerup"), {passive: false});
      screen.addEventListener("pointercancel", ev => queuePointerEvent(ev, "pointercancel"), {passive: false});
    } else {
      log("binding touch events");
      screen.addEventListener("touchstart", ev => queueTouchEvent(ev, "pointerdown"), {passive: false});
      screen.addEventListener("touchmove", ev => queueTouchEvent(ev, "pointermove"), {passive: false});
      screen.addEventListener("touchend", ev => queueTouchEvent(ev, "pointerup"), {passive: false});
      screen.addEventListener("touchcancel", ev => queueTouchEvent(ev, "pointercancel"), {passive: false});
    }

    screen.src = "/stream.mjpg" + (token ? "?token=" + encodeURIComponent(token) : "");
    log("stream started", {src: screen.src});
    refreshStatus();
  </script>
</body>
</html>
"""
