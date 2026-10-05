#!/usr/bin/env python3
"""
flake — run flaky commands with retries, exponential backoff, and jitter.

    flake --retries 5 --timeout 60 -- make test
    flake --retry-on "rate limit" --retry-on "429" -- deploy.sh
    flake --retries 0 -- ./must-pass-once.sh

Output streams live to your terminal while a bounded tail is kept for
--retry-on matching. Standard library only.
"""

import argparse
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

VERSION = "0.1.0"
TAIL_MAX = 131072  # 128 KiB kept for --retry-on matching
TERM_GRACE_SECS = 5


def die(msg, code=1):
    print(f"flake: error: {msg}", file=sys.stderr)
    sys.exit(code)


def _kill_tree(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=TERM_GRACE_SECS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


def run_attempt(cmd, timeout):
    """Run cmd once, streaming output live. Returns dict with
    rc, timed_out, interrupted, tail (bytes), elapsed."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}", 127)
    except OSError as e:
        die(f"failed to start {cmd[0]}: {e}", 126)

    tail = bytearray()
    lock = threading.Lock()
    state = {"interrupted": False}

    def pump(src, dst):
        try:
            for chunk in iter(lambda: src.read(65536), b""):
                dst.write(chunk)
                dst.flush()
                with lock:
                    tail.extend(chunk)
                    del tail[:-TAIL_MAX]
        finally:
            src.close()

    t_out = threading.Thread(target=pump, args=(proc.stdout, sys.stdout.buffer), daemon=True)
    t_err = threading.Thread(target=pump, args=(proc.stderr, sys.stderr.buffer), daemon=True)
    t_out.start()
    t_err.start()

    def _on_int(_signum, _frame):
        state["interrupted"] = True
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass

    old_int = signal.signal(signal.SIGINT, _on_int)
    timed_out = False
    deadline = time.monotonic() + timeout if timeout else None
    t0 = time.monotonic()
    try:
        while True:
            if state["interrupted"]:
                break
            try:
                rc = proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
            if deadline is not None and time.monotonic() >= deadline:
                _kill_tree(proc)
                rc = proc.wait()
                timed_out = True
                break
    finally:
        signal.signal(signal.SIGINT, old_int)
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    return {"rc": rc, "timed_out": timed_out, "interrupted": state["interrupted"],
            "tail": bytes(tail), "elapsed": time.monotonic() - t0}


def backoff_delay(base, max_delay, attempt, jitter):
    cap = min(max_delay, base * (2 ** attempt))
    return random.uniform(0, cap) if jitter else cap


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="flake",
        description="Run a flaky command with retries, exponential backoff, and jitter.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    ap.add_argument("--retries", type=int, default=3, help="retries after the first attempt (default: 3)")
    ap.add_argument("--delay", type=float, default=1.0, help="base backoff seconds (default: 1)")
    ap.add_argument("--max-delay", type=float, default=60.0, help="backoff cap seconds (default: 60)")
    ap.add_argument("--jitter", dest="jitter", action="store_true", default=True,
                    help="full jitter on backoff (default: on)")
    ap.add_argument("--no-jitter", dest="jitter", action="store_false", help="disable jitter")
    ap.add_argument("--timeout", type=float, default=None, help="kill each attempt after N seconds")
    ap.add_argument("--retry-on", action="append", default=[],
                    help="only retry when output contains this substring (repeatable)")
    ap.add_argument("--quiet", action="store_true", help="suppress flake's own stderr chatter")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="command to run (after --)")
    args = ap.parse_args(argv)

    cmd = [c for c in args.cmd if c != "--"]
    if not cmd:
        die("no command given")
    if args.retries < 0:
        die("--retries must be >= 0")
    if args.delay < 0 or args.max_delay <= 0:
        die("--delay must be >= 0 and --max-delay > 0")

    def log(msg):
        if not args.quiet:
            print(f"flake: {msg}", file=sys.stderr)

    last_rc = 1
    for attempt in range(args.retries + 1):
        left = args.retries - attempt
        if attempt:
            log(f"attempt {attempt + 1}/{args.retries + 1}")
        res = run_attempt(cmd, args.timeout)
        if res["interrupted"]:
            log("interrupted, not retrying")
            sys.exit(130)
        last_rc = 124 if res["timed_out"] else res["rc"]
        if res["rc"] == 0:
            if attempt and not args.quiet:
                log(f"succeeded on attempt {attempt + 1}")
            sys.exit(0)
        why = f"killed after {args.timeout}s" if res["timed_out"] else f"exit {res['rc']}"
        if not res["timed_out"] and args.retry_on:
            text = res["tail"].decode(errors="replace")
            if not any(p in text for p in args.retry_on):
                log(f"failed ({why}); output did not match --retry-on, not retrying")
                sys.exit(last_rc)
        if left == 0:
            log(f"failed ({why}); no retries left")
            sys.exit(last_rc)
        wait = backoff_delay(args.delay, args.max_delay, attempt, args.jitter)
        log(f"failed ({why}); retrying in {wait:.1f}s ({left} left)")
        time.sleep(wait)


if __name__ == "__main__":
    main()
