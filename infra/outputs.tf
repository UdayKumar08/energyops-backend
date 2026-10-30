output "web_url" {
  value = "https://${azurerm_container_app.web.ingress[0].fqdn}"
}

output "registry_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "bootstrap_job" {
  value = azurerm_container_app_job.bootstrap.name
}

output "migrate_job" {
  value = azurerm_container_app_job.migrate.name
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "entra_redirect_uri" {
  description = "Add this as a Web redirect URI on the Microsoft app registration."
  value       = "${local.public_url}/api/auth/entra/callback"
}
