package nice.auther.shadow;

import android.os.SystemClock;
import android.view.InputDevice;
import android.view.MotionEvent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

final class RemoteInputInjector {
    private static final int INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH = 2;
    private static final int MAX_REPLAY_DELAY_MS = 250;

    private final int screenWidth;
    private final int screenHeight;
    private final MotionEventInjector injector;
    private final ArrayList<Integer> pointerOrder = new ArrayList<>();
    private final Map<Integer, PointerState> pointers = new HashMap<>();
    private final Map<Integer, Integer> androidPointerIds = new HashMap<>();
    private long gestureDownTime = 0L;
    private double lastClientTimeMs = Double.NaN;

    RemoteInputInjector(int screenWidth, int screenHeight) throws Exception {
        this(screenWidth, screenHeight, new MotionEventInjector());
    }

    RemoteInputInjector(int screenWidth, int screenHeight, MotionEventInjector injector) {
        this.screenWidth = Math.max(1, screenWidth);
        this.screenHeight = Math.max(1, screenHeight);
        this.injector = injector;
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
                replayDelay(event);
                handlePointerEvent(event);
            }
        }
    }

    private void replayDelay(JSONObject event) throws Exception {
        if (!event.has("client_time_ms")) {
            return;
        }
        double current = event.optDouble("client_time_ms", Double.NaN);
        if (Double.isNaN(current)) {
            return;
        }
        if (!Double.isNaN(lastClientTimeMs)) {
            long delayMs = Math.round(Math.max(0.0d, Math.min(MAX_REPLAY_DELAY_MS, current - lastClientTimeMs)));
            if (delayMs > 0) {
                Thread.sleep(delayMs);
            }
        }
        lastClientTimeMs = current;
    }

    private void handlePointerEvent(JSONObject event) throws Exception {
        String type = event.optString("type", "");
        int pointerId = event.optInt("pointer_id", 1);
        if ("pointerdown".equals(type)) {
            int androidPointerId = allocateAndroidPointerId(pointerId);
            PointerState state = mapPointer(event, androidPointerId);
            injectPointerDown(pointerId, state);
        } else if ("pointermove".equals(type)) {
            Integer androidPointerId = androidPointerIds.get(pointerId);
            if (androidPointerId == null) {
                return;
            }
            PointerState state = mapPointer(event, androidPointerId.intValue());
            injectPointerMove(pointerId, state);
        } else if ("pointerup".equals(type) || "pointercancel".equals(type)) {
            int androidPointerId = androidPointerIds.containsKey(pointerId)
                    ? androidPointerIds.get(pointerId).intValue()
                    : allocateAndroidPointerId(pointerId);
            PointerState state = mapPointer(event, androidPointerId);
            injectPointerUp(pointerId, state, "pointercancel".equals(type));
        }
    }

    private int allocateAndroidPointerId(int browserPointerId) {
        Integer existing = androidPointerIds.get(browserPointerId);
        if (existing != null) {
            return existing.intValue();
        }
        for (int candidate = 0; candidate < 32; candidate++) {
            if (!androidPointerIds.containsValue(candidate)) {
                androidPointerIds.put(browserPointerId, candidate);
                return candidate;
            }
        }
        throw new IllegalStateException("too many active pointers");
    }

    private void injectPointerDown(int pointerId, PointerState state) throws Exception {
        long now = SystemClock.uptimeMillis();
        if (pointerOrder.isEmpty()) {
            gestureDownTime = now;
        }
        pointers.put(pointerId, state);
        if (!pointerOrder.contains(pointerId)) {
            pointerOrder.add(pointerId);
        }
        int index = pointerOrder.indexOf(pointerId);
        int action = pointerOrder.size() == 1
                ? MotionEvent.ACTION_DOWN
                : MotionEvent.ACTION_POINTER_DOWN | (index << MotionEvent.ACTION_POINTER_INDEX_SHIFT);
        inject(action, now);
    }

    private void injectPointerMove(int pointerId, PointerState state) throws Exception {
        if (!pointers.containsKey(pointerId)) {
            return;
        }
        pointers.put(pointerId, state);
        inject(MotionEvent.ACTION_MOVE, SystemClock.uptimeMillis());
    }

    private void injectPointerUp(int pointerId, PointerState state, boolean cancel) throws Exception {
        if (!pointers.containsKey(pointerId)) {
            injectPointerDown(pointerId, state);
        } else {
            pointers.put(pointerId, state);
        }
        long now = SystemClock.uptimeMillis();
        int index = pointerOrder.indexOf(pointerId);
        if (index < 0) {
            return;
        }
        int action;
        if (cancel) {
            action = MotionEvent.ACTION_CANCEL;
        } else if (pointerOrder.size() == 1) {
            action = MotionEvent.ACTION_UP;
        } else {
            action = MotionEvent.ACTION_POINTER_UP | (index << MotionEvent.ACTION_POINTER_INDEX_SHIFT);
        }
        inject(action, now);
        pointers.remove(pointerId);
        androidPointerIds.remove(pointerId);
        pointerOrder.remove(index);
        if (pointerOrder.isEmpty()) {
            gestureDownTime = 0L;
            lastClientTimeMs = Double.NaN;
        }
    }

    private PointerState mapPointer(JSONObject event, int androidPointerId) {
        double clientWidth = Math.max(1.0d, event.optDouble("width", screenWidth));
        double clientHeight = Math.max(1.0d, event.optDouble("height", screenHeight));
        float x = clamp((float) (event.optDouble("x", 0) * screenWidth / clientWidth), 0.0f, screenWidth - 1.0f);
        float y = clamp((float) (event.optDouble("y", 0) * screenHeight / clientHeight), 0.0f, screenHeight - 1.0f);
        float pressure = clamp((float) event.optDouble("pressure", 0.5d), 0.0f, 1.0f);
        if (event.optString("type", "").equals("pointerup") || event.optString("type", "").equals("pointercancel")) {
            pressure = 0.0f;
        }
        return new PointerState(androidPointerId, x, y, pressure);
    }

    private void inject(int action, long eventTime) throws Exception {
        if (pointerOrder.isEmpty()) {
            return;
        }
        int pointerCount = pointerOrder.size();
        MotionEvent.PointerProperties[] properties = new MotionEvent.PointerProperties[pointerCount];
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[pointerCount];
        for (int i = 0; i < pointerCount; i++) {
            int pointerId = pointerOrder.get(i);
            PointerState state = pointers.get(pointerId);
            if (state == null) {
                return;
            }
            MotionEvent.PointerProperties property = new MotionEvent.PointerProperties();
            property.id = state.androidPointerId;
            property.toolType = MotionEvent.TOOL_TYPE_FINGER;
            properties[i] = property;

            MotionEvent.PointerCoords coord = new MotionEvent.PointerCoords();
            coord.x = state.x;
            coord.y = state.y;
            coord.pressure = state.pressure;
            coord.size = 1.0f;
            coords[i] = coord;
        }
        long downTime = gestureDownTime > 0L ? gestureDownTime : eventTime;
        MotionEvent motionEvent = MotionEvent.obtain(
                downTime,
                eventTime,
                action,
                pointerCount,
                properties,
                coords,
                0,
                0,
                1.0f,
                1.0f,
                0,
                0,
                InputDevice.SOURCE_TOUCHSCREEN,
                0
        );
        try {
            injector.inject(motionEvent);
        } finally {
            motionEvent.recycle();
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

    private static float clamp(float value, float low, float high) {
        return Math.max(low, Math.min(high, value));
    }

    static final class MotionEventInjector {
        private final Object inputManager;
        private final Method injectInputEvent;

        MotionEventInjector() throws Exception {
            Object manager;
            Method injectMethod;
            try {
                Class<?> inputManagerClass = Class.forName("android.hardware.input.InputManager");
                Method getInstance = inputManagerClass.getDeclaredMethod("getInstance");
                getInstance.setAccessible(true);
                manager = getInstance.invoke(null);
                injectMethod = inputManagerClass.getDeclaredMethod("injectInputEvent", android.view.InputEvent.class, int.class);
            } catch (NoSuchMethodException exc) {
                Class<?> serviceManagerClass = Class.forName("android.os.ServiceManager");
                Method getService = serviceManagerClass.getDeclaredMethod("getService", String.class);
                Object binder = getService.invoke(null, "input");
                Class<?> stubClass = Class.forName("android.hardware.input.IInputManager$Stub");
                Method asInterface = stubClass.getDeclaredMethod("asInterface", android.os.IBinder.class);
                manager = asInterface.invoke(null, binder);
                injectMethod = manager.getClass().getMethod("injectInputEvent", android.view.InputEvent.class, int.class);
            }
            this.inputManager = manager;
            this.injectInputEvent = injectMethod;
            this.injectInputEvent.setAccessible(true);
        }

        void inject(MotionEvent event) throws Exception {
            Boolean ok = (Boolean) injectInputEvent.invoke(inputManager, event, INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH);
            if (!ok.booleanValue()) {
                throw new IllegalStateException("injectInputEvent returned false");
            }
        }
    }

    private static final class PointerState {
        final int androidPointerId;
        final float x;
        final float y;
        final float pressure;

        PointerState(int androidPointerId, float x, float y, float pressure) {
            this.androidPointerId = androidPointerId;
            this.x = x;
            this.y = y;
            this.pressure = pressure;
        }
    }
}
