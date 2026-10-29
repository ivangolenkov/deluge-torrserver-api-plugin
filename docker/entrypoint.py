"""Initialize only fresh profiles; never rewrite policy or recursively chown payload."""

import os
import shutil
import sys
from pathlib import Path


def identity(value):
    number = int(value)
    if not 1 <= number <= 2**31 - 1:
        raise ValueError("PUID/PGID must be positive non-root numeric IDs")
    return number


def prepare(role):
    from deluge.config import Config

    profile = Path("/config")
    (egg,) = Path("/opt/plugin").glob("*.egg")
    plugins = profile / "plugins"
    plugins.mkdir(exist_ok=True)
    installed = list(plugins.glob("Stream-*.egg"))
    if any(path.name != egg.name for path in installed):
        raise ValueError(
            "old Stream egg present: back up profile, remove old egg explicitly"
        )
    destination = plugins / egg.name
    if not destination.exists() or destination.read_bytes() != egg.read_bytes():
        shutil.copyfile(egg, destination)
    password = Path(os.environ["STREAM_RPC_PASSWORD_FILE"]).read_text().strip()
    if len(password) < 24 or any(char.isspace() or char == ":" for char in password):
        raise ValueError(
            "RPC secret needs at least 24 non-whitespace characters without colon"
        )

    def initial(name, defaults, file_version=1):
        if not (profile / name).exists():
            config = Config(
                name,
                defaults=defaults,
                config_dir=str(profile),
                file_version=file_version,
            )
            if config.save() is False:
                raise OSError(f"cannot initialize {name}")

    if role == "core":
        auth = profile / "auth"
        if not auth.exists():
            auth.write_text(f"stream:{password}:10\n")
            auth.chmod(0o600)
        elif f"stream:{password}:10" not in auth.read_text().splitlines():
            raise ValueError(
                "RPC secret differs from persisted auth; rotate credentials explicitly"
            )
        initial(
            "core.conf",
            {
                "enabled_plugins": ["Stream"],
                "allow_remote": True,
                "daemon_port": 58846,
                "download_location": "/downloads",
                "listen_ports": [6881, 6881],
                "random_port": False,
            },
        )
        return ["deluged", "-d", "-c", str(profile), "-i", "0.0.0.0", "-u", "0.0.0.0"]
    initial(
        "hostlist.conf",
        {"hosts": [["stream-core", "core", 58846, "stream", password]]},
        file_version=3,
    )
    return ["deluge-web", "-d", "-c", str(profile), "-i", "0.0.0.0", "--no-ssl"]


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"core", "web"}:
        raise SystemExit("expected core or web")
    role = sys.argv[1]
    uid = identity(os.environ.get("PUID", "1000"))
    gid = identity(os.environ.get("PGID", "1000"))
    if os.geteuid() == 0:
        for name in ["/config", "/downloads"] if role == "core" else ["/config"]:
            path = Path(name)
            path.mkdir(exist_ok=True)
            # Only an empty volume is initialized. Existing ownership is never changed.
            if not any(path.iterdir()):
                os.chown(path, uid, gid)
        os.execvp("gosu", ["gosu", f"{uid}:{gid}", sys.executable, __file__, role])
    os.umask(0o077)
    os.environ["PYTHON_EGG_CACHE"] = "/config/.python-eggs"
    command = prepare(role)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
