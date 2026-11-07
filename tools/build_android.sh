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

python3 - "$WORK/base.apk" "$WORK/classes.dex" <<'EOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "a", zipfile.ZIP_DEFLATED) as z:
    z.write(sys.argv[2], "classes.dex")
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
