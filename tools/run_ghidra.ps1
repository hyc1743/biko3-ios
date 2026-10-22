param(
    [string] $Image = '',
    [string] $Output = '',
    [ValidateRange(1, 1000)]
    [int] $MaxDecompile = 40
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $projectRoot '.tools\analysis-toolchain.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Analysis toolchain is not installed. Run python tools/bootstrap_analysis.py --task ghidra first.'
}
$toolchain = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$headless = Join-Path $toolchain.ghidra_home 'support\analyzeHeadless.bat'
$javaExe = Join-Path $toolchain.java_home 'bin\java.exe'
foreach ($dependency in @($headless, $javaExe)) {
    if (-not (Test-Path -LiteralPath $dependency -PathType Leaf)) { throw "Missing analysis dependency: $dependency" }
}
if (-not $Image) { $Image = Join-Path $projectRoot 'work\biko3-ios\evidence\nro\biko3-analysis.elf' }
if (-not $Output) { $Output = Join-Path $projectRoot 'work\biko3-ios\evidence\ghidra' }
$Image = (Resolve-Path -LiteralPath $Image).Path
$Output = [IO.Path]::GetFullPath($Output)
# Paths cross a Windows batch boundary; reject command expansion characters.
foreach ($argument in @($Image, $Output, $headless, $javaExe, $projectRoot)) {
    if ($argument -match '[%!\r\n&|<>^]') { throw 'Unsupported command-expansion character in an analysis path.' }
}
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$projectDirectory = Join-Path $projectRoot 'work\biko3-ios\ghidra-projects'
New-Item -ItemType Directory -Force -Path $projectDirectory | Out-Null
$projectName = 'biko3-' + [Guid]::NewGuid().ToString('N')
$previousJavaHome = $env:JAVA_HOME
$previousPath = $env:PATH
try {
    $env:JAVA_HOME = $toolchain.java_home
    $env:PATH = (Split-Path -Parent $javaExe) + [IO.Path]::PathSeparator + $previousPath
    & $javaExe -version
    if ($LASTEXITCODE -ne 0) { throw 'The workspace-local Java runtime failed its startup check.' }
    $arguments = @(
        $projectDirectory, $projectName,
        '-import', $Image,
        '-loader', 'ElfLoader', '-loader-imagebase', '0x0',
        '-scriptPath', (Join-Path $PSScriptRoot 'ghidra'),
        '-postScript', 'Biko3Export.java', $Output, $MaxDecompile.ToString(),
        '-analysisTimeoutPerFile', '900',
        '-log', (Join-Path $Output 'ghidra.log'),
        '-scriptlog', (Join-Path $Output 'script.log')
    )
    & $headless @arguments
    if ($LASTEXITCODE -ne 0) { throw "Ghidra Headless exited with code $LASTEXITCODE; inspect logs in $Output" }
    $reportFile = Join-Path $Output 'functions.json'
    if (-not (Test-Path -LiteralPath $reportFile -PathType Leaf)) { throw 'Ghidra did not produce functions.json; inspect its logs.' }
    $report = Get-Content -LiteralPath $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.cancelled) { throw 'Ghidra export was cancelled; the result is incomplete.' }
    if ($report.image_base -ne '0x0') { throw "Unexpected Ghidra image base: $($report.image_base)" }
    $actualHash = (Get-FileHash -LiteralPath $Image -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($report.executable_sha256.ToLowerInvariant() -ne $actualHash) { throw 'Ghidra input hash does not match the requested ELF.' }
    Write-Output "Standalone Ghidra analysis exported: $reportFile"
}
finally {
    $env:JAVA_HOME = $previousJavaHome
    $env:PATH = $previousPath
}
