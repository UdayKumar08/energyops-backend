variable "subscription_id" {
  type        = string
  description = "The Azure subscription to create things in."
}

variable "environment" {
  type        = string
  description = "dev, nonprod or prod. Goes into names and into ENERGYOPS_ENVIRONMENT."
  validation {
    condition     = contains(["dev", "nonprod", "prod"], var.environment)
    error_message = "environment must be dev, nonprod or prod."
  }
}

variable "location" {
  type        = string
  default     = "westeurope"
  description = "An Azure region. Azure has no global option for these resources, so pick one."
}

variable "name_prefix" {
  type    = string
  default = "energyops"
}

variable "entra_tenant_id" {
  type        = string
  description = "The Microsoft Entra tenant whose sign-in tokens the API accepts."
}

variable "entra_client_id" {
  type        = string
  description = "The application (client) ID whose tokens the API accepts as its audience."
}

variable "api_image" {
  type        = string
  description = "Full image reference for the API, for example <registry>/energyops-api:<git sha>. Use a tag that names one build, never 'latest'."
}

variable "web_image" {
  type        = string
  description = "Full image reference for the web app."
}

variable "postgres_sku" {
  type        = string
  default     = "B_Standard_B1ms"
  description = "Burstable is the cheapest tier; use a General Purpose size for production."
}

variable "postgres_storage_mb" {
  type    = number
  default = 32768
}

variable "postgres_backup_retention_days" {
  type    = number
  default = 7
}

variable "data_mode" {
  type        = string
  default     = "live"
  description = "ENERGYOPS_DATA_MODE. The API refuses 'demo' in prod."
}

variable "model_key_secrets" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    Model provider keys, as a map of environment variable name to the name of a Key Vault secret
    that a person has already created, for example { GEMINI_API_KEY = "gemini-api-key" }. The
    keys never pass through Terraform or its state.
  EOT
}

variable "embeddings" {
  type = object({
    provider   = optional(string, "openai_compatible") # or "gemini" (Google's own embeddings method)
    base_url   = optional(string)                      # needed for openai_compatible; optional for gemini
    model      = string
    key_secret = string # name of an existing Key Vault secret holding the API key
  })
  default     = null
  description = "The embeddings provider. Required in prod; the API refuses its word-matching stand-in there."
}

variable "budget_amount" {
  type        = number
  default     = 12000
  description = "Monthly budget for the subscription, in the currency the subscription is billed in. Alerts only: Azure does not stop spending when it is passed."
}

variable "budget_alert_emails" {
  type        = list(string)
  default     = []
  description = "Who is emailed when spending reaches the alert points. Empty means no budget is created."
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "entra_client_secret_name" {
  type        = string
  default     = "entra-client-secret"
  description = "The name of the vault secret that holds the Microsoft app registration's client secret. A person creates it in the vault first."
}

variable "public_url" {
  type        = string
  default     = null
  description = "The web app's public address if it is not the default Container Apps address, for example a custom domain. No trailing slash."
}

variable "vault_admins" {
  type        = list(string)
  default     = []
  description = "Object IDs of the people who may write secrets to the Key Vault by hand. Needed for the model keys and the Microsoft client secret."
}

variable "first_admin_email" {
  type        = string
  default     = ""
  description = "Email of the first platform admin. The migrate job creates them if they are missing. They sign in through Microsoft; no password is involved."
}

variable "first_admin_name" {
  type    = string
  default = "Platform admin"
}

variable "demo_customer_admin_email" {
  type        = string
  default     = ""
  description = "When set (never in prod), the migrate job also creates an invented customer organisation with this person as its administrator."
}

variable "first_admin_object_id" {
  type        = string
  default     = ""
  description = "The first platform admin's Microsoft Entra object id. Sign-in matches on it, so it works whatever address Microsoft puts in the token."
}
