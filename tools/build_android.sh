#!/usr/bin/env bash
# Builds dist/Padthru-android.apk (the Android TV box app) with the Android SDK's own
# tools: aapt2, javac, d8, zipalign, apksigner. No Gradle, no dependencies.
#
#   ANDROID_HOME=~/Android/Sdk bash tools/build_android.sh
#
# Needs a JDK (17 works) and an SDK with a platform and build-tools installed. The APK is
# signed with $PADTHRU_KEYSTORE (default ~/.config/padthru/android.jks, created on the
# first build). Keep that file: Android only installs updates signed with the same key.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}
BT=$(ls -d "$SDK"/build-tools/*/ | sort -V | tail -1)
JAR=$(ls -d "$SDK"/platforms/android-*/ | sort -V | tail -1)android.jar
SRC="$ROOT/android"
VERSION=$(sed -n 's/.*String VERSION = "\([0-9.]*\)".*/\1/p' "$SRC/src/io/github/labanmne/padthru/MainActivity.java")
IFS=. read -r MA MI PA <<<"$VERSION"
CODE=$((MA * 10000 + MI * 100 + PA))
KS=${PADTHRU_KEYSTORE:-$HOME/.config/padthru/android.jks}
OUT="$ROOT/dist/Padthru-android.apk"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "==> Padthru for Android $VERSION (code $CODE), build-tools $(basename "$BT"), $(basename "$(dirname "$JAR")")"
"$BT/aapt2" compile --dir "$SRC/res" -o "$WORK/res.zip"
"$BT/aapt2" link -o "$WORK/base.apk" -I "$JAR" --manifest "$SRC/AndroidManifest.xml" \
  --min-sdk-version 31 --target-sdk-version 31 --version-code "$CODE" --version-name "$VERSION" "$WORK/res.zip"

mkdir -p "$WORK/classes"
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 -bootclasspath "$JAR:$BT/core-lambda-stubs.jar" \
  -d "$WORK/classes" $(find "$SRC/src" -name '*.java')
"$BT/d8" --release --min-api 31 --lib "$JAR" --output "$WORK" $(find "$WORK/classes" -name '*.class')

# The background helper's evdev layer (android/jni), for 32- and 64-bit ARM boxes
NDK=$(ls -d "$SDK"/ndk/*/ 2>/dev/null | sort -V | tail -1)
CLANG_DIR="${NDK}toolchains/llvm/prebuilt/linux-x86_64/bin"
for target in "armeabi-v7a armv7a-linux-androideabi31" "arm64-v8a aarch64-linux-android31"; do
  set -- $target
  mkdir -p "$WORK/lib/$1"
  "$CLANG_DIR/$2-clang" -shared -fPIC -O2 -Wall -Wl,-z,max-page-size=16384 \
    -o "$WORK/lib/$1/libpadthru.so" "$SRC/jni/padthru_evdev.c"
done

python3 - "$WORK" <<'EOF'
import os, sys, zipfile
work = sys.argv[1]
with zipfile.ZipFile(os.path.join(work, "base.apk"), "a") as z:
    z.write(os.path.join(work, "classes.dex"), "classes.dex", zipfile.ZIP_DEFLATED)
    for abi in sorted(os.listdir(os.path.join(work, "lib"))):
        z.write(os.path.join(work, "lib", abi, "libpadthru.so"), f"lib/{abi}/libpadthru.so", zipfile.ZIP_STORED)
EOF
"$BT/zipalign" -f -p 4 "$WORK/base.apk" "$WORK/aligned.apk"

if [ ! -f "$KS" ]; then
  echo "==> creating signing key $KS"
  mkdir -p "$(dirname "$KS")"
  head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$KS.pass"
  chmod 600 "$KS.pass"
  keytool -genkeypair -keystore "$KS" -storepass:file "$KS.pass" -keypass:file "$KS.pass" \
    -alias padthru -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=Padthru" >/dev/null 2>&1
fi
mkdir -p "$ROOT/dist"
"$BT/apksigner" sign --ks "$KS" --ks-pass "file:$KS.pass" --out "$OUT" "$WORK/aligned.apk"
rm -f "$OUT.idsig"
echo "built $OUT ($(stat -c %s "$OUT") bytes)"
