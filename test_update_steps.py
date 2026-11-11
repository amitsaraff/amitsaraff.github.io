import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import update_steps as module

START = date(2026, 9, 1)
END = date(2026, 9, 26)


def test_exact_counts_zero_and_missing_days():
    rows = [
        {"calendarDate": "2026-09-13", "totalSteps": 9001, "stepGoal": 10980},
        {"calendarDate": "2026-09-14", "totalSteps": 0},
        {"calendarDate": "2026-09-15", "totalSteps": None},
        {"calendarDate": "2026-08-31", "totalSteps": 500},
    ]
    assert module.parse_daily_steps(rows, START, END) == {
        "2026-09-13": (9001, 10980), "2026-09-14": (0, None),
    }


@pytest.mark.parametrize("steps", [-1, 1.5, "100", True])
def test_invalid_totals_rejected(steps):
    with pytest.raises(ValueError):
        module.parse_daily_steps([{"calendarDate": "2026-09-13", "totalSteps": steps}], START, END)


@pytest.fixture
def site(tmp_path, monkeypatch):
    csv = tmp_path / "steps.csv"
    data_script = tmp_path / "10000" / "steps.js"
    data_script.parent.mkdir()
    csv.write_text(',Actual,Goal\n09/12/2026,100,9000\n09/13/2026,9004,10980\n')
    # The dashboard deliberately differs: CSV is the source of truth.
    data_script.write_text('before\nconst CSV = `date,steps\n2026-09-13,9004\n`;\nafter\n')
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "STEPS_CSV", csv)
    monkeypatch.setattr(module, "DATA_FILE", data_script)
    return csv, data_script


def test_merge_counts_preserve_missing_goals_and_update_goal_only(site):
    csv, data_script = site
    data = {"2026-09-13": (9001, None), "2026-09-12": (100, 9500), "2026-09-14": (0, 10000)}
    module.update_files(data, dry_run=False)
    assert csv.read_text() == ',Actual,Goal\n09/12/2026,100,9500\n09/13/2026,9001,10980\n09/14/2026,0,10000\n'
    assert data_script.read_text() == 'before\nconst CSV = `date,steps\n2026-09-12,100\n2026-09-13,9001\n2026-09-14,0\n`;\nafter\n'
    before = (csv.stat().st_mtime_ns, data_script.stat().st_mtime_ns)
    module.update_files(data, dry_run=False)
    assert before == (csv.stat().st_mtime_ns, data_script.stat().st_mtime_ns)


def test_dry_run_leaves_files_untouched(site):
    before = [path.read_bytes() for path in site]
    module.update_files({"2026-09-13": (9001, 11000)}, dry_run=True)
    assert before == [path.read_bytes() for path in site]


def test_preserves_dashboard_only_iphone_history(site):
    csv, data_script = site
    data_script.write_text('const CSV = `date,steps\n2026-01-01,1244\n2026-02-01,0\n2026-04-24,10631\n2026-09-13,9004\n`;\n')
    module.update_files({"2026-09-13": (9001, 10980)}, dry_run=False)
    rows = module.read_steps_csv()
    assert rows["2026-01-01"] == (1244, 0)
    assert rows["2026-02-01"] == (0, 0)
    assert rows["2026-04-24"] == (10631, 0)
    assert rows["2026-09-13"] == (9001, 10980)
    for day, steps in [("2026-01-01", 1244), ("2026-02-01", 0), ("2026-04-24", 10631)]:
        assert f"{day},{steps}\n" in data_script.read_text()
    before = [path.read_bytes() for path in site]
    module.update_files({}, dry_run=False)
    assert before == [path.read_bytes() for path in site]


def test_invalid_data_script_does_not_write_csv(site):
    csv, data_script = site
    data_script.write_text("no embedded data")
    before = csv.read_bytes()
    with pytest.raises(ValueError):
        module.update_files({"2026-09-13": (9001, 11000)}, dry_run=False)
    assert csv.read_bytes() == before


@pytest.fixture
def fake_garmin(monkeypatch):
    class AuthenticationError(Exception):
        pass

    factory = Mock()
    monkeypatch.setitem(sys.modules, "garminconnect", SimpleNamespace(
        Garmin=factory, GarminConnectAuthenticationError=AuthenticationError,
    ))
    return factory, AuthenticationError


def test_saved_tokens_skip_credential_prompts(fake_garmin, monkeypatch, tmp_path):
    factory, _ = fake_garmin
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("unexpected prompt")))
    monkeypatch.setattr(module, "getpass", Mock(side_effect=AssertionError("unexpected prompt")))
    assert module.login(tmp_path) is factory.return_value
    factory.return_value.login.assert_called_once_with(str(tmp_path))


def test_fresh_login_prompts_and_caches_tokens(fake_garmin, monkeypatch, tmp_path):
    factory, auth_error = fake_garmin
    cached, fresh = Mock(), Mock()
    cached.login.side_effect = auth_error()
    factory.side_effect = [cached, fresh]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "email@example.com" if "email" in prompt else "123456")
    monkeypatch.setattr(module, "getpass", lambda prompt: "test-password")
    assert module.login(tmp_path) is fresh
    kwargs = factory.call_args.kwargs
    assert kwargs["email"] == "email@example.com"
    assert kwargs["password"] == "test-password"
    assert kwargs["prompt_mfa"]() == "123456"
    fresh.login.assert_called_once_with(str(tmp_path))


def test_connection_failure_does_not_prompt(fake_garmin, monkeypatch, tmp_path):
    factory, _ = fake_garmin
    factory.return_value.login.side_effect = OSError("offline")
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("unexpected prompt")))
    with pytest.raises(OSError):
        module.login(tmp_path)


def test_missing_tokens_in_noninteractive_run(fake_garmin, monkeypatch, tmp_path):
    factory, auth_error = fake_garmin
    factory.return_value.login.side_effect = auth_error()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="terminal"):
        module.login(tmp_path)


def test_main_requests_default_range_and_passes_dry_run(monkeypatch):
    client = Mock()
    client.get_daily_steps.return_value = [{"calendarDate": "2026-09-25", "totalSteps": 10001, "stepGoal": 10000}]
    monkeypatch.setattr(module, "login", lambda token_dir: client)
    update = Mock()
    monkeypatch.setattr(module, "update_files", update)
    monkeypatch.setattr(sys, "argv", ["update_steps.py", "--end", "2026-09-26", "--dry-run"])
    module.main()
    client.get_daily_steps.assert_called_once_with("2026-08-30", "2026-09-26")
    update.assert_called_once_with({"2026-09-25": (10001, 10000)}, True)


def test_empty_response_never_updates_site(monkeypatch):
    client = Mock()
    client.get_daily_steps.return_value = []
    monkeypatch.setattr(module, "login", lambda token_dir: client)
    update = Mock()
    monkeypatch.setattr(module, "update_files", update)
    monkeypatch.setattr(sys, "argv", ["update_steps.py"])
    with pytest.raises(SystemExit, match="No step totals"):
        module.main()
    update.assert_not_called()


def test_update_changes_data_without_touching_page(site):
    csv, data_script = site
    page = data_script.with_name("index.html")
    page.write_text('<script src="steps.js" defer></script>')
    before = page.read_bytes()
    module.update_files({"2026-09-14": (12345, 10000)}, dry_run=False)
    assert "2026-09-14,12345\n" in data_script.read_text()
    assert page.read_bytes() == before
