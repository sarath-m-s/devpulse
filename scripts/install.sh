#!/bin/bash
# One-line install: curl -sSL https://raw.githubusercontent.com/USER/ghost-pulse/main/scripts/install.sh | bash

set -e

echo "🔧 Installing Ghost Pulse..."

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'" 2>/dev/null || {
    echo "❌ Python 3.10+ required (found: $(python3 --version 2>&1))"
    exit 1
}

# Install via pip
pip install --user ghost-pulse

# Initialize
ghost init

echo ""
echo "✅ Ghost Pulse installed!"
echo ""
echo "Add this to your shell config:"
echo ""
if [[ "$SHELL" == *"zsh"* ]]; then
    echo '  echo "source \$(ghost shell-hook --zsh)" >> ~/.zshrc'
    echo "  source ~/.zshrc"
else
    echo '  echo "source \$(ghost shell-hook --bash)" >> ~/.bashrc'
    echo "  source ~/.bashrc"
fi
echo ""
echo "Then start the daemon:"
echo "  ghost start"
echo ""
echo "Local LLM: ghost init installs Ollama and pulls default models when possible."
echo "  Skip with: ghost init --skip-ollama"
