from __future__ import annotations

import importlib
import json


cli = importlib.import_module("intake_skill.cli")


def test_parser_accepts_required_commands() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["doctor"]).command == "doctor"
    sync_args = parser.parse_args(["sync", "--dry-run", "--date", "20260512"])
    assert sync_args.dry_run is True
    assert sync_args.date == "20260512"
    asr_args = parser.parse_args(["asr", "--engine", "mock", "--mock-text", "installer text"])
    assert asr_args.engine == "mock"
    assert asr_args.mock_text == "installer text"
    assert parser.parse_args(["asr"]).engine == "mlx"
    assert parser.parse_args(["postprocess", "--engine", "codex"]).engine == "codex"
    assert parser.parse_args(["postprocess"]).engine == "codex"
    assert parser.parse_args(["run-day", "--asr-engine", "mock", "--postprocess-engine", "mock"]).command == "run-day"
    run_day_defaults = parser.parse_args(["run-day"])
    assert run_day_defaults.asr_engine == "mlx"
    assert run_day_defaults.postprocess_engine == "codex"
    assert parser.parse_args(["install-cron", "--dry-run"]).dry_run is True
    dashboard_args = parser.parse_args(["dashboard", "--port", "8766"])
    assert dashboard_args.command == "dashboard"
    assert dashboard_args.port == 8766
    assert parser.parse_args(["make-sample-audio", "--seconds", "1"]).seconds == 1


def test_doctor_cli_outputs_json(tmp_path, capsys) -> None:
    code = cli.main(["doctor", "--source", str(tmp_path), "--data-dir", str(tmp_path / "data"), "--repo-root", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "doctor"
    assert payload["checks"]["source_exists"] is True
    assert payload["data_dir"] == str(tmp_path / "data")
