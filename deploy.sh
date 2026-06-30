#!/bin/bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        BLOOMYCO.IN  —  DEPLOY TOOL       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Install Homebrew if missing ──────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  echo "▶ Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
else
  echo "✔ Homebrew already installed"
fi

# ── 2. Install GitHub CLI if missing ────────────────────────────────────────
if ! command -v gh &>/dev/null; then
  echo "▶ Installing GitHub CLI..."
  brew install gh
else
  echo "✔ GitHub CLI already installed"
fi

# ── 3. Log in to GitHub (opens browser) ─────────────────────────────────────
if ! gh auth status &>/dev/null; then
  echo ""
  echo "▶ Logging you in to GitHub — a browser window will open."
  echo "  Sign in, then come back here."
  echo ""
  gh auth login --web --git-protocol https
else
  echo "✔ Already logged in to GitHub"
fi

# ── 4. Set git identity ──────────────────────────────────────────────────────
GH_USER=$(gh api user --jq .login)
GH_EMAIL=$(gh api user --jq '.email // empty')
git config --global user.name  "$GH_USER"
git config --global user.email "${GH_EMAIL:-$GH_USER@users.noreply.github.com}"

# ── 5. Initialise git repo ───────────────────────────────────────────────────
cd "$(dirname "$0")"
if [ ! -d .git ]; then
  echo "▶ Initialising git repo..."
  git init -b main
fi
git add -A
git commit -m "Initial deploy — Bloomyco.in terminal" 2>/dev/null || echo "✔ Nothing new to commit"

# ── 6. Create GitHub repo & push ────────────────────────────────────────────
REPO_NAME="bloomyco-terminal"
if gh repo view "$GH_USER/$REPO_NAME" &>/dev/null; then
  echo "✔ GitHub repo already exists"
else
  echo "▶ Creating GitHub repo '$REPO_NAME'..."
  gh repo create "$REPO_NAME" --public --source=. --remote=origin --push
fi

# make sure remote is set
git remote get-url origin &>/dev/null || \
  git remote add origin "https://github.com/$GH_USER/$REPO_NAME.git"

git push -u origin main --force

REPO_URL="https://github.com/$GH_USER/$REPO_NAME"
echo ""
echo "✔ Code pushed to: $REPO_URL"
echo ""

# ── 7. Open Render.com deploy page ──────────────────────────────────────────
RENDER_URL="https://render.com/deploy?repo=$REPO_URL"
echo "▶ Opening Render.com to deploy your server..."
echo "  • Click 'New Web Service'"
echo "  • Connect your GitHub account"
echo "  • Select '$REPO_NAME'"
echo "  • Build:  pip install -r requirements.txt"
echo "  • Start:  python server.py"
echo "  • Hit 'Create Web Service'"
echo ""
open "https://dashboard.render.com/select-repo"

echo "══════════════════════════════════════════"
echo "  GitHub: $REPO_URL"
echo "  Next:   Follow the Render.com steps above"
echo "══════════════════════════════════════════"
echo ""
