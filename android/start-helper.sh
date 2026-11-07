#!/system/bin/sh
# Starts Padthru's background helper on an Android TV box, with the rights of `adb shell`.
# Run it through adb from a computer:
#
#   adb shell sh /data/local/tmp/padthru/start-helper.sh     (after pushing this file), or
#   adb shell "$(cat android/start-helper.sh)"
#
# The Padthru box app runs the same steps itself after a reboot (see docs/android.md).
pkg=io.github.labanmne.padthru
apk=$(pm path $pkg | sed -n 's/^package://p' | head -n 1)
[ -n "$apk" ] || { echo "Padthru isn't installed on this box"; exit 1; }
mkdir -p /data/local/tmp/padthru
CLASSPATH="$apk" setsid nohup app_process /system/bin $pkg.Helper \
  >> /data/local/tmp/padthru/helper.log 2>&1 < /dev/null &
sleep 2
tail -n 8 /data/local/tmp/padthru/helper.log
