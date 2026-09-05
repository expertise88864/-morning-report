[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (& git -C $ScriptDir rev-parse --show-toplevel 2>$null).Trim()
if (-not $RepoRoot) { throw 'This script must run inside a Git repository.' }

$Required = @(
    (Join-Path $RepoRoot '.githooks\pre-commit'),
    (Join-Path $RepoRoot '.githooks\pre-push'),
    (Join-Path $RepoRoot '.githooks\commit-msg'),
    (Join-Path $RepoRoot 'tools\claude_review_queue.py'),
    (Join-Path $RepoRoot 'tools\claude_diff_review.py')
)
foreach ($Path in $Required) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required review-gate file is missing: $Path"
    }
}

foreach ($Hook in @('.githooks/pre-commit', '.githooks/pre-push', '.githooks/commit-msg')) {
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

Write-Host '[claude-review] Review hooks installed (quota-only marked deferral).'
Write-Host '[claude-review] Exact reviewer: claude-opus-5, effort=high.'
