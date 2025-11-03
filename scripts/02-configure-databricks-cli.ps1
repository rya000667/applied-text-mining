param(
    [ValidateSet('OIDC','ServicePrincipal','ManagedIdentity')]
    [string]$AuthMode = 'OIDC',
    [string]$DatabricksHost = $env:DATABRICKS_HOST,
    [string]$ServicePrincipalClientId = $env:SP_CLIENT_ID,
    [string]$ServicePrincipalClientSecret = $env:SP_CLIENT_SECRET,
    [string]$ServicePrincipalTenantId = $env:SP_TENANT_ID,
    [string]$ManagedIdentityClientId = $env:ARM_CLIENT_ID,
    [switch]$SkipCurrentUserValidation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    $timestamp = Get-Date -Format o
    Write-Host "[$timestamp][INFO] $Message"
}

function Get-DatabricksVersion {
    $output = & databricks -v
    if (-not $output) {
        throw 'Unable to determine Databricks CLI version. Ensure it is installed.'
    }
    $match = [System.Text.RegularExpressions.Regex]::Match($output, '(\d+\.\d+\.\d+)')
    if (-not $match.Success) {
        throw "Failed to parse Databricks CLI version from: $output"
    }
    return [Version]$match.Value
}

$minimumVersion = [Version]'0.218.0'
$cliVersion = Get-DatabricksVersion
if ($cliVersion -lt $minimumVersion) {
    throw "Databricks CLI $cliVersion detected. Version $minimumVersion or higher is required for Asset Bundles."
}
Write-Info "Databricks CLI version $cliVersion detected."

if ($DatabricksHost) {
    $env:DATABRICKS_HOST = $DatabricksHost
    Write-Info "DATABRICKS_HOST set for current session."
} else {
    Write-Info 'DATABRICKS_HOST is not set. Set it before running bundle commands (e.g., from templates/.env.example).'
}

switch ($AuthMode) {
    'OIDC' {
        Write-Info 'Auth mode: OIDC via Azure CLI / Workload Identity Federation.'
        Write-Info 'Expecting AzureCLI@2 task with useGlobalConfig: true to establish context via <AZDO_SERVICE_CONNECTION>.'
        # No additional configuration required; the CLI picks up Azure CLI tokens automatically.
    }
    'ServicePrincipal' {
        Write-Info 'Auth mode: Azure service principal client secret.'
        # Example environment variables (populate securely):
        #   $env:SP_CLIENT_ID = '<SP_CLIENT_ID>'
        #   $env:SP_CLIENT_SECRET = '<SP_CLIENT_SECRET>'
        #   $env:SP_TENANT_ID = '<SP_TENANT_ID>'
        if (-not $DatabricksHost) {
            throw 'Service Principal mode requires DATABRICKS_HOST to be provided (e.g., https://adb-xxxx.azuredatabricks.net).'
        }
        if (-not $ServicePrincipalClientId -or -not $ServicePrincipalClientSecret -or -not $ServicePrincipalTenantId) {
            throw 'Provide SP_CLIENT_ID, SP_CLIENT_SECRET, and SP_TENANT_ID via secure environment variables.'
        }

        $env:azure_client_id = $ServicePrincipalClientId
        $env:azure_client_secret = $ServicePrincipalClientSecret
        $env:azure_tenant_id = $ServicePrincipalTenantId

        Write-Info 'Exported azure_client_id / azure_client_secret / azure_tenant_id for Databricks CLI (process scope only).'
    }
    'ManagedIdentity' {
        Write-Info 'Auth mode: Azure Managed Identity.'
        # Example environment variables for managed identity scenarios:
        #   $env:ARM_USE_MSI = 'true'
        #   $env:ARM_CLIENT_ID = '<MI_CLIENT_ID>'  # optional user-assigned identity
        #   $env:DATABRICKS_AZURE_RESOURCE_ID = '<DATABRICKS_AZURE_RESOURCE_ID>'
        $env:ARM_USE_MSI = 'true'
        if ($ManagedIdentityClientId) {
            $env:ARM_CLIENT_ID = $ManagedIdentityClientId
            Write-Info 'User-assigned managed identity client ID exported.'
        }
        if (-not $env:DATABRICKS_AZURE_RESOURCE_ID -and -not $DatabricksHost) {
            Write-Info 'Set DATABRICKS_AZURE_RESOURCE_ID and/or DATABRICKS_HOST for the Databricks CLI to target the workspace.'
        }
    }
    default {
        throw "Unsupported AuthMode '$AuthMode'"
    }
}

if (-not $SkipCurrentUserValidation -and -not $env:TF_BUILD) {
    try {
        Write-Info 'Validating Databricks authentication with current-user me.'
        & databricks current-user me | Out-Null
        Write-Info 'Databricks authentication validated.'
    } catch {
        Write-Info "Databricks authentication check failed: $($_.Exception.Message)"
        Write-Info 'Ensure credentials are available before running bundle commands.'
    }
} else {
    Write-Info 'Skipping current-user validation (CI detected or SkipCurrentUserValidation specified).'
}
