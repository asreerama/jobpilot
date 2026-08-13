"""Phone notifications for pipeline runs.

Best-effort by design: every failure here is swallowed, so a notification
outage can never take down an apply run.

Config is a shell-sourceable env file at ~/.config/jobpilot/notify.env. It
lives in $HOME rather than pipeline/secrets/ because bin/apply.sh has to read
it even when the repo's disk is unreadable, which is the launchd TCC failure
we most need to be told about.

CLI, used by the shell hooks:
    python -m jobpilot.notify "title" "body" [priority] [tags]
"""
import json
import os
import sys
import urllib.request

CONF = os.path.expanduser("~/.config/jobpilot/notify.env")
TIMEOUT_S = 5

# ntfy priority: 1 min, 3 default, 4 high, 5 max (time-sensitive on iOS).
P_LOW, P_DEFAULT, P_HIGH, P_URGENT = 2, 3, 4, 5


def _conf():
    """Parse the env file, letting real environment variables win."""
    c = {}
    try:
        with open(CONF) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                c[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in ("NOTIFY_BACKEND", "NTFY_SERVER", "NTFY_TOPIC", "PUSHCUT_KEY",
              "PUSHCUT_NOTIFICATION"):
        if os.environ.get(k):
            c[k] = os.environ[k]
    return c


def _post(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.status


def push(title, body, priority=P_DEFAULT, tags=None, click=None):
    """Send one notification. Returns True on success, False on any problem.

    Publishes via ntfy's JSON format rather than X-Title headers: HTTP headers
    are ASCII-only and the titles here carry emoji.
    """
    try:
        c = _conf()
        backend = c.get("NOTIFY_BACKEND", "ntfy")

        if backend == "ntfy":
            topic = c.get("NTFY_TOPIC")
            if not topic:
                return False
            payload = {
                "topic": topic,
                "title": title,
                "message": body,
                "priority": int(priority),
            }
            if tags:
                payload["tags"] = tags
            if click:
                payload["click"] = click
            server = c.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
            return _post(server, payload, {}) < 300

        if backend == "pushcut":
            key = c.get("PUSHCUT_KEY")
            name = c.get("PUSHCUT_NOTIFICATION", "JobPilot")
            if not key:
                return False
            payload = {"title": title, "text": body,
                       "isTimeSensitive": int(priority) >= P_HIGH}
            url = f"https://api.pushcut.io/v1/notifications/{name}"
            return _post(url, payload, {"API-Key": key}) < 300

        return False
    except Exception:  # noqa: BLE001 - notifications never break a run
        return False


def main():
    a = sys.argv[1:]
    if not a:
        print("usage: python -m jobpilot.notify TITLE BODY [PRIORITY] [TAGS]")
        return 2
    title = a[0]
    body = a[1] if len(a) > 1 else ""
    prio = int(a[2]) if len(a) > 2 and a[2].isdigit() else P_DEFAULT
    tags = a[3].split(",") if len(a) > 3 and a[3] else None
    ok = push(title, body, prio, tags)
    print("sent" if ok else "FAILED (check ~/.config/jobpilot/notify.env)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
