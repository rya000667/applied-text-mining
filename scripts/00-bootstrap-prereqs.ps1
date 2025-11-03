Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','WARN','ERROR')][string]$Level = 'INFO'
    )
    $timestamp = Get-Date -Format o
    Write-Host "[$timestamp][$Level] $Message"
}

function Assert-Administrator {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '00-bootstrap-prereqs.ps1 must be run from an elevated PowerShell session.'
    }
}

function Ensure-Tls12 {
    try {
        $protocols = [Net.ServicePointManager]::SecurityProtocol
        if (($protocols -band [Net.SecurityProtocolType]::Tls12) -eq 0) {
            [Net.ServicePointManager]::SecurityProtocol = $protocols -bor [Net.SecurityProtocolType]::Tls12
        }
    } catch {
        Write-Log -Level 'WARN' -Message "Unable to set TLS 1.2 explicitly: $($_.Exception.Message)"
    }
}

function Invoke-WithRetry {
    param(
        [Parameter(Mandatory)][ScriptBlock]$ScriptBlock,
        [int]$MaxRetries = 3,
        [int]$InitialDelaySeconds = 2,
        [object[]]$ArgumentList = @()
    )

    $attempt = 0
    $delay = $InitialDelaySeconds
    while ($true) {
        try {
            $attempt++
            return & $ScriptBlock @ArgumentList
        } catch {
            if ($attempt -ge $MaxRetries) {
                throw
            }
            Write-Log -Level 'WARN' -Message "Attempt $attempt failed: $($_.Exception.Message). Retrying in $delay seconds."
            Start-Sleep -Seconds $delay
            $delay *= 2
        }
    }
}

function Ensure-ExecutionPolicy {
    try {
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
    } catch {
        Write-Log -Level 'WARN' -Message "Failed to set execution policy: $($_.Exception.Message)"
    }
}

function Ensure-Chocolatey {
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Log -Message 'Chocolatey already installed.'
        return
    }

    Write-Log -Message 'Installing Chocolatey...'
    $installScript = {
        Set-ExecutionPolicy Bypass -Scope Process -Force
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        $script = Invoke-RestMethod -Uri 'https://community.chocolatey.org/install.ps1'
        Invoke-Expression $script
    }
    Invoke-WithRetry -ScriptBlock $installScript

    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        throw 'Chocolatey installation failed. Ensure internet connectivity and rerun.'
    }
}

function Install-ChocoPackage {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$Version,
        [string[]]$Params
    )

    $chocoArgs = @('install', $Name, '-y', '--no-progress')
    if ($Version) {
        $chocoArgs += @('--version', $Version)
    }
    if ($Params) {
        $chocoArgs += $Params
    }

    Invoke-WithRetry -ScriptBlock { param($argsToUse) choco @argsToUse } -ArgumentList (, $chocoArgs)
}

function Ensure-ProgramInPath {
    param(
        [Parameter(Mandatory)][string]$PathToAdd
    )

    $currentMachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if (-not $currentMachinePath.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries) -contains $PathToAdd) {
        $newPath = if ([string]::IsNullOrEmpty($currentMachinePath)) { $PathToAdd } else { $currentMachinePath + ';' + $PathToAdd }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'Machine')
        Write-Log -Message "Added $PathToAdd to machine PATH."
    }

    $currentProcessPath = [Environment]::GetEnvironmentVariable('Path', 'Process')
    if (-not $currentProcessPath.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries) -contains $PathToAdd) {
        $newProcessPath = if ([string]::IsNullOrEmpty($currentProcessPath)) { $PathToAdd } else { $currentProcessPath + ';' + $PathToAdd }
        [Environment]::SetEnvironmentVariable('Path', $newProcessPath, 'Process')
    }

    if ($env:ChocolateyInstall) {
        $refreshPs = Join-Path $env:ChocolateyInstall 'bin\RefreshEnv.ps1'
        if (Test-Path $refreshPs) {
            & $refreshPs | Out-Null
        }
        $refreshCmd = Join-Path $env:ChocolateyInstall 'bin\RefreshEnv.cmd'
        if (Test-Path $refreshCmd) {
            & $refreshCmd | Out-Null
        }
    }

    if ($env:TF_BUILD) {
        Write-Host "##vso[task.prependpath]$PathToAdd"
    }
}

