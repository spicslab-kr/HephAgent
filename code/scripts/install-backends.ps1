# Install the native coding/LLM clients (Claude Code CLI and Codex CLI), check their
# login state, and optionally connect them to an sdlc project.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-backends.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install-backends.ps1 -Project demo
#   powershell -ExecutionPolicy Bypass -File scripts\install-backends.ps1 -Project demo -Brain claude
#
# What it does: installs Node.js LTS with winget if missing, installs
# @anthropic-ai/claude-code and @openai/codex globally with npm, prints how to log in,
# verifies the login with the official clients, and (with -Project) runs
# `sdlc connection add` for each client plus `sdlc doctor --live`.
# It never asks for, stores or copies a password, token or API key.
param(
    [string]$Project = "",
    [ValidateSet("claude", "codex")] [string]$Brain = "claude",
    [string]$ClaudeModel = "sonnet",
    [string]$CodexModel = "",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Refresh-Path {
    $env:PATH = "C:\Program Files\nodejs;$env:APPDATA\npm;" + [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH", "User")
}

if (-not $SkipInstall) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -and -not (Test-Path "C:\Program Files\nodejs\node.exe")) {
        Write-Host "Installing Node.js LTS with winget (an administrator prompt may appear)..."
        winget install --id OpenJS.NodeJS.LTS --exact --accept-package-agreements --accept-source-agreements --silent
    }
    Refresh-Path
    Write-Host "Installing Claude Code CLI and Codex CLI with npm..."
    npm install -g @anthropic-ai/claude-code @openai/codex
}
Refresh-Path

# Native CLIs write status lines to stderr; merge streams through cmd so Windows PowerShell
# does not turn them into terminating errors.
function Invoke-Native([string]$CommandLine) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return (cmd /c "$CommandLine 2>&1" | Out-String) } finally { $ErrorActionPreference = $previous }
}

Write-Host ""
Write-Host "== Versions"
Write-Host "claude: $((Invoke-Native 'claude --version').Trim())"
Write-Host "codex : $((Invoke-Native 'codex --version').Trim())"

Write-Host ""
Write-Host "== Login (official clients only; sdlc never sees your credentials)"
$claudeStatus = Invoke-Native 'claude auth status'
if ($claudeStatus -match '"loggedIn":\s*true') {
    Write-Host "claude: logged in" -ForegroundColor Green
} else {
    Write-Host "claude: NOT logged in. Run:  claude auth login   (opens the browser; uses your Claude subscription)" -ForegroundColor Yellow
}
$codexStatus = Invoke-Native 'codex login status'
if ($codexStatus -match "Logged in") {
    Write-Host "codex : logged in ($($codexStatus.Trim()))" -ForegroundColor Green
} else {
    Write-Host "codex : NOT logged in. Run:  codex login   (ChatGPT sign-in; or `codex login --with-api-key` to bill an API account)" -ForegroundColor Yellow
}

if ($Project -ne "") {
    if (-not (Test-Path ".venv\Scripts\sdlc.exe")) {
        Write-Host "The project environment is missing; running scripts\setup.ps1 first..."
        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 | Out-Null
    }
    $sdlc = ".venv\Scripts\sdlc.exe"
    if (-not (Test-Path ".sdlc\$Project\project.yaml")) {
        & $sdlc init $Project | Out-Null
        Write-Host "Created project '$Project'."
    }
    Write-Host ""
    Write-Host "== Connecting the clients to project '$Project' (replacing the example placeholders)"
    $ErrorActionPreference = "Continue"
    & $sdlc connection add claude-native --project $Project --type claude-cli --model $ClaudeModel --quota-scope claude-subscription --replace
    if ($CodexModel -ne "") {
        & $sdlc connection add codex-native --project $Project --type codex-cli --model $CodexModel --quota-scope codex-subscription --replace
    } else {
        & $sdlc connection add codex-native --project $Project --type codex-cli --quota-scope codex-subscription --replace
    }
    $brainConnection = if ($Brain -eq "claude") { "claude-native" } else { "codex-native" }
    & $sdlc connection bind --project $Project --role brain --connection $brainConnection --all-teams
    Write-Host ""
    Write-Host "== Live check (one small request per connection, billed to your subscriptions)"
    & $sdlc doctor --project $Project --live
    Write-Host ""
    Write-Host "Done. Next:  $sdlc import --project $Project --team planning --file examples\library-reservation\brief.md"
}
