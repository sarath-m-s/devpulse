# DevPulse shell integration — zsh
# Source this file from ~/.zshrc:
#   source "$(devpulse shell-hook --zsh)"

export DEVPULSE_SESSION_ID="$(date +%s)-$$"

devpulse_preexec() {
    export DEVPULSE_CMD_START=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
    export DEVPULSE_LAST_CMD="$1"
}

devpulse_precmd() {
    local exit_code=$?
    if [[ -n "$DEVPULSE_LAST_CMD" ]]; then
        local end_time
        end_time=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
        local duration_ms=$(( (end_time - ${DEVPULSE_CMD_START:-$end_time}) / 1000000 ))
        { devpulse log-cmd \
            --cmd "$DEVPULSE_LAST_CMD" \
            --cwd "$PWD" \
            --exit-code $exit_code \
            --duration-ms $duration_ms \
            --session "$DEVPULSE_SESSION_ID" &>/dev/null } &
        disown
        unset DEVPULSE_LAST_CMD
    fi
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec devpulse_preexec
add-zsh-hook precmd devpulse_precmd
