# Fabric Activity Poller

This Azure Functions project polls the Microsoft Fabric / Power BI Admin **Get Activity Events** API at the tenant scope and publishes each event to Azure Event Hubs for downstream ingestion (e.g., Splunk). A timer trigger drives the polling loop, persisting crash-safe checkpoints in Azure Blob Storage to deliver at-least-once semantics.

## Features

- **Timer trigger** with CRON schedule supplied via `TIMER_SCHEDULE`.
- **Service Principal authentication** using MSAL and a secret stored in Azure Key Vault, retrieved with the Function's Managed Identity.
- **Page-by-page ingestion** with support for both `continuationUri` and `continuationToken` pagination patterns.
- **At-least-once delivery** with day-level replay and Event Hubs idempotent partitions. Event IDs are attached to `EventData.properties` for downstream deduplication.
- **Crash-safe checkpointing** in Blob Storage with the mandated three-field schema: `lastEnd`, `resumeFrom`, `continuationUri`.
- **Structured logging** for metrics, retries, and checkpoint transitions.
- **Graceful shutdown** that finishes the in-flight page before exiting on `SIGTERM`.

## Project layout

```
ActivityPoller/__init__.py          # Timer trigger entry point
ActivityPoller/pbi_client.py        # Fabric Admin API client with retries & continuation support
ActivityPoller/state_store.py       # Checkpoint persistence helpers and day math utilities
ActivityPoller/publisher.py         # Event Hubs producer with batching and envelope logic
ActivityPoller/logging_util.py      # Structured logging helpers and metrics container
function.json                       # Timer trigger binding definition
host.json                           # Host configuration
requirements.txt                    # Python dependencies
local.settings.json.example         # Sample local configuration (no secrets)
README.md                           # This guide
```

## Prerequisites

- Python 3.11
- Azure CLI (for local identity testing)
- Access to an Azure subscription with the following resources:
  - Azure Key Vault containing the Service Principal client secret
  - Azure Storage account (Blob) for checkpointing
  - Azure Event Hubs namespace with a target hub for Fabric events
- The Function App's **system-assigned managed identity** needs these roles:
  - Key Vault: `Key Vault Secrets User` (or `Get` permission via access policy)
  - Storage account: `Storage Blob Data Contributor` scoped to the checkpoint container
  - Event Hubs: `Azure Event Hubs Data Sender` scoped to the target hub

The Service Principal used for the Fabric Admin API must have the Power BI admin permissions required for tenant-wide activity event access.

## Configuration

All runtime configuration is supplied via environment variables:

| Variable | Description |
| --- | --- |
| `TENANT_ID` | Entra ID tenant for MSAL authority and scope |
| `CLIENT_ID` | Service Principal application (client) ID |
| `KEYVAULT_URL` | Key Vault base URI (e.g., `https://mykv.vault.azure.net`) |
| `KEYVAULT_SECRET_NAME` | Secret name holding the SP client secret |
| `STORAGE_ACCOUNT_URL` | Storage account blob endpoint URL |
| `STATE_CONTAINER` | Blob container holding checkpoints |
| `EVENTHUB_FQDN` | Event Hubs namespace FQDN (e.g., `myns.servicebus.windows.net`) |
| `EVENTHUB_NAME` | Event Hub name |
| `TIMER_SCHEDULE` | NCRONTAB expression for the timer trigger |
| `OVERLAP_SECONDS` | (Optional) Overlap to replay on resume, default `60` |
| `REQUEST_TIMEOUT_SECONDS` | (Optional) HTTP timeout for Fabric Admin API calls, default `30` |
| `MAX_RETRIES` | (Optional) Maximum retry attempts for Fabric Admin API calls, default `5` |

Copy `local.settings.json.example` to `local.settings.json` and fill in local values for development. Secrets should be stored in user secrets or environment variables, not in source control.

## Local development

1. Create and activate a Python 3.11 virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Sign in with the Azure CLI so the managed identity substitute (DefaultAzureCredential) can obtain tokens:

   ```bash
   az login
   ```

4. Populate `local.settings.json` with test values. When running locally, the Function uses developer credentials instead of the managed identity to reach Key Vault, Storage, and Event Hubs.

5. Start the Functions host:

   ```bash
   func start
   ```

## Checkpoint schema

Checkpoints live in `state/fabric/{TENANT_ID}.json` within the container specified by `STATE_CONTAINER`. The JSON document always conforms to:

```json
{
  "lastEnd": "ISO-8601 UTC string or null",
  "resumeFrom": "ISO-8601 UTC string or null",
  "continuationUri": "string or null"
}
```

- `lastEnd` updates only after the poller publishes all events through `utcNow()` at the end of a run.
- `resumeFrom` tracks the UTC midnight for the day currently being processed.
- `continuationUri` stores the Fabric Admin continuation URL to resume within the current day slice.

Updates use blob ETags for optimistic concurrency to ensure a single logical writer.

## Testing

Unit tests cover day slicing, resume logic, and checkpoint concurrency behavior. Run them with:

```bash
pytest
```

Mocks isolate Azure dependencies so tests run without real cloud services.

## Deployment

1. Create an Azure Function App configured for Python 3.11.
2. Assign the managed identity and grant the permissions listed above.
3. Configure the environment variables (App Settings) listed in the **Configuration** section.
4. Deploy the function (e.g., `func azure functionapp publish <app-name>` or CI/CD).

Monitor Application Insights or Azure Monitor logs for `run.metrics`, `checkpoint.update`, and retry events to verify healthy ingestion.

