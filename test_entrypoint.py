"""Regression tests for the container browser display bootstrap."""

import os
from pathlib import Path
import subprocess
import socket
import tempfile
import unittest


class EntrypointTests(unittest.TestCase):
    def test_stale_xvfb_socket_is_replaced_before_app_starts(self):
        display_number = str(200 + os.getpid() % 1000)
        display = ":" + display_number
        socket_path = Path("/tmp/.X11-unix") / ("X" + display_number)
        lock = Path("/tmp/.X" + display_number + "-lock")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            state = root / "state"
            state.mkdir()

            (fake_bin / "Xvfb").write_text(
                "#!/bin/sh\n"
                "display_number=${1#:}\n"
                "sleep 0.4\n"
                "touch \"/tmp/.X11-unix/X${display_number}\"\n"
                "printf '%s\\n' \"$$\" > \"/tmp/.X${display_number}-lock\"\n"
                "touch \"$FAKE_XVFB_READY\"\n"
                "trap 'rm -f \"/tmp/.X11-unix/X${display_number}\" \"/tmp/.X${display_number}-lock\" \"$FAKE_XVFB_READY\"' EXIT\n"
                "while :; do sleep 1; done\n"
            )
            (fake_bin / "xdpyinfo").write_text(
                "#!/bin/sh\n"
                "test -f \"$FAKE_XVFB_READY\"\n"
            )
            app = fake_bin / "app"
            app.write_text(
                "#!/bin/sh\n"
                "test -f \"$FAKE_XVFB_READY\" || exit 42\n"
                "printf 'app-ran\\n'\n"
            )
            for path in fake_bin.iterdir():
                path.chmod(0o755)

            socket_path.parent.mkdir(parents=True, exist_ok=True)
            stale_socket = socket.socket(socket.AF_UNIX)
            stale_socket.bind(str(socket_path))
            stale_socket.close()
            lock.write_text("stale\n")
            env = os.environ.copy()
            env.update({
                "DISPLAY": display,
                "FAKE_XVFB_READY": str(state / "ready"),
                "PATH": str(fake_bin) + os.pathsep + env["PATH"],
            })
            try:
                result = subprocess.run(
                    [str(Path(__file__).parent / "entrypoint.sh"), str(app)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            finally:
                socket_path.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "app-ran\n")


if __name__ == "__main__":
    unittest.main()
