# Ghost Pulse shell integration — zsh
# Source this file from ~/.zshrc:
#   source "$(ghost shell-hook --zsh)"

export GHOST_PULSE_SESSION_ID="$(date +%s)-$$"

ghost_preexec() {
    export GHOST_PULSE_CMD_START=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
    export GHOST_PULSE_LAST_CMD="$1"
}

ghost_precmd() {
    local exit_code=$?
    if [[ -n "$GHOST_PULSE_LAST_CMD" ]]; then
        local end_time
        end_time=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
        local duration_ms=$(( (end_time - ${GHOST_PULSE_CMD_START:-$end_time}) / 1000000 ))
        { ghost log-cmd \
            --cmd "$GHOST_PULSE_LAST_CMD" \
            --cwd "$PWD" \
            --exit-code $exit_code \
            --duration-ms $duration_ms \
            --session "$GHOST_PULSE_SESSION_ID" &>/dev/null } &
        disown
        unset GHOST_PULSE_LAST_CMD
    fi
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec ghost_preexec
add-zsh-hook precmd ghost_precmd
