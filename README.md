# flake

[![ci](https://github.com/intertermux-code/flake/actions/workflows/ci.yml/badge.svg)](https://github.com/intertermux-code/flake/actions)

Run flaky commands with retries, exponential backoff, and jitter. One
binary, zero dependencies — for the CI step that fails every third run, the
deploy script that hits a rate limit, the `pip install` that drops once a
week.

```sh
pip install flake

flake --retries 5 -- make test
flake --retries 5 --timeout 120 -- ./deploy.sh
flake --retry-on "rate limit" --retry-on "429" -- ./call-api.sh
flake --retries 0 -- ./must-pass-once.sh
```

Output streams live to your terminal exactly as the command produces it;
flake's own chatter (`attempt 2/4 failed (exit 1), retrying in 3.2s`) goes
to stderr, and `--quiet` silences it.

## Behavior

- Exit code is the command's exit code: `0` on success, the last attempt's
  code on failure, `124` if an attempt was killed by `--timeout`, `127`/`126`
  if the command can't start (no pointless retries), `130` on Ctrl-C
  (interrupts the child too, never retried).
- Backoff is `delay * 2^attempt`, capped at `--max-delay` (defaults: 1s base,
  60s cap), with full jitter on by default (`--no-jitter` disables it).
- `--timeout` kills the whole process group per attempt: SIGTERM, then
  SIGKILL after a 5s grace period. Timeouts always retry.
- `--retry-on` (repeatable) retries only when the attempt's output contains
  the substring — retry on `rate limit`, not on `invalid API key`. A bounded
  128 KiB tail is kept for matching, so memory stays flat on huge logs.

## Reference

| Flag | Default | What it does |
|---|---|---|
| `--retries N` | 3 | retries after the first attempt |
| `--delay S` | 1.0 | base backoff in seconds |
| `--max-delay S` | 60.0 | backoff cap in seconds |
| `--jitter` / `--no-jitter` | on | full jitter on the backoff |
| `--timeout S` | none | kill each attempt after S seconds |
| `--retry-on SUB` | none | only retry when output contains SUB |
| `--quiet` | off | suppress flake's stderr chatter |

POSIX only (Linux, macOS). Python 3.9+.

## License

MIT.
