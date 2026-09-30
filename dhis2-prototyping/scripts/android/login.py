#!/usr/bin/env python3
"""Get Capture Android to a clean, logged-in state for a recording pass.

    login.py --flow flows/login_3.4.2.yaml --server http://10.0.2.2:8080 \
             --user demo_enumerator [--password …] [--clear] [--ready "REGEX"]

Steps:
  1. --clear: `pm clear` the app and re-grant its runtime permissions (both undo each
     other, so always together), then re-apply mock GPS if you use it (setup.md).
  2. Relaunch the app until the login screen is really there. The first
     `monkey -p <app> 1` right after `pm clear` often does nothing, so this loops:
     launch, wait for --login-screen, force-stop, launch again.
  3. Run your Maestro login flow with SERVER, USER and PASSWORD passed as `-e` values
     (refer to them as ${SERVER}, ${USER}, ${PASSWORD} in the flow).
  4. Wait for --ready (a text on the first screen after login and metadata download;
     with a small tracked-entity download limit that takes about a minute).

The login flow is not bundled because the login screen changes between Capture
releases: build it once per app version from `ui.py` output (keep one flow file per
version you test). The app reads server settings and metadata only at login, so after
any metadata change run this again with --clear.

Password: --password, else $D2_PASSWORD. Device: --serial or $ANDROID_SERIAL.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PERMISSIONS = ("POST_NOTIFICATIONS", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flow", required=True, help="Maestro login flow using ${SERVER} ${USER} ${PASSWORD}")
    ap.add_argument("--server", required=True, help="as the device sees it, e.g. http://10.0.2.2:<port>")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", default=os.environ.get("D2_PASSWORD"))
    ap.add_argument("--app-id", default="com.dhis2")
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"))
    ap.add_argument("--clear", action="store_true", help="pm clear first (fresh settings and metadata)")
    ap.add_argument("--login-screen", default=r"(?i).*(server url|log ?in|username).*",
                    help="regex that proves the login screen is showing")
    ap.add_argument("--ready", default=r"(?i).*(home|programs?).*",
                    help="regex that proves login and metadata download finished")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to wait for --ready")
    a = ap.parse_args()
    if not a.password:
        ap.error("no password: pass --password or set D2_PASSWORD")

    from ui import adb, wait_for

    if a.clear:
        adb(a.serial, "shell", "pm", "clear", a.app_id)
        for p in PERMISSIONS:
            adb(a.serial, "shell", "pm", "grant", a.app_id, f"android.permission.{p}")

    for attempt in range(8):
        adb(a.serial, "shell", "monkey", "-p", a.app_id, "1")
        if wait_for(a.login_screen, a.serial, timeout=15):
            break
        if wait_for(a.ready, a.serial, timeout=1):
            print("already logged in")
            return
        adb(a.serial, "shell", "am", "force-stop", a.app_id)
    else:
        sys.exit("login screen never appeared (check --login-screen against `ui.py` output)")

    maestro = os.environ.get("MAESTRO", "maestro")
    env = dict(os.environ, MAESTRO_CLI_NO_ANALYTICS="1")
    cmd = [maestro] + (["--device", a.serial] if a.serial else []) + [
        "test", "-e", f"SERVER={a.server}", "-e", f"USER={a.user}", "-e", f"PASSWORD={a.password}", a.flow]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode:
        sys.exit("login flow failed:\n" + (r.stdout + r.stderr)[-2000:])
    if not wait_for(a.ready, a.serial, timeout=a.timeout):
        sys.exit(f"logged in, but {a.ready!r} did not appear within {a.timeout:.0f} s")
    print("logged in as", a.user)


if __name__ == "__main__":
    main()
