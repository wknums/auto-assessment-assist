mock_provider "azurerm" {
  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/external-identity-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/customer-http-identity"
      client_id    = "11111111-1111-1111-1111-111111111111"
      principal_id = "22222222-2222-2222-2222-222222222222"
      tenant_id    = "33333333-3333-3333-3333-333333333333"
      location     = "eastus2"
    }
  }
}

run "deployment_manages_identity_and_roles" {
  command = plan

  variables {
    resource_group_name     = "app-rg"
    location                = "eastus2"
    container_app_env_id    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/app-rg/providers/Microsoft.App/managedEnvironments/app-env"
    acr_login_server        = "example.azurecr.io"
    acr_id                  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/acr-rg/providers/Microsoft.ContainerRegistry/registries/example"
    blob_account_id         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/storage-rg/providers/Microsoft.Storage/storageAccounts/example"
    az_storage_name         = "example"
    az_storage_rg           = "storage-rg"
    aoai_account_id         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ai-rg/providers/Microsoft.CognitiveServices/accounts/example"
    azure_openai_endpoint   = "https://example.openai.azure.com/"
    reasoning_deployment_01 = "reasoning-model"
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.awreason) == 1
    error_message = "The default path must continue to manage one user-assigned identity."
  }

  assert {
    condition = (
      length(data.azurerm_user_assigned_identity.awreason) == 0 &&
      length(azurerm_role_assignment.blob_contributor) == 1 &&
      length(azurerm_role_assignment.aoai_user) == 1 &&
      length(azurerm_role_assignment.acr_pull) == 1
    )
    error_message = "The default path must continue to manage all three runtime role assignments."
  }
}

run "deployment_reuses_external_identity_and_roles" {
  command = plan

  variables {
    resource_group_name                     = "app-rg"
    location                                = "eastus2"
    reuse_managed_identity                  = true
    reuse_managed_identity_role_assignments = true
    managed_identity_name                   = "customer-http-identity"
    managed_identity_resource_group_name    = "external-identity-rg"
    container_app_env_id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/app-rg/providers/Microsoft.App/managedEnvironments/app-env"
    acr_login_server                        = "example.azurecr.io"
    acr_id                                  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/acr-rg/providers/Microsoft.ContainerRegistry/registries/example"
    blob_account_id                         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/storage-rg/providers/Microsoft.Storage/storageAccounts/example"
    az_storage_name                         = "example"
    az_storage_rg                           = "storage-rg"
    aoai_account_id                         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ai-rg/providers/Microsoft.CognitiveServices/accounts/example"
    azure_openai_endpoint                   = "https://example.openai.azure.com/"
    reasoning_deployment_01                 = "reasoning-model"
  }

  assert {
    condition = (
      length(azurerm_user_assigned_identity.awreason) == 0 &&
      length(data.azurerm_user_assigned_identity.awreason) == 1
    )
    error_message = "Reuse mode must read the external identity without managing it as a resource."
  }

  assert {
    condition = (
      length(azurerm_role_assignment.blob_contributor) == 0 &&
      length(azurerm_role_assignment.aoai_user) == 0 &&
      length(azurerm_role_assignment.acr_pull) == 0
    )
    error_message = "Role-assignment reuse mode must not manage any runtime role assignments."
  }

  assert {
    condition     = output.managed_identity_client_id == "11111111-1111-1111-1111-111111111111"
    error_message = "The Container App must expose the reused identity client ID."
  }
}
