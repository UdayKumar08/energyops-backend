# One-off: an earlier apply was cancelled while Azure was creating the database
# server, so the server exists in Azure but was never written to the state. This
# adopts it instead of failing with "already exists". Once an apply has
# succeeded, delete this file.
import {
  to = azurerm_postgresql_flexible_server.main
  id = "/subscriptions/9e5047ee-0cd4-4bc5-afa6-bc9a3bdcaf97/resourceGroups/rg-energyops-dev/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-energyops-dev-q45d4"
}
