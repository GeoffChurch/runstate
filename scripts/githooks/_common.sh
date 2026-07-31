# Pick the interpreter the repo's tools actually live in. `uv run` is avoided
# deliberately: without `--extra test` it falls through to whatever `pytest` is
# on PATH, whose interpreter may carry a DIFFERENT editable runstate -- the
# import-shadowing trap that has produced false greens in this repo before.
repo_root="$(git rev-parse --show-toplevel)"
if [ -x "$repo_root/.venv/bin/python" ]; then
    PY="$repo_root/.venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi
[ -n "$PY" ] || { echo "hook: no python found; skipping (commit with --no-verify to silence)"; exit 0; }

have() { "$PY" -c "import $1" >/dev/null 2>&1; }

run_gate() {  # run_gate <label> <module> <args...>
    label="$1"; mod="$2"; shift 2
    have "$mod" || { printf '  skip  %-6s (not installed in %s)\n' "$label" "$PY"; return 0; }
    if out=$("$PY" -m "$mod" "$@" 2>&1); then
        printf '  ok    %s\n' "$label"
    else
        printf '  FAIL  %s\n' "$label"
        printf '%s\n' "$out" | tail -20
        return 1
    fi
}
