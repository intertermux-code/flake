"""flake tests. Run: python -m pytest tests/ -x -q"""
import os
import subprocess
import sys
import time

import pytest

BIN = [sys.executable, "-m", "flake"]


def run(*args, **kwargs):
    return subprocess.run(BIN + list(args), capture_output=True, text=True,
                          timeout=kwargs.pop("timeout", 60), **kwargs)


def counter_script(path, fail_times, code=1):
    """Python one-liner: appends a tick per run, exits `code` until `fail_times` ticks seen."""
    return [sys.executable, "-c",
            f"import os,time; p={str(path)!r}; "
            f"n=len(open(p).read().split()) if os.path.exists(p) else 0; "
            f"open(p,'a').write('x\\n'); "
            f"raise SystemExit({code} if n < {fail_times} else 0)"]


def test_success_first_try(tmp_path):
    marker = tmp_path / "ticks"
    r = run("--", *counter_script(marker, 0), timeout=30)
    assert r.returncode == 0, r.stderr
    assert len(marker.read_text().split()) == 1


def test_eventual_success(tmp_path):
    marker = tmp_path / "ticks"
    r = run("--retries", "3", "--delay", "0.1", "--", *counter_script(marker, 2), timeout=30)
    assert r.returncode == 0, r.stderr
    assert len(marker.read_text().split()) == 3
    assert "succeeded on attempt 3" in r.stderr


def test_all_fail_reports_last_code(tmp_path):
    marker = tmp_path / "ticks"
    r = run("--retries", "2", "--delay", "0.1", "--", *counter_script(marker, 99, code=3), timeout=30)
    assert r.returncode == 3
    assert len(marker.read_text().split()) == 3  # 1 + 2 retries
    assert "no retries left" in r.stderr


def test_retry_on_match_retries(tmp_path):
    marker = tmp_path / "ticks"
    script = [sys.executable, "-c",
              f"p={str(marker)!r}; import os; n=len(open(p).read().split()) if os.path.exists(p) else 0; "
              f"open(p,'a').write('x\\n'); print('rate limit hit' if n < 1 else 'fine'); "
              f"raise SystemExit(1 if n < 1 else 0)"]
    r = run("--retries", "2", "--delay", "0.1", "--retry-on", "rate limit", "--", *script, timeout=30)
    assert r.returncode == 0, r.stderr
    assert len(marker.read_text().split()) == 2


def test_retry_on_no_match_no_retry(tmp_path):
    marker = tmp_path / "ticks"
    script = [sys.executable, "-c",
              f"p={str(marker)!r}; open(p,'a').write('x\\n'); print('permanent failure'); raise SystemExit(1)"]
    r = run("--retries", "3", "--delay", "0.1", "--retry-on", "rate limit", "--", *script, timeout=30)
    assert r.returncode == 1
    assert len(marker.read_text().split()) == 1
    assert "did not match --retry-on" in r.stderr


def test_timeout_kills_and_retries(tmp_path):
    marker = tmp_path / "ticks"
    script = [sys.executable, "-c",
              f"p={str(marker)!r}; import os,time; n=len(open(p).read().split()) if os.path.exists(p) else 0; "
              f"open(p,'a').write('x\\n'); time.sleep(30 if n < 1 else 0)"]
    t0 = time.monotonic()
    r = run("--retries", "1", "--delay", "0.1", "--timeout", "1", "--", *script, timeout=30)
    dt = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    assert dt < 15
    assert "killed after" in r.stderr


def test_missing_binary():
    r = run("--", "definitely-not-a-real-binary-xyz", timeout=30)
    assert r.returncode == 127
    assert "command not found" in r.stderr


def test_backoff_timing(tmp_path):
    marker = tmp_path / "ticks"
    t0 = time.monotonic()
    r = run("--retries", "2", "--delay", "0.5", "--no-jitter", "--", *counter_script(marker, 99), timeout=30)
    dt = time.monotonic() - t0
    assert r.returncode == 1
    # waits: 0.5 + 1.0 = 1.5s minimum
    assert dt >= 1.4, dt


def test_output_streams_live():
    r = run("--", sys.executable, "-c", "print('hello-out')", timeout=30)
    assert r.returncode == 0
    assert "hello-out" in r.stdout


def test_quiet(tmp_path):
    marker = tmp_path / "ticks"
    r = run("--quiet", "--retries", "1", "--delay", "0.1", "--", *counter_script(marker, 99), timeout=30)
    assert r.returncode == 1
    assert "flake:" not in r.stderr
