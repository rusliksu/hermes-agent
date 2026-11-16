"""Real native Docker generation must produce a profile-bound host artifact."""
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.agent.test_gurra_memory_snapshot import homes
from tests.tools.test_gurra_docker_boundary import _BINARY, _DESCRIPTOR, pytestmark

_FIXTURES = (homes,)  # imported for pytest fixture discovery, not called here


_ACTOR = r'''
import json,sys
from pathlib import Path
from gateway.access_registry import DeliveryTarget,ResolvedAccessContext
from gateway.run import _profile_runtime_scope
from gateway.access_context import bind_resolved_access_context
from tools.terminal_tool import terminal_tool
from tools.artifact_delivery_tool import _deliver_artifact,validate_bound_artifact_output
root,profile,foreign=sys.argv[1:]
room=profile=="beta"
ctx=ResolvedAccessContext(principal_id="fixture-"+profile,role_id="shared_room" if room else "family",
 profile_id=profile,conversation_scope="room" if room else "dm-alpha",
 capabilities=frozenset({"documents","docker_terminal"}),
 delivery_target=DeliveryTarget("telegram","docker-fixture-bot","group" if room else "dm","-1007" if room else "1007","31" if room else None))
with _profile_runtime_scope(Path(root)/"profiles"/profile),bind_resolved_access_context(ctx):
 result=json.loads(terminal_tool("printf '%s\\n' 'Saved: /workspace/report.csv' | tee /workspace/report.csv",task_id="artifact-fixture",timeout=20,force=True))
 publication=json.loads(_deliver_artifact({"path":foreign or "/workspace/report.csv"},task_id="artifact-fixture"))
 output_guard=validate_bound_artifact_output("/workspace/new.csv","artifact-fixture")
print(json.dumps({"terminal":result,"publication":publication,"new_output_guard":output_guard}))
'''


def test_real_docker_generated_document_translates_without_foreign_profile_fallback(homes, monkeypatch):
    descriptor = json.loads(_DESCRIPTOR.read_text())
    monkeypatch.setenv("HERMES_TEST_DOCKER_IMAGE", descriptor["image"])
    monkeypatch.setenv("HERMES_DOCKER_BINARY", descriptor["binary"])
    monkeypatch.setenv("DOCKER_HOST", "unix://" + descriptor["socket"])
    monkeypatch.setenv("DOCKER_CONFIG", descriptor["client_config"])
    config = {"terminal": {"backend": "docker", "cwd": "/workspace", "docker_image": descriptor["image"],
        "container_cpu": 0.5, "container_memory": 256, "container_disk": 0, "container_persistent": True,
        "docker_persist_across_processes": True, "docker_network": False, "docker_shm_size": "64m",
        "docker_orphan_reaper": False, "docker_forward_env": [], "docker_env": {}, "docker_volumes": [],
        "docker_mount_cwd_to_workspace": False}, "security": {"allow_lazy_installs": False}}
    for home in homes.values():
        (home / "config.yaml").write_text(json.dumps(config))
    root = homes["alpha"].parent.parent
    keys = ("PATH", "HOME", "LANG", "TZ", "TMPDIR", "DOCKER_HOST", "DOCKER_CONFIG",
            "HERMES_DOCKER_BINARY", "HERMES_TEST_DOCKER_IMAGE", "PYTHONDONTWRITEBYTECODE")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env["HERMES_HOME"] = str(root)
    def actor(profile, foreign=""):
        result = subprocess.run([sys.executable, "-c", _ACTOR, str(root), profile, foreign],
                                env=env, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stderr[-1500:]
        return json.loads(result.stdout)
    own = actor("alpha")
    assert own["terminal"]["exit_code"] == 0
    assert own["publication"]["success"] is True, own
    assert own["publication"]["status"] == "ready_for_delivery"
    assert own["new_output_guard"] is None
    path = Path(own["publication"]["media_tag"].removeprefix("MEDIA:"))
    assert path.is_relative_to(homes["alpha"])
    assert path.read_text() == "Saved: /workspace/report.csv\n"
    foreign = actor("beta", str(path))
    assert foreign["terminal"]["exit_code"] == 0
    assert foreign["publication"]["success"] is False
    assert "media_tag" not in foreign["publication"]
