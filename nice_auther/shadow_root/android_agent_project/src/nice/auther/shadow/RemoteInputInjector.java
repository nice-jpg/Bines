package nice.auther.shadow;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

final class RemoteInputInjector {
    private final int screenWidth;
    private final int screenHeight;
    private final Map<Integer, Gesture> gestures = new HashMap<>();

    RemoteInputInjector(int screenWidth, int screenHeight) {
        this.screenWidth = Math.max(1, screenWidth);
        this.screenHeight = Math.max(1, screenHeight);
    }

    synchronized void handleJson(String json) throws Exception {
        JSONObject object = new JSONObject(json);
        String commandType = object.optString("type", "");
        if ("wake".equals(commandType)) {
            wake();
            return;
        }
        JSONArray events = object.optJSONArray("events");
        if (events == null) {
            return;
        }
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event != null) {
                handlePointerEvent(event);
            }
        }
    }

    private void handlePointerEvent(JSONObject event) throws Exception {
        String type = event.optString("type", "");
        int pointerId = event.optInt("pointer_id", 1);
        Point point = mapPoint(event);
        long timeMs = Math.round(event.optDouble("client_time_ms", System.currentTimeMillis()));
        if ("pointerdown".equals(type)) {
            gestures.put(pointerId, new Gesture(point, timeMs));
            return;
        }
        Gesture gesture = gestures.get(pointerId);
        if (gesture == null) {
            if ("pointermove".equals(type)) {
                return;
            }
            gesture = new Gesture(point, timeMs);
        }
        gesture.update(point, timeMs);
        if ("pointerup".equals(type) || "pointercancel".equals(type)) {
            gestures.remove(pointerId);
            injectGesture(gesture);
        }
    }

    private Point mapPoint(JSONObject event) {
        double clientWidth = Math.max(1.0d, event.optDouble("width", screenWidth));
        double clientHeight = Math.max(1.0d, event.optDouble("height", screenHeight));
        int x = clamp((int) Math.round(event.optDouble("x", 0) * screenWidth / clientWidth), 0, screenWidth - 1);
        int y = clamp((int) Math.round(event.optDouble("y", 0) * screenHeight / clientHeight), 0, screenHeight - 1);
        return new Point(x, y);
    }

    private void injectGesture(Gesture gesture) throws Exception {
        int dx = Math.abs(gesture.end.x - gesture.start.x);
        int dy = Math.abs(gesture.end.y - gesture.start.y);
        int durationMs = clamp((int) (gesture.endTimeMs - gesture.startTimeMs), 1, 5000);
        if (dx <= 12 && dy <= 12) {
            runInput("input tap " + gesture.end.x + " " + gesture.end.y);
        } else {
            runInput(
                    "input swipe "
                            + gesture.start.x + " "
                            + gesture.start.y + " "
                            + gesture.end.x + " "
                            + gesture.end.y + " "
                            + durationMs
            );
        }
    }

    private void wake() throws Exception {
        runInput("input keyevent KEYCODE_WAKEUP");
        runInput("input keyevent KEYCODE_MENU");
    }

    private void runInput(String command) throws Exception {
        System.err.println("nice_shadow_agent inject " + command);
        Process process = new ProcessBuilder("sh", "-c", command).redirectErrorStream(true).start();
        int exit = process.waitFor();
        if (exit != 0) {
            System.err.println("nice_shadow_agent inject exit=" + exit + " command=" + command);
        }
    }

    private static int clamp(int value, int low, int high) {
        return Math.max(low, Math.min(high, value));
    }

    private static final class Point {
        final int x;
        final int y;

        Point(int x, int y) {
            this.x = x;
            this.y = y;
        }
    }

    private static final class Gesture {
        final Point start;
        final long startTimeMs;
        Point end;
        long endTimeMs;

        Gesture(Point start, long startTimeMs) {
            this.start = start;
            this.end = start;
            this.startTimeMs = startTimeMs;
            this.endTimeMs = startTimeMs;
        }

        void update(Point point, long timeMs) {
            this.end = point;
            this.endTimeMs = timeMs;
        }
    }
}
