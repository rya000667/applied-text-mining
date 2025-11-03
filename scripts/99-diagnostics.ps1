Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    $timestamp = Get-Date -Format o
    Write-Host "[$timestamp][INFO] $Message"
}

function Mask-Value {
    param([string]$Value)
    if ([string]::IsNullOrEmpty($Value)) {
        return '<empty>'
    }
    if ($Value.Length -le 4) {
        return '****'
    }
    return ($Value.Substring(0, 2) + '****' + $Value.Substring($Value.Length - 2))
}

Write-Info "OS: $([System.Environment]::OSVersion.VersionString)"
Write-Info "CPU Architecture: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"
Write-Info "PowerShell version: $($PSVersionTable.PSVersion)"

try {
    & az --version | Select-Object -First 1 | ForEach-Object { Write-Info "Azure CLI version: $_" }
} catch {
    Write-Info "Azure CLI version unavailable: $($_.Exception.Message)"
}

try {
    $tfLine = (& terraform -version | Select-Object -First 1)
    Write-Info $tfLine
} catch {
    Write-Info "Terraform not available: $($_.Exception.Message)"
}

try {
    $dbVersion = & databricks -v
    Write-Info "Databricks CLI: $dbVersion"
} catch {
    Write-Info "Databricks CLI not available: $($_.Exception.Message)"
}

try {
    $gitVersion = & git --version
    Write-Info $gitVersion
} catch {
    Write-Info "Git not available: $($_.Exception.Message)"
}

$envVars = @(
    'TF_PLUGIN_CACHE_DIR',
    'ARM_CLIENT_ID',
    'ARM_TENANT_ID',
    'ARM_USE_MSI',
    'DATABRICKS_HOST',
    'DATABRICKS_AZURE_RESOURCE_ID',
    'TF_BUILD'
)

foreach ($name in $envVars) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ($value) {
        Write-Info "ENV::$name=$(Mask-Value -Value $value)"
    } else {
        Write-Info "ENV::$name=<not set>"
    }
}

$bundleFile = Join-Path (Get-Location) 'databricks.yml'
if (Test-Path $bundleFile) {
    Write-Info 'databricks.yml detected. Attempting bundle validate (non-fatal).'
    try {
        & databricks bundle validate
    } catch {
        Write-Info "databricks bundle validate failed: $($_.Exception.Message)"
    }
} else {
    Write-Info 'databricks.yml not found in current directory.'
}
