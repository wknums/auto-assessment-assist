# ══════════════════════════════════════════════════════════════════════
#  Terraform module – Azure Container App for awreason HTTP Service
# ══════════════════════════════════════════════════════════════════════
#
#  This is an OPTIONAL convenience module.  It provisions:
#    • A User-Assigned Managed Identity
#    • A Container App in an existing Container Apps Environment
#    • Role assignments for Blob Storage and Cognitive Services
#
#  Usage:
#    terraform init
#    terraform plan -var-file=terraform.tfvars
#    terraform apply
# ══════════════════════════════════════════════════════════════════════

terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.80"
    }
  }
}

provider "azurerm" {
  features {}
}

# ── Variables ─────────────────────────────────────────────────────────

variable "resource_group_name" {
  type        = string
  description = "Name of the existing resource group."
}

variable "location" {
  type        = string
  default     = "eastus2"
  description = "Azure region."
}

variable "container_app_env_id" {
  type        = string
  description = "Resource ID of the Container Apps Environment."
}

variable "acr_login_server" {
  type        = string
  description = "ACR login server (e.g. myacr.azurecr.io)."
}

variable "acr_id" {
  type        = string
  description = "Resource ID of the Azure Container Registry."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Container image tag."
}

variable "blob_account_id" {
  type        = string
  description = "Resource ID of the Blob Storage account."
}

variable "az_storage_name" {
  type        = string
  description = "Azure Storage account name."
}

variable "az_storage_rg" {
  type        = string
  description = "Resource group containing the Azure Storage account."
}

variable "aoai_account_id" {
  type        = string
  description = "Resource ID of the Azure OpenAI / Cognitive Services account."
}

variable "azure_openai_endpoint" {
  type        = string
  description = "Azure OpenAI endpoint URL."
}

variable "aoai_deployment" {
  type        = string
  default     = "o1"
  description = "Azure OpenAI deployment name."
}

variable "aoai_api_version" {
  type        = string
  default     = "2024-12-01-preview"
  description = "Azure OpenAI API version."
}

variable "apim_aoai_base_url" {
  type        = string
  default     = ""
  description = "APIM AI Gateway base URL (leave empty to call AOAI directly)."
}

variable "aad_issuer" {
  type        = string
  default     = ""
  description = "Entra ID / AAD token issuer URL."
}

variable "aad_audience" {
  type        = string
  default     = ""
  description = "Expected JWT audience."
}

variable "aad_client_id" {
  type        = string
  default     = ""
  description = "App Registration client ID for Streamlit Entra ID auth."
}

variable "aad_client_secret" {
  type        = string
  default     = ""
  sensitive   = true
  description = "App Registration client secret for Streamlit Entra ID auth."
}

variable "azure_tenant_id" {
  type        = string
  default     = ""
  description = "Entra ID tenant ID for Streamlit auth."
}

variable "azure_subscription_id" {
  type        = string
  default     = ""
  description = "Azure subscription ID."
}

variable "auth_mode" {
  type        = string
  default     = "none"
  description = "API auth mode: none, apikey, or entra."
}

variable "api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Shared API key for apikey auth mode."
}

variable "log_level" {
  type        = string
  default     = "INFO"
  description = "Log level for the application."
}

variable "streamlit_redirect_uri" {
  type        = string
  default     = ""
  description = "Full redirect URI for Streamlit Entra ID auth (e.g. https://<fqdn>/)."
}

variable "appinsights_connection_string" {
  type        = string
  default     = ""
  description = "Application Insights connection string. When set, APPLICATIONINSIGHTS_CONNECTION_STRING env var is added to the container."
}

variable "vnet_subnet_id" {
  type        = string
  default     = ""
  description = "Subnet resource ID for ACA VNet integration. Empty = no VNet (current behavior)."
}

# ── Managed Identity ──────────────────────────────────────────────────

resource "azurerm_user_assigned_identity" "awreason" {
  name                = "id-awreason-http-service"
  resource_group_name = var.resource_group_name
  location            = var.location
}

# ── Role: Storage Blob Data Contributor on the storage account ───────

resource "azurerm_role_assignment" "blob_contributor" {
  scope                = var.blob_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.awreason.principal_id
}

# ── Role: Cognitive Services OpenAI User on the AOAI resource ────────

resource "azurerm_role_assignment" "aoai_user" {
  scope                = var.aoai_account_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.awreason.principal_id
}

# ── Role: AcrPull on the ACR ─────────────────────────────────────────

resource "azurerm_role_assignment" "acr_pull" {
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.awreason.principal_id
}

# ── Storage Network Rules (VNet mode only) ────────────────────────────

