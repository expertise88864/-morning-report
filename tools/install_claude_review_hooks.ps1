[CmdletBinding()]
param()

# File name kept for existing entry points (commit_push.bat). Since the user
# decision of 2026-10-15 there is no per-commit review hook: only the tracked
# pre-push delivery gate is installed. Each PR is reviewed once with
# tools/pr_review.py (gpt-6.1-sol, xhigh, read-only).

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (& git -C $ScriptDir rev-parse --show-toplevel 2>$null).Trim()
if (-not $RepoRoot) { throw 'This script must run inside a Git repository.' }

$Required = @(
    (Join-Path $RepoRoot '.githooks\pre-push'),
    (Join-Path $RepoRoot '_delivery.py'),
    (Join-Path $RepoRoot 'tools\pr_review.py')
)
foreach ($Path in $Required) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required delivery file is missing: $Path"
    }
}

foreach ($Hook in @('.githooks/pre-push')) {
    $IndexEntry = (& git -C $RepoRoot ls-files --stage -- $Hook).Trim()
    if (-not $IndexEntry.StartsWith('100755 ')) {
        throw "$Hook must be committed with executable Git mode 100755."
    }
}

& git -C $RepoRoot config --local core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) { throw 'Unable to configure core.hooksPath.' }
$Actual = (& git -C $RepoRoot config --local --get core.hooksPath).Trim()
if ($Actual -ne '.githooks') {
    throw "core.hooksPath verification failed (got '$Actual')."
}

Write-Host '[delivery] pre-push delivery gate installed (no per-commit review hooks).'
Write-Host '[delivery] Review each pushed codex/* PR once: python tools/pr_review.py (gpt-6.1-sol, xhigh, read-only).'
