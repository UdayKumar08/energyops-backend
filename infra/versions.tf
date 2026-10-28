terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.11"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # State holds the generated database passwords, so it lives in a private Azure Storage
  # container (blob versioning on, no public access), never in the repository. The container is
  # created by hand once; its details are passed at `terraform init` with -backend-config (the
  # deploy workflow does this). Without them `terraform init` stops, which is intended.
  backend "azurerm" {}
}

provider "azurerm" {
  features {}
  # Authentication comes from the environment: `az login` locally, or the GitHub Actions OIDC
  # login in .github/workflows/deploy.yml (ARM_USE_OIDC and friends). No secret is set here.
  subscription_id = var.subscription_id
}