function Install-AzureCLI {
    if (Get-Command az -ErrorAction SilentlyContinue) {
        Write-Log -Message 'Azure CLI already installed.'
        return
    }
    Write-Log -Message 'Installing Azure CLI via Chocolatey.'
    Install-ChocoPackage -Name 'azure-cli'
}

function Install-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Log -Message 'Git already installed.'
        return
    }
    Write-Log -Message 'Installing Git via Chocolatey.'
    Install-ChocoPackage -Name 'git'
}

function Install-Terraform {
    param([string]$Version = '1.13.7')

    $terraform = Get-Command terraform -ErrorAction SilentlyContinue
    if ($terraform) {
        $currentVersionString = (& terraform -version | Select-Object -First 1) -replace 'Terraform v', ''
        $currentVersion = [Version]$currentVersionString
        if ($currentVersion.Major -eq 1 -and $currentVersion.Minor -eq 13) {
            Write-Log -Message "Terraform $currentVersionString already installed."
            return
        }
        Write-Log -Level 'WARN' -Message "Terraform version $currentVersionString does not match required 1.13.x; reinstalling."
    }

    Write-Log -Message "Installing Terraform $Version via Chocolatey."
    Install-ChocoPackage -Name 'terraform' -Version $Version
}

function Install-DatabricksCli {
    $minimumVersion = [Version]'0.218.0'
    # Databricks CLI >= 0.218.0 is required for Databricks Asset Bundles (see https://docs.databricks.com/dev-tools/cli/index.html).
    $existing = Get-Command databricks -ErrorAction SilentlyContinue
    if ($existing) {
        $current = Get-DatabricksVersion
        if ($current -ge $minimumVersion) {
            Write-Log -Message "Databricks CLI $current already installed."
            return
        }
        Write-Log -Level 'WARN' -Message "Databricks CLI version $current is lower than required $minimumVersion; reinstalling."
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            Write-Log -Message 'Installing Databricks CLI via winget.'
            Invoke-WithRetry -ScriptBlock { winget install --id Databricks.DatabricksCLI -e --accept-package-agreements --accept-source-agreements }
            Ensure-ProgramInPath "$env:ProgramFiles\Databricks\bin"
            $installed = Get-DatabricksVersion
            if ($installed -ge $minimumVersion) { return }
            Write-Log -Level 'WARN' -Message "Winget installed Databricks CLI version $installed which is below required $minimumVersion."
        } catch {
            Write-Log -Level 'WARN' -Message "Winget installation failed: $($_.Exception.Message)"
        }
    }

    try {
        Write-Log -Message 'Installing Databricks CLI via Chocolatey (experimental package).'
        Install-ChocoPackage -Name 'databricks-cli'
        Ensure-ProgramInPath "$env:ProgramFiles\Databricks\bin"
        $installed = Get-DatabricksVersion
        if ($installed -ge $minimumVersion) { return }
        Write-Log -Level 'WARN' -Message "Chocolatey package provided Databricks CLI version $installed which is below required $minimumVersion."
    } catch {
        Write-Log -Level 'WARN' -Message "Chocolatey Databricks CLI install failed: $($_.Exception.Message)"
    }

    Write-Log -Message 'Falling back to direct download of Databricks CLI binary.'
    $release = Get-LatestDatabricksRelease
    $version = [Version]($release.tag_name.TrimStart('v'))
    if ($version -lt $minimumVersion) {
        throw "Latest Databricks CLI release $version is below required version $minimumVersion."
    }

    $downloadUrl = $release.assets | Where-Object { $_.name -like '*windows_amd64-signed.zip' } | Select-Object -First 1 -ExpandProperty browser_download_url
    if (-not $downloadUrl) {
        throw 'Unable to locate signed Windows amd64 asset in release metadata.'
    }

    $installRoot = Join-Path $env:ProgramFiles 'Databricks'
    $binDir = Join-Path $installRoot 'bin'
    if (-not (Test-Path $binDir)) {
        New-Item -Path $binDir -ItemType Directory -Force | Out-Null
    }

    $tempZip = Join-Path $env:TEMP "databricks_cli_$($version.ToString()).zip"
    Invoke-WithRetry -ScriptBlock { param($uri, $destination) Invoke-WebRequest -Uri $uri -OutFile $destination } -ArgumentList @($downloadUrl, $tempZip)
    Expand-Archive -LiteralPath $tempZip -DestinationPath $binDir -Force
    Remove-Item $tempZip -Force

    Ensure-ProgramInPath $binDir
    $installedVersion = Get-DatabricksVersion
    Write-Log -Message "Installed Databricks CLI version $installedVersion from GitHub release."
}

