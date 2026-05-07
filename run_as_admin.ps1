# ============================================================
# Council of Agents — PowerShell Launcher
# Auto-elevates to Administrator
# ============================================================

#Requires -Version 5.0

[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$Command = "",
    
    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)

# Function to check if running as Administrator
function Test-Admin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Auto-elevate if not Admin
if (-not (Test-Admin)) {
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Yellow
    Write-Host "  Council of Agents requires Administrator privileges" -ForegroundColor Yellow
    Write-Host "  مجلس الوكلاء يحتاج صلاحيات الإدارة" -ForegroundColor Yellow
    Write-Host "==========================================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Requesting elevation..." -ForegroundColor Cyan
    
    # Build argument list for elevated launch
    $argList = @("-NoExit", "-File", "`"$PSCommandPath`"")
    if ($Command) { $argList += "`"$Command`"" }
    if ($Arguments) { $argList += $Arguments }
    
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit
}

# Set window title
$Host.UI.RawUI.WindowTitle = "Council of Agents v0.2 - Administrator"

# Banner
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  🛡️  Council of Agents v0.2 - Running as Administrator  " -ForegroundColor Green
Write-Host "  مجلس الوكلاء - يعمل بصلاحيات الإدارة" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host ""

# Change to script directory
Set-Location -Path $PSScriptRoot

# Verify Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Python: $pythonVersion" -ForegroundColor Gray
} catch {
    Write-Host "ERROR: Python not found in PATH" -ForegroundColor Red
    Write-Host "Please install Python 3.11+ from https://python.org" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Verify Ollama is running (optional check)
try {
    $ollamaCheck = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
    if ($ollamaCheck.StatusCode -eq 200) {
        Write-Host "Ollama: ✓ running" -ForegroundColor Green
    }
} catch {
    Write-Host "Ollama: ✗ not detected (LLM features will fall back to heuristics)" -ForegroundColor Yellow
    Write-Host "  Install from https://ollama.com and run: ollama serve" -ForegroundColor Gray
}

Write-Host ""

# Run the command
if ([string]::IsNullOrEmpty($Command)) {
    python run.py
} else {
    python run.py $Command @Arguments
}

Write-Host ""
Read-Host "Press Enter to exit"
