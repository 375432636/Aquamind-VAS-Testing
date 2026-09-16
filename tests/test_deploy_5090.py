import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", ["", "build", "health"])
def test_deploy_only_records_healthy_version_and_restores_failed_update(
    tmp_path, failure
):
    root = tmp_path / "deploy"
    (root / "private").mkdir(parents=True)
    (root / "private/web.env").write_text("WEB_PASSWORD=test\n")
    (root / "releases").mkdir()
    old = "a" * 40
    new = "b" * 40
    previous = f"APP_IMAGE=aquamind-vas-testing:{old}\nAPP_REVISION={old}\n"
    (root / "current.env").write_text(previous)
    (root / "releases" / f"{old}.compose.yaml").write_text("services: {}\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in {
        "docker": """#!/bin/sh
printf '%s %s\\n' "$APP_REVISION" "$*" >> "$CALL_LOG"
if [ "$1" = build ] && [ "$FAILURE" = build ]; then exit 1; fi
if [ "$1" = compose ] && [ "$APP_REVISION" = "$NEW_REVISION" ] && [ "$FAILURE" = health ]; then exit 1; fi
""",
        "curl": "#!/bin/sh\nexit 0\n",
        "flock": "#!/bin/sh\nexit 0\n",
    }.items():
        path = fake / name
        path.write_text(body)
        path.chmod(0o755)
    log = tmp_path / "calls.log"
    env = dict(
        os.environ,
        DEPLOY_ROOT=str(root),
        APP_REVISION=new,
        NEW_REVISION=new,
        FAILURE=failure,
        CALL_LOG=str(log),
        PATH=f"{fake}:{os.environ['PATH']}",
        NO_PROXY="localhost,127.0.0.1,internal.example",
    )
    result = subprocess.run(
        ["bash", "scripts/deploy_5090.sh"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = log.read_text()
    assert "--build-arg HTTP_PROXY --build-arg HTTPS_PROXY" in calls
    assert (
        "--build-arg NO_PROXY=localhost,127.0.0.1,internal.example,deb.debian.org,pypi.org,files.pythonhosted.org"
        in calls
    )
    assert (
        "--build-arg no_proxy=localhost,127.0.0.1,internal.example,deb.debian.org,pypi.org,files.pythonhosted.org"
        in calls
    )
    if failure:
        assert result.returncode != 0
        assert (root / "current.env").read_text() == previous
        if failure == "health":
            assert f"{old} compose" in calls
        else:
            assert "compose" not in calls
    else:
        assert result.returncode == 0, result.stderr
        assert new in (root / "current.env").read_text()
        assert (root / "releases" / f"{new}.compose.yaml").exists()
