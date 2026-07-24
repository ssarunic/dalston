# Experimental GCP deployment

> **Experimental:** `infra/scripts/dalston-gcp` provisions a GCP split
> deployment without Terraform. It is not at feature parity with
> `dalston-aws`: `reconcile`, `patch`, and single-engine `engine` mode are
> currently stubs. Treat the script and `infra/templates/gcp-split.yaml` as an
> engineering preview, not a supported production deployment contract.

## What works

The script can check prerequisites, provision shared infrastructure, launch a
CPU control plane and optional GPU worker, show status, SSH, stop/start
instances, terminate instances, and tear down provisioned resources.

It stores state in `~/.dalston/gcp-state.yaml` and appends operations to the
shared `~/.dalston/audit.log`.

## Prerequisites

- Python 3.11+ and PyYAML.
- `gcloud` installed and authenticated.
- An active GCP project with billing.
- Compute Engine, Cloud Storage, and Secret Manager APIs.
- A Tailscale auth key stored as
  `dalston-tailscale-auth-key` in Google Secret Manager.

Run the built-in diagnostic before provisioning:

```bash
.venv/bin/python infra/scripts/dalston-gcp doctor
.venv/bin/python infra/scripts/dalston-gcp doctor --fix
```

Review every resource and region it proposes. `--fix` can enable APIs and
therefore changes project configuration.

## Preview workflow

```bash
# Provision bucket, service account, firewall, and secret placeholders.
.venv/bin/python infra/scripts/dalston-gcp setup \
  --template split \
  --project YOUR_PROJECT_ID \
  --region europe-west2

# Add the Tailscale key after setup creates the secret.
echo -n 'tskey-auth-...' | gcloud secrets versions add \
  dalston-tailscale-auth-key \
  --data-file=- \
  --project=YOUR_PROJECT_ID

# Launch control plane, then one spot GPU worker.
.venv/bin/python infra/scripts/dalston-gcp launch control-plane
.venv/bin/python infra/scripts/dalston-gcp launch gpu \
  --spot \
  --engines nemo

.venv/bin/python infra/scripts/dalston-gcp status
```

The default template uses `e2-standard-4` for the control plane and
`n1-standard-4` plus one T4 for the GPU worker in `europe-west2`. These are
configuration defaults, not availability or price guarantees.

## Day-to-day commands

```bash
.venv/bin/python infra/scripts/dalston-gcp ssh gpu --name nemo
.venv/bin/python infra/scripts/dalston-gcp down
.venv/bin/python infra/scripts/dalston-gcp up
.venv/bin/python infra/scripts/dalston-gcp terminate gpu --name NAME
```

`down` stops instances without deleting them. `terminate` deletes instances
while preserving shared disks/bucket according to the script. `teardown` is
destructive: inspect `teardown --help` and current state before using it.

## Known gaps

- No Terraform modules or declarative state backend.
- No supported parity claim with `dalston-aws`.
- Reconciliation and OS patch automation are stubs.
- Direct single-engine mode is a stub.
- The GCP script maintains its own preset dictionary; drift checks against AWS
  are not yet automated.
- Production hardening, multi-zone recovery, load balancing, private
  networking, IAM review, and cost validation remain operator work.

For a supported/documented cloud path today, use the
[AWS deployment guide](aws-deploy.md). For local/private deployments, use the
[self-hosted tutorial](self-hosted-deployment-tutorial.md).
