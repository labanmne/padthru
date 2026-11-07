package io.github.labanmne.padthru;

import android.graphics.Color;
import android.hardware.lights.Light;
import android.hardware.lights.LightState;
import android.hardware.lights.LightsManager;
import android.hardware.lights.LightsRequest;
import android.os.Build;
import android.view.InputDevice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Padthru's background helper for Android TV boxes.
 *
 * Runs with the rights of `adb shell` (started by `app_process`, see docs/android.md), so it
 * can do what an ordinary app can't: read controllers in the background, straight from their
 * Linux input devices, while the box keeps playing whatever it plays. It follows the TV's
 * active input over HDMI-CEC: while the TV shows another input (the PC), it grabs the
 * controllers, so the box ignores them, and sends them to the Padthru PC app; when the TV
 * switches back to the box, it lets go and the controllers work on the box again.
 */
public final class Helper {
    static final String DIR = "/data/local/tmp/padthru";
    static final int STATUS_PORT = 47812;
    private static final long KEEPALIVE_MS = 100;
    private static final long MIN_GAP_NS = 2_000_000;
    private static final long CEC_POLL_MS = 700;
    private static final Pattern ACTIVE = Pattern.compile("isActiveSource\\(\\):\\s*(true|false)");

    // ------------------------------------------------------------------ state

    private static final class Node {
        final String path;
        final int fd;
        final int kind; // 0 gamepad, 1 motion sensors, 2 touchpad
        final Pad pad;
        final int[][] abs = new int[0x40][];
        volatile boolean closed;
        boolean grabbed;

        Node(String path, int fd, int kind, Pad pad) {
            this.path = path;
            this.fd = fd;
            this.kind = kind;
            this.pad = pad;
        }
    }

    private static final class Pad {
        final String key;
        int idx = -1;
        String name = "", tvId = "";
        int vendor, product;
        boolean usb, sony, analogTriggers;
        Node gamepad, motion, touch;
        final float[] buttons = new float[18];
        final float[] axes = new float[4];
        final int[] rawMotion = new int[6];
        float[] gyro, accel;
        final float[][] fingers = new float[2][];
        final int[] slotId = {-1, -1};
        final float[][] slotPos = {new float[2], new float[2]};
        int slot;
        int battery = -1;
        String batteryFile;
        long batteryAt;
        boolean dirty = true, sentId;
        long lastSentNs, lastSentMs;
        int sent, rate;
        String pcKind = "";
        int rumbleId = -1;
        int ffFd = -1;              // a writable handle for kernel rumble, where the box allows one
        int rumbleLarge, rumbleSmall;
        android.os.VibratorManager vibrators;
        LightsManager.LightsSession lights;
        Light rgb;

        Pad(String key) {
            this.key = key;
        }
    }

    private final Object lock = new Object();
    private final Map<String, Pad> groups = new HashMap<>();   // by uniq/phys, including pads without a gamepad node yet
    private final Map<String, Node> nodes = new HashMap<>();   // by /dev/input path
    private final List<String> outbox = new ArrayList<>();
    private final ServerSocket statusSocket;
    private PcLink link;
    private volatile boolean boxActive = true; // until CEC says otherwise
    private volatile boolean cecKnown;
    private volatile String pcStatus = "Looking for your PC…";

    private Helper(ServerSocket statusSocket) {
        this.statusSocket = statusSocket;
    }

    public static void main(String[] args) {
        ServerSocket status;
        try {
            // one helper only: the status port doubles as a lock
            status = new ServerSocket(STATUS_PORT, 8, InetAddress.getByName("127.0.0.1"));
        } catch (Exception e) {
            System.out.println("Padthru helper is already running");
            return;
        }
        try {
            loadNative();
        } catch (Throwable t) {
            log("cannot load the native library: " + t);
            System.exit(1);
        }
        fillActivityThread();
        new Helper(status).run();
    }

