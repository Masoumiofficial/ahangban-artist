from __future__ import annotations

import json
from pathlib import Path

from ahangban_artists.cli import main


def test_cli_three_input_intake_finalize_and_json_report(
    system_root: Path, image_factory, capsys
) -> None:
    reference = image_factory(system_root / "reference.png")
    master = image_factory(system_root / "external-master.png")

    result = main(
        [
            "--root",
            str(system_root),
            "intake",
            "--reference",
            str(reference),
            "--name-fa",
            "آزمون رابط",
            "--name-en",
            "CLI Fixture",
        ]
    )
    assert result == 0
    assert "پرتره تولید نشد" in capsys.readouterr().out

    result = main(
        [
            "--root",
            str(system_root),
            "finalize",
            "--artist",
            "cli-fixture",
            "--master",
            str(master),
            "--generator",
            "synthetic-cli-fixture",
        ]
    )
    assert result == 0
    assert "pending-visual-review" in capsys.readouterr().out

    result = main(["--root", str(system_root), "report", "--artist", "cli-fixture", "--json"])
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["name_fa"] == "آزمون رابط"
    assert report["verification"]["status"] == "unverified"
    assert report["technical"]["width"] == report["technical"]["height"] == 1080
    assert report["quality"]["technical_status"] == "pass"
    assert report["quality"]["acceptance_status"] == "pending-visual-review"
    assert report["github_operations"].startswith("not-attempted-by-cli")
