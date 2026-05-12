from __future__ import annotations

import importlib
import json
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from intake_skill.config import IntakeConfig


dashboard = importlib.import_module("intake_skill.dashboard")


def test_collect_status_reports_days_and_cron(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source"
    data = tmp_path / "data"
    repo = tmp_path / "repo"
    day_dir = data / "20260512"
    source.mkdir()
    day_dir.mkdir(parents=True)
    repo.mkdir()
    (day_dir / "20260512_0915_watch.m4a").write_bytes(b"audio")
    (day_dir / "transcript_20260512.csv").write_text("speaker,content\n,hello\n", encoding="utf-8")
    (day_dir / "daily_20260512.md").write_text("# Daily\n", encoding="utf-8")
    (day_dir / "meetings").mkdir()
    (day_dir / "meetings" / "no_meeting_20260512.md").write_text("# None\n", encoding="utf-8")
    (repo / "logs").mkdir()
    (repo / "logs" / "intake_cron.log").write_text("ran once\n", encoding="utf-8")

    config = IntakeConfig(source_dir=source, data_dir=data, repo_root=repo)
    monkeypatch.setattr(dashboard, "read_current_crontab", lambda: f"{dashboard.cron_line(repo)}\n")
    monkeypatch.setattr(dashboard, "_current_processes", lambda: [{"pid": "123", "command": "python -m intake_skill run-day"}])
    monkeypatch.setattr(dashboard, "build_sync_plan", lambda source_dir, data_dir, day=None: [])
    monkeypatch.setattr(dashboard, "_audio_duration_seconds", lambda path: 12.5)

    status = dashboard.collect_status(config, now=datetime(2026, 5, 12, 15, 0, 0))

    assert status["cron"]["active"] is True
    assert status["runtime"]["running"] is True
    assert status["days"][0]["day"] == "20260512"
    assert status["days"][0]["audio_count"] == 1
    assert status["days"][0]["audio_duration_seconds"] == 12.5
    assert status["days"][0]["transcript"]["non_empty_rows"] == 1
    assert status["days"][0]["transcript"]["content_chars"] == 5
    assert status["days"][0]["generated_text"]["total_chars"] > 0
    assert status["totals"]["audio_duration_seconds"] == 12.5
    assert status["totals"]["transcript_rows"] == 1
    assert status["days"][0]["daily_md_exists"] is True
    assert status["days"][0]["meeting_count"] == 1
    assert status["log"]["tail"] == ["ran once"]


def test_sync_preview_reports_permission_errors(monkeypatch, tmp_path) -> None:
    config = IntakeConfig(source_dir=tmp_path / "source", data_dir=tmp_path / "data", repo_root=tmp_path)

    def fail(*args, **kwargs):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(dashboard, "build_sync_plan", fail)
    preview = dashboard._sync_preview(config, "20260512")

    assert preview["ok"] is False
    assert "Operation not permitted" in preview["error"]


def test_log_summary_reports_running_and_errors() -> None:
    assert dashboard._log_summary([], running=False)["status"] == "not run yet"
    assert dashboard._log_summary(["all good"], running=True)["status"] == "running"
    summary = dashboard._log_summary(["=== manual run started ===", "ERROR: failed"], running=False)
    assert summary["status"] == "needs review"
    assert summary["last_error"] == "ERROR: failed"
    summary = dashboard._log_summary(["ERROR: old failure", "=== manual run started ===", "{\"status\": \"ok\"}"], running=False)
    assert summary["status"] == "last log ok"
    assert summary["last_error"] is None


def test_cleanup_tmp_removes_only_tmp_contents(tmp_path) -> None:
    config = IntakeConfig(source_dir=tmp_path / "source", data_dir=tmp_path / "data", repo_root=tmp_path)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "scratch.txt").write_text("temporary", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "keep.txt").write_text("keep", encoding="utf-8")

    summary = dashboard.cleanup_tmp(config)

    assert summary["ok"] is True
    assert summary["removed_entries"] == 1
    assert not (tmp_dir / "scratch.txt").exists()
    assert (data_dir / "keep.txt").exists()


def test_run_now_starts_today_pipeline(monkeypatch, tmp_path) -> None:
    config = IntakeConfig(source_dir=tmp_path / "source", data_dir=tmp_path / "data", repo_root=tmp_path)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    started = {}

    class DummyProcess:
        pid = 456

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["cwd"] = kwargs.get("cwd")
        return DummyProcess()

    monkeypatch.setattr(dashboard, "_current_processes", lambda: [])
    monkeypatch.setattr(dashboard.subprocess, "Popen", fake_popen)

    summary = dashboard.run_now(config)

    assert summary["ok"] is True
    assert "--date" not in started["command"]
    assert started["cwd"] == tmp_path


def test_post_requires_dashboard_token(monkeypatch, tmp_path) -> None:
    config = IntakeConfig(source_dir=tmp_path / "source", data_dir=tmp_path / "data", repo_root=tmp_path)
    token = "test-token"
    called = {"count": 0}

    class Handler(dashboard.DashboardHandler):
        def get_status(self):
            return {"ok": True}

    Handler.dashboard_config = config
    Handler.dashboard_token = token

    def fake_run_now(received_config):
        called["count"] += 1
        return {"ok": True}

    monkeypatch.setattr(dashboard, "run_now", fake_run_now)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/run-now"
    try:
        request = Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        try:
            urlopen(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("missing token should be forbidden")
        assert called["count"] == 0

        request = Request(
            url,
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Intake-Dashboard-Token": token,
                "Origin": f"http://127.0.0.1:{server.server_address[1]}",
            },
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert called["count"] == 1
    finally:
        server.shutdown()
        server.server_close()
