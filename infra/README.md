# Azure infrastructure

Terraform for one EnergyOps environment (`dev`, `nonprod` or `prod`). **It has never been
planned or applied.** CI runs `terraform fmt` and `terraform validate` on it, which check its shape
against the azurerm 5.11 schema but not whether Azure will accept the values, so expect the first
`plan` and `apply` to find more.

Nothing here runs automatically. The deploy workflow is started by hand.

## What it creates

A resource group with a virtual network, a private PostgreSQL 16 flexible server (no public
address, `VECTOR` allowed as an extension), a Key Vault, a container registry, a Log Analytics
workspace, a Container Apps environment, the API (reachable only inside the environment), the web
app (the only public address), and two manual jobs: `bootstrap` and `migrate`.

These cost money while they exist. The default database size is the cheapest burstable tier and
the apps keep one replica running each. Do not apply until you have decided to pay for them, and
destroy a trial environment when you finish.

## Secrets

Terraform generates the database passwords and writes the connection strings to Key Vault. The
apps read them from the vault through a managed identity, so no password is an environment
variable in a definition. The passwords are also in Terraform's state, which is why the state
must live in a private storage container and not in the repository.

The Microsoft client secret is the same: create it in the vault first, under the name in the
`entra_client_secret_name` variable (default `entra-client-secret`), before the `full` apply, because the
web app reads it from there. After the apply, add the `entra_redirect_uri` output as a Web redirect
URI on the app registration. The app registration also needs, under Expose an API, an Application ID
URI of `api://<client id>` and a delegated scope named exactly `access_as_user`, and its manifest needs
`requestedAccessTokenVersion` set to 2, because the API accepts only version 2 tokens and Entra issues
version 1 tokens to an app that exposes its own API unless told otherwise.

Model provider keys and the embeddings key are not created by Terraform and never pass through
its state. A person creates each one in the vault first (`az keyvault secret set`), then names it
in the `model_key_secrets` and `embeddings` variables.

## Order of operations

1. Once: run `infra/scripts/azure-setup.sh` in Azure Cloud Shell (it does the rest of this step and
   prints the variable values). It creates the state storage container, and an app registration with a federated
   credential for this repository's GitHub environment (OIDC). Give it Contributor and User
   Access Administrator at subscription scope, because Terraform creates the resource group and
   the role assignments inside it. Add the IDs as variables on the GitHub environment:
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `ENTRA_TENANT_ID`,
   `ENTRA_CLIENT_ID`, `TFSTATE_RESOURCE_GROUP`, `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`
   and `VAULT_ADMIN_OBJECT_IDS` (a JSON list such as `["<your object id>"]`, the people who may
   write secrets by hand). Add required reviewers to the environment.
2. Run the Deploy workflow with stage `foundation`, action `plan`, read it, then `apply`. This
   creates the resource group, the registry, the identity and the Key Vault, so there is
   somewhere to put images and secrets.
2b. Put the secrets in the vault yourself: the Microsoft client secret under `entra-client-secret`,
   the model provider keys, and the embeddings key if you use one. Nothing here creates them.
3. Run it with stage `full` and action `plan`, read it, then `apply`. This builds both images in
   the registry and creates everything else.
4. Start the `bootstrap` job once (`az containerapp job start`). It creates the owner and
   application roles and the `vector` extension, using the server administrator, and it is the
   only thing that ever uses that account.
5. Start the `migrate` job. Repeat this step after every release that adds a migration. The API
   is only ever given the application role.

## Deploy variables

Besides the IDs in step 1, these GitHub environment variables are read by the Deploy workflow
(all are names or settings, never secrets): `FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_NAME` (the first
platform admin, created by the migrate job), `DEMO_CUSTOMER_ADMIN_EMAIL` (not in `prod`),
`BUDGET_ALERT_EMAILS` (a JSON list) and `BUDGET_AMOUNT`, `AZURE_LOCATION`, `VAULT_ADMIN_OBJECT_IDS`,
`MODEL_KEY_SECRETS_JSON` such as `{"GEMINI_API_KEY":"gemini-api-key","NEBIUS_API_KEY":"nebius-api-key"}`
and `EMBEDDINGS_JSON` such as `{"provider":"gemini","model":"gemini-embedding-2","key_secret":"gemini-api-key"}`.
GitHub issues its sign-in token with ID-based repository names, so the deploy identity needs a
federated credential whose subject is `repo:OWNER@OWNER_ID/NAME@REPO_ID:environment:dev`.

## Known gaps

The Microsoft Entra sign-in has been run once in a browser against a real tenant: a sign-in as the
first platform admin reached the admin console. It has not been run for a customer user, with
a second tenant, or after the client secret expires (the secret has a fixed lifetime and nothing
reminds anyone to renew it). The API validates Entra tokens against the tenant's published keys.

A monthly budget with email alerts is defined (when `budget_alert_emails` is set); no other alerts or dashboards are. The Key Vault accepts public network access
(protected by role-based access) so that the deploy job can write to it. The Dockerfiles have not
been built, no image digests are pinned, and GitHub actions are pinned by tag, not by commit.
Embeddings are required in `prod` and the stand-in is refused there, so `prod` also needs the
`embeddings` variable set.
