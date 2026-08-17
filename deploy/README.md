# Deployment

Two paths are scaffolded. Pick one — building both is wasted effort for a
portfolio project.

## Option A — Azure Container Apps Jobs (recommended)

The most enterprise-relevant of the realistic options: a scheduled trigger,
secrets pulled from Key Vault through a managed identity, no `.env` anywhere
near the image. `container-apps-job.bicep` is a starting point, not a
validated template — read it before you deploy it.

Rough sequence:

```powershell
# 1. Build and push the image
docker build -t <registry>.azurecr.io/earthquake-pipeline:latest -f Dockerfile ..
az acr login --name <registry>
docker push <registry>.azurecr.io/earthquake-pipeline:latest

# 2. Put secrets in Key Vault
az keyvault secret set --vault-name <kv> --name database-url --value "postgresql+psycopg2://..."
az keyvault secret set --vault-name <kv> --name graph-client-secret --value "..."

# 3. Create a user-assigned identity and grant it read on the vault's secrets
az identity create -g <rg> -n pipeline-identity
az keyvault set-policy --name <kv> --object-id <identity-principal-id> --secret-permissions get list
# (or, if the vault uses Azure RBAC instead of access policies:)
az role assignment create --assignee <identity-principal-id> --role "Key Vault Secrets User" --scope <vault-resource-id>

# 4. Deploy the job
az deployment group create -g <rg> -f container-apps-job.bicep `
  -p environmentName=<env> keyVaultName=<kv> identityName=pipeline-identity `
     containerImage=<registry>.azurecr.io/earthquake-pipeline:latest
```

**Alert on absence, not just on error.** A Container Apps Job that silently
stops being triggered fails no health check — nothing errors, it just
doesn't run. Wire an Azure Monitor scheduled query against the job's
execution history (`ContainerAppJobExecutions` in Log Analytics) that fires
if no successful execution exists in the last N hours, where N is a few
multiples of the schedule interval. That's the alert the acceptance
criteria are actually asking for — "the job failed" is the easy case.

## Option B — GitHub Actions on a schedule (no cloud account needed)

`github-actions-schedule.yml` — copy it to `.github/workflows/` at the repo
root. Good for actually proving the "ran unattended for seven days"
acceptance criterion cheaply, since it needs nothing beyond a GitHub repo
and a Postgres instance to point `DATABASE_URL` at (Neon and Supabase both
have usable free tiers).

The gotcha worth knowing here, because it's a genuinely good interview
answer to "how would you know if this broke": **GitHub disables scheduled
workflows after 60 days of repository inactivity**, not workflow inactivity
— pushing to any branch resets the clock, but a quiet repo with a perfectly
healthy pipeline can still go dark with zero errors logged anywhere in the
Actions UI. The workflow's last step pings an external dead-man's-switch
(`HEARTBEAT_URL` — [healthchecks.io](https://healthchecks.io) has a usable
free tier) only on success; that external service, not GitHub, is what
alerts on the run simply not happening.

## Local dev / testing against real Postgres

```powershell
docker compose -f docker-compose.local.yml up -d
$env:DATABASE_URL = "postgresql+psycopg2://pipeline:localdev@localhost:5432/earthquakes"
python -m pipeline.run --dry-run
```

SQLite (`sqlite:///./local.db`, the `.env.example` default) is fine for
day-to-day development — the upsert logic is exercised identically against
both dialects by the test suite.
