#!/bin/bash
# One-line install: curl -sSL https://raw.githubusercontent.com/USER/devpulse/main/scripts/install.sh | bash

set -e

echo "🔧 Installing DevPulse..."

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'" 2>/dev/null || {
    echo "❌ Python 3.10+ required (found: $(python3 --version 2>&1))"
    exit 1
}

# Install via pip
pip install --user devpulse

# Initialize
devpulse init

echo ""
echo "✅ DevPulse installed!"
echo ""
echo "Add this to your shell config:"
echo ""
if [[ "$SHELL" == *"zsh"* ]]; then
    echo '  echo "source \$(devpulse shell-hook --zsh)" >> ~/.zshrc'
    echo "  source ~/.zshrc"
else
    echo '  echo "source \$(devpulse shell-hook --bash)" >> ~/.bashrc'
    echo "  source ~/.bashrc"
fi
echo ""
echo "Then start the daemon:"
echo "  devpulse start"
echo ""
echo "Free local LLM (recommended):"
echo "  curl -fsSL https://ollama.com/install.sh | sh"
echo "  ollama pull llama3.1"
echo "  devpulse config set llm.provider ollama"
