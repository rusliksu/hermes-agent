"""Actual rootless Docker must retain one profile's workspace and isolate another."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

import pytest

from tests.agent.test_gurra_memory_snapshot import homes

_FIXTURES = (homes,)  # imported for pytest fixture discovery, not called here

_BINARY = shutil.which("docker") or ""
_DESCRIPTOR = Path(_BINARY).parent.parent / "descriptor.json"
pytestmark = pytest.mark.skipif(
    not (_BINARY.startswith("/home/openclaw/staging/gurra-hermes-0215-preparation-20261005/rootless-engine-accept-")
         and _DESCRIPTOR.is_file()), reason="requires the task-owned rootless Docker harness")

_ACTOR = r'''
import json,sys
from pathlib import Path
from gateway.access_registry import DeliveryTarget, ResolvedAccessContext
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context
from tools import terminal_tool as terminal
root,profile,role,command,workdir=sys.argv[1:]
room=role=="shared_room"
context=ResolvedAccessContext(principal_id="fixture-"+profile,role_id=role,profile_id=profile,
 conversation_scope="room" if room else "dm-"+profile,capabilities=frozenset({"docker_terminal"}),
 delivery_target=DeliveryTarget("telegram","docker-fixture-bot","group" if room else "dm",
                               "-1007" if room else "1007","31" if room else None))
with _profile_runtime_scope(Path(root)/"profiles"/profile),bind_resolved_access_context(context):
 result=json.loads(terminal.terminal_tool(command,workdir=workdir or None,
                                        task_id="docker-fixture",timeout=20,force=True))
 environments=list(terminal._active_environments.values())
 payload={"result":result,"container_ids":[getattr(env,"_container_id",None) for env in environments],
          "backends":[type(env).__name__ for env in environments]}
print(json.dumps(payload))
'''


def test_real_profile_containers_persist_across_processes_and_deny_escape(homes, monkeypatch):
    descriptor = json.loads(_DESCRIPTOR.read_text())
    image, binary = descriptor["image"], descriptor["binary"]
    assert binary == _BINARY and descriptor["rootless"] is True
    monkeypatch.setenv("HERMES_TEST_DOCKER_IMAGE", image)
    monkeypatch.setenv("HERMES_DOCKER_BINARY", binary)
    monkeypatch.setenv("DOCKER_HOST", "unix://" + descriptor["socket"])
    monkeypatch.setenv("DOCKER_CONFIG", descriptor["client_config"])
    assert "@sha256:" in image
    assert binary.startswith("/home/openclaw/staging/gurra-hermes-0215-preparation-20261005/")
    assert os.environ["DOCKER_HOST"].startswith("unix:///run/user/1000/gurra-0215-")
    root = homes["alpha"].parent.parent
    config = {"terminal": {"backend": "docker", "cwd": "/workspace", "docker_image": image,
                           "container_cpu": 0.5, "container_memory": 256, "container_disk": 0,
                           "container_persistent": True, "docker_persist_across_processes": True,
                           "docker_network": False, "docker_shm_size": "64m", "docker_orphan_reaper": False,
                           "docker_forward_env": [], "docker_env": {}, "docker_volumes": [],
                           "docker_mount_cwd_to_workspace": False},
              "security": {"allow_lazy_installs": False}}
    for home in homes.values():
        (home / "config.yaml").write_text(json.dumps(config))

    def run(profile, role, command, workdir=""):
        keys = ("PATH", "HOME", "LANG", "TZ", "TMPDIR", "DOCKER_HOST", "DOCKER_CONFIG",
                "HERMES_DOCKER_BINARY", "HERMES_TEST_DOCKER_IMAGE", "PYTHONDONTWRITEBYTECODE")
        env = {key: os.environ[key] for key in keys if key in os.environ}
        env["HERMES_HOME"] = str(root)
        child = subprocess.run([sys.executable, "-c", _ACTOR, str(root), profile, role, command, workdir],
                               env=env, capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == 0, child.stderr[-2000:]
        data = json.loads(child.stdout)
        if data["result"]["exit_code"] == 0:
            assert data["backends"] == ["DockerEnvironment"]
            assert len(data["container_ids"]) == 1 and data["container_ids"][0]
            # Docker run returns 64 hex digits; native reuse may return a short ID.
            canonical = subprocess.run([binary, "inspect", "--format", "{{.Id}}", data["container_ids"][0]],
                                       capture_output=True, text=True, timeout=15, check=False)
            assert canonical.returncode == 0, canonical.stderr
            data["container_ids"] = [canonical.stdout.strip()]
            assert len(data["container_ids"][0]) == 64
        return data

    alpha = run("alpha", "family", "printf ALPHA_CONTAINER_FIXTURE > marker; cat marker")
    assert alpha["result"]["exit_code"] == 0, alpha
    assert "ALPHA_CONTAINER_FIXTURE" in alpha["result"]["output"]
    beta = run("beta", "shared_room",
               "if [ -e marker ]; then cat marker; exit 9; fi; printf BETA_CONTAINER_FIXTURE > marker; cat marker")
    assert beta["result"]["exit_code"] == 0, beta
    assert "BETA_CONTAINER_FIXTURE" in beta["result"]["output"]
    assert "ALPHA_CONTAINER_FIXTURE" not in beta["result"]["output"]
    reloaded = run("alpha", "family", "cat marker")
    assert reloaded["result"]["exit_code"] == 0, reloaded
    assert "ALPHA_CONTAINER_FIXTURE" in reloaded["result"]["output"]
    assert alpha["container_ids"] == reloaded["container_ids"]
    assert alpha["container_ids"] != beta["container_ids"]
    escaped = run("alpha", "family", "printf SHOULD_NOT_EXECUTE", "/etc")
    assert escaped["result"]["exit_code"] != 0
    assert "cwd outside profile workspace" in str(escaped["result"])
    assert "SHOULD_NOT_EXECUTE" not in escaped["result"].get("output", "")

    for container in alpha["container_ids"] + beta["container_ids"]:
        inspected = subprocess.run([binary, "inspect", container], capture_output=True, text=True, timeout=15, check=False)
        assert inspected.returncode == 0, inspected.stderr
        actual = json.loads(inspected.stdout)[0]
        assert actual["HostConfig"]["NetworkMode"] == "none"
        assert actual["HostConfig"]["Memory"] == 256 * 1024 * 1024
        assert actual["HostConfig"]["NanoCpus"] == 500_000_000
        assert actual["HostConfig"]["PidsLimit"] > 0
        assert "ALL" in actual["HostConfig"]["CapDrop"]
        assert "no-new-privileges" in actual["HostConfig"]["SecurityOpt"]
