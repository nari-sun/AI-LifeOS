[CmdletBinding()]
param(
    [ValidateSet("install", "dev", "build")]
    [string]$Mode = "dev"
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$appDir = Join-Path $root "desktop\app"
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "chat_gui_task.log"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    $env:AI_LIFEOS_PYTHON = $venvPython
}
$env:AI_LIFEOS_GUI_LOG = Join-Path $logDir "chat_gui_bridge.log"
$env:AI_LIFEOS_TAURI_LOG = Join-Path $logDir "chat_gui_tauri.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-TaskLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
    "$timestamp pid=$PID $Message" | Tee-Object -FilePath $logFile -Append
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,

        [string[]]$CommandArgs = @()
    )

    Write-TaskLog "command.start exe=$Exe args=$($CommandArgs -join ' ') cwd=$appDir"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @CommandArgs 2>&1 | ForEach-Object {
            $_.ToString() | Tee-Object -FilePath $logFile -Append
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    Write-TaskLog "command.exit exe=$Exe code=$exitCode"

    if ($exitCode -ne 0) {
        exit $exitCode
    }
}

Write-TaskLog "task.start mode=$Mode"
Push-Location $appDir
try {
    if ($Mode -eq "install") {
        Invoke-LoggedCommand -Exe "npm.cmd" -CommandArgs @("install")
    }
    elseif ($Mode -eq "dev") {
        Invoke-LoggedCommand -Exe "npm.cmd" -CommandArgs @("install")
        # Tauri's Rust watcher can report unchanged build.rs/icon files as changed
        # shortly after startup and restart the whole app process tree. Vite keeps
        # frontend hot reload active; restart this task manually for Rust changes.
        Invoke-LoggedCommand -Exe "npm.cmd" -CommandArgs @("run", "tauri", "--", "dev", "--no-watch")
    }
    elseif ($Mode -eq "build") {
        Invoke-LoggedCommand -Exe "npm.cmd" -CommandArgs @("install")
        Invoke-LoggedCommand -Exe "npm.cmd" -CommandArgs @("run", "bundle")
    }
}
finally {
    Pop-Location
    Write-TaskLog "task.end mode=$Mode"
}
