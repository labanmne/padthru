/*
 * Padthru for Android TV boxes: a small JNI layer over Linux evdev, used by the background
 * helper (Helper.java), which runs with the rights of `adb shell` (the "input" group).
 * It reads controllers' event nodes directly, grabs them so the box ignores them while the
 * TV shows the PC, and plays rumble through the kernel's force-feedback interface.
 */
#include <errno.h>
#include <fcntl.h>
#include <jni.h>
#include <linux/input.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define FN(name) Java_io_github_labanmne_padthru_Evdev_##name
#define LONG_BITS (8 * sizeof(unsigned long))
#define NLONGS(n) (((n) + LONG_BITS - 1) / LONG_BITS)

/* Read-only is enough for reading and grabbing; writing (rumble) is a separate open, because
 * some boxes' SELinux policy lets adb shell read input devices but not write to them. */
JNIEXPORT jint JNICALL FN(open)(JNIEnv *env, jclass cls, jstring path, jboolean write) {
    const char *p = (*env)->GetStringUTFChars(env, path, NULL);
    int fd = open(p, (write ? O_RDWR : O_RDONLY) | O_CLOEXEC);
    int err = errno;
    (*env)->ReleaseStringUTFChars(env, path, p);
    return fd < 0 ? -err : fd;
}

JNIEXPORT void JNICALL FN(close)(JNIEnv *env, jclass cls, jint fd) {
    close(fd);
}

JNIEXPORT jint JNICALL FN(grab)(JNIEnv *env, jclass cls, jint fd, jboolean on) {
    return ioctl(fd, EVIOCGRAB, on ? 1 : 0) < 0 ? -errno : 0;
}

static jstring string_ioctl(JNIEnv *env, int fd, unsigned long req) {
    char buf[256];
    memset(buf, 0, sizeof buf);
    if (ioctl(fd, req, buf) < 0) buf[0] = 0;
    buf[sizeof buf - 1] = 0;
    return (*env)->NewStringUTF(env, buf);
}

JNIEXPORT jstring JNICALL FN(name)(JNIEnv *env, jclass cls, jint fd) {
    return string_ioctl(env, fd, EVIOCGNAME(255));
}

JNIEXPORT jstring JNICALL FN(uniq)(JNIEnv *env, jclass cls, jint fd) {
    return string_ioctl(env, fd, EVIOCGUNIQ(255));
}

JNIEXPORT jstring JNICALL FN(phys)(JNIEnv *env, jclass cls, jint fd) {
    return string_ioctl(env, fd, EVIOCGPHYS(255));
}

/* bus, vendor, product, version */
JNIEXPORT jintArray JNICALL FN(ids)(JNIEnv *env, jclass cls, jint fd) {
    struct input_id id;
    memset(&id, 0, sizeof id);
    ioctl(fd, EVIOCGID, &id);
    jint v[4] = {id.bustype, id.vendor, id.product, id.version};
    jintArray out = (*env)->NewIntArray(env, 4);
    (*env)->SetIntArrayRegion(env, out, 0, 4, v);
    return out;
}

JNIEXPORT jboolean JNICALL FN(hasCode)(JNIEnv *env, jclass cls, jint fd, jint type, jint code) {
    unsigned long bits[NLONGS(KEY_MAX + 1)];
    memset(bits, 0, sizeof bits);
    if (ioctl(fd, EVIOCGBIT(type, sizeof bits), bits) < 0) return JNI_FALSE;
    return (bits[code / LONG_BITS] >> (code % LONG_BITS)) & 1 ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL FN(hasProp)(JNIEnv *env, jclass cls, jint fd, jint prop) {
    unsigned long bits[NLONGS(INPUT_PROP_MAX + 1)];
    memset(bits, 0, sizeof bits);
    if (ioctl(fd, EVIOCGPROP(sizeof bits), bits) < 0) return JNI_FALSE;
    return (bits[prop / LONG_BITS] >> (prop % LONG_BITS)) & 1 ? JNI_TRUE : JNI_FALSE;
}

/* value, minimum, maximum, fuzz, flat, resolution; null if the axis doesn't exist */
JNIEXPORT jintArray JNICALL FN(absInfo)(JNIEnv *env, jclass cls, jint fd, jint code) {
    struct input_absinfo a;
    if (ioctl(fd, EVIOCGABS(code), &a) < 0) return NULL;
    jint v[6] = {a.value, a.minimum, a.maximum, a.fuzz, a.flat, a.resolution};
    jintArray out = (*env)->NewIntArray(env, 6);
    (*env)->SetIntArrayRegion(env, out, 0, 6, v);
    return out;
}

/* Blocks until events arrive; fills out with (type, code, value) triples. Returns the
 * number of events, or -errno (-ENODEV when the controller went away). */
JNIEXPORT jint JNICALL FN(read)(JNIEnv *env, jclass cls, jint fd, jintArray out) {
    struct input_event ev[64];
    jint tmp[64 * 3];
    ssize_t n = read(fd, ev, sizeof ev);
    if (n < 0) return -errno;
    int count = (int) (n / sizeof(struct input_event));
    jsize cap = (*env)->GetArrayLength(env, out) / 3;
    if (count > cap) count = cap;
    for (int i = 0; i < count; i++) {
        tmp[i * 3] = ev[i].type;
        tmp[i * 3 + 1] = ev[i].code;
        tmp[i * 3 + 2] = ev[i].value;
    }
    (*env)->SetIntArrayRegion(env, out, 0, count * 3, tmp);
    return count;
}

/* Uploads (or updates, when id >= 0) a rumble effect that plays until stopped, and plays
 * it. Magnitudes 0-65535. Returns the effect id or -errno. */
JNIEXPORT jint JNICALL FN(rumble)(JNIEnv *env, jclass cls, jint fd, jint id, jint strong, jint weak) {
    struct ff_effect e;
    memset(&e, 0, sizeof e);
    e.type = FF_RUMBLE;
    e.id = (short) id;
    e.u.rumble.strong_magnitude = (unsigned short) strong;
    e.u.rumble.weak_magnitude = (unsigned short) weak;
    e.replay.length = 0; /* until stopped */
    if (ioctl(fd, EVIOCSFF, &e) < 0) return -errno;
    struct input_event play;
    memset(&play, 0, sizeof play);
    play.type = EV_FF;
    play.code = (unsigned short) e.id;
    play.value = 1;
    if (write(fd, &play, sizeof play) < 0) return -errno;
    return e.id;
}

JNIEXPORT void JNICALL FN(stopRumble)(JNIEnv *env, jclass cls, jint fd, jint id) {
    struct input_event play;
    memset(&play, 0, sizeof play);
    play.type = EV_FF;
    play.code = (unsigned short) id;
    play.value = 0;
    write(fd, &play, sizeof play);
}
