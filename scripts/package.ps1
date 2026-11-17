# Build a distributable zip of the project (ASCII-only for Windows PowerShell 5.1).
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -IncludeModels
#
# Output:
#   <project>\dist\local-speech-to-text-<date>-src.zip
#   <project>\dist\local-speech-to-text-<date>-with-models.zip

param(
    [switch]$IncludeModels,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$name = Split-Path $root -Leaf
if (-not $OutputDir) {
    $OutputDir = Join-Path $root "dist"
}

$stamp = Get-Date -Format "yyyyMMdd"
$suffix = if ($IncludeModels) { "-with-models" } else { "-src" }
$zip = Join-Path $OutputDir "$name-$stamp$suffix.zip"

$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("a2t-package-" + [guid]::NewGuid().ToString("N"))
$stage = Join-Path $stageRoot $name

$excludeDirs = @(
    ".venv", "venv", "__pycache__", ".pytest_cache",
    ".idea", ".vscode", "dist", ".git"
)
if (-not $IncludeModels) {
    $excludeDirs += "models"
}

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null

    # robocopy exit codes 0-7 mean success (1 = files copied).
    $roboArgs = @($root, $stage, "/E", "/XD")
    $roboArgs += $excludeDirs
    $roboArgs += @("/XF", "*.log", "*.pyc")
    $roboOutput = & robocopy @roboArgs
    $roboCode = $LASTEXITCODE
    if ($roboCode -ge 8) {
        $roboOutput | Select-Object -Last 30 | Write-Host
        throw "robocopy failed with exit code $roboCode"
    }

    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal -Force
}
finally {
    Remove-Item -Recurse -Force $stageRoot -ErrorAction SilentlyContinue
}

$size = (Get-Item $zip).Length / 1MB
Write-Host ("Created: {0} ({1:N1} MB)" -f $zip, $size)
if ($IncludeModels) {
    Write-Host "models/ included: the recipient can start offline after installing dependencies."
} else {
    Write-Host "models/ excluded: the first start downloads about 900 MB of models (network required)."
}
