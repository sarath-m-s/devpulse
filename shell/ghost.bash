# DevPulse shell integration — bash
# Source this file from ~/.bashrc:
#   source "$(devpulse shell-hook --bash)"

export DEVPULSE_SESSION_ID="$(date +%s)-$$"

_devpulse_cmd_start=0
_devpulse_last_cmd=""

_devpulse_preexec() {
    _devpulse_cmd_start=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
    _devpulse_last_cmd="$BASH_COMMAND"
}

_devpulse_precmd() {
    local exit_code=$?
    if [[ -n "$_devpulse_last_cmd" ]]; then
        local end_time
        end_time=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
        local duration_ms=$(( (end_time - _devpulse_cmd_start) / 1000000 ))
        devpulse log-cmd \
            --cmd "$_devpulse_last_cmd" \
            --cwd "$PWD" \
            --exit-code $exit_code \
            --duration-ms $duration_ms \
            --session "$DEVPULSE_SESSION_ID" &>/dev/null &
        _devpulse_last_cmd=""
    fi
}

# Use DEBUG trap for preexec equivalent
trap '_devpulse_preexec' DEBUG

# Append to PROMPT_COMMAND
if [[ -z "$PROMPT_COMMAND" ]]; then
    PROMPT_COMMAND="_devpulse_precmd"
else
    PROMPT_COMMAND="${PROMPT_COMMAND%;}; _devpulse_precmd"
fi
