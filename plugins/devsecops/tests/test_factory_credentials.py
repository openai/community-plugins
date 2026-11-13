from pathlib import Path
import json
import os
import stat

import pytest

from devsecops.factory.cli import main
from devsecops.factory.credentials import initialize


def test_create_is_private_and_returns_no_secret_material(tmp_path):
    directory = tmp_path.resolve() / "operator" / "private"
    result = initialize(directory, environment_id="owned-test-lab")
    stored = json.loads(Path(result["credential_file"]).read_text())
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "credentials.json").stat().st_mode) == 0o600
    assert stored["environment_id"] == "owned-test-lab"
    assert stored["account_status"] == result["account_status"] == "not_provisioned"
    passwords = [entry["password"] for entry in stored["credentials"].values()]
    assert len(set(passwords)) == 3
    assert all(len(password) >= 64 and password not in json.dumps(result) for password in passwords)


def test_never_overwrite_existing_credentials(tmp_path):
    directory = tmp_path.resolve() / "private"
    initialize(directory, environment_id="owned-test-lab")
    before = (directory / "credentials.json").read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        initialize(directory, environment_id="other-test-lab")
    assert (directory / "credentials.json").read_bytes() == before


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o777])
def test_reject_nonprivate_directory(tmp_path, mode):
    directory = tmp_path.resolve() / "not-private"
    directory.mkdir()
    directory.chmod(mode)
    with pytest.raises(ValueError, match="0700"):
        initialize(directory, environment_id="owned-test-lab")
    assert not (directory / "credentials.json").exists()


def test_reject_symlink_ancestor(tmp_path):
    actual = tmp_path.resolve() / "actual"
    actual.mkdir(mode=0o700)
    link = tmp_path.resolve() / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        initialize(link / "secret", environment_id="owned-test-lab")
    assert list(actual.iterdir()) == []


def test_reject_symlink_file(tmp_path):
    directory = tmp_path.resolve() / "private"
    directory.mkdir(mode=0o700)
    outside = tmp_path.resolve() / "outside"
    outside.write_text("untouched")
    (directory / "credentials.json").symlink_to(outside)
    with pytest.raises(ValueError):
        initialize(directory, environment_id="owned-test-lab")
    assert outside.read_text() == "untouched"


@pytest.mark.parametrize("value", ["../lab", "demo\n", "D3MO", "a" * 64, "a", ""])
def test_reject_invalid_identity_before_writing(tmp_path, value):
    directory = tmp_path.resolve() / "private"
    with pytest.raises(ValueError):
        initialize(directory, environment_id=value)
    assert not directory.exists()


def test_cli_prints_only_retrieval_metadata(tmp_path, capsys):
    directory = tmp_path.resolve() / "private"
    assert main(["init-credentials", "--directory", str(directory), "--environment-id", "owned-test-lab"]) == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["credential_file"] == str(directory / "credentials.json")
    stored = json.loads((directory / "credentials.json").read_text())
    for entry in stored["credentials"].values():
        assert entry["password"] not in out


def test_directory_is_not_silently_chmodded(tmp_path):
    directory = tmp_path.resolve() / "shared"
    directory.mkdir(mode=0o750)
    # mkdir's mode is filtered by the caller's umask. Establish an actually
    # shared directory even when the runner correctly starts with umask 077.
    directory.chmod(0o750)
    with pytest.raises(ValueError):
        initialize(directory, environment_id="owned-test-lab")
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o750
