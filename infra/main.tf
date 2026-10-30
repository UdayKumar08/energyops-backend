# EnergyOps AI on Azure. NOT APPLIED and NOT VALIDATED: no Terraform binary was available where
# this was written, and no Azure subscription was used. It was written against the azurerm 5.11
# documentation. Read infra/README.md before running `terraform plan`.

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  base = "${var.name_prefix}-${var.environment}"
  # Registry and vault names are global and allow only letters and digits (registry) or a
  # length of 24 (vault).
  acr_name   = "${var.name_prefix}${var.environment}${random_string.suffix.result}"
  vault_name = substr("${var.name_prefix}-${var.environment}-${random_string.suffix.result}", 0, 24)
  tags       = merge({ app = "energyops", environment = var.environment }, var.tags)

  web_app_name = "ca-${local.base}-web"
  # A Container App's public address is its name plus the environment's default domain, so the
  # web app can know its own address without referring to itself. A custom domain overrides it.
  public_url = coalesce(var.public_url, "https://${local.web_app_name}.${azurerm_container_app_environment.main.default_domain}")

  app_db_url   = "postgresql+psycopg://energyops_app:${random_password.app.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/energyops?sslmode=require"
  owner_db_url = "postgresql+psycopg://energyops_owner:${random_password.owner.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/energyops?sslmode=require"
  admin_db_url = "postgresql://energyops_admin:${random_password.server_admin.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/energyops?sslmode=require"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.base}"
  location = var.location
  tags     = local.tags
}

# ---- Logs ------------------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

# ---- Network ---------------------------------------------------------------------------------

resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.40.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "snet-apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.0.0/23"]

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "database" {
  name                 = "snet-database"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.2.0/24"]

  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "postgres" {
  # The zone name for a server in a delegated subnet must end in .postgres.database.azure.com.
  name                = "${local.base}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                = "postgres-link"
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id
  virtual_network_id  = azurerm_virtual_network.main.id
  tags                = local.tags
}

# ---- Identity, registry, vault -----------------------------------------------------------------

resource "azurerm_user_assigned_identity" "apps" {
  name                = "id-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_container_registry" "main" {
  name                = local.acr_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "pull_images" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

resource "azurerm_key_vault" "main" {
  name                       = local.vault_name
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = 7
  tags                       = local.tags
}

# The identity that runs Terraform needs to write secrets; the apps only ever read them.
resource "azurerm_role_assignment" "deployer_writes_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# People who put secrets in the vault by hand (model keys, the Microsoft client secret). Their
# object IDs come from the vault_admins variable. This is data access only, not control of Azure.
resource "azurerm_role_assignment" "vault_admins" {
  for_each             = toset(var.vault_admins)
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "apps_read_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

# ---- Database --------------------------------------------------------------------------------

resource "random_password" "server_admin" {
  length  = 40
  special = false # keeps the generated connection strings free of characters that need escaping
}

resource "random_password" "owner" {
  length  = 40
  special = false
}

resource "random_password" "app" {
  length  = 40
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.base}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  sku_name                      = var.postgres_sku
  storage_mb                    = var.postgres_storage_mb
  administrator_login           = "energyops_admin"
  administrator_password        = random_password.server_admin.result
  delegated_subnet_id           = azurerm_subnet.database.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
  backup_retention_days         = var.postgres_backup_retention_days
  geo_redundant_backup_enabled  = var.environment == "prod"
  tags                          = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  lifecycle {
    # Azure picks the zone; do not fight it.
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "energyops"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# pgvector is named VECTOR in the allow-list. Without this the bootstrap job cannot create it.
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

# Connection strings go to the vault; the apps read them from there, so no password is ever an
# environment variable in the app's definition.
resource "azurerm_key_vault_secret" "app_db_url" {
  name         = "database-url"
  value        = local.app_db_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_writes_secrets]
}

resource "azurerm_key_vault_secret" "owner_db_url" {
  name         = "owner-database-url"
  value        = local.owner_db_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_writes_secrets]
}

resource "azurerm_key_vault_secret" "admin_db_url" {
  name         = "admin-database-url"
  value        = local.admin_db_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_writes_secrets]
}

resource "azurerm_key_vault_secret" "owner_password" {
  name         = "owner-password"
  value        = random_password.owner.result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_writes_secrets]
}

resource "azurerm_key_vault_secret" "app_password" {
  name         = "app-password"
  value        = random_password.app.result
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_writes_secrets]
}

# ---- Container Apps --------------------------------------------------------------------------

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.base}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  logs_destination           = "log-analytics"
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  tags                       = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

