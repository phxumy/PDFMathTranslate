param(
    [Parameter(Mandatory = $true)][string] $ReleaseRoot,
    [string] $ReleaseTag = "v1.9.11-codex.5.4",
    [string] $OutputRoot,
    [string] $Compiler
)
$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputRoot) { $OutputRoot = Join-Path $repositoryRoot "release-dist" }
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$OutputRoot = (Resolve-Path -LiteralPath $OutputRoot).Path
$versionMatch = [regex]::Match($ReleaseTag, '^v(\d+)\.(\d+)\.(\d+)-codex\.(\d+)(?:\.(\d+))?$')
if (-not $versionMatch.Success) { throw "Invalid release tag." }
$hotfix = if ($versionMatch.Groups[5].Success) { [int]$versionMatch.Groups[5].Value } else { 0 }
$versionParts = @(
    [int]$versionMatch.Groups[1].Value
    [int]$versionMatch.Groups[2].Value
    [int]$versionMatch.Groups[3].Value
    ([long]$versionMatch.Groups[4].Value * 100 + $hotfix)
)
if ($hotfix -gt 99 -or @($versionParts | Where-Object { $_ -gt 65535 }).Count) {
    throw "Release version exceeds the Windows four-part version range."
}
$fileVersion = $versionParts -join '.'
if (-not $Compiler) {
    $candidate = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($candidate) { $Compiler = $candidate.Source }
    else { $Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
}
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw "Install Inno Setup 6 and provide -Compiler with the path to ISCC.exe."
}
foreach ($required in @("PDFMathTranslate-Codex.exe", "pdf2zh.exe", "runtime\python.exe", "site-packages\pdf2zh\version.py", "codex-cli\x86_64-pc-windows-msvc\bin\codex.exe", "LICENSE")) {
    if (-not (Test-Path -LiteralPath (Join-Path $ReleaseRoot $required))) { throw "Missing required file: $required" }
}
$versionSource = Get-Content -LiteralPath (Join-Path $ReleaseRoot "site-packages\pdf2zh\version.py") -Raw
if (-not $versionSource.Contains('APP_VERSION = "' + $ReleaseTag + '"')) {
    throw "The staged application version does not match the release tag."
}
$forbidden = @(Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -Force | Where-Object {
    $_.Name -in @("auth.json", ".codex", "pdf2zh_files", ".env") -or
    $_.Name -like ".env.*" -or $_.Extension -in @(".pdf", ".db", ".sqlite", ".sqlite3")
})
if ($forbidden.Count -gt 0) { throw "Private data or user documents found in release staging." }
$installerSource = Join-Path $repositoryRoot "packaging\windows-installer.iss"
& $Compiler /Qp "/DReleaseRoot=$ReleaseRoot" "/DReleaseTag=$ReleaseTag" "/DFileVersion=$fileVersion" "/DOutputRoot=$OutputRoot" $installerSource
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler failed with exit code $LASTEXITCODE." }
$installer = Join-Path $OutputRoot "PDFMathTranslate-Codex-Setup-win64.exe"
if ((Get-Item -LiteralPath $installer).Length -ge 2GB) { throw "Installer exceeds GitHub's asset size limit." }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  PDFMathTranslate-Codex-Setup-win64.exe" | Set-Content -LiteralPath (Join-Path $OutputRoot "SHA256SUMS.txt") -Encoding ascii
Write-Output "Installer: $installer"
Write-Output "SHA-256: $hash"
