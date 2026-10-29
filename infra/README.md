# Azure infrastructure

Terraform for one EnergyOps environment (`dev`, `nonprod` or `prod`). **It has never been
applied and has never been validated.** No Terraform program was available where it was written,
so the only check it has had is that the files parse as HCL. Expect `terraform validate` or the
first `plan` to find mistakes. It was written against the azurerm 5.11 documentation.

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

Model provider keys and the embeddings key are not created by Terraform and never pass through
its state. A person creates each one in the vault first (`az keyvault secret set`), then names it
in the `model_key_secrets` and `embeddings` variables.

## Order of operations

1. Once, by hand: create the state storage container, and an app registration with a federated
   credential for this repository's GitHub environment (OIDC). Give it Contributor and User
   Access Administrator on the resource group (it creates role assignments). Add the IDs as
   variables on the GitHub environment: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `TFSTATE_RESOURCE_GROUP`,
   `TFSTATE_STORAGE_ACCOUNT`, `TFSTATE_CONTAINER`. Add required reviewers to the environment.
2. Run the Deploy workflow with stage `foundation`, action `plan`, read it, then `apply`. This
   creates the registry and identity so there is somewhere to put images.
3. Run it with stage `full` and action `plan`, read it, then `apply`. This builds both images in
   the registry and creates everything else.
4. Start the `bootstrap` job once (`az containerapp job start`). It creates the owner and
   application roles and the `vector` extension, using the server administrator, and it is the
   only thing that ever uses that account.
5. Start the `migrate` job. Repeat this step after every release that adds a migration. The API
   is only ever given the application role.

## Known gaps

The web app cannot sign anyone in on Azure yet. Its Microsoft Entra sign-in is written and
unit-tested but has never been run in a browser or against Microsoft, and this Terraform does not
yet pass the web app `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` (from Key Vault)
and `PUBLIC_URL`. The API validates Entra tokens (tested with generated keys and a local key
server only).

No alerts, dashboards or budgets are defined. The Key Vault accepts public network access
(protected by role-based access) so that the deploy job can write to it. The Dockerfiles have not
been built, no image digests are pinned, and GitHub actions are pinned by tag, not by commit.
Embeddings are required in `prod` and the stand-in is refused there, so `prod` also needs the
`embeddings` variable set.
