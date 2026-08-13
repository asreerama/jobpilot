"""Single-instance locks so launchd jobs never overlap themselves."""
import fcntl
import os
import sys


def acquire(name: str, root: str):
    path = os.path.join(root, "logs", f"{name}.lock")
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"{name}: another instance is running, exiting")
        sys.exit(0)
    fh.write(str(os.getpid()))
    fh.flush()
    return fh  # keep a reference or the lock dies with the fd
