Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    $timestamp = Get-Date -Format o
    Write-Host "[$timestamp][INFO] $Message"
}

$pluginCacheDir = if ($env:TF_PLUGIN_CACHE_DIR) { $env:TF_PLUGIN_CACHE_DIR } else { 'C:\tf-plugin-cache' }
if (-not (Test-Path $pluginCacheDir)) {
    New-Item -ItemType Directory -Path $pluginCacheDir -Force | Out-Null
    Write-Info "Created Terraform plugin cache at $pluginCacheDir"
} else {
    Write-Info "Terraform plugin cache already exists at $pluginCacheDir"
}

[Environment]::SetEnvironmentVariable('TF_PLUGIN_CACHE_DIR', $pluginCacheDir, 'Process')
Write-Info "TF_PLUGIN_CACHE_DIR exported for current session."
if ($env:TF_BUILD) {
    Write-Host "##vso[task.setvariable variable=TF_PLUGIN_CACHE_DIR]$pluginCacheDir"
}

$terraformDir = Join-Path (Get-Location) 'terraform'
if (-not (Test-Path $terraformDir)) {
    New-Item -ItemType Directory -Path $terraformDir -Force | Out-Null
    Write-Info "Created terraform directory at $terraformDir"
}

# Example backend variables (set via environment or pipeline variables, never in source control):
#   $env:AZ_SUBSCRIPTION_ID = '<AZ_SUBSCRIPTION_ID>'
#   $env:AZ_RESOURCE_GROUP_FOR_STATE = '<AZ_RESOURCE_GROUP_FOR_STATE>'
#   $env:AZ_STORAGE_ACCOUNT_FOR_STATE = '<AZ_STORAGE_ACCOUNT_FOR_STATE>'
#   $env:AZ_STORAGE_CONTAINER_FOR_STATE = '<AZ_STORAGE_CONTAINER_FOR_STATE>'
#   $env:TF_STATE_KEY = '<TF_STATE_KEY>'

$providersPath = Join-Path $terraformDir 'providers.tf'
if (-not (Test-Path $providersPath)) {
    @'
terraform {
  required_version = ">= 1.13.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}
'@ | Set-Content -Path $providersPath -Encoding UTF8
    Write-Info "Scaffolded $providersPath"
} else {
    Write-Info "providers.tf already present; leaving unchanged."
}

$backendExamplePath = Join-Path $terraformDir 'backend.azurerm.hcl.example'
if (-not (Test-Path $backendExamplePath)) {
    @'
subscription_id     = "<AZ_SUBSCRIPTION_ID>"
resource_group_name = "<AZ_RESOURCE_GROUP_FOR_STATE>"
storage_account_name= "<AZ_STORAGE_ACCOUNT_FOR_STATE>"
container_name      = "<AZ_STORAGE_CONTAINER_FOR_STATE>"
key                 = "<TF_STATE_KEY>"
'@ | Set-Content -Path $backendExamplePath -Encoding UTF8
    Write-Info "Scaffolded backend example at $backendExamplePath"
} else {
    Write-Info "backend.azurerm.hcl.example already exists; leaving unchanged."
}

$terraformRcPath = Join-Path $env:USERPROFILE '.terraformrc'
if (-not (Test-Path $terraformRcPath)) {
    "plugin_cache_dir = \"$pluginCacheDir\"" | Set-Content -Path $terraformRcPath -Encoding UTF8
    Write-Info "Created $terraformRcPath referencing plugin cache."
} else {
    $content = Get-Content -Path $terraformRcPath -Raw
    if ($content -notmatch 'plugin_cache_dir') {
        $backupPath = "$terraformRcPath.bak_$(Get-Date -Format yyyyMMddHHmmss)"
        Copy-Item -Path $terraformRcPath -Destination $backupPath -Force
        Write-Info "Existing .terraformrc detected. Backup created at $backupPath."
        Add-Content -Path $terraformRcPath -Value "`nplugin_cache_dir = \"$pluginCacheDir\"" -Encoding UTF8
        Write-Info "Appended plugin_cache_dir to existing .terraformrc."
    } else {
        Write-Info ".terraformrc already references a plugin cache; no changes made."
    }
}

Write-Info "Next steps: populate backend.azurerm.hcl or pass -backend-config values using secure Azure DevOps variables."
Write-Info "Reminder: export ARM_*, DATABRICKS_* env vars via Azure DevOps secret variables rather than committing secrets."
