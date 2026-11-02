"""Restart standard Deluge entry points after normal interpreter cleanup."""

import atexit
import os
import sys
from pathlib import Path


def command(role, profile=None):
    expected = {"core": "deluged", "web": "deluge-web"}[role]
    argv = list(sys.argv)
    if sys.platform != "linux" or not argv or Path(argv[0]).name != expected:
        raise ValueError("restart_unsupported")
    script = Path(argv[0]).resolve(strict=True)
    executable = Path(sys.executable).absolute()
    if not script.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("restart_unsupported")
    # Keep the service/container's PID even if the original invocation forked.
    if "-d" not in argv and "--do-not-daemonize" not in argv:
        argv.append("--do-not-daemonize")
    if profile is not None:
        # Deluge may have changed cwd while daemonizing. The actual config path
        # wins over an originally relative -c option on the repeated invocation.
        argv.extend(["--config", str(Path(profile).resolve(strict=True))])
    return [str(executable), str(script), *argv[1:]]


def schedule(reactor, role, argv=None):
    argv = command(role) if argv is None else argv

    def restart():
        # atexit runs after Deluge's finally blocks and reactor shutdown triggers.
        # exec preserves PID, UID, environment and stdout/stderr of the service.
        atexit.register(os.execv, argv[0], argv)
        reactor.stop()

    return reactor.callLater(1, restart)
