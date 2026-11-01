#!/bin/bash
# Build the GitHub Updater APK from source.
# Toolchain: tools/sdk (build-tools 35.0.0, platform android-35) + Temurin JDK 21.
set -euo pipefail
cd "$(dirname "$0")"

export JAVA_HOME="$HOME/workspace/custom-skills/local-rea/tools/jdk"
export PATH="$JAVA_HOME/bin:$PATH"
BT="${ANDROID_SDK_ROOT:-tools/sdk}/build-tools/35.0.0"
ANDROID_JAR="${ANDROID_SDK_ROOT:-tools/sdk}/platforms/android-35/android.jar"
KS="keystore/metamuse.keystore"
KS_PASS_FILE="keystore/keystore-password.txt"

mkdir -p build/gen build/classes build/dex keystore

if [ ! -f "$KS" ]; then
  PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
  echo "$PASS" > "$KS_PASS_FILE"
  chmod 600 "$KS_PASS_FILE"
  keytool -genkeypair -keystore "$KS" -alias onemuse -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass "$PASS" -keypass "$PASS" \
    -dname "CN=GitHub Updater, O=JonathanCruzate, C=US" 2>/dev/null
  echo "keystore created"
fi
PASS="$(cat "$KS_PASS_FILE")"

echo "== aapt2 compile =="
find res -type f | sort > build/res-files.txt
"$BT/aapt2" compile --dir res -o build/res.zip

echo "== aapt2 link =="
"$BT/aapt2" link -o build/unsigned.apk \
  -I "$ANDROID_JAR" \
  --manifest AndroidManifest.xml \
  --java build/gen \
  --min-sdk-version 24 --target-sdk-version 35 \
  --version-code 2 --version-name "1.1.0" \
  build/res.zip

echo "== javac =="
find src build/gen -name "*.java" > build/sources.txt
javac -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -d build/classes @build/sources.txt

echo "== d8 =="
"$BT/d8" --release --min-api 24 --lib "$ANDROID_JAR" --output build/dex $(find build/classes -name "*.class")

echo "== package dex =="
# classes.dex must sit at the APK root, not under dex/
(cd build/dex && zip -q ../unsigned.apk classes.dex)

echo "== zipalign + sign =="
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
"$BT/apksigner" sign --ks "$KS" --ks-pass "pass:$PASS" --key-pass "pass:$PASS" \
  --out GitHub-Updater-1.1.0.apk build/aligned.apk

echo "== verify =="
"$BT/apksigner" verify --print-certs GitHub-Updater-1.1.0.apk
ls -lh GitHub-Updater-1.1.0.apk
