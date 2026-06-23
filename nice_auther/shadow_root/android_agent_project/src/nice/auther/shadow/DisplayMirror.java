package nice.auther.shadow;

import android.graphics.Rect;
import android.hardware.display.VirtualDisplay;
import android.view.Surface;

import java.io.Closeable;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

final class DisplayMirror implements Closeable {
    static final class DisplayInfo {
        final int width;
        final int height;
        final int rotation;
        final int layerStack;

        DisplayInfo(int width, int height, int rotation, int layerStack) {
            this.width = width;
            this.height = height;
            this.rotation = rotation;
            this.layerStack = layerStack;
        }
    }

    static final class EncodedSize {
        final int width;
        final int height;

        EncodedSize(int width, int height) {
            this.width = width;
            this.height = height;
        }
    }

    private final Object displayToken;
    private final boolean virtualDisplay;

    private DisplayMirror(Object displayToken, boolean virtualDisplay) {
        this.displayToken = displayToken;
        this.virtualDisplay = virtualDisplay;
    }

    static DisplayInfo mainDisplayInfo() throws Exception {
        Class<?> serviceManager = Class.forName("android.os.ServiceManager");
        Object binder = serviceManager.getMethod("getService", String.class).invoke(null, "display");
        Class<?> stub = Class.forName("android.hardware.display.IDisplayManager$Stub");
        Object displayManager = stub.getMethod("asInterface", Class.forName("android.os.IBinder")).invoke(null, binder);
        Object info = displayManager.getClass().getMethod("getDisplayInfo", int.class).invoke(displayManager, 0);
        int width = intField(info, "logicalWidth", "appWidth");
        int height = intField(info, "logicalHeight", "appHeight");
        int rotation = optionalIntField(info, 0, "rotation");
        int layerStack = optionalIntField(info, 0, "layerStack");
        if (width <= 0 || height <= 0) {
            throw new IllegalStateException("invalid display size: " + width + "x" + height);
        }
        return new DisplayInfo(width, height, rotation, layerStack);
    }

    static EncodedSize encodedSize(DisplayInfo info, int maxSize) {
        int longSide = Math.max(info.width, info.height);
        if (longSide <= maxSize) {
            return evenSize(info.width, info.height);
        }
        float scale = (float) maxSize / (float) longSide;
        return evenSize(Math.round(info.width * scale), Math.round(info.height * scale));
    }

    static DisplayMirror create(Surface surface, DisplayInfo info, EncodedSize encodedSize) throws Exception {
        try {
            VirtualDisplay virtualDisplay = createVirtualDisplay(surface, encodedSize);
            System.err.println("nice_shadow_agent display mirror using DisplayManager");
            return new DisplayMirror(virtualDisplay, true);
        } catch (Exception displayManagerException) {
            System.err.println("nice_shadow_agent DisplayManager mirror failed " + displayManagerException);
            displayManagerException.printStackTrace(System.err);
        }
        Object displayToken = createDisplayToken();
        invokeStaticByName("openTransaction");
        try {
            invokeStaticByName("setDisplaySurface", displayToken, surface);
            invokeStaticByName("setDisplayLayerStack", displayToken, info.layerStack);
            Rect source = new Rect(0, 0, info.width, info.height);
            Rect destination = new Rect(0, 0, encodedSize.width, encodedSize.height);
            invokeStaticByName("setDisplayProjection", displayToken, info.rotation, source, destination);
        } finally {
            invokeStaticByName("closeTransaction");
        }
        System.err.println("nice_shadow_agent display mirror using SurfaceControl");
        return new DisplayMirror(displayToken, false);
    }

    @Override
    public void close() {
        try {
            if (virtualDisplay) {
                ((VirtualDisplay) displayToken).release();
            } else {
                invokeStaticByName("destroyDisplay", displayToken);
            }
        } catch (Exception ignored) {
        }
    }

    private static EncodedSize evenSize(int width, int height) {
        return new EncodedSize(Math.max(2, width & ~1), Math.max(2, height & ~1));
    }

