[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CodexArgs
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues["Get-Content:Encoding"] = "UTF8"
$PSDefaultParameterValues["Select-String:Encoding"] = "UTF8"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$projectConfig = Join-Path $repositoryRoot ".codex\config.toml"
if (-not (Test-Path -LiteralPath $projectConfig -PathType Leaf)) {
    throw "Repository-only Codex configuration is missing: .codex/config.toml"
}

# Do not let this workspace profile override or bypass its repository-only
# permission policy. Other ordinary Codex options may still be forwarded.
$blockedOptions = @(
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--full-auto",
    "--sandbox",
    "-s",
    "--add-dir",
    "--cd",
    "-C",
    "--profile",
    "-p",
    "--config",
    "-c"
)
foreach ($argument in $CodexArgs) {
    $optionName = ($argument -split "=", 2)[0]
    if ($blockedOptions -contains $optionName) {
        throw "This VS Code profile does not allow Codex permission or workspace overrides: $optionName"
    }
}

Set-Location -LiteralPath $repositoryRoot

$codexCommand = Get-Command "codex.cmd" -CommandType Application -ErrorAction SilentlyContinue
if (-not $codexCommand) {
    throw "Codex CLI was not found on PATH. Install or update it outside this repository manually."
}

& $codexCommand.Source @CodexArgs
exit $LASTEXITCODE
