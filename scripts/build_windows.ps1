param(
    [string]$Python = "py",
    [string[]]$PythonArgs = @("-3.12")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "TicketPilot muss für Windows auf einem Windows-System gebaut werden."
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$ArgumentList = @()
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Befehl fehlgeschlagen (Exitcode $LASTEXITCODE): $FilePath $($ArgumentList -join ' ')"
    }
}

$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$VenvPath = Join-Path $ProjectRoot ".venv-build"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$ExecutablePath = Join-Path $ProjectRoot "dist\TicketPilot\TicketPilot.exe"
$BuildManifestPath = Join-Path $ProjectRoot "dist\TicketPilot-build-requirements.txt"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    $CreateVenvArgs = @($PythonArgs) + @("-m", "venv", $VenvPath)
    Invoke-NativeCommand -FilePath $Python -ArgumentList $CreateVenvArgs
}

Push-Location $ProjectRoot
try {
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "-e", ".[dev]")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "check")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "pytest")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "ruff", "check", "src", "tests")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "mypy", "src/ticketpilot")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "compileall", "-q", "src")
    Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "PyInstaller", "--noconfirm", "--clean", "TicketPilot.spec")

    if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        throw "PyInstaller meldete Erfolg, aber TicketPilot.exe wurde nicht gefunden: $ExecutablePath"
    }

    $ResolvedDependencies = @(
        Invoke-NativeCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "freeze", "--all")
    )
    $ResolvedDependencies | Set-Content -LiteralPath $BuildManifestPath -Encoding UTF8
}
finally {
    Pop-Location
}

Write-Host "Build abgeschlossen: $ExecutablePath"
Write-Host "Abhängigkeiten protokolliert: $BuildManifestPath"
Write-Host "Für die Weitergabe wird der vollständige Ordner dist\TicketPilot benötigt."
