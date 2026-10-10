#!/usr/bin/env sh
# Install the native coding/LLM clients (Claude Code CLI and Codex CLI) on Linux/macOS,
# check their login state, and optionally connect them to an sdlc project.
#
#   sh scripts/install-backends.sh
#   sh scripts/install-backends.sh --project demo [--brain claude|codex] [--claude-model sonnet] [--codex-model MODEL]
#
# Installs Node.js LTS if missing (Ubuntu/Debian: NodeSource apt repository; macOS:
# Homebrew), then @anthropic-ai/claude-code and @openai/codex globally with npm into
# ~/.npm-global so no sudo is needed for the CLIs. It never asks for, stores or copies
# a password, token or API key.
set -eu
cd "$(dirname "$0")/.."

PROJECT=""; BRAIN="claude"; CLAUDE_MODEL="sonnet"; CODEX_MODEL=""; SKIP_INSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --brain) BRAIN="$2"; shift 2 ;;
    --claude-model) CLAUDE_MODEL="$2"; shift 2 ;;
    --codex-model) CODEX_MODEL="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL=1; shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done

export PATH="$HOME/.npm-global/bin:$PATH"

if [ "$SKIP_INSTALL" -eq 0 ]; then
  if ! command -v node >/dev/null 2>&1; then
    echo "Installing Node.js LTS..."
    if command -v apt-get >/dev/null 2>&1; then
      curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
      sudo apt-get install -y nodejs
    elif command -v brew >/dev/null 2>&1; then
      brew install node
    else
      echo "Install Node.js LTS from https://nodejs.org and re-run."; exit 1
    fi
  fi
  mkdir -p "$HOME/.npm-global"
  npm config set prefix "$HOME/.npm-global"
  echo "Installing Claude Code CLI and Codex CLI with npm (into ~/.npm-global)..."
  npm install -g @anthropic-ai/claude-code @openai/codex
  case ":$PATH:" in *":$HOME/.npm-global/bin:"*) ;; *)
    echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.profile"
    echo "Added ~/.npm-global/bin to PATH in ~/.profile (open a new shell later)." ;;
  esac
fi

echo
echo "== Versions"
echo "claude: $(claude --version 2>&1 | head -n 1)"
echo "codex : $(codex --version 2>&1 | head -n 1)"

echo
echo "== Login (official clients only; sdlc never sees your credentials)"
if claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
  echo "claude: logged in"
else
  echo "claude: NOT logged in. Run:  claude auth login   (uses your Claude subscription)"
fi
if codex login status 2>&1 | grep -qi "logged in"; then
  echo "codex : logged in ($(codex login status 2>&1 | head -n 1))"
else
  echo "codex : NOT logged in. Run:  codex login   (ChatGPT sign-in; or 'codex login --with-api-key' to bill an API account)"
fi

if [ -n "$PROJECT" ]; then
  [ -x .venv/bin/sdlc ] || sh scripts/setup.sh >/dev/null
  SDLC=.venv/bin/sdlc
  [ -f ".sdlc/$PROJECT/project.yaml" ] || { $SDLC init "$PROJECT" >/dev/null; echo "Created project '$PROJECT'."; }
  echo
  echo "== Connecting the clients to project '$PROJECT'"
  $SDLC connection add claude-native --project "$PROJECT" --type claude-cli --model "$CLAUDE_MODEL" --quota-scope claude-subscription --replace
  if [ -n "$CODEX_MODEL" ]; then
    $SDLC connection add codex-native --project "$PROJECT" --type codex-cli --model "$CODEX_MODEL" --quota-scope codex-subscription --replace
  else
    $SDLC connection add codex-native --project "$PROJECT" --type codex-cli --quota-scope codex-subscription --replace
  fi
  if [ "$BRAIN" = "claude" ]; then B=claude-native; else B=codex-native; fi
  $SDLC connection bind --project "$PROJECT" --role brain --connection "$B" --all-teams
  echo
  echo "== Live check (one small request per connection, billed to your subscriptions)"
  $SDLC doctor --project "$PROJECT" --live
  echo
  echo "Done. Next:  $SDLC import --project $PROJECT --team planning --file examples/library-reservation/brief.md"
fi