locals {
  vault_secret_ids = {
    database-url       = azurerm_key_vault_secret.app_db_url.versionless_id
    owner-database-url = azurerm_key_vault_secret.owner_db_url.versionless_id
    admin-database-url = azurerm_key_vault_secret.admin_db_url.versionless_id
    owner-password     = azurerm_key_vault_secret.owner_password.versionless_id
    app-password       = azurerm_key_vault_secret.app_password.versionless_id
  }

  # Secrets a person created in the vault ahead of time (model keys, embeddings key).
  external_secret_names = merge(
    { for env_name, secret_name in var.model_key_secrets : env_name => secret_name },
    var.embeddings == null ? {} : { ENERGYOPS_EMBEDDINGS_API_KEY = var.embeddings.key_secret },
  )
  external_secret_ids = {
    for env_name, secret_name in local.external_secret_names :
    env_name => "${azurerm_key_vault.main.vault_uri}secrets/${secret_name}"
  }
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.base}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = local.vault_secret_ids["database-url"]
    identity            = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = local.external_secret_ids
    content {
      name                = lower(replace(secret.key, "_", "-"))
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.apps.id
    }
  }

  # Only the web app is reachable from outside. The API answers calls from inside the environment.
  ingress {
    external_enabled = false
    target_port      = 8100
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"
      args   = ["serve"]

      env {
        name  = "ENERGYOPS_ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "ENERGYOPS_DATA_MODE"
        value = var.data_mode
      }
      env {
        name  = "ENERGYOPS_ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      env {
        name  = "ENERGYOPS_ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }
      env {
        name        = "ENERGYOPS_DATABASE_URL"
        secret_name = "database-url"
      }
      dynamic "env" {
        for_each = var.embeddings == null ? [] : [var.embeddings]
        content {
          name  = "ENERGYOPS_EMBEDDINGS_BASE_URL"
          value = env.value.base_url
        }
      }
      dynamic "env" {
        for_each = var.embeddings == null ? [] : [var.embeddings]
        content {
          name  = "ENERGYOPS_EMBEDDINGS_MODEL"
          value = env.value.model
        }
      }
      dynamic "env" {
        for_each = local.external_secret_ids
        content {
          name        = env.key
          secret_name = lower(replace(env.key, "_", "-"))
        }
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8100
        path      = "/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8100
        path      = "/health"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.pull_images,
    azurerm_role_assignment.apps_read_secrets,
  ]
}

resource "azurerm_container_app" "web" {
  name                         = local.web_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  # The Microsoft client secret is created in the vault by a person, never by Terraform, so it
  # never passes through the state file.
  secret {
    name                = "entra-client-secret"
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${var.entra_client_secret_name}"
    identity            = azurerm_user_assigned_identity.apps.id
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "BACKEND_URL"
        value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
      }
      env {
        name  = "NODE_ENV"
        value = "production"
      }
      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      env {
        name  = "ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }
      env {
        name        = "ENTRA_CLIENT_SECRET"
        secret_name = "entra-client-secret"
      }
      env {
        name  = "PUBLIC_URL"
        value = local.public_url
      }

      liveness_probe {
        transport = "HTTP"
        port      = 3000
        path      = "/login"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 3000
        path      = "/login"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.pull_images,
    azurerm_role_assignment.apps_read_secrets,
  ]
}

# ---- One-off database jobs (run by hand, never on a schedule) ------------------------------------

resource "azurerm_container_app_job" "bootstrap" {
  name                         = "caj-${local.base}-bootstrap"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  secret {
    name                = "admin-database-url"
    key_vault_secret_id = local.vault_secret_ids["admin-database-url"]
    identity            = azurerm_user_assigned_identity.apps.id
  }
  secret {
    name                = "owner-password"
    key_vault_secret_id = local.vault_secret_ids["owner-password"]
    identity            = azurerm_user_assigned_identity.apps.id
  }
  secret {
    name                = "app-password"
    key_vault_secret_id = local.vault_secret_ids["app-password"]
    identity            = azurerm_user_assigned_identity.apps.id
  }

  template {
    container {
      name   = "bootstrap"
      image  = var.api_image
      cpu    = 0.25
      memory = "0.5Gi"
      args   = ["bootstrap"]

      env {
        name        = "ENERGYOPS_BOOTSTRAP_ADMIN_URL"
        secret_name = "admin-database-url"
      }
      env {
        name        = "ENERGYOPS_BOOTSTRAP_OWNER_PASSWORD"
        secret_name = "owner-password"
      }
      env {
        name        = "ENERGYOPS_BOOTSTRAP_APP_PASSWORD"
        secret_name = "app-password"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.pull_images,
    azurerm_role_assignment.apps_read_secrets,
    azurerm_postgresql_flexible_server_configuration.extensions,
    azurerm_postgresql_flexible_server_database.main,
  ]
}

resource "azurerm_container_app_job" "migrate" {
  name                         = "caj-${local.base}-migrate"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 0
  tags                         = local.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  secret {
    name                = "owner-database-url"
    key_vault_secret_id = local.vault_secret_ids["owner-database-url"]
    identity            = azurerm_user_assigned_identity.apps.id
  }

  template {
    container {
      name   = "migrate"
      image  = var.api_image
      cpu    = 0.25
      memory = "0.5Gi"
      args   = ["migrate"]

      env {
        name  = "ENERGYOPS_ENVIRONMENT"
        value = var.environment
      }
      env {
        name        = "ENERGYOPS_ADMIN_DATABASE_URL"
        secret_name = "owner-database-url"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.pull_images,
    azurerm_role_assignment.apps_read_secrets,
  ]
}
