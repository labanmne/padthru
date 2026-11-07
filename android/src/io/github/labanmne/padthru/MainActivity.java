package io.github.labanmne.padthru;

import android.app.Activity;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.hardware.BatteryState;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.input.InputManager;
import android.hardware.lights.Light;
import android.hardware.lights.LightState;
import android.hardware.lights.LightsManager;
import android.hardware.lights.LightsRequest;
import android.os.Build;
import android.os.Bundle;
import android.os.CombinedVibration;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.VibratorManager;
import android.util.SparseArray;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Padthru for Android TV boxes.
 *
 * Reads the controllers connected to the box (Bluetooth or USB) and streams them to the
 * Padthru PC app, like the Samsung TV app does, plus what Android 12 exposes and Samsung
 * TVs don't: the DualShock's motion sensors, touchpad and battery. Games' rumble and light
 * bar color come back from the PC. Android only gives controller input to the app on
 * screen, so this app stays open on the box while the TV shows the PC's HDMI input.
 */
public class MainActivity extends Activity {
    static final String VERSION = "0.4.0";
    private static final long KEEPALIVE_MS = 100;   // the PC releases input after 600 ms of silence
    private static final long MIN_GAP_NS = 2_000_000; // at most 500 messages a second per controller

    /** One controller connected to the box. */
    private static final class Pad {
        final int deviceId;
        final int idx;
        final String name;
        final String id;     // described like Chrome does, so the PC recognises Sony pads
        final boolean usb;
        final boolean analogTriggers;
        final float[] buttons = new float[18];
        final float[] axes = new float[4];
        float[] gyro;        // deg/s
        float[] accel;       // g
        final float[][] fingers = new float[2][];
        int battery = -1;
        boolean dirty = true;
        boolean sentId;
        long lastSentNs;
        long lastSentMs;
        int sentThisSecond, rate;
        String pcKind = "";
        SensorManager sensors;
        SensorEventListener sensorListener;
        LightsManager.LightsSession lights;
        Light rgb;
        VibratorManager vibrators;
        int rumbleLarge, rumbleSmall;
        boolean accelInSi = true;
        int accelSamples;
        float accelMagSum;

        Pad(InputDevice d, int idx) {
            this.deviceId = d.getId();
            this.idx = idx;
            // Android merges a DualShock's gamepad, motion and touchpad parts into one device
            // and may name it after any of them
            this.name = d.getName().replaceAll(" (Touchpad|Motion Sensors)$", "");
            this.id = String.format("%s (STANDARD GAMEPAD Vendor: %04x Product: %04x)", name, d.getVendorId(), d.getProductId());
            // a DualShock 4 calls itself "Wireless Controller" over Bluetooth and
            // "Sony Interactive Entertainment Wireless Controller" over USB
            this.usb = name.startsWith("Sony Interactive Entertainment");
            this.analogTriggers = d.getMotionRange(MotionEvent.AXIS_LTRIGGER) != null
                    || d.getMotionRange(MotionEvent.AXIS_BRAKE) != null;
        }
    }

    private final SparseArray<Pad> pads = new SparseArray<>();
    private final java.util.ArrayDeque<String> outbox = new java.util.ArrayDeque<>();
    private final Object lock = new Object();
    private PcLink link;
    private Handler ui;
    private HandlerThread sensorThread;
    private Handler sensorHandler;
    private InputManager inputManager;
    private TextView pcText, padText, hintText;
    private View root;
    private volatile boolean sending = true;
    private Thread sender;

    // ---------------------------------------------------------------- life cycle

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        ui = new Handler(Looper.getMainLooper());
        buildUi();

        sensorThread = new HandlerThread("padthru-sensors");
        sensorThread.start();
        sensorHandler = new Handler(sensorThread.getLooper());
        inputManager = (InputManager) getSystemService(Context.INPUT_SERVICE);

