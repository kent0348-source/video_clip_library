param(
    [string]$OutputPath = "dist\clip_library.ankiaddon"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot "src\video_clip_library"
$output = Join-Path $projectRoot $OutputPath
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("clip_library_addon_" + [guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Get-ChildItem -LiteralPath $source -Recurse -File |
        Where-Object {
            $_.Name -notin @("meta.json", "README.md", "test_clip_library.py") -and
            $_.FullName -notmatch "\\__pycache__\\" -and
            $_.Extension -notin @(".pyc", ".pyo")
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($source.Length).TrimStart("\\")
            $destination = Join-Path $staging $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination
        }

    $required = @("__init__.py", "config.json", "manifest.json")
    foreach ($name in $required) {
        if (-not (Test-Path (Join-Path $staging $name))) {
            throw "Required addon file is missing from staging: $name"
        }
    }

    $outputDirectory = Split-Path -Parent $output
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    if (Test-Path $output) {
        Remove-Item -LiteralPath $output -Force
    }
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $output -CompressionLevel Optimal
    Write-Output "Created $output"
}
finally {
    if (Test-Path $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
