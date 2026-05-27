# Queue Worker Deployment Notes

This folder reconstructs the missing Azure Container Apps manifest for the Service Bus worker in [wrappers/queue-worker/main.py](../../queue-worker/main.py).

## What Exists In This Repo

The worker deployment surface is split across these files:

- [docker/worker.Dockerfile](../../../docker/worker.Dockerfile): builds the `awreason-queue-worker` image.
- [runtime/config.py](../../../runtime/config.py): authoritative list of worker env vars.
- [wrappers/queue-worker/servicebus_io.py](../../queue-worker/servicebus_io.py): Service Bus receiver/sender using `DefaultAzureCredential`.
- [engine_core/blob_io.py](../../../engine_core/blob_io.py): Blob Storage access using `DefaultAzureCredential`.
- [wrappers/queue-worker/platform_client.py](../../queue-worker/platform_client.py): optional HTTP callback to the platform.
- [wrappers/http-service/deploy](../../http-service/deploy): the only existing checked-in Azure deployment assets before this folder was added.

The top-level [infra](../../../infra) folder does not provision the queue worker. It contains evaluation and RAG utilities, not Container Apps or Service Bus deployment code.

## Azure Resource Map

The worker depends on these Azure resources:

| Resource | Required | Used by | Notes |
|---|---|---|---|
| Azure Container Registry | Yes | Container Apps | Stores the `awreason-queue-worker` image. |
| Azure Container Apps Environment | Yes | Container Apps | Existing managed environment that hosts the app. |
| User-Assigned Managed Identity | Yes | Runtime + KEDA + ACR pull | Attached to the Container App and referenced by the KEDA scale rule. |
| Azure Service Bus namespace + inbound queue | Yes | Worker receive loop + KEDA | `SB_NAMESPACE` and `SB_QUEUE`. KEDA scales from queue depth. |
| Azure Service Bus results queue | Conditional | Result reporting | Needed only when `REPORT_MODE=servicebus`. |
| Azure Blob Storage account | Yes | Input download + artifact upload + idempotency marker | `BLOB_ACCOUNT_URL`, `BLOB_RESULTS_CONTAINER`, `BLOB_RESULTS_PREFIX`. |
| Platform API / app registration | Conditional | HTTP result reporting | Needed only when `REPORT_MODE=http`. |
| Azure OpenAI or APIM AI Gateway | Optional | `engine_core.runner` path | Only required if the run path needs AOAI/APIM settings. |
| OTLP / observability endpoint | Optional | Telemetry | Used only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. |

## Required RBAC

The worker's managed identity needs at least:

- `AcrPull` on the Azure Container Registry.
- `Azure Service Bus Data Receiver` on the inbound queue namespace.
- `Azure Service Bus Data Sender` on the results queue namespace when `REPORT_MODE=servicebus`.
- `Storage Blob Data Contributor` on the storage account used for input/output blobs.
- `Cognitive Services OpenAI User` on the AOAI resource when direct Azure OpenAI access is used.

If `REPORT_MODE=http`, the identity also needs permission to acquire tokens for the platform audience configured by `PLATFORM_AUDIENCE`.

## Existing Codified Azure Pattern

The nearest existing Azure deployment implementation is the HTTP service under [wrappers/http-service/deploy](../../http-service/deploy):

- [wrappers/http-service/deploy/terraform/main.tf](../../http-service/deploy/terraform/main.tf) creates a user-assigned managed identity, assigns `AcrPull`, `Storage Blob Data Contributor`, and `Cognitive Services OpenAI User`, and deploys a Container App.
- [wrappers/http-service/deploy/setup-identity.sh](../../http-service/deploy/setup-identity.sh) shows the expected CLI/RBAC flow for managed identities.
- [wrappers/http-service/deploy/deploy.sh](../../http-service/deploy/deploy.sh) shows the expected ACR, Container Apps Environment, Log Analytics, and App Insights provisioning pattern.

That means the worker should fit into the same Azure topology rather than a separate `infra/` stack.

## KEDA Scaling In The New Manifest

The reconstructed ACA manifest in [wrappers/queue-worker/deploy/aca-containerapp.yaml](./aca-containerapp.yaml) encodes the intended queue-depth scaling directly:

- Scale type: `azure-servicebus`
- Authentication: user-assigned managed identity via `custom.identity`
- Queue target: `queueName={{ SB_QUEUE }}`
- Namespace target: `namespace={{ SB_NAMESPACE_NAME }}`
- Target depth: `messageCount=5`
- Bounds: `minReplicas=1`, `maxReplicas=50`
- Polling/cooldown: `30s` / `300s`

The Service Bus namespace name in the scale rule must be the bare namespace name, not the fully-qualified host name. For example:

- `SB_NAMESPACE=mybus.servicebus.windows.net`
- `SB_NAMESPACE_NAME=mybus`

## Deployment Flow

1. Build and push the image from [docker/worker.Dockerfile](../../../docker/worker.Dockerfile).
2. Ensure the managed identity exists and has the RBAC listed above.
3. Replace the placeholders in [wrappers/queue-worker/deploy/aca-containerapp.yaml](./aca-containerapp.yaml).
4. Deploy with `az containerapp create --yaml ...` or `az containerapp update --yaml ...`.
5. Send a test message to `SB_QUEUE` and confirm the app processes it and scales as expected.