    /**
     * Some Android APIs (the controller light bar among them) expect an app context. A process
     * started with app_process has none, so give it a system one, the way scrcpy does.
     */
    private static void fillActivityThread() {
        try {
            android.os.Looper.prepareMainLooper();
            Class<?> at = Class.forName("android.app.ActivityThread");
            java.lang.reflect.Constructor<?> ctor = at.getDeclaredConstructor();
            ctor.setAccessible(true);
            Object thread = ctor.newInstance();
            java.lang.reflect.Field current = at.getDeclaredField("sCurrentActivityThread");
            current.setAccessible(true);
            current.set(null, thread);
            java.lang.reflect.Field system = at.getDeclaredField("mSystemThread");
            system.setAccessible(true);
            system.setBoolean(thread, true);
        } catch (Throwable t) {
            log("no app context (light bar may not work): " + t);
        }
    }

    private static void loadNative() throws Exception {
        new File(DIR).mkdirs();
        String apk = System.getProperty("java.class.path");
        File out = new File(DIR, "libpadthru.so");
        try (ZipFile zip = new ZipFile(apk)) {
            ZipEntry entry = null;
            for (String abi : Build.SUPPORTED_ABIS) {
                entry = zip.getEntry("lib/" + abi + "/libpadthru.so");
                if (entry != null) break;
            }
            if (entry == null) throw new IllegalStateException("no libpadthru.so for " + String.join(",", Build.SUPPORTED_ABIS));
            try (InputStream in = zip.getInputStream(entry); OutputStream o = new FileOutputStream(out)) {
                byte[] b = new byte[16384];
                int n;
                while ((n = in.read(b)) > 0) o.write(b, 0, n);
            }
        }
        System.load(out.getAbsolutePath());
    }