function Get-LatestDatabricksRelease {
    $headers = @{ 'User-Agent' = 'DatabricksCLIInstaller/1.0' }
    $uri = 'https://api.github.com/repos/databricks/cli/releases/latest'
    $scriptBlock = {
        param($requestUri, $requestHeaders)
        Invoke-RestMethod -Uri $requestUri -Headers $requestHeaders
    }
    return Invoke-WithRetry -ScriptBlock $scriptBlock -ArgumentList @($uri, $headers)
}

function Get-DatabricksVersion {
    $output = & databricks -v
    if (-not $output) {
        throw 'Failed to get Databricks CLI version.'
    }
    $match = [System.Text.RegularExpressions.Regex]::Match($output, '(\d+\.\d+\.\d+)')
    if (-not $match.Success) {
        throw "Unable to parse Databricks CLI version from output: $output"
    }
    return [Version]$match.Value
}

function Ensure-TerraformPluginCache {
    $pluginCacheDir = if ($env:TF_PLUGIN_CACHE_DIR) { $env:TF_PLUGIN_CACHE_DIR } else { 'C:\tf-plugin-cache' }
    if (-not (Test-Path $pluginCacheDir)) {
        New-Item -Path $pluginCacheDir -ItemType Directory -Force | Out-Null
        Write-Log -Message "Created Terraform plugin cache directory at $pluginCacheDir."
    }
    [Environment]::SetEnvironmentVariable('TF_PLUGIN_CACHE_DIR', $pluginCacheDir, 'Machine')
    [Environment]::SetEnvironmentVariable('TF_PLUGIN_CACHE_DIR', $pluginCacheDir, 'Process')
    if ($env:TF_BUILD) {
        Write-Host "##vso[task.setvariable variable=TF_PLUGIN_CACHE_DIR]$pluginCacheDir"
    }
}

function Print-Versions {
    Write-Log -Message "PowerShell version: $($PSVersionTable.PSVersion)"
    & az --version | Select-Object -First 1 | ForEach-Object { Write-Log -Message "Azure CLI version: $_" }

    $terraformVersionLine = (& terraform -version | Select-Object -First 1)
    Write-Log -Message $terraformVersionLine
    $terraformVersion = [Version]($terraformVersionLine -replace 'Terraform v', '')
    if ($terraformVersion -lt [Version]'1.13.0') {
        throw "Terraform version $terraformVersion is below the required 1.13.0."
    }

    $gitVersionLine = (& git --version)
    Write-Log -Message $gitVersionLine

    $databricksVersion = Get-DatabricksVersion
    if ($databricksVersion -lt [Version]'0.218.0') {
        throw "Databricks CLI version $databricksVersion is below the required 0.218.0."
    }
    Write-Log -Message "Databricks CLI version: $databricksVersion"
}

Assert-Administrator
Ensure-Tls12
Ensure-ExecutionPolicy
Ensure-Chocolatey
Install-AzureCLI
Install-Git
Install-Terraform
Install-DatabricksCli
Ensure-ProgramInPath "$env:ProgramFiles\Databricks\bin"
Ensure-TerraformPluginCache
Print-Versions

Write-Log -Message 'Bootstrap complete.'
