# Applied Text Mining DevOps Bootstrap

This repository now includes Azure DevOps automation for deploying Terraform infrastructure and Databricks Asset Bundles from a Windows self-hosted agent.

## Getting Started

1. **Bootstrap the agent once**
   - Sign in to the Azure Marketplace VM (SQL Server 2022 on Windows Server 2022).
   - Launch **PowerShell 7 as Administrator** and run:
     ```powershell
     scripts\00-bootstrap-prereqs.ps1
     ```
   - The script installs Azure CLI, Terraform 1.13.x, Git for Windows, and the dependency-free Databricks CLI (>= 0.218.0). It also prepares the Terraform plugin cache at `C:\tf-plugin-cache`.

2. **Configure Terraform scaffolding**
   - From any PowerShell 7 session on the agent, run:
     ```powershell
     scripts\01-configure-terraform.ps1
     ```
   - Populate the generated `terraform/backend.azurerm.hcl.example` using secure Azure DevOps variables or local environment variables sourced from `templates/.env.example`.

3. **Databricks CLI authentication**
   - Prefer **Azure DevOps OIDC** service connections with `useGlobalConfig: true` in `AzureCLI@2` tasks. The Databricks CLI can reuse the resulting Azure CLI token for Asset Bundle commands.
   - Service principal secrets remain supported by exporting `SP_CLIENT_ID`, `SP_CLIENT_SECRET`, and `SP_TENANT_ID` via secure pipeline variables only.
   - For managed identity flows, ensure the VM identity has Contributor rights on the Databricks workspace and Azure resources, then export `ARM_USE_MSI=true`, optional `ARM_CLIENT_ID`, and `DATABRICKS_AZURE_RESOURCE_ID` before running bundle commands.

4. **Diagnostics**
   - Run `scripts\99-diagnostics.ps1` to capture versions, environment configuration, and validate Databricks bundles when debugging the agent.

## Azure Pipelines

`azure-pipelines.yml` defines three stages:

1. **Setup**: Idempotently bootstraps the agent tooling if Databricks CLI is missing.
2. **Terraform**: Runs Terraform `init`, `plan`, and conditionally `apply` (skipped on pull requests) via `AzureCLI@2` using OIDC.
3. **DatabricksBundles**: Validates, plans, and deploys the sample Databricks bundle using the new CLI. Commented examples show alternative authentication flows for service principals and managed identities.

Configure the required variables (`AZ_SUBSCRIPTION_ID`, storage backend details, Databricks workspace info, etc.) as secure pipeline variables or library secrets before running the pipeline.

## Sample Databricks Bundle

A minimal `databricks.yml` is included with a `hello_job` notebook at `notebooks/hello.py`. Update the bundle definition to match your workspace resources before deploying to non-development environments.