    private static VirtualDisplay createVirtualDisplay(Surface surface, EncodedSize encodedSize) throws Exception {
        Class<?> displayManager = Class.forName("android.hardware.display.DisplayManager");
        try {
            Method method = displayManager.getMethod("createVirtualDisplay", String.class, int.class, int.class, int.class, Surface.class);
            System.err.println("nice_shadow_agent invoke " + displayManager.getName() + ".createVirtualDisplay mirror");
            return (VirtualDisplay) method.invoke(null, "nice_auther_shadow", encodedSize.width, encodedSize.height, 0, surface);
        } catch (NoSuchMethodException mirrorMethodMissing) {
            Object displayManagerInstance = displayManager.getDeclaredConstructor().newInstance();
            Method method = displayManager.getMethod("createVirtualDisplay", String.class, int.class, int.class, int.class, Surface.class, int.class);
            System.err.println("nice_shadow_agent invoke " + displayManager.getName() + ".createVirtualDisplay public");
            return (VirtualDisplay) method.invoke(displayManagerInstance, "nice_auther_shadow", encodedSize.width, encodedSize.height, 320, surface, 0);
        }
    }

    private static int intField(Object target, String... names) throws Exception {
        for (String name : names) {
            try {
                Field field = target.getClass().getField(name);
                return field.getInt(target);
            } catch (NoSuchFieldException ignored) {
            }
        }
        throw new NoSuchFieldException("none of fields found");
    }

    private static int optionalIntField(Object target, int fallback, String... names) {
        try {
            return intField(target, names);
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static Object createDisplayToken() throws Exception {
        try {
            return invokeStaticByName("createDisplay", "nice_auther_shadow", true);
        } catch (NoSuchMethodException first) {
            try {
                return invokeStaticByName("createDisplay", "nice_auther_shadow", true, 0);
            } catch (NoSuchMethodException second) {
                second.addSuppressed(first);
                throw second;
            }
        }
    }

    private static Object invokeStaticByName(String name, Object... args) throws Exception {
        Exception last = null;
        for (String className : new String[]{"android.view.DisplayControl", "android.view.SurfaceControl"}) {
            try {
                Class<?> clazz = Class.forName(className);
                return invokeStaticByName(clazz, name, args);
            } catch (ClassNotFoundException | NoSuchMethodException exc) {
                last = exc;
            }
        }
        NoSuchMethodException exception = new NoSuchMethodException(name);
        if (last != null) {
            exception.addSuppressed(last);
        }
        throw exception;
    }

    private static Object invokeStaticByName(Class<?> clazz, String name, Object... args) throws Exception {
        Method best = null;
        for (Method method : clazz.getDeclaredMethods()) {
            if (!method.getName().equals(name) || method.getParameterTypes().length != args.length) {
                continue;
            }
            if (!compatible(method.getParameterTypes(), args)) {
                continue;
            }
            best = method;
            break;
        }
        if (best == null) {
            throw new NoSuchMethodException(clazz.getName() + "." + name);
        }
        System.err.println("nice_shadow_agent invoke " + clazz.getName() + "." + name);
        best.setAccessible(true);
        return best.invoke(null, args);
    }

    private static boolean compatible(Class<?>[] parameterTypes, Object[] args) {
        for (int i = 0; i < parameterTypes.length; i++) {
            if (args[i] == null) {
                continue;
            }
            Class<?> parameterType = wrap(parameterTypes[i]);
            if (!parameterType.isAssignableFrom(args[i].getClass())) {
                return false;
            }
        }
        return true;
    }

    private static Class<?> wrap(Class<?> type) {
        if (!type.isPrimitive()) {
            return type;
        }
        if (type == boolean.class) {
            return Boolean.class;
        }
        if (type == int.class) {
            return Integer.class;
        }
        if (type == long.class) {
            return Long.class;
        }
        if (type == float.class) {
            return Float.class;
        }
        if (type == double.class) {
            return Double.class;
        }
        if (type == byte.class) {
            return Byte.class;
        }
        if (type == short.class) {
            return Short.class;
        }
        if (type == char.class) {
            return Character.class;
        }
        return Void.class;
    }
}
