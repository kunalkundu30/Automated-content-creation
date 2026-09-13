#!/usr/bin/env bash
# claude_retry.sh — Run a `claude` command and automatically retry when the
# subscription usage limit is hit.
#
# Usage:
#   ./claude_retry.sh [claude flags and arguments]
#
# Examples:
#   ./claude_retry.sh --print "run the pipeline and fix any errors"
#   ./claude_retry.sh --continue
#   ./claude_retry.sh --resume <session-id> "next task"
#
# Environment variables:
#   CLAUDE_RETRY_INTERVAL   Seconds to wait between retries (default: 300 = 5 min)
#   CLAUDE_MAX_WAIT         Max total wait before giving up, in seconds (default: 86400 = 24 h)
#
# How it works:
#   Runs `claude` and pipes output to a temp file while also printing it.
#   If the exit code is non-zero AND the output contains a usage-limit phrase,
#   the script sleeps and retries. Any other non-zero exit (real error) is
#   propagated immediately without retrying.

set -uo pipefail

RETRY_INTERVAL=${CLAUDE_RETRY_INTERVAL:-300}
MAX_WAIT=${CLAUDE_MAX_WAIT:-86400}

_log() { printf '\n[claude_retry] %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Detect whether captured output looks like a usage-limit response
# ---------------------------------------------------------------------------
_is_usage_limit() {
    local output_file="$1"
    grep -qiE \
        'usage limit|rate limit reached|upgrade.*plan|claude\.ai/upgrade|try again (after|in)|limit resets' \
        "$output_file" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Parse "retry in N minutes" or "resets in N minutes" from the output.
# Returns the number of seconds to wait, or empty string if not found.
# ---------------------------------------------------------------------------
_parse_wait_secs() {
    local output_file="$1"
    local minutes
    # Match patterns like "retry in 47 minutes", "resets in 2 minutes", etc.
    minutes=$(grep -oiE '(retry|reset[s]?) in [0-9]+ minute' "$output_file" \
              | grep -oE '[0-9]+' | head -1)
    if [ -n "$minutes" ]; then
        # Add a 30-second buffer so we don't hit the limit right at the boundary
        echo $(( minutes * 60 + 30 ))
    fi
}

# ---------------------------------------------------------------------------
# Main retry loop
# ---------------------------------------------------------------------------
run_with_retry() {
    local elapsed=0
    local attempt=1
    local tmpfile
    tmpfile=$(mktemp /tmp/claude_retry_XXXXXX)
    # Clean up temp file on any exit
    trap 'rm -f "$tmpfile"' EXIT

    while true; do
        _log "Attempt $attempt — running: claude $*"

        # Run claude; tee output to terminal AND temp file simultaneously.
        # PIPESTATUS[0] captures claude's exit code even through the pipe.
        set +e
        claude "$@" 2>&1 | tee "$tmpfile"
        exit_code=${PIPESTATUS[0]}
        set -e

        # ── Success ──────────────────────────────────────────────────────────
        if [ "$exit_code" -eq 0 ]; then
            _log "Completed successfully on attempt $attempt."
            return 0
        fi

        # ── Usage limit hit ───────────────────────────────────────────────────
        if _is_usage_limit "$tmpfile"; then
            if [ "$elapsed" -ge "$MAX_WAIT" ]; then
                _log "Max wait time (${MAX_WAIT}s) exceeded after $attempt attempt(s). Giving up."
                return 1
            fi

            # Honour the reset time the CLI reports, if parseable
            parsed=$(_parse_wait_secs "$tmpfile")
            wait_secs=${parsed:-$RETRY_INTERVAL}

            _log "Usage limit reached. Waiting ${wait_secs}s before retry" \
                 "(elapsed: ${elapsed}s / max: ${MAX_WAIT}s, attempt: $attempt)."
            sleep "$wait_secs"
            elapsed=$(( elapsed + wait_secs ))
            attempt=$(( attempt + 1 ))

        # ── Any other non-zero exit — do not retry ────────────────────────────
        else
            _log "Exited with code $exit_code (not a usage-limit error). Not retrying."
            return "$exit_code"
        fi
    done
}

run_with_retry "$@"
