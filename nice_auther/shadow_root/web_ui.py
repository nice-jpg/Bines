"""Browser UI served by shadow_root."""

INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>nice_auther shadow_root</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }
    header { height: 48px; display: flex; gap: 8px; align-items: center; padding: 0 12px; background: #1f2933; }
    button { height: 32px; padding: 0 12px; border: 0; border-radius: 4px; background: #e5e7eb; color: #111; }
    #screen { display: block; max-width: 100vw; max-height: calc(100vh - 48px); margin: 0 auto; touch-action: none; user-select: none; }
    #state { margin-left: auto; font-size: 13px; color: #cbd5e1; }
  </style>
</head>
<body>
  <header>
    <button id="start">开始</button>
    <button id="stop">结束</button>
    <span id="state">idle</span>
  </header>
  <img id="screen" draggable="false">
  <script>
    const params = new URLSearchParams(location.search);
    const token = params.get("token") || "";
    const screen = document.getElementById("screen");
    const state = document.getElementById("state");
    let intervalMs = 500;

    async function post(path, payload = {}) {
      const res = await fetch(path + (token ? "?token=" + encodeURIComponent(token) : ""), {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Shadow-Token": token},
        body: JSON.stringify(payload)
      });
      return await res.json();
    }

    async function refreshStatus() {
      const res = await fetch("/status" + (token ? "?token=" + encodeURIComponent(token) : ""), {headers: {"X-Shadow-Token": token}});
      const data = await res.json();
      intervalMs = data.frame_interval_ms || 500;
      state.textContent = data.recording ? "recording" : "idle";
    }

    function refreshFrame() {
      screen.src = "/frame.png?t=" + Date.now() + (token ? "&token=" + encodeURIComponent(token) : "");
      setTimeout(refreshFrame, intervalMs);
    }

    function pointerPayload(ev, type) {
      const rect = screen.getBoundingClientRect();
      return {
        type,
        pointer_id: ev.pointerId || 1,
        x: ev.clientX - rect.left,
        y: ev.clientY - rect.top,
        width: rect.width,
        height: rect.height
      };
    }

    document.getElementById("start").onclick = async () => { await post("/recording/start"); await refreshStatus(); };
    document.getElementById("stop").onclick = async () => {
      const result = await post("/recording/stop");
      await refreshStatus();
      console.log("recording bundle", result.bundle);
    };
    screen.addEventListener("pointerdown", ev => { screen.setPointerCapture(ev.pointerId); post("/event", pointerPayload(ev, "pointerdown")); });
    screen.addEventListener("pointerup", ev => post("/event", pointerPayload(ev, "pointerup")));

    refreshStatus().finally(refreshFrame);
  </script>
</body>
</html>
"""

