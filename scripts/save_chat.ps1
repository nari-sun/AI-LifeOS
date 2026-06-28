[CmdletBinding()]
param(
    [string]$Date,
    [switch]$KeepInbox,
    [switch]$SkipCodex
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "python command was not found. Install Python 3 and make sure python is on PATH."
}

$ProcessArgs = @("scripts/process_chat.py", "--commit")
if ($Date) {
    $ProcessArgs += @("--date", $Date)
}
if ($KeepInbox) {
    $ProcessArgs += "--keep-inbox"
}
if (-not $SkipCodex) {
    $ProcessArgs += "--run-codex"
}

& python @ProcessArgs
exit $LASTEXITCODE