        String hello;
        try {
            hello = new JSONObject().put("hello", new JSONObject()
                    .put("app", "padthru-android").put("v", VERSION)
                    .put("ua", "Android " + Build.VERSION.RELEASE + ", " + Build.MANUFACTURER + " " + Build.MODEL)).toString();
        } catch (Exception e) {
            hello = "{\"hello\":{\"app\":\"padthru-android\",\"v\":\"" + VERSION + "\"}}";
        }
        link = new PcLink(getSharedPreferences("padthru", MODE_PRIVATE), hello, new PcLink.Listener() {
            @Override
            public void onStatus(String text, boolean connected) {
                ui.post(() -> {
                    pcText.setText(text);
                    pcText.setTextColor(connected ? 0xFF7CE38B : 0xFFFFB86B);
                });
            }

            @Override
            public void onConnected() {
                synchronized (lock) {
                    for (int i = 0; i < pads.size(); i++) {
                        Pad p = pads.valueAt(i);
                        p.sentId = false;
                        p.dirty = true;
                    }
                    lock.notifyAll();
                }
            }

            @Override
            public void onMessage(JSONObject m) {
                handlePcMessage(m);
            }
        });
        link.start();
        sender = new Thread(this::sendLoop, "padthru-send");
        sender.start();
        ui.post(this::refreshUi);
    }

    @Override
    protected void onResume() {
        super.onResume();
        inputManager.registerInputDeviceListener(deviceListener, ui);
        for (int id : inputManager.getInputDeviceIds()) addDevice(id);
        root.requestUnbufferedDispatch(InputDevice.SOURCE_CLASS_JOYSTICK | InputDevice.SOURCE_GAMEPAD);
    }

    @Override
    protected void onPause() {
        super.onPause();
        inputManager.unregisterInputDeviceListener(deviceListener);
        // Android stops sending controller input to apps in the background: release everything
        synchronized (lock) {
            for (int i = pads.size() - 1; i >= 0; i--) removePad(pads.valueAt(i));
        }
    }

    @Override
    protected void onDestroy() {
        sending = false;
        if (sender != null) sender.interrupt();
        link.stop();
        sensorThread.quitSafely();
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            // the DualShock touchpad only reports raw finger positions to an app that captures the pointer
            try {
                root.requestPointerCapture();
            } catch (Exception ignored) {
            }
        }
    }

    // ---------------------------------------------------------------- controllers

    private final InputManager.InputDeviceListener deviceListener = new InputManager.InputDeviceListener() {
        @Override
        public void onInputDeviceAdded(int deviceId) {
            addDevice(deviceId);
        }

        @Override
        public void onInputDeviceRemoved(int deviceId) {
            synchronized (lock) {
                Pad p = pads.get(deviceId);
                if (p != null) removePad(p);
            }
        }

        @Override
        public void onInputDeviceChanged(int deviceId) {
            addDevice(deviceId);
        }
    };

    /** Real game controllers only: gamepad buttons and a stick. Box remotes and Android's
     *  "virtual-search" device claim one or the other (seen on a Skyworth box), not both. */
    private static boolean isGamepad(InputDevice d) {
        if (d == null || d.isVirtual()) return false;
        int s = d.getSources();
        boolean joystick = (s & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
        boolean gamepad = (s & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD;
        return gamepad && joystick
                && d.getMotionRange(MotionEvent.AXIS_X, InputDevice.SOURCE_JOYSTICK) != null
                && d.getMotionRange(MotionEvent.AXIS_Y, InputDevice.SOURCE_JOYSTICK) != null;
    }

    private void addDevice(int deviceId) {
        InputDevice d = InputDevice.getDevice(deviceId);
        if (!isGamepad(d)) return;
        Pad p;
        synchronized (lock) {
            if (pads.get(deviceId) != null) return;
            int idx = 0;
            for (; idx < 4; idx++) {
                boolean used = false;
                for (int i = 0; i < pads.size(); i++) if (pads.valueAt(i).idx == idx) used = true;
                if (!used) break;
            }
            if (idx >= 4) return;
            p = new Pad(d, idx);
            pads.put(deviceId, p);
        }
        setUpExtras(p, d);
        wake();
    }

    private void removePad(Pad p) {
        pads.remove(p.deviceId);
        if (p.sensors != null && p.sensorListener != null) p.sensors.unregisterListener(p.sensorListener);
        if (p.lights != null) {
            try {
                p.lights.close();
            } catch (Exception ignored) {
            }
        }
        if (p.vibrators != null) p.vibrators.cancel();
        // sent by the sender thread: Android forbids network traffic on the screen thread
        synchronized (lock) {
            outbox.add("{\"i\":" + p.idx + ",\"gone\":1}");
            lock.notifyAll();
        }
    }

    /** Motion sensors, light bar, rumble motors and battery, where the controller has them. */
    private void setUpExtras(Pad p, InputDevice d) {
        try {
            SensorManager sm = d.getSensorManager();
            Sensor gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE);
            Sensor accel = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            if (gyro != null || accel != null) {
                p.sensors = sm;
                p.sensorListener = new SensorEventListener() {
                    @Override
                    public void onSensorChanged(SensorEvent e) {
                        onSensor(p, e);
                    }

                    @Override
                    public void onAccuracyChanged(Sensor s, int accuracy) {
                    }
                };
                if (gyro != null) sm.registerListener(p.sensorListener, gyro, SensorManager.SENSOR_DELAY_FASTEST, sensorHandler);
                if (accel != null) sm.registerListener(p.sensorListener, accel, SensorManager.SENSOR_DELAY_FASTEST, sensorHandler);
            }
        } catch (Exception ignored) {
        }
        try {
            LightsManager lm = d.getLightsManager();
            for (Light l : lm.getLights()) {
                if (l.hasRgbControl()) {
                    p.rgb = l;
                    break;
                }
            }
            if (p.rgb != null) p.lights = lm.openSession();
        } catch (Exception ignored) {
        }
        try {
            VibratorManager vm = d.getVibratorManager();
            if (vm.getVibratorIds().length > 0) p.vibrators = vm;
        } catch (Exception ignored) {
        }
        readBattery(p);
    }

    private void readBattery(Pad p) {
        try {
            InputDevice d = InputDevice.getDevice(p.deviceId);
            if (d == null) return;
            BatteryState b = d.getBatteryState();
            if (b != null && b.isPresent() && !Float.isNaN(b.getCapacity())) {
                synchronized (lock) {
                    p.battery = Math.round(b.getCapacity() * 100);
                }
            }
        } catch (Exception ignored) {
        }
    }

    private void onSensor(Pad p, SensorEvent e) {
        float[] v = e.values;
        synchronized (lock) {
            if (e.sensor.getType() == Sensor.TYPE_GYROSCOPE) {
                // Android reports rad/s
                p.gyro = new float[]{(float) Math.toDegrees(v[0]), (float) Math.toDegrees(v[1]), (float) Math.toDegrees(v[2])};
            } else {
                // Android reports m/s^2; check once against gravity in case a device reports g
                float mag = (float) Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
                if (p.accelSamples < 50) {
                    p.accelSamples++;
                    p.accelMagSum += mag;
                    if (p.accelSamples == 50) p.accelInSi = p.accelMagSum / 50 > 4;
                }
                float k = p.accelInSi ? 1 / SensorManager.GRAVITY_EARTH : 1;
                p.accel = new float[]{v[0] * k, v[1] * k, v[2] * k};
            }
            p.dirty = true;
            lock.notifyAll();
        }
    }

    private Pad padFor(int deviceId) {
        synchronized (lock) {
            return pads.get(deviceId);
        }
    }

    private static int buttonIndex(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_BUTTON_A: return 0;
            case KeyEvent.KEYCODE_BUTTON_B: return 1;
            case KeyEvent.KEYCODE_BUTTON_X: return 2;
            case KeyEvent.KEYCODE_BUTTON_Y: return 3;
            case KeyEvent.KEYCODE_BUTTON_L1: return 4;
            case KeyEvent.KEYCODE_BUTTON_R1: return 5;
            case KeyEvent.KEYCODE_BUTTON_L2: return 6;
            case KeyEvent.KEYCODE_BUTTON_R2: return 7;
            case KeyEvent.KEYCODE_BUTTON_SELECT: return 8;
            case KeyEvent.KEYCODE_BUTTON_START: return 9;
            case KeyEvent.KEYCODE_BUTTON_THUMBL: return 10;
            case KeyEvent.KEYCODE_BUTTON_THUMBR: return 11;
            case KeyEvent.KEYCODE_DPAD_UP: return 12;
            case KeyEvent.KEYCODE_DPAD_DOWN: return 13;
            case KeyEvent.KEYCODE_DPAD_LEFT: return 14;
            case KeyEvent.KEYCODE_DPAD_RIGHT: return 15;
            case KeyEvent.KEYCODE_BUTTON_MODE: return 16;
            default: return -1;
        }
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        Pad p = padFor(e.getDeviceId());
        if (p == null) return super.dispatchKeyEvent(e); // the box's remote works as usual
        int i = buttonIndex(e.getKeyCode());
        // L2/R2 also arrive as analog axes on most pads; the keys only count on pads without them
        if (i >= 0 && e.getRepeatCount() == 0 && !((i == 6 || i == 7) && p.analogTriggers)) {
            boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
            synchronized (lock) {
                p.buttons[i] = down ? 1 : 0;
                p.dirty = true;
                lock.notifyAll();
            }
        }
        return true; // never let a controller button act as Back/Home on the box
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent e) {
        Pad p = padFor(e.getDeviceId());
        if (p == null || (e.getSource() & InputDevice.SOURCE_CLASS_JOYSTICK) == 0) return super.dispatchGenericMotionEvent(e);
        float lt = Math.max(e.getAxisValue(MotionEvent.AXIS_LTRIGGER), e.getAxisValue(MotionEvent.AXIS_BRAKE));
        float rt = Math.max(e.getAxisValue(MotionEvent.AXIS_RTRIGGER), e.getAxisValue(MotionEvent.AXIS_GAS));
        float hx = e.getAxisValue(MotionEvent.AXIS_HAT_X), hy = e.getAxisValue(MotionEvent.AXIS_HAT_Y);
        synchronized (lock) {
            p.axes[0] = e.getAxisValue(MotionEvent.AXIS_X);
            p.axes[1] = e.getAxisValue(MotionEvent.AXIS_Y);
            p.axes[2] = e.getAxisValue(MotionEvent.AXIS_Z);
            p.axes[3] = e.getAxisValue(MotionEvent.AXIS_RZ);
            if (p.analogTriggers) {
                p.buttons[6] = lt;
                p.buttons[7] = rt;
            }
            p.buttons[12] = hy < -0.5f ? 1 : 0;
            p.buttons[13] = hy > 0.5f ? 1 : 0;
            p.buttons[14] = hx < -0.5f ? 1 : 0;
            p.buttons[15] = hx > 0.5f ? 1 : 0;
            p.dirty = true;
            lock.notifyAll();
        }
        return true;
    }

    /** Touchpad fingers and click, while the pointer is captured. */
    private boolean onCapturedPointer(View v, MotionEvent e) {
        Pad p = padFor(e.getDeviceId());
        if (p == null) return false;
        InputDevice d = e.getDevice();
        InputDevice.MotionRange rx = d == null ? null : d.getMotionRange(MotionEvent.AXIS_X, e.getSource());
        InputDevice.MotionRange ry = d == null ? null : d.getMotionRange(MotionEvent.AXIS_Y, e.getSource());
        float w = rx != null && rx.getRange() > 0 ? rx.getRange() : 1920, x0 = rx != null ? rx.getMin() : 0;
        float h = ry != null && ry.getRange() > 0 ? ry.getRange() : 942, y0 = ry != null ? ry.getMin() : 0;
        int action = e.getActionMasked();
        synchronized (lock) {
            p.fingers[0] = null;
            p.fingers[1] = null;
            if (action != MotionEvent.ACTION_UP && action != MotionEvent.ACTION_CANCEL) {
                int n = 0;
                for (int i = 0; i < e.getPointerCount() && n < 2; i++) {
                    if (action == MotionEvent.ACTION_POINTER_UP && i == e.getActionIndex()) continue;
                    p.fingers[n++] = new float[]{clamp01((e.getX(i) - x0) / w), clamp01((e.getY(i) - y0) / h)};
                }
            }
            p.buttons[17] = (e.getButtonState() & MotionEvent.BUTTON_PRIMARY) != 0 ? 1 : 0;
            p.dirty = true;
            lock.notifyAll();
        }
        return true;
    }

    private static float clamp01(float v) {
        return v < 0 ? 0 : v > 1 ? 1 : v;
    }

    // ---------------------------------------------------------------- to the PC

    private void wake() {
        synchronized (lock) {
            lock.notifyAll();
        }
    }

    private void sendLoop() {
        while (sending) {
            String[] out = new String[12];
            int n = 0;
            synchronized (lock) {
                while (!outbox.isEmpty() && n < 8) {
                    String m = outbox.poll();
                    if (link.isConnected()) out[n++] = m;
                }
                long nowNs = SystemClock.elapsedRealtimeNanos();
                long nowMs = nowNs / 1_000_000;
                long waitMs = KEEPALIVE_MS;
                if (link.isConnected()) {
                    for (int i = 0; i < pads.size(); i++) {
                        Pad p = pads.valueAt(i);
                        boolean due = (p.dirty && nowNs - p.lastSentNs >= MIN_GAP_NS) || nowMs - p.lastSentMs >= KEEPALIVE_MS;
                        if (due && n < out.length) {
                            out[n++] = toJson(p);
                            p.dirty = false;
                            p.lastSentNs = nowNs;
                            p.lastSentMs = nowMs;
                            p.sentThisSecond++;
                        } else if (p.dirty) {
                            waitMs = Math.min(waitMs, 1);
                        }
                    }
                }
                if (n == 0) {
                    try {
                        lock.wait(waitMs);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
            }
            for (int i = 0; i < n; i++) link.sendText(out[i]);
        }
    }

    private static void appendFloat(StringBuilder sb, float v, int decimals) {
        float k = decimals == 1 ? 10f : decimals == 3 ? 1000f : 10000f;
        float r = Math.round(v * k) / k;
        if (r == (long) r) sb.append((long) r);
        else sb.append(r);
    }

    private static String toJson(Pad p) {
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"i\":").append(p.idx).append(",\"b\":[");
        for (int i = 0; i < p.buttons.length; i++) {
            if (i > 0) sb.append(',');
            appendFloat(sb, p.buttons[i], 3);
        }
        sb.append("],\"a\":[");
        for (int i = 0; i < 4; i++) {
            if (i > 0) sb.append(',');
            appendFloat(sb, p.axes[i], 3);
        }
        sb.append(']');
        if (p.gyro != null) {
            sb.append(",\"g\":[");
            for (int i = 0; i < 3; i++) {
                if (i > 0) sb.append(',');
                appendFloat(sb, p.gyro[i], 1);
            }
            sb.append(']');
        }
        if (p.accel != null) {
            sb.append(",\"ac\":[");
            for (int i = 0; i < 3; i++) {
                if (i > 0) sb.append(',');
                appendFloat(sb, p.accel[i], 4);
            }
            sb.append(']');
        }
        if (p.sensors != null || p.fingers[0] != null) {
            sb.append(",\"t\":[");
            for (int i = 0; i < 2; i++) {
                if (i > 0) sb.append(',');
                float[] f = p.fingers[i];
                if (f == null) sb.append("null");
                else {
                    sb.append('[');
                    appendFloat(sb, f[0], 4);
                    sb.append(',');
                    appendFloat(sb, f[1], 4);
                    sb.append(']');
                }
            }
            sb.append(']');
        }
        if (p.battery >= 0) sb.append(",\"bat\":").append(p.battery);
        if (p.sensors != null) sb.append(",\"usb\":").append(p.usb);
        if (!p.sentId) {
            sb.append(",\"id\":").append(JSONObject.quote(p.id)).append(",\"m\":\"standard\"");
            p.sentId = true;
        }
        return sb.append('}').toString();
    }

    // ---------------------------------------------------------------- from the PC

    private Pad padByIdx(int idx) {
        for (int i = 0; i < pads.size(); i++) if (pads.valueAt(i).idx == idx) return pads.valueAt(i);
        return null;
    }

    private void handlePcMessage(JSONObject m) {
        JSONArray a;
        if ((a = m.optJSONArray("r")) != null) {
            Pad p;
            synchronized (lock) {
                p = padByIdx(a.optInt(0, -1));
                if (p == null) return;
                p.rumbleLarge = a.optInt(1);
                p.rumbleSmall = a.optInt(2);
            }
            rumble(p);
        } else if ((a = m.optJSONArray("l")) != null) {
            Pad p;
            synchronized (lock) {
                p = padByIdx(a.optInt(0, -1));
            }
            if (p != null && p.lights != null && p.rgb != null) {
                int color = Color.rgb(a.optInt(1), a.optInt(2), a.optInt(3));
                try {
                    p.lights.requestLights(new LightsRequest.Builder()
                            .addLight(p.rgb, new LightState.Builder().setColor(color).build()).build());
                } catch (Exception ignored) {
                }
            }
        } else if ((a = m.optJSONArray("k")) != null) {
            synchronized (lock) {
                Pad p = padByIdx(a.optInt(0, -1));
                if (p != null) p.pcKind = a.optString(1);
            }
        }
    }

    private final Runnable rumbleRefresh = new Runnable() {
        @Override
        public void run() {
            boolean any = false;
            synchronized (lock) {
                for (int i = 0; i < pads.size(); i++) {
                    Pad p = pads.valueAt(i);
                    if (p.rumbleLarge > 0 || p.rumbleSmall > 0) {
                        any = true;
                        vibrate(p, 300);
                    }
                }
            }
            if (any) ui.postDelayed(this, 200);
        }
    };

    private void rumble(Pad p) {
        if (p.vibrators == null) return;
        if (p.rumbleLarge == 0 && p.rumbleSmall == 0) {
            p.vibrators.cancel();
            return;
        }
        vibrate(p, 300);
        ui.removeCallbacks(rumbleRefresh);
        ui.postDelayed(rumbleRefresh, 200);
    }

    /** Strong (left) motor first, weak (right) second, like the DualShock's own order. */
    private static void vibrate(Pad p, long ms) {
        try {
            int[] ids = p.vibrators.getVibratorIds();
            CombinedVibration.ParallelCombination c = CombinedVibration.startParallel();
            int[] amp = {p.rumbleLarge, p.rumbleSmall};
            boolean any = false;
            for (int i = 0; i < Math.min(2, ids.length); i++) {
                if (amp[i] > 0) {
                    c.addVibrator(ids[i], VibrationEffect.createOneShot(ms, Math.min(255, amp[i])));
                    any = true;
                }
            }
            if (ids.length == 1 && !any && amp[1] > 0) {
                c.addVibrator(ids[0], VibrationEffect.createOneShot(ms, Math.min(255, amp[1])));
                any = true;
            }
            if (any) p.vibrators.vibrate(c.combine());
        } catch (Exception ignored) {
        }
    }

    // ---------------------------------------------------------------- screen

    private TextView label(float sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, 12, 0, 12);
        return t;
    }

    private void buildUi() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(120, 60, 120, 60);
        box.setBackgroundColor(0xFF101418);
        TextView title = label(40, Color.WHITE, true);
        title.setText("Padthru");
        pcText = label(22, 0xFFFFB86B, false);
        pcText.setText("Looking for your PC…");
        padText = label(20, 0xFFE0E0E0, false);
        hintText = label(16, 0xFF9AA4AE, false);
        hintText.setText("Leave Padthru open and switch the TV to your PC's HDMI input.\n"
                + "Controllers connected to this box (Bluetooth or USB) go to the PC.\n"
                + "Back on the box remote closes Padthru.   Padthru " + VERSION);
        box.addView(title);
        box.addView(pcText);
        box.addView(padText);
        box.addView(hintText);
        box.setFocusable(true);
        box.setFocusableInTouchMode(true);
        box.setOnCapturedPointerListener(this::onCapturedPointer);
        root = box;
        setContentView(box);
        box.requestFocus();
    }

    private void refreshUi() {
        StringBuilder sb = new StringBuilder();
        long now = SystemClock.elapsedRealtime();
        synchronized (lock) {
            for (int i = 0; i < pads.size(); i++) {
                Pad p = pads.valueAt(i);
                p.rate = p.sentThisSecond;
                p.sentThisSecond = 0;
                sb.append("Player ").append(p.idx + 1).append(": ").append(p.name).append(p.usb ? " (USB)" : "");
                if (!p.pcKind.isEmpty()) sb.append("  →  PC: ").append(p.pcKind);
                sb.append("\n      ").append(p.rate).append(" updates/s");
                if (p.gyro != null) sb.append(" · motion");
                if (p.rgb != null) sb.append(" · light bar");
                if (p.vibrators != null) sb.append(" · rumble");
                if (p.battery >= 0) sb.append(" · battery ").append(p.battery).append('%');
                sb.append('\n');
            }
        }
        if (sb.length() == 0) sb.append("No controller yet. Pair one in the box's Bluetooth settings or plug it into USB, then press a button.");
        padText.setText(sb.toString().trim());
        if (now / 1000 % 30 == 0) {
            synchronized (lock) {
                for (int i = 0; i < pads.size(); i++) readBattery(pads.valueAt(i));
            }
        }
        ui.postDelayed(this::refreshUi, 1000);
    }
}
