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
- Target depth: `messageCount=1`
- Bounds: `minReplicas=3`, `maxReplicas=50`
- Polling/cooldown: `5s` / `120s`

These defaults are intentionally biased toward burst fan-out validation rather than cost minimization. For small E2E batches, they reduce scaler reaction lag so queue wait does not dominate model execution time.

The Service Bus namespace name in the scale rule must be the bare namespace name, not the fully-qualified host name. For example:

- `SB_NAMESPACE=mybus.servicebus.windows.net`
- `SB_NAMESPACE_NAME=mybus`

## Detailed Build And Deploy Commands

The queue worker is deployed with [wrappers/queue-worker/deploy/deploy.sh](./deploy.sh). The script sources values from repo root `.env_qa` by default and supports these actions:

- `preview`: resolve and print effective deployment values
- `infra`: ensure dependent Azure resources exist (with reuse safety checks)
- `build`: build and push image to ACR
- `yaml`: render and apply ACA YAML (`create` or `update` automatically)
- `all`: run `infra`, then `build`, then `yaml`

### 1) Prerequisites

Run from repo root:

```bash
az login
az account set --subscription <subscription-id>
```

Ensure `.env_qa` has the required values used by the script and YAML, including:

- `AZURE_SUBSCRIPTION_ID`
- `AZ_ACR_NAME`, `AZ_ACR_RG`
- `AZ_CONTAINER_APP_ENV_NAME`, `AZ_CONTAINER_APP_ENV_RG`
- `AZ_CONTAINER_APP_NAME_Q_WORKER`
- `AZ_MI_NAME` / resolved MI IDs
- `SB_NAMESPACE`, `SB_RUNS_QUEUE`, `SB_RESULTS_QUEUE`
- `AZ_STORAGE_NAME`, `AZ_STORAGE_RG`
- `QUEUE_WORKER_PER_REPLICA_CONCURRENCY` (recommended) or `PER_REPLICA_CONCURRENCY` (legacy fallback)

### 2) Preview Effective Settings

```bash
cd wrappers/queue-worker/deploy
bash deploy.sh preview
```

Use this output to confirm image target, ACA environment, Service Bus namespace/queue, and scale settings before deployment.

### 3) Build Latest Image

```bash
cd wrappers/queue-worker/deploy
bash deploy.sh build
```

Notes:

- Default build context mode is `staged` (`BUILD_CONTEXT_MODE=staged`) for faster ACR builds.
- The script writes the pushed tag to `.last_image_tag`.
- It pushes both `<tag>` and `latest` for `awreason-queue-worker`.

### 4) Apply Deployment (YAML)

```bash
cd wrappers/queue-worker/deploy
bash deploy.sh yaml
```

This action:

1. Generates [wrappers/queue-worker/deploy/_resolved-aca.yaml](./_resolved-aca.yaml)
2. Applies via `az containerapp update --yaml ...` if app exists
3. Falls back to `az containerapp create --yaml ...` if app is missing

### 5) Optional: End-To-End In One Command

```bash
cd wrappers/queue-worker/deploy
bash deploy.sh all
```

Use this when you also want infra checks/provisioning in the same run.

## Validation Commands

### Check active revision and image

```bash
az containerapp show \
	--resource-group <rg> \
	--name <queue-worker-app-name> \
	--query "{latestRevisionName:properties.latestRevisionName,latestReadyRevisionName:properties.latestReadyRevisionName,image:properties.template.containers[0].image,runningStatus:properties.runningStatus}" \
	-o json
```

### Check queue depth

```bash
az servicebus queue show \
	--resource-group <rg> \
	--namespace-name <servicebus-namespace-name> \
	--name <runs-queue-name> \
	--query "{active:countDetails.activeMessageCount,deadLetter:countDetails.deadLetterMessageCount,scheduled:countDetails.scheduledMessageCount}" \
	-o json
```

### Tail worker logs

```bash
az containerapp logs show \
	--resource-group <rg> \
	--name <queue-worker-app-name> \
	--tail 200
```

## Safe Reuse Behavior

The script is designed to avoid destructive changes on reused resources:

- `*_REUSE=TRUE` resources are never deleted
- Reused-resource mutations are blocked unless `ALLOW_REUSE_RESOURCE_MUTATIONS=TRUE`

This lets you safely run build/deploy in shared QA environments without accidental teardown.

## Troubleshooting

- If build stalls on source packaging, keep `BUILD_CONTEXT_MODE=staged`.
- If `yaml` fails on Windows path handling, run from Git Bash as shown above.
- If image tag resolves empty, remove stale `.last_image_tag` and rerun `build`.
- If processing fails with blob authorization errors, verify MI blob roles on the exact storage account(s) hosting `cv-uploads` and `batch-results`.