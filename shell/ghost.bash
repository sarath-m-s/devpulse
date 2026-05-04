# Ghost Pulse shell integration — bash
# Source this file from ~/.bashrc:
#   source "$(ghost shell-hook --bash)"

export GHOST_PULSE_SESSION_ID="$(date +%s)-$$"

_ghost_cmd_start=0
_ghost_last_cmd=""

_ghost_preexec() {
    _ghost_cmd_start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
    _ghost_last_cmd="$BASH_COMMAND"
}

_ghost_precmd() {
    local exit_code=$?
    if [[ -n "$_ghost_last_cmd" ]]; then
        local end_time
        end_time=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
        local duration_ms=$(( (end_time - _ghost_cmd_start) / 1000000 ))
        ghost log-cmd \
            --cmd "$_ghost_last_cmd" \
            --cwd "$PWD" \
            --exit-code $exit_code \
            --duration-ms $duration_ms \
            --session "$GHOST_PULSE_SESSION_ID" &>/dev/null &
        _ghost_last_cmd=""
    fi
}

# Use DEBUG trap for preexec equivalent
trap '_ghost_preexec' DEBUG

# Append to PROMPT_COMMAND
if [[ -z "$PROMPT_COMMAND" ]]; then
    PROMPT_COMMAND="_ghost_precmd"
else
    PROMPT_COMMAND="${PROMPT_COMMAND%;}; _ghost_precmd"
fi
