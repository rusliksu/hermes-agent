"""Disposable mutation check for restart timestamp preservation coverage."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


repo = Path.cwd()
evidence = repo / ".spec-kitty/evidence/hermes-tg-polling-obs-gy5/mutation_drop_platforms.xml"

with TemporaryDirectory(prefix="hermes-status-mutation-") as td:
    tmp = Path(td)
    (tmp / "gateway").mkdir(parents=True)
    (tmp / "tests/gateway").mkdir(parents=True)
    for src, dst in [
        ("gateway/status.py", "gateway/status.py"),
        ("gateway/__init__.py", "gateway/__init__.py"),
        ("hermes_constants.py", "hermes_constants.py"),
        ("utils.py", "utils.py"),
        ("tests/gateway/test_status.py", "tests/gateway/test_status.py"),
    ]:
        shutil.copy2(repo / src, tmp / dst)

    status_path = tmp / "gateway/status.py"
    text = status_path.read_text(encoding="utf-8")
    text = text.replace(
        'payload.setdefault("platforms", {})',
        'payload["platforms"] = {}',
    )
    status_path.write_text(text, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp)
    result = subprocess.run(
        [
            str(repo / ".test-venv/bin/python"),
            "-m",
            "pytest",
            str(
                tmp
                / "tests/gateway/test_status.py::TestGatewayRuntimeStatus::test_write_runtime_status_preserves_poll_timestamp_on_restart"
            ),
            f"--junitxml={evidence}",
        ],
        cwd=tmp,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout)
    sys.exit(result.returncode)
