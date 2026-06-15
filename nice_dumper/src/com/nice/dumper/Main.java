package com.nice.dumper;

import android.app.UiAutomation;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.graphics.Rect;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public final class Main {
    private static final int EXIT_OK = 0;
    private static final int EXIT_USAGE = 2;
    private static final int EXIT_DUMP_FAILED = 3;
    private static final int EXIT_WRITE_FAILED = 4;

    private static final long DEFAULT_TIMEOUT_MS = 15_000L;
    private static final String TMP_DIR = "/data/local/tmp";
    private static final String[] ANIMATION_SCALE_SETTINGS = new String[] {
            "window_animation_scale",
            "transition_animation_scale",
            "animator_duration_scale",
    };

    private Main() {
    }

    public static void main(String[] args) {
        int exitCode = run(args);
        if (exitCode != EXIT_OK) {
            System.exit(exitCode);
        }
    }

    static int run(String[] args) {
        Options options;
        try {
            options = Options.parse(args);
        } catch (UsageException e) {
            System.err.println(e.getMessage());
            printUsage(System.err);
            return EXIT_USAGE;
        }

        if (options.help) {
            printUsage(System.out);
            return EXIT_OK;
        }

        String tempPath = TMP_DIR + "/nice-dumper-" + android.os.Process.myPid() + ".xml";
        try {
            String xml = dumpUiXml(tempPath, options);
            writeAtomically(options.dumpPath, xml + "\n");
            chmodReadable(options.dumpPath);
            System.err.println("wrote " + options.dumpPath);
            return EXIT_OK;
        } catch (DumpException e) {
            System.err.println("dump failed: " + e.getMessage());
            return EXIT_DUMP_FAILED;
        } catch (IOException e) {
            System.err.println("write failed: " + e.getMessage());
            return EXIT_WRITE_FAILED;
        } finally {
            new File(tempPath).delete();
        }
    }

    private static String dumpUiXml(String tempPath, Options options) throws DumpException {
        List<String> errors = new ArrayList<String>();
        try {
            return dumpViaUiAutomation(options.preferCompressed);
        } catch (Exception e) {
            errors.add("ui-automation: " + e.getMessage());
        }

        List<DumpStrategy> strategies = buildDumpStrategies(tempPath, options.preferCompressed);
        AnimationScales originalScales = captureAnimationScales();
        disableAnimations();
        try {
            for (DumpStrategy strategy : strategies) {
                try {
                    if (strategy.usesFile) {
                        deleteQuietly(tempPath);
                    }

                    CommandResult result = runCommand(strategy.command, options.timeoutMs);
                    if (result.exitCode != 0 || containsFatalDumpSignal(result.combinedOutput())) {
                        errors.add(formatCommandError(strategy.command, result));
                        continue;
                    }

                    String rawXml = strategy.usesFile ? readFileOrOutput(tempPath, result) : result.combinedOutput();
                    String xml = stripUiAutomatorNoise(rawXml);
                    if (xml.contains("<hierarchy")) {
                        return xml;
                    }
                    errors.add(join(strategy.command, " ") + " produced no hierarchy XML");
                } catch (IOException e) {
                    errors.add(strategy.name + ": " + e.getMessage());
                } catch (DumpException e) {
                    errors.add(strategy.name + ": " + e.getMessage());
                }
            }

            throw new DumpException(join(errors, " | "));
        } finally {
            restoreAnimationScales(originalScales);
        }
    }

    private static String dumpViaUiAutomation(boolean compressed) throws Exception {
        ensureMainLooper();
        HandlerThread thread = new HandlerThread("nice-dumper-ui-automation");
        thread.start();

        UiAutomation automation = null;
        AccessibilityNodeInfo root = null;
        try {
            automation = createUiAutomation(thread.getLooper());
            connectUiAutomation(automation);
            configureUiAutomation(automation);
            root = findRootNode(automation, 2_000L);
            if (root == null) {
                throw new IllegalStateException("no accessibility root available");
            }

            StringBuilder xml = new StringBuilder(64 * 1024);
            xml.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
            xml.append("<hierarchy rotation=\"0\" source=\"ui-automation\">\n");
            appendNodeXml(xml, root, 0, compressed);
            xml.append("</hierarchy>");
            return xml.toString();
        } finally {
            if (root != null) {
                root.recycle();
            }
            if (automation != null) {
                disconnectUiAutomation(automation);
            }
            quitHandlerThread(thread);
        }
    }

    private static void configureUiAutomation(UiAutomation automation) {
        try {
            AccessibilityServiceInfo info = new AccessibilityServiceInfo();
            info.eventTypes = -1;
            info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC;
            info.flags = AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            automation.setServiceInfo(info);
        } catch (RuntimeException ignored) {
            // Some builds reject service info changes; root lookup still may work.
        }
    }

    private static AccessibilityNodeInfo findRootNode(UiAutomation automation, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() <= deadline) {
            AccessibilityNodeInfo root = automation.getRootInActiveWindow();
            if (root != null) {
                return root;
            }

            root = firstWindowRoot(automation);
            if (root != null) {
                return root;
            }

            try {
                Thread.sleep(100L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return null;
            }
        }
        return null;
    }

    private static AccessibilityNodeInfo firstWindowRoot(UiAutomation automation) {
        List<AccessibilityWindowInfo> windows;
        try {
            windows = automation.getWindows();
        } catch (RuntimeException e) {
            return null;
        }
        if (windows == null) {
            return null;
        }

        AccessibilityWindowInfo fallback = null;
        try {
            for (AccessibilityWindowInfo window : windows) {
                if (window == null) {
                    continue;
                }
                if (window.isActive() || window.isFocused()) {
                    AccessibilityNodeInfo root = window.getRoot();
                    if (root != null) {
                        return root;
                    }
                }
                if (fallback == null && window.getType() == AccessibilityWindowInfo.TYPE_APPLICATION) {
                    fallback = window;
                }
            }
            if (fallback != null) {
                return fallback.getRoot();
            }
            for (AccessibilityWindowInfo window : windows) {
                if (window != null) {
                    AccessibilityNodeInfo root = window.getRoot();
                    if (root != null) {
                        return root;
                    }
                }
            }
            return null;
        } finally {
            for (AccessibilityWindowInfo window : windows) {
                if (window != null) {
                    window.recycle();
                }
            }
        }
    }

    private static void ensureMainLooper() {
        try {
            if (Looper.getMainLooper() == null) {
                Looper.prepareMainLooper();
            }
        } catch (RuntimeException ignored) {
            // A main looper already exists in this process.
        }
    }

    private static UiAutomation createUiAutomation(Looper looper) throws Exception {
        Class<?> connectionClass = Class.forName("android.app.UiAutomationConnection");
        Object connection = connectionClass.getDeclaredConstructor().newInstance();

        for (java.lang.reflect.Constructor<?> constructor : UiAutomation.class.getDeclaredConstructors()) {
            Class<?>[] parameterTypes = constructor.getParameterTypes();
            if (parameterTypes.length == 2
                    && Looper.class.isAssignableFrom(parameterTypes[0])
                    && parameterTypes[1].isInstance(connection)) {
                constructor.setAccessible(true);
                return (UiAutomation) constructor.newInstance(looper, connection);
            }
        }

        for (java.lang.reflect.Constructor<?> constructor : UiAutomation.class.getDeclaredConstructors()) {
            if (constructor.getParameterTypes().length == 0) {
                constructor.setAccessible(true);
                return (UiAutomation) constructor.newInstance();
            }
        }

        throw new IllegalStateException("no usable UiAutomation constructor");
    }

    private static void connectUiAutomation(UiAutomation automation) throws Exception {
        for (java.lang.reflect.Method method : UiAutomation.class.getDeclaredMethods()) {
            if (!"connect".equals(method.getName())) {
                continue;
            }
            method.setAccessible(true);
            Class<?>[] parameterTypes = method.getParameterTypes();
            if (parameterTypes.length == 0) {
                method.invoke(automation);
                return;
            }
            if (parameterTypes.length == 1 && parameterTypes[0] == int.class) {
                method.invoke(automation, Integer.valueOf(0));
                return;
            }
        }
    }

    private static void disconnectUiAutomation(UiAutomation automation) {
        try {
            for (java.lang.reflect.Method method : UiAutomation.class.getDeclaredMethods()) {
                if ("disconnect".equals(method.getName()) && method.getParameterTypes().length == 0) {
                    method.setAccessible(true);
                    method.invoke(automation);
                    return;
                }
            }
        } catch (Exception ignored) {
            // Best effort cleanup.
        }
    }

    private static void quitHandlerThread(HandlerThread thread) {
        try {
            thread.quitSafely();
        } catch (RuntimeException ignored) {
            thread.quit();
        }
    }

    private static void appendNodeXml(
            StringBuilder xml,
            AccessibilityNodeInfo node,
            int index,
            boolean compressed) {
        if (compressed && !node.isVisibleToUser()) {
            return;
        }

        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);

        xml.append("  <node");
        appendAttr(xml, "index", String.valueOf(index));
        appendAttr(xml, "text", node.getText());
        appendAttr(xml, "resource-id", node.getViewIdResourceName());
        appendAttr(xml, "class", node.getClassName());
        appendAttr(xml, "package", node.getPackageName());
        appendAttr(xml, "content-desc", node.getContentDescription());
        appendAttr(xml, "checkable", node.isCheckable());
        appendAttr(xml, "checked", node.isChecked());
        appendAttr(xml, "clickable", node.isClickable());
        appendAttr(xml, "enabled", node.isEnabled());
        appendAttr(xml, "focusable", node.isFocusable());
        appendAttr(xml, "focused", node.isFocused());
        appendAttr(xml, "scrollable", node.isScrollable());
        appendAttr(xml, "long-clickable", node.isLongClickable());
        appendAttr(xml, "password", node.isPassword());
        appendAttr(xml, "selected", node.isSelected());
        appendAttr(xml, "bounds", "[" + bounds.left + "," + bounds.top + "][" + bounds.right + "," + bounds.bottom + "]");

        int childCount = node.getChildCount();
        if (childCount <= 0) {
            xml.append(" />\n");
            return;
        }

        xml.append(">\n");
        for (int i = 0; i < childCount; i += 1) {
            AccessibilityNodeInfo child = null;
            try {
                child = node.getChild(i);
                if (child != null) {
                    appendNodeXml(xml, child, i, compressed);
                }
            } catch (RuntimeException ignored) {
                // Accessibility nodes can disappear while the screen is changing.
            } finally {
                if (child != null) {
                    child.recycle();
                }
            }
        }
        xml.append("  </node>\n");
    }

    private static void appendAttr(StringBuilder xml, String name, boolean value) {
        appendAttr(xml, name, String.valueOf(value));
    }

    private static void appendAttr(StringBuilder xml, String name, Object value) {
        xml.append(' ')
                .append(name)
                .append("=\"")
                .append(escapeXmlAttr(value == null ? "" : String.valueOf(value)))
                .append('"');
    }

    private static String escapeXmlAttr(String text) {
        String value = text == null ? "" : text;
        StringBuilder escaped = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i += 1) {
            char ch = value.charAt(i);
            if (ch == '&') {
                escaped.append("&amp;");
            } else if (ch == '"') {
                escaped.append("&quot;");
            } else if (ch == '<') {
                escaped.append("&lt;");
            } else if (ch == '>') {
                escaped.append("&gt;");
            } else {
                escaped.append(ch);
            }
        }
        return escaped.toString();
    }

    private static List<DumpStrategy> buildDumpStrategies(String tempPath, boolean preferCompressed) {
        List<DumpStrategy> standard = new ArrayList<DumpStrategy>();
        standard.add(new DumpStrategy("standard-file", command(false, tempPath), true));
        standard.add(new DumpStrategy("standard-tty", command(false, "/dev/tty"), false));

        List<DumpStrategy> compressed = new ArrayList<DumpStrategy>();
        compressed.add(new DumpStrategy("compressed-file", command(true, tempPath), true));
        compressed.add(new DumpStrategy("compressed-tty", command(true, "/dev/tty"), false));

        List<DumpStrategy> strategies = new ArrayList<DumpStrategy>();
        if (preferCompressed) {
            strategies.addAll(compressed);
            strategies.addAll(standard);
        } else {
            strategies.addAll(standard);
            strategies.addAll(compressed);
        }
        return strategies;
    }

    private static List<String> command(boolean compressed, String outputPath) {
        List<String> command = new ArrayList<String>();
        command.add("uiautomator");
        command.add("dump");
        if (compressed) {
            command.add("--compressed");
        }
        command.add(outputPath);
        return command;
    }

    private static CommandResult runCommand(List<String> command, long timeoutMs) throws IOException, DumpException {
        Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
        StreamCollector collector = new StreamCollector(process.getInputStream());
        Thread collectorThread = new Thread(collector, "nice-dumper-output");
        collectorThread.start();

        boolean completed = waitFor(process, timeoutMs);
        if (!completed) {
            process.destroy();
            joinQuietly(collectorThread);
            throw new DumpException("timeout after " + timeoutMs + "ms running " + join(command, " "));
        }

        joinQuietly(collectorThread);
        return new CommandResult(process.exitValue(), collector.output());
    }

    private static boolean waitFor(Process process, long timeoutMs) throws DumpException {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (true) {
            try {
                process.exitValue();
                return true;
            } catch (IllegalThreadStateException stillRunning) {
                if (System.currentTimeMillis() >= deadline) {
                    return false;
                }
                try {
                    Thread.sleep(50L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    process.destroy();
                    throw new DumpException("interrupted while waiting for command");
                }
            }
        }
    }

    private static String readUtf8(String path) throws IOException {
        InputStream input = new FileInputStream(path);
        try {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            input.close();
        }
    }

    private static String readFileOrOutput(String path, CommandResult result) throws IOException {
        File file = new File(path);
        if (file.exists()) {
            return readUtf8(path);
        }

        String output = result.combinedOutput();
        String xml = stripUiAutomatorNoise(output);
        if (xml.contains("<hierarchy")) {
            return xml;
        }

        throw new IOException(path + ": dump file was not created; command output: " + truncate(output, 300));
    }

    static String stripUiAutomatorNoise(String text) {
        String raw = text == null ? "" : text;
        int start = raw.indexOf("<?xml");
        if (start < 0) {
            start = raw.indexOf("<hierarchy");
        }
        if (start < 0) {
            return raw.trim();
        }

        String xml = raw.substring(start);
        int end = xml.indexOf("</hierarchy>");
        if (end >= 0) {
            return xml.substring(0, end + "</hierarchy>".length()).trim();
        }
        return xml.trim();
    }

    private static void writeAtomically(String targetPath, String content) throws IOException {
        File target = new File(targetPath);
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs() && !parent.exists()) {
            throw new IOException("cannot create parent directory " + parent);
        }

        File temp = new File(targetPath + ".tmp." + android.os.Process.myPid());
        FileOutputStream output = new FileOutputStream(temp);
        try {
            output.write(content.getBytes(StandardCharsets.UTF_8));
            output.getFD().sync();
        } finally {
            output.close();
        }

        if (target.exists() && !target.delete()) {
            temp.delete();
            throw new IOException("cannot replace existing file " + targetPath);
        }
        if (!temp.renameTo(target)) {
            temp.delete();
            throw new IOException("cannot rename " + temp + " to " + targetPath);
        }
    }

    private static void chmodReadable(String path) {
        try {
            runCommand(Arrays.asList("chmod", "0644", path), 3_000L);
        } catch (Exception ignored) {
            // Best effort only. Root callers can still read files they created.
        }
    }

    private static AnimationScales captureAnimationScales() {
        String[] values = new String[ANIMATION_SCALE_SETTINGS.length];
        for (int i = 0; i < ANIMATION_SCALE_SETTINGS.length; i += 1) {
            try {
                CommandResult result = runCommand(
                        Arrays.asList("settings", "get", "global", ANIMATION_SCALE_SETTINGS[i]),
                        2_000L);
                if (result.exitCode == 0) {
                    values[i] = result.combinedOutput().trim();
                }
            } catch (Exception ignored) {
                values[i] = "";
            }
        }
        return new AnimationScales(values);
    }

    private static void disableAnimations() {
        for (String setting : ANIMATION_SCALE_SETTINGS) {
            try {
                runCommand(Arrays.asList("settings", "put", "global", setting, "0"), 2_000L);
            } catch (Exception ignored) {
                // Dump can still work when these settings cannot be changed.
            }
        }
    }

    private static void restoreAnimationScales(AnimationScales scales) {
        if (scales == null) {
            return;
        }
        for (int i = 0; i < ANIMATION_SCALE_SETTINGS.length; i += 1) {
            String value = scales.values[i];
            if (value == null || value.length() == 0 || "null".equals(value)) {
                continue;
            }
            try {
                runCommand(Arrays.asList("settings", "put", "global", ANIMATION_SCALE_SETTINGS[i], value), 2_000L);
            } catch (Exception ignored) {
                // Best effort restore.
            }
        }
    }

    private static boolean containsFatalDumpSignal(String output) {
        String value = output == null ? "" : output;
        return value.contains("Killed")
                || value.contains("Permission denied")
                || value.contains("not found")
                || value.contains("No such file or directory");
    }

    private static String formatCommandError(List<String> command, CommandResult result) {
        String output = truncate(result.combinedOutput().trim(), 300);
        return join(command, " ") + " exit=" + result.exitCode + " output=" + output;
    }

    private static String truncate(String text, int maxLength) {
        String value = text == null ? "" : text;
        if (value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength) + "...";
    }

    private static String join(List<String> values, String separator) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < values.size(); i += 1) {
            if (i > 0) {
                builder.append(separator);
            }
            builder.append(values.get(i));
        }
        return builder.toString();
    }

    private static void deleteQuietly(String path) {
        try {
            new File(path).delete();
        } catch (RuntimeException ignored) {
        }
    }

    private static void joinQuietly(Thread thread) {
        try {
            thread.join(1_000L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void printUsage(java.io.PrintStream stream) {
        stream.println("Usage: project -d <path> [--compressed] [--timeout-ms <ms>]");
        stream.println();
        stream.println("Options:");
        stream.println("  -d, --dump <path>       Save current UIAutomator XML to this path.");
        stream.println("      --compressed        Prefer uiautomator dump --compressed.");
        stream.println("      --timeout-ms <ms>   Command timeout. Default: 15000.");
        stream.println("  -h, --help              Show this help.");
    }

    private static final class Options {
        final String dumpPath;
        final boolean preferCompressed;
        final long timeoutMs;
        final boolean help;

        Options(String dumpPath, boolean preferCompressed, long timeoutMs, boolean help) {
            this.dumpPath = dumpPath;
            this.preferCompressed = preferCompressed;
            this.timeoutMs = timeoutMs;
            this.help = help;
        }

        static Options parse(String[] args) throws UsageException {
            String dumpPath = "";
            boolean compressed = false;
            long timeoutMs = DEFAULT_TIMEOUT_MS;
            boolean help = false;

            for (int i = 0; i < args.length; i += 1) {
                String token = args[i];
                if ("-h".equals(token) || "--help".equals(token)) {
                    help = true;
                } else if ("-d".equals(token) || "--dump".equals(token)) {
                    if (i + 1 >= args.length) {
                        throw new UsageException(token + " requires a path");
                    }
                    dumpPath = args[++i];
                } else if ("--compressed".equals(token)) {
                    compressed = true;
                } else if ("--timeout-ms".equals(token)) {
                    if (i + 1 >= args.length) {
                        throw new UsageException("--timeout-ms requires a value");
                    }
                    try {
                        timeoutMs = Long.parseLong(args[++i]);
                    } catch (NumberFormatException e) {
                        throw new UsageException("--timeout-ms must be an integer");
                    }
                    if (timeoutMs <= 0) {
                        throw new UsageException("--timeout-ms must be positive");
                    }
                } else {
                    throw new UsageException("unknown argument: " + token);
                }
            }

            if (!help && dumpPath.trim().isEmpty()) {
                throw new UsageException("-d/--dump is required");
            }
            return new Options(dumpPath, compressed, timeoutMs, help);
        }
    }

    private static final class CommandResult {
        final int exitCode;
        final String output;

        CommandResult(int exitCode, String output) {
            this.exitCode = exitCode;
            this.output = output == null ? "" : output;
        }

        String combinedOutput() {
            return output;
        }
    }

    private static final class DumpStrategy {
        final String name;
        final List<String> command;
        final boolean usesFile;

        DumpStrategy(String name, List<String> command, boolean usesFile) {
            this.name = name;
            this.command = command;
            this.usesFile = usesFile;
        }
    }

    private static final class AnimationScales {
        final String[] values;

        AnimationScales(String[] values) {
            this.values = values;
        }
    }

    private static final class StreamCollector implements Runnable {
        private final InputStream input;
        private final ByteArrayOutputStream output = new ByteArrayOutputStream();

        StreamCollector(InputStream input) {
            this.input = input;
        }

        @Override
        public void run() {
            byte[] buffer = new byte[4096];
            int read;
            try {
                while ((read = input.read(buffer)) >= 0) {
                    output.write(buffer, 0, read);
                }
            } catch (IOException ignored) {
            }
        }

        String output() {
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static final class UsageException extends Exception {
        UsageException(String message) {
            super(message);
        }
    }

    private static final class DumpException extends Exception {
        DumpException(String message) {
            super(message);
        }
    }
}
