# Environment setup

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

Written for the agent sandbox (d2-broker instances, the host's emulator over adb, no sudo). Outside
it, the same steps apply to any DHIS2 instance you can reset and restore, and a local emulator or
device: skip the port forward to `host.docker.internal:5037`, and replace broker calls with your own
instance management.

This skill owns the Maestro, `screenrecord` and emulator-preparation recipes for recorded flows;
`dhis2-android-testing` covers basic adb driving (install, screenshot, tap, logcat).

### Tools that are not in the image, and how to get them without sudo

| Need | Solution |
|---|---|
| ffmpeg | `pip install imageio-ffmpeg` in a venv; the static binary is in the package (`python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'` prints its path; the bundled scripts find it themselves, or set `FFMPEG`). It has **no `drawtext`** filter but has **libass** (`subtitles`/`ass`), which is better anyway. |
| Java (for Maestro) | `pip install jdk4py` gives a JRE; set `JAVA_HOME` to its `java-runtime` directory. |
| Maestro | `curl -L https://github.com/mobile-dev-inc/maestro/releases/latest/download/maestro.zip` (GitHub is allowlisted). Set `MAESTRO_CLI_NO_ANALYTICS=1`. |
| Capture APKs | `gh release download <tag> -R dhis2/dhis2-android-capture-app -p 'dhis2-v<tag>.apk'` (the plain APK, not `-training`). Get the current release and one about a year older for smoke tests. |
| Postgres access | `pg8000` or the `psql` in the image; broker DBs are on dev-net as `dhis2-<name>-db`. |

Tools in `~/tools` are lost when the sandbox is rebuilt: record where they are (memory file).

### Two port forwards make everything work

```bash
# Maestro talks to adb at localhost:5037 (dadb); the host's adb server is elsewhere
socat TCP-LISTEN:5037,bind=127.0.0.1,fork,reuseaddr TCP:host.docker.internal:5037

# Web Capture refuses to load on plain http unless the origin is localhost (secure context)
socat TCP-LISTEN:8089,bind=127.0.0.1,fork,reuseaddr TCP:dhis2-<name>:8080
```

Run each with the shell's background mode (not `&`, `nohup` or `setsid`). Without the second one, Capture shows "The application
could not be loaded … privacy mode"; the Chromium flag `--unsafely-treat-insecure-origin-as-secure`
did not help in headless mode. The same limit applies to real deployments: Capture needs https.

### Emulator preparation (do this after every `pm clear`)

```bash
for p in POST_NOTIFICATIONS ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION; do
  adb shell pm grant com.dhis2 android.permission.$p; done
# fake GPS so "my location" works (adb emu does not reach the host's emulator console)
adb shell appops set com.android.shell android:mock_location allow
for p in gps network fused; do
  adb shell cmd location providers add-test-provider $p
  adb shell cmd location providers set-test-provider-enabled $p true
  adb shell cmd location providers set-test-provider-location $p --location 8.4840,-13.2299 --accuracy 5
done
```

The first `monkey -p com.dhis2 1` right after `pm clear` often does nothing: relaunch until the
login screen appears (`scripts/android/login.py` loops).

### Server-side Android settings (before the first login)

```bash
POST /api/dataStore/ANDROID_SETTING_APP/general_settings   {"allowScreenCapture": true, "encryptDB": false, "reservedValues": 50}
POST /api/dataStore/ANDROID_SETTING_APP/program_settings   {"globalSettings": {"settingDownload":"GLOBAL","teiDownload":20, ...}}
```

Screen capture must be allowed or every screenshot/recording is empty. A low download limit keeps
logins to about a minute even with thousands of synthetic tracked entities. The app reads settings
and metadata **only at login**: after any metadata change, clear the app and log in again, or the
flows test old metadata (seen with a changed stage setting and with reserved form numbers
generated under an old text pattern).

### Instances

- One instance for everything, all options side by side with code prefixes, worked well and is what
  the user wanted to share. Keep it; label it in the broker.
- Use a **second, temporary instance** for anything that would distort the shared one (the 20k scale
  test). Importing the full metadata package into an empty instance also proves the package.
- Assign `local_admin`/`admin` to the root org unit (capture, view, search), or they see no data.
- **Analytics tables do not survive a database restart** (they are UNLOGGED): after an instance
  restart, run analytics again before verification or dashboard screenshots.
- Python helpers need `pip install requests pillow imageio-ffmpeg` (in a venv); `requests` is not in
  the sandbox image.
