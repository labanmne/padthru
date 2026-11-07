package io.github.labanmne.padthru;

/** Linux evdev calls (android/jni/padthru_evdev.c), for the background helper. */
final class Evdev {
    static final int EV_SYN = 0, EV_KEY = 1, EV_ABS = 3, EV_FF = 0x15;
    static final int SYN_REPORT = 0;
    static final int ABS_X = 0, ABS_Y = 1, ABS_Z = 2, ABS_RX = 3, ABS_RY = 4, ABS_RZ = 5;
    static final int ABS_HAT0X = 0x10, ABS_HAT0Y = 0x11;
    static final int ABS_MT_SLOT = 0x2f, ABS_MT_POSITION_X = 0x35, ABS_MT_POSITION_Y = 0x36, ABS_MT_TRACKING_ID = 0x39;
    static final int BTN_LEFT = 0x110, BTN_SOUTH = 0x130, BTN_EAST = 0x131, BTN_NORTH = 0x133, BTN_WEST = 0x134;
    static final int BTN_TL = 0x136, BTN_TR = 0x137, BTN_TL2 = 0x138, BTN_TR2 = 0x139;
    static final int BTN_SELECT = 0x13a, BTN_START = 0x13b, BTN_MODE = 0x13c, BTN_THUMBL = 0x13d, BTN_THUMBR = 0x13e;
    static final int BTN_DPAD_UP = 0x220, BTN_DPAD_DOWN = 0x221, BTN_DPAD_LEFT = 0x222, BTN_DPAD_RIGHT = 0x223;
    static final int FF_RUMBLE = 0x50;
    static final int INPUT_PROP_ACCELEROMETER = 6;
    static final int ENODEV = 19;

    static native int open(String path, boolean write);
    static native void close(int fd);
    static native int grab(int fd, boolean on);
    static native String name(int fd);
    static native String uniq(int fd);
    static native String phys(int fd);
    static native int[] ids(int fd);
    static native boolean hasCode(int fd, int type, int code);
    static native boolean hasProp(int fd, int prop);
    static native int[] absInfo(int fd, int code);
    static native int read(int fd, int[] out);
    static native int rumble(int fd, int id, int strong, int weak);
    static native void stopRumble(int fd, int id);

    private Evdev() {
    }
}
