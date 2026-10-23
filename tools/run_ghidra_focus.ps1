param([string] $Image = '', [string] $Output = '', [string] $Boundaries = '', [string] $Nro = '', [string] $Addresses = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolchain = Get-Content -LiteralPath (Join-Path $projectRoot '.tools\analysis-toolchain.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$headless = Join-Path $toolchain.ghidra_home 'support\analyzeHeadless.bat'
if (-not $Image) { $Image = Join-Path $projectRoot 'work\biko3-ios\evidence\nro\biko3-analysis.elf' }
if (-not $Output) { $Output = Join-Path $projectRoot 'work\biko3-ios\evidence\ghidra-focus' }
if (-not $Boundaries) { $Boundaries = Join-Path $projectRoot 'work\biko3-ios\nxnative-preparation\main.function-boundaries.json' }
if (-not $Nro) { $Nro = Join-Path $projectRoot 'switch\biko3\biko3-runtime.nro' }
$Image = (Resolve-Path -LiteralPath $Image).Path
$Boundaries = (Resolve-Path -LiteralPath $Boundaries).Path
$Nro = (Resolve-Path -LiteralPath $Nro).Path
$Output = [IO.Path]::GetFullPath($Output)
if ($Addresses -and $Addresses -notmatch '^0x[0-9a-fA-F]+(,0x[0-9a-fA-F]+){0,39}$') { throw 'Addresses must be 1-40 comma-separated hexadecimal offsets.' }
foreach ($argument in @($Image, $Output, $Boundaries, $Nro, $headless, $projectRoot, $toolchain.java_home)) {
    if ($argument -match '[%!\r\n&|<>^]') { throw 'Unsupported command-expansion character in path.' }
}
if (Test-Path -LiteralPath $Output) { throw 'Use a fresh output directory; focus logs are never overwritten.' }
New-Item -ItemType Directory -Path $Output | Out-Null
$projects = Join-Path $projectRoot 'work\biko3-ios\ghidra-projects'
New-Item -ItemType Directory -Force -Path $projects | Out-Null
$oldJava = $env:JAVA_HOME
$oldPath = $env:PATH
$oldHeap = $env:GHIDRA_HEADLESS_MAXMEM
try {
    $env:JAVA_HOME = $toolchain.java_home
    $env:PATH = (Join-Path $toolchain.java_home 'bin') + [IO.Path]::PathSeparator + $oldPath
    $env:GHIDRA_HEADLESS_MAXMEM = '4G'
    $arguments = @($projects, ('biko3-focus-' + [Guid]::NewGuid().ToString('N')),
        '-import', $Image, '-loader', 'ElfLoader', '-loader-imagebase', '0x0', '-noanalysis',
        '-scriptPath', (Join-Path $PSScriptRoot 'ghidra'),
        '-postScript', 'Biko3Focus.java', $Output, $Boundaries, $Nro)
    if ($Addresses) { $arguments += $Addresses }
    $arguments += @('-log', (Join-Path $Output 'ghidra.log'), '-scriptlog', (Join-Path $Output 'script.log'))
    & $headless @arguments
    if ($LASTEXITCODE -ne 0) { throw "Ghidra focus exited with code $LASTEXITCODE; inspect $Output" }
    $reportPath = Join-Path $Output 'functions.json'
    if (-not (Test-Path -LiteralPath $reportPath)) { throw 'Ghidra did not produce functions.json.' }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.cancelled -or $report.image_base -ne '0x0') { throw 'Focus report is cancelled or has an unexpected image base.' }
    if ($report.executable_sha256 -ne (Get-FileHash -LiteralPath $Image -Algorithm SHA256).Hash.ToLowerInvariant()) { throw 'Input ELF hash mismatch.' }
    if ($report.source_nro_sha256 -ne (Get-FileHash -LiteralPath $Nro -Algorithm SHA256).Hash.ToLowerInvariant()) { throw 'Input NRO hash mismatch.' }
    $failed = @($report.decompilation | Where-Object { -not $_.completed })
    if ($failed.Count) { throw "$($failed.Count) focus target(s) failed; inspect $reportPath" }
    Write-Output "Bounded Ghidra pseudocode exported: $reportPath"
} finally {
    $env:JAVA_HOME = $oldJava
    $env:PATH = $oldPath
    $env:GHIDRA_HEADLESS_MAXMEM = $oldHeap
}