resource "azurerm_storage_account_network_rules" "vnet_access" {
  count              = var.vnet_subnet_id != "" ? 1 : 0
  storage_account_id = var.blob_account_id
  default_action     = "Deny"
  bypass             = ["Logging", "Metrics", "AzureServices"]

  virtual_network_subnet_ids = [var.vnet_subnet_id]

  # Preserve existing IP rules (managed outside Terraform via .env / deploy.sh)
  lifecycle {
    ignore_changes = [ip_rules]
  }
}

# ── Container App ─────────────────────────────────────────────────────

resource "azurerm_container_app" "awreason" {
  name                         = "awreason-http-service"
  container_app_environment_id = var.container_app_env_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  depends_on = [azurerm_role_assignment.acr_pull]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.awreason.id]
  }

  secret {
    name  = "aad-client-secret"
    value = var.aad_client_secret
  }

  secret {
    name  = "api-key"
    value = var.api_key
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  registry {
    server   = var.acr_login_server
    identity = azurerm_user_assigned_identity.awreason.id
  }

  template {
    min_replicas = 1
    max_replicas = 10

    volume {
      name         = "workdir-emptydir"
      storage_type = "EmptyDir"
    }

    container {
      name   = "awreason-http-service"
      image  = "${var.acr_login_server}/awreason-http-service:${var.image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      volume_mounts {
        name = "workdir-emptydir"
        path = "/work"
      }

      env {
        name  = "WORKDIR_BASE"
        value = "/work"
      }

      env {
        name  = "PER_REPLICA_CONCURRENCY"
        value = "1"
      }

      env {
        name  = "AZ_STORAGE_NAME"
        value = var.az_storage_name
      }

      env {
        name  = "AZ_STORAGE_RG"
        value = var.az_storage_rg
      }

      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = var.azure_openai_endpoint
      }

      env {
        name  = "APIM_AOAI_BASE_URL"
        value = var.apim_aoai_base_url
      }

      env {
        name  = "AOAI_DEPLOYMENT"
        value = var.aoai_deployment
      }

      env {
        name  = "AOAI_API_VERSION"
        value = var.aoai_api_version
      }

      env {
        name  = "USE_AAD_FOR_AOAI"
        value = "true"
      }

      env {
        name  = "AUTH_MODE"
        value = var.auth_mode
      }

      env {
        name        = "API_KEY"
        secret_name = "api-key"
      }

      env {
        name  = "AAD_ISSUER"
        value = var.aad_issuer
      }

      env {
        name  = "AAD_AUDIENCE"
        value = var.aad_audience
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.awreason.client_id
      }

      env {
        name  = "AWREASON_CLI_CMD"
        value = "/app/o1-assessment/awreason.py"
      }

      env {
        name  = "CONTAINER_APP_NAME"
        value = "awreason-http-service"
      }

      env {
        name  = "AAD_CLIENT_ID"
        value = var.aad_client_id
      }

      env {
        name      = "AAD_CLIENT_SECRET"
        secret_name = "aad-client-secret"
      }

      env {
        name  = "AWR_API_ENDPOINT"
        value = "http://localhost:8080"
      }

      env {
        name  = "AAD_TENANT_ID"
        value = var.azure_tenant_id
      }

      env {
        name  = "AZURE_TENANT_ID"
        value = var.azure_tenant_id
      }

      env {
        name  = "AZURE_SUBSCRIPTION_ID"
        value = var.azure_subscription_id
      }

      env {
        name  = "AZURE_OPENAI_DEPLOYMENT_O1"
        value = var.aoai_deployment
      }

      env {
        name  = "AZURE_OPENAI_API_VERSION"
        value = var.aoai_api_version
      }

      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }

      env {
        name  = "STREAMLIT_REDIRECT_URI"
        value = var.streamlit_redirect_uri
      }

      dynamic "env" {
        for_each = var.appinsights_connection_string != "" ? [1] : []
        content {
          name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
          value = var.appinsights_connection_string
        }
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8000

        initial_delay    = 5
        interval_seconds = 30
        failure_count_threshold = 3
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/ready"
        port      = 8000

        initial_delay    = 10
        interval_seconds = 15
        failure_count_threshold = 3
      }
    }
  }
}

# ── Outputs ───────────────────────────────────────────────────────────

output "container_app_fqdn" {
  value = azurerm_container_app.awreason.ingress[0].fqdn
}

output "streamlit_url" {
  value       = "https://${azurerm_container_app.awreason.ingress[0].fqdn}/"
  description = "Streamlit UI"
}

output "api_base_url" {
  value       = "https://${azurerm_container_app.awreason.ingress[0].fqdn}/api"
  description = "FastAPI base URL (Swagger at /api/docs)"
}

output "managed_identity_client_id" {
  value = azurerm_user_assigned_identity.awreason.client_id
}
