# Build environment

## Anywhere

- JDK 17, Android SDK platform 36 + build-tools, Gradle wrapper 9.3.1 (copy Capture's wrapper).
- Repositories: `google()`, `mavenCentral()`, and JitPack **restricted to `com.github.dhis2`** for
  `sms-compression`. Put `mavenLocal()` (same group filter) **before** JitPack if JitPack may be
  unreachable: Gradle treats a connection failure as fatal instead of trying the next repository.
- Pure-JVM modules use `org.jetbrains.kotlin.jvm` with `jvmToolchain(17)`; Android modules use
  AGP 9's built-in Kotlin (no `kotlin-android` plugin), plus `org.jetbrains.kotlin.plugin.compose`
  and `com.google.devtools.ksp` where needed.
- In a Gradle Kotlin script inside `tasks.register<Exec>`, `java.io.…` resolves against the `java`
  extension: import `java.io.ByteArrayOutputStream` at the top instead.
- A version-catalog alias `x-root` next to `x` turns `libs.x` into an accessor group and breaks
  `libs.x.get()`; name it `xRoot`.

## In the agent sandbox (Linux aarch64)

Needs sudo for the apt steps (ask the user to grant it temporarily) and these hosts on the egress
allowlist:
`repo1.maven.org`, `dl.google.com`, `maven.google.com`, `services.gradle.org`,
`plugins.gradle.org` (and ideally `jitpack.io`). `scripts/setup-android-build.sh` in this skill
does the steps below; run it once per container.

1. `apt install openjdk-17-jdk-headless unzip`; Android cmdline-tools into `~/android-sdk`,
   `sdkmanager "platforms;android-36" "build-tools;36.0.0"`; `local.properties` →
   `sdk.dir=$HOME/android-sdk` expanded (not committed).
2. **aapt2 is x86_64 only** and `binfmt_misc` isn't mounted, so run it under qemu:
   `apt install qemu-user-static`; `dpkg --add-architecture amd64` with an
   `archive.ubuntu.com` source limited to `Architectures: amd64` (and `Architectures: arm64` on the
   ports source); `apt install libc6:amd64 libgcc-s1:amd64`; a wrapper
   `exec qemu-x86_64-static -L / ~/android-sdk/build-tools/36.0.0/aapt2 "$@"`; and in
   `~/.gradle/gradle.properties` (not the repo):
   `android.aapt2FromMavenOverride=<$HOME>/bin/aapt2` (absolute path).
3. **JitPack blocked**: build `sms-compression` 0.2.0 from its GitHub tag with
   `javac --release 8` against gson 2.8.2, commons-lang3 3.6, commons-io 2.6 into
   `~/.m2/repository/com/github/dhis2/sms-compression/0.2.0/` with a matching POM.
4. **Connected tests**: don't use `connectedAndroidTest` (AGP downloads an x86_64 `adb` into
   `platform-tools` and hangs); install APKs with the system `adb` and run `am instrument`
   (see `testing.md`). Never `adb kill-server`.

Without sudo, only the JDK can be worked around: unpack a Temurin 17 tarball from
`github.com/adoptium/temurin17-binaries` releases into `~/` and set `JAVA_HOME`. qemu and the
amd64 libraries (step 2) still need sudo, so on aarch64 a no-sudo sandbox cannot run aapt2.

All of this lives in the container and `~/`; a recreated sandbox needs it again.

The emulator reaches the host at `10.0.2.2`; a broker instance is `http://10.0.2.2:<http_port>`
from the app, and `http://dhis2-<name>:8080` from the sandbox.
