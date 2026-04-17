<#
.SYNOPSIS
    Sets up a new versioned OpenStudio model working directory.

.DESCRIPTION
    Creates the directory structure for a new model version, copies the seed
    model and weather file, and sets up the measures directory. Handles Windows
    paths with spaces correctly (the reason this is PowerShell, not bash).

.PARAMETER SeedModel
    Path to the seed .osm file to copy as the new version.

.PARAMETER NewVersion
    Version identifier for the new model (e.g., "v10", "v11").
    The new filename will replace the last version segment in the seed filename.

.PARAMETER ProjectDir
    Parent directory where the new version directory will be created.
    Defaults to the directory containing the seed model.

.PARAMETER WeatherFile
    Path to the .epw weather file. If not specified, searches for *.epw
    in the seed model's directory.

.PARAMETER MeasuresSource
    Path to an existing measures directory to copy. Optional.
    Only copies measure directories that don't have Windows long-path issues.

.EXAMPLE
    .\setup-model-version.ps1 -SeedModel "C:\path\to\model_v9.osm" -NewVersion "v10"

.EXAMPLE
    .\setup-model-version.ps1 -SeedModel ".\model_v9.osm" -NewVersion "v10" -WeatherFile ".\weather.epw"

.NOTES
    Part of the running-openstudio-models skill toolset.
    Python: N/A (PowerShell script)
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$SeedModel,

    [Parameter(Mandatory=$true)]
    [string]$NewVersion,

    [string]$ProjectDir,

    [string]$WeatherFile,

    [string]$MeasuresSource
)

# Resolve seed model path
$SeedModel = Resolve-Path $SeedModel -ErrorAction Stop
$seedDir = Split-Path $SeedModel -Parent
$seedName = [System.IO.Path]::GetFileNameWithoutExtension($SeedModel)

# Determine project directory
if (-not $ProjectDir) {
    $ProjectDir = $seedDir
}
$ProjectDir = Resolve-Path $ProjectDir -ErrorAction Stop

# Generate new model name by replacing last version segment
# Pattern: anything_vN → anything_<NewVersion>
if ($seedName -match '^(.+_)(v\d+)$') {
    $newName = $Matches[1] + $NewVersion
} else {
    $newName = "${seedName}_${NewVersion}"
}

$workDir = Join-Path $ProjectDir $newName
$newModelPath = Join-Path $workDir "${newName}.osm"

Write-Host "=== Setup Model Version ===" -ForegroundColor Cyan
Write-Host "  Seed:      $SeedModel"
Write-Host "  New Model: $newModelPath"
Write-Host "  Work Dir:  $workDir"

# Create directory structure
$dirs = @(
    $workDir,
    (Join-Path $workDir 'measures'),
    (Join-Path $workDir 'files')
)
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Green
    }
}

# Copy seed model
Copy-Item $SeedModel $newModelPath -Force
Write-Host "  Copied:  seed model -> $newModelPath" -ForegroundColor Green

# Find and copy weather file
if (-not $WeatherFile) {
    $epwFiles = Get-ChildItem $seedDir -Filter '*.epw' -ErrorAction SilentlyContinue
    if ($epwFiles.Count -eq 1) {
        $WeatherFile = $epwFiles[0].FullName
        Write-Host "  Auto-detected weather file: $WeatherFile" -ForegroundColor Yellow
    } elseif ($epwFiles.Count -gt 1) {
        Write-Host "  WARNING: Multiple .epw files found. Specify -WeatherFile." -ForegroundColor Red
        $WeatherFile = $epwFiles[0].FullName
        Write-Host "  Using first: $WeatherFile" -ForegroundColor Yellow
    } else {
        Write-Host "  WARNING: No .epw file found in $seedDir" -ForegroundColor Red
    }
}

if ($WeatherFile -and (Test-Path $WeatherFile)) {
    $epwName = Split-Path $WeatherFile -Leaf
    # Copy to both root and files/ (OpenStudio checks both)
    Copy-Item $WeatherFile (Join-Path $workDir $epwName) -Force
    Copy-Item $WeatherFile (Join-Path $workDir "files\$epwName") -Force
    Write-Host "  Copied:  weather file -> $epwName" -ForegroundColor Green
}

# Copy measures if source specified
if ($MeasuresSource -and (Test-Path $MeasuresSource)) {
    $measureDirs = Get-ChildItem $MeasuresSource -Directory
    foreach ($mdir in $measureDirs) {
        $destMeasure = Join-Path $workDir "measures\$($mdir.Name)"
        try {
            Copy-Item $mdir.FullName $destMeasure -Recurse -Force -ErrorAction Stop
            Write-Host "  Copied:  measure $($mdir.Name)" -ForegroundColor Green
        } catch {
            Write-Host "  SKIPPED: measure $($mdir.Name) (likely long path issue)" -ForegroundColor Yellow
        }
    }
}

# Generate default workflow.osw
$epwFileName = if ($WeatherFile) { Split-Path $WeatherFile -Leaf } else { "WEATHER_FILE_NEEDED.epw" }
$workflow = @{
    seed_file = "${newName}.osm"
    weather_file = $epwFileName
    measure_paths = @("measures")
    file_paths = @("files")
    steps = @()
} | ConvertTo-Json -Depth 3

$workflowPath = Join-Path $workDir 'workflow.osw'
Set-Content -Path $workflowPath -Value $workflow -Encoding UTF8
Write-Host "  Created: workflow.osw - add measures to steps before running" -ForegroundColor Green

# Summary
Write-Host ""
Write-Host "=== Ready ===" -ForegroundColor Cyan
Write-Host "  Working directory: $workDir"
Write-Host "  Model file:       ${newName}.osm"
Write-Host "  Workflow:          workflow.osw"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Add measures to measures\ directory"
Write-Host "  2. Update workflow.osw steps array"
Write-Host "  3. Run: C:\openstudio-3.10.0\bin\openstudio.exe run --workflow workflow.osw"
