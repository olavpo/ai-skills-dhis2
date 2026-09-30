#!/usr/bin/env bash
# Prepares the agent sandbox (Ubuntu 24.04) to build apps on the DHIS2 Android SDK; on aarch64 it
# also sets up aapt2 under qemu.
# Needs passwordless sudo and the Maven/Google/Gradle hosts on the egress allowlist.
# Idempotent: safe to run again, including after a failed run. Everything lands in the container
# and ~/, not in the project.
set -euo pipefail

SDK="${ANDROID_HOME:-$HOME/android-sdk}"
BUILD_TOOLS="36.0.0"
PLATFORM="android-36"
ARCH="$(uname -m)"

sudo -n true || { echo "needs passwordless sudo (ask the user to grant it)"; exit 1; }
# Every host the build needs; any HTTP answer (even 404) means reachable, a timeout means blocked.
for u in https://repo1.maven.org/maven2/ \
         https://dl.google.com/android/repository/repository2-1.xml \
         https://maven.google.com/web/index.html \
         https://services.gradle.org/distributions/ \
         https://plugins.gradle.org/m2/; do
  curl -s -o /dev/null -m 10 "$u" || { echo "blocked: $u (allowlist it in init-firewall.sh)"; exit 1; }
done

pkgs="openjdk-17-jdk-headless unzip"
[ "$ARCH" = "aarch64" ] && pkgs="$pkgs qemu-user-static"
echo "== apt: $pkgs"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
# shellcheck disable=SC2086
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $pkgs

# Guard on the installed library, not the foreign arch: a run that added the arch and then failed
# must redo this block.
if [ "$ARCH" = "aarch64" ] && ! dpkg -s libc6:amd64 >/dev/null 2>&1; then
  echo "== amd64 multiarch for aapt2"
  grep -q '^Architectures: arm64' /etc/apt/sources.list.d/ubuntu.sources ||
    sudo sed -i 's|^URIs: http://ports.ubuntu.com/ubuntu-ports/$|URIs: http://ports.ubuntu.com/ubuntu-ports/\nArchitectures: arm64|' /etc/apt/sources.list.d/ubuntu.sources
  sudo tee /etc/apt/sources.list.d/amd64.sources >/dev/null <<'EOF'
Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-security
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
  sudo dpkg --add-architecture amd64
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libc6:amd64 libgcc-s1:amd64
fi

if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  echo "== Android cmdline-tools"
  mkdir -p "$SDK/cmdline-tools"
  zip=$(curl -fsSL https://dl.google.com/android/repository/repository2-1.xml |
        grep -o 'commandlinetools-linux-[0-9]*_latest.zip' | sort -uV | tail -1)
  [ -n "$zip" ] || { echo "could not find cmdline-tools in repository2-1.xml"; exit 1; }
  tmpzip=$(mktemp --suffix=.zip)
  curl -fsSL -o "$tmpzip" "https://dl.google.com/android/repository/$zip"
  (cd "$SDK/cmdline-tools" && rm -rf cmdline-tools latest && unzip -q -o "$tmpzip" && mv cmdline-tools latest)
  rm -f "$tmpzip"
fi
yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null || true
# Progress bars only are dropped; errors still reach the terminal.
"$SDK/cmdline-tools/latest/bin/sdkmanager" "platforms;$PLATFORM" "build-tools;$BUILD_TOOLS" | grep -v '^\[' || true
[ -d "$SDK/build-tools/$BUILD_TOOLS" ] || { echo "sdkmanager did not install build-tools $BUILD_TOOLS"; exit 1; }

if [ "$ARCH" = "aarch64" ]; then
  echo "== aapt2 under qemu"
  mkdir -p "$HOME/bin" "$HOME/.gradle"
  cat > "$HOME/bin/aapt2" <<EOF
#!/bin/sh
exec /usr/bin/qemu-x86_64-static -L / $SDK/build-tools/$BUILD_TOOLS/aapt2 "\$@"
EOF
  chmod +x "$HOME/bin/aapt2"
  "$HOME/bin/aapt2" version
  props="$HOME/.gradle/gradle.properties"
  touch "$props"
  sed -i '/^android\.aapt2FromMavenOverride=/d' "$props"   # replace a stale value
  echo "android.aapt2FromMavenOverride=$HOME/bin/aapt2" >> "$props"
fi

M2="$HOME/.m2/repository/com/github/dhis2/sms-compression/0.2.0"
if ! curl -s -o /dev/null -m 10 https://jitpack.io/ && [ ! -f "$M2/sms-compression-0.2.0.jar" ]; then
  echo "== sms-compression 0.2.0 from source (JitPack unreachable)"
  tmp=$(mktemp -d)
  git clone -q --depth 1 --branch 0.2.0 https://github.com/dhis2/sms-compression.git "$tmp/src"
  mkdir -p "$tmp/lib" "$tmp/classes" "$M2"
  MC=https://repo1.maven.org/maven2
  curl -fsSL -o "$tmp/lib/gson.jar"  "$MC/com/google/code/gson/gson/2.8.2/gson-2.8.2.jar"
  curl -fsSL -o "$tmp/lib/lang3.jar" "$MC/org/apache/commons/commons-lang3/3.6/commons-lang3-3.6.jar"
  curl -fsSL -o "$tmp/lib/io.jar"    "$MC/commons-io/commons-io/2.6/commons-io-2.6.jar"
  find "$tmp/src/src/main/java" -name '*.java' > "$tmp/sources.txt"
  javac -nowarn --release 8 -d "$tmp/classes" -cp "$tmp/lib/*" @"$tmp/sources.txt"
  (cd "$tmp/classes" && jar cf "$M2/sms-compression-0.2.0.jar" .)
  cat > "$M2/sms-compression-0.2.0.pom" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.github.dhis2</groupId>
  <artifactId>sms-compression</artifactId>
  <version>0.2.0</version>
  <dependencies>
    <dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId><version>2.8.2</version></dependency>
    <dependency><groupId>org.apache.commons</groupId><artifactId>commons-lang3</artifactId><version>3.6</version></dependency>
    <dependency><groupId>commons-io</groupId><artifactId>commons-io</artifactId><version>2.6</version></dependency>
  </dependencies>
</project>
EOF
  rm -rf "$tmp"
fi

echo "== done. In the project: local.properties → sdk.dir=$SDK ; settings.gradle.kts → mavenLocal() (group com.github.dhis2) before jitpack"
