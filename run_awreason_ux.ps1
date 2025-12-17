# PowerShell script to start AWReason Frontend
# Run this script from the root folder of the project

Write-Host "Starting AWReason Frontend..." -ForegroundColor Green

# Get the script's directory (root folder)
$rootDir = $PSScriptRoot
if (-not $rootDir) {
    $rootDir = Get-Location
}

# Set the path to the o1-assessment directory
$o1AssessmentDir = Join-Path $rootDir "o1-assessment"

# Check if the directory exists
if (-not (Test-Path $o1AssessmentDir)) {
    Write-Host "ERROR: o1-assessment directory not found at $o1AssessmentDir" -ForegroundColor Red
    exit 1
}

# Change to the o1-assessment directory
Set-Location $o1AssessmentDir

# Check for and activate virtual environment
$venvPath = Join-Path $rootDir ".venv"
$venvActivate = Join-Path $venvPath "Scripts\Activate.ps1"

if (Test-Path $venvActivate) {
    Write-Host "Found virtual environment. Activating..." -ForegroundColor Cyan
    & $venvActivate
    Write-Host "Virtual environment activated: $venvPath" -ForegroundColor Green
} else {
    Write-Host "No virtual environment found at $venvPath" -ForegroundColor Yellow
    Write-Host "Using system Python installation..." -ForegroundColor Yellow
}

# Check if Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Using Python: $pythonVersion" -ForegroundColor Cyan
} catch {
    Write-Host "ERROR: Python not found. Please install Python first." -ForegroundColor Red
    exit 1
}

# Check if Streamlit is installed
Write-Host "Checking for Streamlit..." -ForegroundColor Cyan
$streamlitCheck = python -m pip show streamlit 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Streamlit not found. Installing..." -ForegroundColor Yellow
    python -m pip install streamlit
}

# Start the frontend using the run_frontend.py script
Write-Host "`nLaunching Streamlit application..." -ForegroundColor Green
Write-Host "The application will open in your default browser." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop the server.`n" -ForegroundColor Yellow

python run_frontend.py
