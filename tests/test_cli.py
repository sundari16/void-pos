from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from void_pos.cli import main
from void_pos.models import Base


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def _run(db_url: str, *args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(["--database-url", db_url, *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_set_up_a_venue_from_the_command_line(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(
        db_url, "create-location", "--org", "Madras Curry House", "--name", "Brisbane CBD",
        capsys=capsys,
    )
    assert code == 0
    location_id = out.strip().rsplit(" ", 1)[-1]

    code, out, _ = _run(
        db_url, "add-device", "--location", location_id, "--name", "Till 1", "--range", "1000-1999",
        capsys=capsys,
    )
    assert code == 0
    assert re.search(r"Pairing code: [A-Z2-9]{4}-[A-Z2-9]{4} \(expires \d\d:\d\d AEST\)", out)
    device_id = out.splitlines()[0].rsplit(" ", 1)[-1]

    code, out, _ = _run(db_url, "list-devices", "--location", location_id, capsys=capsys)
    assert "Till 1" in out and "not paired" in out

    code, out, _ = _run(db_url, "pairing-code", "--device", device_id, capsys=capsys)
    assert code == 0 and "Pairing code for Till 1" in out

    code, out, _ = _run(db_url, "revoke-device", "--device", device_id, capsys=capsys)
    assert code == 0 and out.startswith("Revoked Till 1")

    code, out, _ = _run(db_url, "list-locations", capsys=capsys)
    assert location_id in out


def test_errors_exit_non_zero_with_a_message(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(db_url, "pairing-code", "--device", "00000000000000000000000000", capsys=capsys)
    assert code == 1
    assert err.strip() == "error: device not found"


def test_a_bad_range_is_a_usage_error(db_url: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--database-url", db_url, "add-device", "--location", "x", "--name", "T", "--range", "1000"])
    assert exc.value.code == 2
