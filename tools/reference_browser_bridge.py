"""Loopback-only browser access to an explicitly named offline research container.

docker exec transports individual HTTP requests; no Docker network or externally
reachable TorrServer port is needed. Stop with Ctrl-C after capture.
"""

import argparse
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TUNNEL = """
import socket,sys
with socket.create_connection(('127.0.0.1',18091), timeout=40) as s:
    s.sendall(sys.stdin.buffer.read())
    s.shutdown(socket.SHUT_WR)
    while True:
        data=s.recv(65536)
        if not data:
            break
        sys.stdout.buffer.write(data)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    if args.container.startswith("-"):
        parser.error("container must not be a Docker option")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def relay(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2 << 20 or self.headers.get("Transfer-Encoding"):
                self.send_error(413)
                return
            request = self.requestline + "\r\n"
            request += "".join(
                f"{k}: {v}\r\n"
                for k, v in self.headers.items()
                if k.lower() != "connection"
            )
            request += "Connection: close\r\n\r\n"
            try:
                result = subprocess.run(
                    ["docker", "exec", "-i", args.container, "python", "-c", TUNNEL],
                    input=request.encode("latin-1") + self.rfile.read(length),
                    capture_output=True,
                    timeout=45,
                    check=True,
                )
                self.wfile.write(result.stdout)
                if self.path == "/__finish":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            except (subprocess.SubprocessError, OSError):
                self.send_error(502, "offline research container unavailable")

        do_GET = relay
        do_POST = relay
        do_HEAD = relay
        do_OPTIONS = relay

    with ThreadingHTTPServer(("127.0.0.1", 18091), Handler) as server:
        print("Offline reference browser bridge: http://127.0.0.1:18091", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