    private static final SimpleDateFormat TIME = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US);

    static void log(String msg) {
        synchronized (TIME) {
            System.out.println(TIME.format(new Date()) + " " + msg);
        }
    }

    /** Logs on the box and in the PC's Padthru log. */
    private void report(String msg) {
        log(msg);
        try {
            queue(new JSONObject().put("log", "box helper: " + msg).toString());
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------------ main loop

    private void run() {
        String hello;
        try {
            hello = new JSONObject().put("hello", new JSONObject()
                    .put("app", "padthru-android").put("v", MainActivity.VERSION)
                    .put("ua", "background helper, Android " + Build.VERSION.RELEASE + ", " + Build.MANUFACTURER + " " + Build.MODEL)).toString();
        } catch (Exception e) {
            hello = "{\"hello\":{\"app\":\"padthru-android\"}}";
        }
        File hostFile = new File(DIR, "host");
        link = new PcLink(new PcLink.Store() {
            @Override
            public String get() {
                try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(hostFile), StandardCharsets.UTF_8))) {
                    String s = r.readLine();
                    return s == null ? "" : s.trim();
                } catch (Exception e) {
                    return "";
                }
            }

            @Override
            public void set(String host) {
                try (OutputStream o = new FileOutputStream(hostFile)) {
                    o.write(host.getBytes(StandardCharsets.UTF_8));
                } catch (Exception ignored) {
                }
            }
        }, hello, new PcLink.Listener() {
            @Override
            public void onStatus(String text, boolean connected) {
                pcStatus = text;
                log(text);
            }

            @Override
            public void onConnected() {
                synchronized (lock) {
                    for (Pad p : groups.values()) {
                        p.sentId = false;
                        p.dirty = true;
                    }
                    lock.notifyAll();
                }
                report("connected (" + (cecKnown ? (boxActive ? "TV shows the box: controllers stay on the box" : "TV shows another input: controllers go to the PC") : "TV input unknown") + ")");
            }

            @Override
            public void onMessage(JSONObject m) {
                fromPc(m);
            }
        });
        link.start();
        thread("padthru-status", this::statusServer);
        thread("padthru-cec", this::cecLoop);
        thread("padthru-scan", this::scanLoop);
        thread("padthru-rumble", this::rumbleRefreshLoop);
        log("Padthru helper " + MainActivity.VERSION + " started");
        sendLoop();
    }

    private static void thread(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    private void queue(String msg) {
        synchronized (lock) {
            if (outbox.size() < 50) outbox.add(msg);
            lock.notifyAll();
        }
    }

    /** True while the controllers belong to the PC: the TV shows something other than the box. */
    private boolean pcMode() {
        return !boxActive;
    }

    // ------------------------------------------------------------------ TV input (HDMI-CEC)

    private void cecLoop() {
        while (true) {
            Boolean active = null;
            try {
                Process p = Runtime.getRuntime().exec(new String[]{"dumpsys", "hdmi_control"});
                StringBuilder sb = new StringBuilder();
                try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        if (line.contains("isActiveSource")) sb.append(line);
                    }
                }
                p.waitFor();
                Matcher m = ACTIVE.matcher(sb);
                if (m.find()) active = Boolean.parseBoolean(m.group(1));
            } catch (Exception ignored) {
            }
            boolean known = active != null;
            boolean nowBox = known && active;
            if (nowBox != boxActive || known != cecKnown) {
                cecKnown = known;
                setBoxActive(nowBox);
            }
            sleep(CEC_POLL_MS);
        }
    }

    private void setBoxActive(boolean active) {
        List<String> gone = new ArrayList<>();
        synchronized (lock) {
            boxActive = active;
            for (Node n : nodes.values()) applyGrab(n);
            for (Pad p : groups.values()) {
                if (p.idx < 0) continue;
                if (active) {
                    gone.add("{\"i\":" + p.idx + ",\"gone\":1}");
                } else {
                    p.sentId = false;
                    p.dirty = true;
                }
            }
            outbox.addAll(gone);
            lock.notifyAll();
        }
        report(active ? "TV switched to the box: controllers work on the box" : "TV switched away from the box: controllers go to the PC");
    }

    /** Grab a controller's nodes while it belongs to the PC, so the box ignores it. Lock held. */
    private void applyGrab(Node n) {
        boolean want = pcMode() && n.pad.gamepad != null && !n.closed;
        if (want != n.grabbed) {
            int rc = Evdev.grab(n.fd, want);
            if (rc == 0) n.grabbed = want;
            else log("grab " + n.path + " failed: " + rc);
        }
    }

    // ------------------------------------------------------------------ finding controllers

    private void scanLoop() {
        while (true) {
            File[] files = new File("/dev/input").listFiles();
            if (files != null) {
                for (File f : files) {
                    String path = f.getAbsolutePath();
                    if (!f.getName().startsWith("event")) continue;
                    synchronized (lock) {
                        if (nodes.containsKey(path)) continue;
                    }
                    probe(path);
                }
            }
            synchronized (lock) {
                long now = System.currentTimeMillis();
                for (Pad p : groups.values()) {
                    if (p.idx >= 0 && now - p.batteryAt > 30000) {
                        p.batteryAt = now;
                        readBattery(p);
                    }
                }
            }
            sleep(2000);
        }
    }

    private void probe(String path) {
        int fd = Evdev.open(path, false);
        if (fd < 0) return;
        boolean gamepad = Evdev.hasCode(fd, Evdev.EV_KEY, Evdev.BTN_SOUTH) && Evdev.hasCode(fd, Evdev.EV_ABS, Evdev.ABS_X);
        boolean motion = Evdev.hasProp(fd, Evdev.INPUT_PROP_ACCELEROMETER);
        boolean touch = !gamepad && Evdev.hasCode(fd, Evdev.EV_ABS, Evdev.ABS_MT_POSITION_X)
                && Evdev.hasCode(fd, Evdev.EV_KEY, Evdev.BTN_LEFT);
        if (!gamepad && !motion && !touch) {
            Evdev.close(fd);
            return;
        }
        String uniq = Evdev.uniq(fd);
        String phys = Evdev.phys(fd);
        int slash = phys.lastIndexOf('/');
        String key = !uniq.isEmpty() ? uniq : (slash > 0 ? phys.substring(0, slash) : path);
        int[] ids = Evdev.ids(fd);
        String name = Evdev.name(fd);
        Node node;
        synchronized (lock) {
            Pad p = groups.get(key);
            if (p == null) {
                p = new Pad(key);
                groups.put(key, p);
            }
            node = new Node(path, fd, gamepad ? 0 : motion ? 1 : 2, p);
            for (int code = 0; code < 0x40; code++) node.abs[code] = Evdev.absInfo(fd, code);
            if (gamepad) {
                p.gamepad = node;
                p.name = name;
                p.vendor = ids[1];
                p.product = ids[2];
                p.usb = ids[0] == 3;
                p.sony = ids[1] == 0x054c;
                p.analogTriggers = Evdev.hasCode(fd, Evdev.EV_ABS, Evdev.ABS_Z) && Evdev.hasCode(fd, Evdev.EV_ABS, Evdev.ABS_RZ);
                p.tvId = String.format(Locale.US, "%s (STANDARD GAMEPAD Vendor: %04x Product: %04x)", name, ids[1], ids[2]);
                if (p.ffFd >= 0) Evdev.close(p.ffFd);
                p.ffFd = Evdev.hasCode(fd, Evdev.EV_FF, Evdev.FF_RUMBLE) ? Evdev.open(path, true) : -1;
                p.rumbleId = -1;
                if (p.idx < 0) {
                    p.idx = freeIndex();
                }
                p.sentId = false;
                p.dirty = true;
                findBattery(p);
            } else if (motion) {
                p.motion = node;
            } else {
                p.touch = node;
            }
            nodes.put(path, node);
            for (Node n : nodes.values()) if (n.pad == p) applyGrab(n);
            lock.notifyAll();
        }
        log("found " + (gamepad ? "controller" : motion ? "motion sensors" : "touchpad") + " " + path + ": " + name
                + (gamepad ? String.format(Locale.US, " %04x:%04x %s", ids[1], ids[2], ids[0] == 3 ? "USB" : ids[0] == 5 ? "Bluetooth" : "bus " + ids[0]) : ""));
        if (gamepad) report("controller connected: " + name + (ids[0] == 3 ? " (USB)" : ids[0] == 5 ? " (Bluetooth)" : ""));
        thread("padthru-read-" + path.substring(path.lastIndexOf('/') + 1), () -> readLoop(node));
    }

    private int freeIndex() {
        for (int i = 0; i < 4; i++) {
            boolean used = false;
            for (Pad p : groups.values()) if (p.idx == i) used = true;
            if (!used) return i;
        }
        return 3;
    }

    private void readLoop(Node n) {
        int[] buf = new int[64 * 3];
        while (!n.closed) {
            int count = Evdev.read(n.fd, buf);
            if (count < 0) {
                if (count == -4) continue; // EINTR
                remove(n);
                return;
            }
            synchronized (lock) {
                for (int i = 0; i < count; i++) handle(n, buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2]);
            }
        }
    }

    private void remove(Node n) {
        String gone = null;
        synchronized (lock) {
            n.closed = true;
            nodes.remove(n.path);
            Pad p = n.pad;
            if (p.gamepad == n) {
                p.gamepad = null;
                if (p.ffFd >= 0) Evdev.close(p.ffFd);
                p.ffFd = -1;
                p.vibrators = null;
                if (p.idx >= 0) gone = "{\"i\":" + p.idx + ",\"gone\":1}";
                p.idx = -1;
                if (p.lights != null) {
                    try {
                        p.lights.close();
                    } catch (Exception ignored) {
                    }
                    p.lights = null;
                }
            } else if (p.motion == n) {
                p.motion = null;
                p.gyro = p.accel = null;
            } else if (p.touch == n) {
                p.touch = null;
            }
            if (p.gamepad == null && p.motion == null && p.touch == null) groups.remove(p.key);
            if (gone != null && pcMode()) outbox.add(gone);
            lock.notifyAll();
        }
        Evdev.close(n.fd);
        log("removed " + n.path);
        if (gone != null) report("controller disconnected: " + n.pad.name);
    }

    // ------------------------------------------------------------------ events

    private static float norm(int[] abs, int v, boolean centered) {
        if (abs == null || abs[2] <= abs[1]) return 0;
        float f = (v - abs[1]) / (float) (abs[2] - abs[1]);
        return centered ? f * 2 - 1 : f;
    }

    private int buttonIndex(Pad p, int code) {
        switch (code) {
            case Evdev.BTN_SOUTH: return 0;
            case Evdev.BTN_EAST: return 1;
            // Linux: Sony pads put Square/Triangle on WEST/NORTH; xpad and most others put X/Y
            // on BTN_X/BTN_Y, which share the NORTH/WEST codes the other way round
            case Evdev.BTN_NORTH: return p.sony ? 3 : 2;
            case Evdev.BTN_WEST: return p.sony ? 2 : 3;
            case Evdev.BTN_TL: return 4;
            case Evdev.BTN_TR: return 5;
            case Evdev.BTN_TL2: return p.analogTriggers ? -1 : 6;
            case Evdev.BTN_TR2: return p.analogTriggers ? -1 : 7;
            case Evdev.BTN_SELECT: return 8;
            case Evdev.BTN_START: return 9;
            case Evdev.BTN_THUMBL: return 10;
            case Evdev.BTN_THUMBR: return 11;
            case Evdev.BTN_DPAD_UP: return 12;
            case Evdev.BTN_DPAD_DOWN: return 13;
            case Evdev.BTN_DPAD_LEFT: return 14;
            case Evdev.BTN_DPAD_RIGHT: return 15;
            case Evdev.BTN_MODE: return 16;
            default: return -1;
        }
    }

    /** One input event; lock held. */
    private void handle(Node n, int type, int code, int value) {
        Pad p = n.pad;
        if (type == Evdev.EV_SYN) {
            if (code != Evdev.SYN_REPORT) return;
            if (n.kind == 1) {
                int[][] a = n.abs;
                float ra = a[Evdev.ABS_X] != null && a[Evdev.ABS_X][5] > 0 ? a[Evdev.ABS_X][5] : 8192;
                float rg = a[Evdev.ABS_RX] != null && a[Evdev.ABS_RX][5] > 0 ? a[Evdev.ABS_RX][5] : 1024;
                p.accel = new float[]{p.rawMotion[0] / ra, p.rawMotion[1] / ra, p.rawMotion[2] / ra};
                p.gyro = new float[]{p.rawMotion[3] / rg, p.rawMotion[4] / rg, p.rawMotion[5] / rg};
            } else if (n.kind == 2) {
                for (int i = 0; i < 2; i++) p.fingers[i] = p.slotId[i] >= 0 ? p.slotPos[i].clone() : null;
            }
            if (p.idx >= 0) {
                p.dirty = true;
                lock.notifyAll();
            }
            return;
        }
        if (n.kind == 0) {
            if (type == Evdev.EV_KEY) {
                int i = buttonIndex(p, code);
                if (i >= 0) p.buttons[i] = value != 0 ? 1 : 0;
            } else if (type == Evdev.EV_ABS) {
                int[] abs = n.abs[code & 0x3f];
                switch (code) {
                    case Evdev.ABS_X: p.axes[0] = norm(abs, value, true); break;
                    case Evdev.ABS_Y: p.axes[1] = norm(abs, value, true); break;
                    case Evdev.ABS_RX: p.axes[2] = norm(abs, value, true); break;
                    case Evdev.ABS_RY: p.axes[3] = norm(abs, value, true); break;
                    case Evdev.ABS_Z: p.buttons[6] = norm(abs, value, false); break;
                    case Evdev.ABS_RZ: p.buttons[7] = norm(abs, value, false); break;
                    case Evdev.ABS_HAT0X:
                        p.buttons[14] = value < 0 ? 1 : 0;
                        p.buttons[15] = value > 0 ? 1 : 0;
                        break;
                    case Evdev.ABS_HAT0Y:
                        p.buttons[12] = value < 0 ? 1 : 0;
                        p.buttons[13] = value > 0 ? 1 : 0;
                        break;
                    default:
                }
            }
        } else if (n.kind == 1) {
            if (type == Evdev.EV_ABS && code <= Evdev.ABS_RZ) p.rawMotion[code] = value;
        } else {
            if (type == Evdev.EV_KEY && code == Evdev.BTN_LEFT) {
                p.buttons[17] = value != 0 ? 1 : 0;
            } else if (type == Evdev.EV_ABS) {
                if (code == Evdev.ABS_MT_SLOT) {
                    p.slot = value;
                } else if (p.slot >= 0 && p.slot < 2) {
                    if (code == Evdev.ABS_MT_TRACKING_ID) {
                        p.slotId[p.slot] = value;
                    } else if (code == Evdev.ABS_MT_POSITION_X) {
                        p.slotPos[p.slot][0] = norm(n.abs[code & 0x3f], value, false);
                    } else if (code == Evdev.ABS_MT_POSITION_Y) {
                        p.slotPos[p.slot][1] = norm(n.abs[code & 0x3f], value, false);
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------ battery

    private void findBattery(Pad p) {
        File[] supplies = new File("/sys/class/power_supply").listFiles();
        if (supplies == null || p.key.isEmpty()) return;
        for (File s : supplies) {
            if (s.getName().toLowerCase(Locale.US).contains(p.key.toLowerCase(Locale.US))) {
                p.batteryFile = new File(s, "capacity").getAbsolutePath();
                readBattery(p);
                return;
            }
        }
    }

    private static void readBattery(Pad p) {
        if (p.batteryFile == null) return;
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(p.batteryFile)))) {
            p.battery = Integer.parseInt(r.readLine().trim());
        } catch (Exception e) {
            p.batteryFile = null;
        }
    }

    // ------------------------------------------------------------------ to the PC

    private void sendLoop() {
        while (true) {
            List<String> out = new ArrayList<>();
            synchronized (lock) {
                boolean connected = link.isConnected();
                if (connected) out.addAll(outbox);
                outbox.clear();
                long nowNs = System.nanoTime(), nowMs = nowNs / 1_000_000;
                long waitMs = KEEPALIVE_MS;
                if (connected && pcMode()) {
                    for (Pad p : groups.values()) {
                        if (p.idx < 0) continue;
                        boolean due = (p.dirty && nowNs - p.lastSentNs >= MIN_GAP_NS) || nowMs - p.lastSentMs >= KEEPALIVE_MS;
                        if (due) {
                            out.add(toJson(p));
                            p.dirty = false;
                            p.lastSentNs = nowNs;
                            p.lastSentMs = nowMs;
                            p.sent++;
                        } else if (p.dirty) {
                            waitMs = 1;
                        }
                    }
                }
                if (out.isEmpty()) {
                    try {
                        lock.wait(waitMs);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
            }
            for (String m : out) link.sendText(m);
        }
    }

    private static void num(StringBuilder sb, float v, float k) {
        float r = Math.round(v * k) / k;
        if (r == (long) r) sb.append((long) r);
        else sb.append(r);
    }

    private static String toJson(Pad p) {
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"i\":").append(p.idx).append(",\"b\":[");
        for (int i = 0; i < p.buttons.length; i++) {
            if (i > 0) sb.append(',');
            num(sb, p.buttons[i], 1000);
        }
        sb.append("],\"a\":[");
        for (int i = 0; i < 4; i++) {
            if (i > 0) sb.append(',');
            num(sb, p.axes[i], 1000);
        }
        sb.append(']');
        if (p.gyro != null && p.accel != null) {
            sb.append(",\"g\":[");
            for (int i = 0; i < 3; i++) {
                if (i > 0) sb.append(',');
                num(sb, p.gyro[i], 10);
            }
            sb.append("],\"ac\":[");
            for (int i = 0; i < 3; i++) {
                if (i > 0) sb.append(',');
                num(sb, p.accel[i], 10000);
            }
            sb.append(']');
        }
        if (p.touch != null) {
            sb.append(",\"t\":[");
            for (int i = 0; i < 2; i++) {
                if (i > 0) sb.append(',');
                float[] f = p.fingers[i];
                if (f == null) sb.append("null");
                else {
                    sb.append('[');
                    num(sb, f[0], 10000);
                    sb.append(',');
                    num(sb, f[1], 10000);
                    sb.append(']');
                }
            }
            sb.append(']');
        }
        if (p.battery >= 0) sb.append(",\"bat\":").append(p.battery);
        if (p.motion != null) sb.append(",\"usb\":").append(p.usb);
        if (!p.sentId) {
            sb.append(",\"id\":").append(JSONObject.quote(p.tvId)).append(",\"m\":\"standard\"");
            p.sentId = true;
        }
        return sb.append('}').toString();
    }

    // ------------------------------------------------------------------ from the PC

    private Pad padByIdx(int idx) {
        for (Pad p : groups.values()) if (p.idx == idx && p.gamepad != null) return p;
        return null;
    }

    private void fromPc(JSONObject m) {
        JSONArray a;
        if ((a = m.optJSONArray("r")) != null) {
            Pad p;
            synchronized (lock) {
                p = padByIdx(a.optInt(0, -1));
                if (p == null) return;
                p.rumbleLarge = a.optInt(1);
                p.rumbleSmall = a.optInt(2);
                if (p.ffFd >= 0) {
                    int strong = p.rumbleLarge * 257, weak = p.rumbleSmall * 257;
                    if (strong == 0 && weak == 0) {
                        if (p.rumbleId >= 0) Evdev.stopRumble(p.ffFd, p.rumbleId);
                        return;
                    }
                    int id = Evdev.rumble(p.ffFd, p.rumbleId, strong, weak);
                    if (id >= 0) {
                        p.rumbleId = id;
                        return;
                    }
                    Evdev.close(p.ffFd); // the box won't let us; use Android's vibration API instead
                    p.ffFd = -1;
                }
            }
            androidRumble(p);
        } else if ((a = m.optJSONArray("l")) != null) {
            Pad p;
            synchronized (lock) {
                p = padByIdx(a.optInt(0, -1));
            }
            if (p != null) light(p, Color.rgb(a.optInt(1), a.optInt(2), a.optInt(3)));
        } else if ((a = m.optJSONArray("k")) != null) {
            synchronized (lock) {
                Pad p = padByIdx(a.optInt(0, -1));
                if (p != null) p.pcKind = a.optString(1);
            }
        }
    }

    private boolean rumbleFailed;

    /** Rumble through Android's controller vibration API (boxes that don't allow kernel rumble). */
    private void androidRumble(Pad p) {
        try {
            if (p.vibrators == null) {
                InputDevice d = androidDevice(p);
                if (d == null) return;
                p.vibrators = d.getVibratorManager();
            }
            if (p.rumbleLarge == 0 && p.rumbleSmall == 0) {
                p.vibrators.cancel();
                return;
            }
            int[] ids = p.vibrators.getVibratorIds();
            android.os.CombinedVibration.ParallelCombination c = android.os.CombinedVibration.startParallel();
            int[] amp = {p.rumbleLarge, p.rumbleSmall};
            boolean any = false;
            for (int i = 0; i < Math.min(2, ids.length); i++) {
                if (amp[i] > 0) {
                    c.addVibrator(ids[i], android.os.VibrationEffect.createOneShot(2000, Math.min(255, amp[i])));
                    any = true;
                }
            }
            if (!any && ids.length > 0) {
                c.addVibrator(ids[0], android.os.VibrationEffect.createOneShot(2000, Math.min(255, Math.max(amp[0], amp[1]))));
            }
            p.vibrators.vibrate(c.combine());
        } catch (Throwable t) {
            if (!rumbleFailed) {
                rumbleFailed = true;
                report("rumble not available: " + (t.getCause() != null ? t.getCause() : t));
            }
        }
    }

    /** Keeps Android-API rumble going while a game holds it (each request lasts 2 s). */
    private void rumbleRefreshLoop() {
        while (true) {
            sleep(1000);
            List<Pad> active = new ArrayList<>();
            synchronized (lock) {
                for (Pad p : groups.values()) {
                    if (p.gamepad != null && p.ffFd < 0 && p.vibrators != null && (p.rumbleLarge > 0 || p.rumbleSmall > 0)) active.add(p);
                }
            }
            for (Pad p : active) androidRumble(p);
        }
    }

    /** The Android input device for one of our controllers. */
    private static InputDevice androidDevice(Pad p) throws Exception {
        Object im;
        try { // Android 14 moved the process-wide input manager to InputManagerGlobal
            im = Class.forName("android.hardware.input.InputManagerGlobal").getMethod("getInstance").invoke(null);
        } catch (ClassNotFoundException e) {
            im = Class.forName("android.hardware.input.InputManager").getMethod("getInstance").invoke(null);
        }
        Class<?> imc = im.getClass();
        int[] ids = (int[]) imc.getMethod("getInputDeviceIds").invoke(im);
        for (int id : ids) {
            InputDevice d = (InputDevice) imc.getMethod("getInputDevice", int.class).invoke(im, id);
            if (d != null && !d.isVirtual() && d.getVendorId() == p.vendor && d.getProductId() == p.product
                    && (d.getSources() & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD) return d;
        }
        for (int id : ids) { // a DualShock's merged device may not list gamepad first
            InputDevice d = (InputDevice) imc.getMethod("getInputDevice", int.class).invoke(im, id);
            if (d != null && !d.isVirtual() && d.getVendorId() == p.vendor && d.getProductId() == p.product) return d;
        }
        return null;
    }

    private boolean lightFailed;

    /** The light bar through Android's own controller-lights API (where the box offers it). */
    private void light(Pad p, int color) {
        try {
            if (p.lights == null) {
                InputDevice d = androidDevice(p);
                if (d == null) return;
                LightsManager lm = d.getLightsManager();
                for (Light l : lm.getLights()) {
                    if (l.hasRgbControl()) {
                        p.rgb = l;
                        p.lights = lm.openSession();
                        break;
                    }
                }
            }
            if (p.lights != null && p.rgb != null) {
                p.lights.requestLights(new LightsRequest.Builder()
                        .addLight(p.rgb, new LightState.Builder().setColor(color).build()).build());
            }
        } catch (Throwable t) {
            if (!lightFailed) {
                lightFailed = true;
                report("light bar not available: " + (t.getCause() != null ? t.getCause() : t));
            }
        }
    }

    // ------------------------------------------------------------------ status for the app

    private void statusServer() {
        while (true) {
            try (Socket s = statusSocket.accept()) {
                JSONObject j = new JSONObject();
                j.put("helper", MainActivity.VERSION);
                j.put("pc", pcStatus);
                j.put("connected", link.isConnected());
                j.put("tv", !cecKnown ? "unknown" : boxActive ? "box" : "other");
                JSONArray pads = new JSONArray();
                synchronized (lock) {
                    for (Pad p : groups.values()) {
                        if (p.idx < 0) continue;
                        p.rate = p.sent;
                        p.sent = 0;
                        pads.put(new JSONObject().put("player", p.idx + 1).put("name", p.name).put("usb", p.usb)
                                .put("motion", p.motion != null).put("touchpad", p.touch != null)
                                .put("battery", p.battery).put("pc", p.pcKind).put("rate", p.rate));
                    }
                }
                j.put("pads", pads);
                s.getOutputStream().write((j.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            } catch (Exception ignored) {
            }
        }
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }
}
