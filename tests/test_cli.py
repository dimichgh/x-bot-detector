import json

from xbotdetect.cli import main


def test_report_command(tmp_path, farm_world, capsys):
    ds_path = farm_world.save(tmp_path / "dataset.json.gz")
    out = tmp_path / "out"
    assert main(["report", str(ds_path), "--out", str(out), "--format", "html,json", "-q"]) == 0
    text = capsys.readouterr().out
    assert "@TargetAccount" in text and "FOLLOW CLUSTERS" in text
    assert (out / "report.html").exists()
    assert json.loads((out / "report.json").read_text())["seeds"][0]["level"] in ("high", "very high")


def test_analyze_offline_source(tmp_path, farm_world, capsys):
    ds_path = farm_world.save(tmp_path / "dataset.json")
    rc = main(
        [
            "analyze",
            "@TargetAccount",
            "--source",
            "offline",
            "--dataset",
            str(ds_path),
            "--no-cache",
            "--followers",
            "400",
            "--following",
            "200",
            "--timeline",
            "60",
            "--expand",
            "20",
            "--no-files",
            "--json",
            "-q",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    seed = data["seeds"][0]
    assert seed["handle"] == "TargetAccount"
    assert any(s["key"] == "follower_map_bands" for s in seed["signals"])
    assert data["collection"]["options"]["expand"] == 20


def test_unknown_handle_fails_cleanly(tmp_path, farm_world, capsys):
    ds_path = farm_world.save(tmp_path / "dataset.json")
    rc = main(
        [
            "profile",
            "nobody_here",
            "--source",
            "offline",
            "--dataset",
            str(ds_path),
            "--no-cache",
            "--no-files",
            "-q",
        ]
    )
    assert rc == 2
    assert "none of the handles could be resolved" in capsys.readouterr().err
