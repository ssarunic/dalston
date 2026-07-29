# M93: AWS Security Hardening

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Reduce the blast radius of a compromised instance to near-zero by enforcing IMDSv2, encrypting all volumes, splitting the shared IAM role, and making Tailscale the sole access and policy layer |
| **Duration**       | 3–5 days                                                     |
| **Dependencies**   | None (infra-only; no pipeline changes)                       |
| **Deliverable**    | Hardened `dalston-aws` launcher, split IAM roles, tailnet Grants policy, Tailscale SSH, interface-restricted host ports, foundational GuardDuty |
| **Status**         | Not Started                                                  |

## User Story

> *"As the operator of a self-hosted transcription service handling customer audio, I want a compromised GPU worker to be unable to delete artifacts, enroll tailnet devices, or read credentials, so that a single-instance breach stays a single-instance breach."*

---

## Motivation

A two-pass security review (2026-07-28) of the live eu-west-2 deployment found a strong baseline — no public SG ingress, scoped S3, encrypted control-plane volumes — but four substantive gaps and several launcher bugs:

1. **GPU worker accepts IMDSv1** (`HttpTokens: optional`). Any SSRF or container escape on the GPU box can steal instance-role credentials without a session token. The launcher never sets `MetadataOptions`, so workers inherit the AMI default.
2. **The GPU root volume is unencrypted, and the encrypted volume is unused.** The Ubuntu DLAMI's root device is `/dev/sda1`, but `launch_instance()` hard-codes its encrypted 100 GB mapping to `/dev/xvda`. Result: customer audio lands on an unencrypted 75 GB root while an encrypted, unformatted 100 GB volume sits attached and idle (~$9/mo wasted while running). EBS encryption-by-default is disabled in eu-west-2.
3. **Both instances share one IAM role** (`dalston-instance-profile`). A compromised GPU worker can therefore delete every object in the artifact bucket and read `/dalston/*` SSM parameters — including the reusable Tailscale auth key, i.e. the ability to enroll attacker devices into the tailnet.
4. **The cross-region launcher opens SSH to the world.** `_ensure_cross_region_sg()` creates `0.0.0.0/0:22` ingress. Not present on any live SG (both live SGs come from the main path), but every future cross-region worker would be born internet-exposed.

Secondary findings: all services on the control plane bind `0.0.0.0`, leaving the security group as the *only* layer between Postgres/Redis and the internet; the tailnet has no Grants policy (default allow-all — every personal device can reach every port); and daily security patching exists but never advances the pinned AL2023 release.

Verified as already fine (no action): S3 public-access block + SSE, scoped S3/ECR/SSM policies (modulo the shared-role issue), control-plane IMDSv2, daily patch timers on both hosts, no public SG ingress on live groups.

---

## Architecture

Trust boundaries after M93:

```
┌─────────────────────────────────────────────────────────────────────┐
│  TAILNET (Grants policy, default deny)                              │
│                                                                     │
│   Mac (admin) ──ssh/web──▶ tag:dalston-ctrl     tag:dalston-gpu     │
│                                  │                    │             │
│                                  │◀── redis 6379 ─────┤             │
│                                  │◀── otlp 4317 ──────┤             │
│                                  │◀── loki 3100 ──────┤             │
│                                  ├─── metrics 9100-9101 ──▶         │
│                                                                     │
│   iPhone / dalstonserver ──▶ (no access to dalston nodes)           │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐        ┌──────────────────────────────┐
│  dalston-ctrl-role       │        │  dalston-gpu-role            │
│  s3: Get/Put/Delete/List │        │  s3: Get/Put/List (no Delete)│
│  ssm: /dalston/ctrl/*    │        │  ssm: /dalston/gpu/*         │
│  ecr: pull dalston/*     │        │  ecr: pull dalston/*         │
└──────────────────────────┘        └──────────────────────────────┘

AWS SGs: zero ingress rules (Tailscale needs none; WireGuard is
outbound-initiated). Host ports bind tailscale0 / localhost only.
```

---

## Steps

### 93.1: Enforce IMDSv2 everywhere

**Files modified:**

- `infra/scripts/dalston-aws` — `launch_instance()`: add `MetadataOptions` to `run_kwargs`

**Deliverables:**

Launcher change (covers control plane and all GPU workers, both launch paths):

```python
"MetadataOptions": {
    "HttpTokens": "required",
    "HttpPutResponseHopLimit": 2,  # AWS-recommended for container hosts
    "HttpEndpoint": "enabled",
},
```

One-off operations (runbook, not code):

```bash
# Fix the running GPU worker
aws ec2 modify-instance-metadata-options --region eu-west-2 \
  --instance-id <gpu-instance-id> --http-tokens required --http-put-response-hop-limit 2

# Set the account+region default so nothing can regress
aws ec2 modify-instance-metadata-defaults --region eu-west-2 \
  --http-tokens required --http-put-response-hop-limit 2
```

Note: IMDSv2 blocks SSRF-style credential theft; it does **not** stop code already running on the box from fetching its own token. Least-privilege IAM (93.3) is the complementary control.

---

### 93.2: EBS encryption-by-default + root-device fix

**Files modified:**

- `infra/scripts/dalston-aws` — `launch_instance()`: resolve the AMI's actual root device instead of hard-coding `/dev/xvda`

**Deliverables:**

```python
ami = ec2.describe_images(ImageIds=[ami_id])["Images"][0]
root_device = ami["RootDeviceName"]  # "/dev/sda1" on Ubuntu DLAMI, "/dev/xvda" on AL2023
# use root_device in BlockDeviceMappings; sized ROOT_VOLUME_SIZE, gp3, Encrypted=True
```

This makes the encrypted mapping actually apply to the root volume and stops provisioning the orphan 100 GB disk. The current worker self-heals on next relaunch (`DeleteOnTermination`).

One-off operation, repeated in any region GPUs may launch:

```bash
aws ec2 enable-ebs-encryption-by-default --region eu-west-2
```

---

### 93.3: Split IAM roles (control plane vs GPU)

**Files modified:**

- `infra/scripts/dalston-aws` — `create_iam_role()` → create two roles/instance profiles; `cmd_setup()` and GPU launch paths pass the appropriate profile

**Deliverables:**

- `dalston-ctrl-role` / `dalston-ctrl-profile`: current S3 policy unchanged (Get/Put/Delete/List — orchestrator cleanup needs Delete), SSM `GetParameter` on `/dalston/ctrl/*` **and** `/dalston/gpu/*`, ECR pull on `dalston/*`.
- `dalston-gpu-role` / `dalston-gpu-profile`: S3 `GetObject`/`PutObject`/`ListBucket` on the artifact bucket — **no `DeleteObject`**; SSM `GetParameter` on `/dalston/gpu/*` only; ECR pull on `dalston/*`. (Path-scoping S3 to `models/*`, `jobs/*`, `tasks/*` prefixes is a refinement — land the no-Delete split first.)
- SSM parameter migration: move the Tailscale auth key to `/dalston/ctrl/tailscale-auth-key`; create a **separate GPU-join key** at `/dalston/gpu/tailscale-auth-key` that is **tagged (`tag:dalston-gpu`), ephemeral, and pre-authorized** — a stolen GPU key can then only mint nodes that the Grants policy (93.4) confines to GPU-level access, and ephemeral nodes vanish on disconnect.
- State file (`aws-state.yaml`) gains `gpu_instance_profile`; `up`/`launch` paths select profile by role.

Live migration: `aws ec2 replace-iam-instance-profile-association` on the control plane (no restart needed); GPU workers pick up the new profile on next relaunch.

---

### 93.4: Tailnet Grants, Tailscale SSH, zero SSH ingress

**Files modified:**

- `infra/scripts/dalston-aws` — `_tailscale_cloud_init_block()`: add `--ssh` and `--advertise-tags`; `cmd_ssh()` (SSH helper): prefer `tailscale ssh`, drop the PEM path; `create_security_group()` / `create_gpu_worker_security_group()`: stop creating port-22 rules; `_ensure_cross_region_sg()`: **remove the `0.0.0.0/0:22` rule entirely** (cross-region workers need no ingress — all traffic is Tailscale)

**Deliverables:**

Tailnet policy (admin console, done *before* enabling — order matters, see Deployment):

- Tag definitions: `tag:dalston-ctrl`, `tag:dalston-gpu` (owner: your login).
- Grants (not legacy ACLs): Mac → both tags, all ports; `tag:dalston-gpu` → `tag:dalston-ctrl` on 6379, 4317, 3100; `tag:dalston-ctrl` → `tag:dalston-gpu` on 9100–9101; SSH grants: Mac → both tags as root/ubuntu/ec2-user. Everything else (iPhone, `dalstonserver`) gets nothing.

After `tailscale ssh <host>` is verified working from the Mac: revoke the existing port-22 rules on both live SGs. The `100.64.0.0/10` rules never gated tunnel traffic anyway (it arrives inside WireGuard, decrypted on-host).

Explicitly excluded: Tailscale SSH session recording (beta, plan-gated, needs a recorder node, captures secrets in terminal output).

---

### 93.5: Interface-restrict published ports + generated credentials

**Files modified:**

- `docker-compose.yml` — every published port parameterized with a **loopback default**: `"${DALSTON_BIND_IP:-127.0.0.1}:6379:6379"` (same for 8000, 9000/9001, 16686, 4317/4318, 9090, 3001, 3100); Postgres uses its own `"${DALSTON_PG_BIND_IP:-127.0.0.1}"` so it stays on loopback even on AWS. Grafana: `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-dalston}`
- `infra/docker/docker-compose.aws.yml` — gateway override becomes `127.0.0.1:8000:8000` (loopback for `tailscale serve`, which proxies from localhost; direct tailnet access comes from the base mapping via `DALSTON_BIND_IP`)
- `infra/scripts/dalston-aws` — `ensure_control_plane_secrets()` (called from `setup` and `up`) creates generate-once SecureStrings `/dalston/ctrl/postgres-password` and `/dalston/ctrl/grafana-admin-password` (never overwritten — the Postgres data dir keeps the password it was initialised with); a `secrets` subcommand prints them; the control-plane user data resolves credentials as **existing `.env` → SSM → legacy default**, writes them into `.env.aws`/`.env` (chmod 600), and builds `DATABASE_URL` from the resolved value; `/usr/local/bin/dalston-bind-ip.sh` (systemd `ExecStartPre`, ordered after `dalston-tailscale.service`) upserts `DALSTON_BIND_IP=$(tailscale ip -4)` into `.env.aws` on every service start (the IP changes on relaunch)

**Deliverables:**

- On AWS: Postgres published on loopback only (nothing remote uses it — GPU engines speak Redis + S3; admin access is `tailscale ssh` + local psql). All other services publish on the `tailscale0` address only, so the SG stops being the sole layer for Docker-published ports (Docker's DNAT bypasses host INPUT rules; bind address is the reliable control).
- Postgres and Grafana passwords are no longer the publicly-known repo defaults on AWS; they are generated once, recorded in SSM (retrievable via `dalston-aws secrets`), and never need to live in the operator's local `.env`.
- Local dev: known default passwords are kept deliberately (loopback-only binding is the control that matters on a laptop; random local secrets add bootstrap friction with no payoff). The bind-default change means the dev stack is no longer reachable from the LAN unless `DALSTON_BIND_IP=0.0.0.0` is set explicitly.
- **Migration for pre-M93 deployments** (data volume initialised with `password`): boot is safe — the `.env`-first precedence keeps old credentials working. To rotate: `new=$(dalston-aws secrets ...)`; `docker compose exec postgres psql -U dalston -c "ALTER USER dalston PASSWORD '<new>'"`; update `POSTGRES_PASSWORD` in `/data/dalston/.env`; `systemctl restart dalston`. Grafana similarly persists its admin password in `grafana-data` — rotate with `docker compose exec grafana grafana-cli admin reset-admin-password <new>`.
- Verify before merge: Prometheus→GPU scrape and GPU→ctrl Redis/Loki/OTLP paths already use Tailscale MagicDNS names (`CTRL_PLANE_HOST="dalston-control-plane"`), so they keep working; the SG-to-SG rules for 5432/6379/4317/3100 are vestigial and may be dropped or kept as harmless defence-in-depth.

---

### 93.6: Foundational GuardDuty

**Files modified:** none (account operation)

**Deliverables:**

Foundational detection only (CloudTrail + VPC flow log + DNS analysis) — directly detects instance-credential exfiltration, the exact scenario 93.1/93.3 defend against. Skip the S3/Runtime/Malware protection plans (cost without proportionate value here).

```bash
aws guardduty create-detector --region eu-west-2 --enable \
  --finding-publishing-frequency ONE_HOUR
# EventBridge rule: GuardDuty finding severity >= 4 → SNS → email
```

---

### 93.7: AL2023 release advancement

**Files modified:**

- `infra/scripts/dalston-aws` — control-plane user data: add a monthly systemd timer alongside the existing daily security-patch timer

**Deliverables:**

The daily `dnf update --security` patches within the *pinned* AL2023 release; new releases are never adopted. Add `dalston-release-upgrade.timer` (monthly):

```bash
dnf check-release-update 2>&1 | tee -a /var/log/dalston-patch.log
dnf upgrade -y --releasever=latest >> /var/log/dalston-patch.log 2>&1
needs-restarting -r || echo "REBOOT REQUIRED $(date)" >> /var/log/dalston-patch.log
```

Reboot stays manual (operator-initiated during a quiet window); the log line is the signal. GPU workers need nothing — they get a fresh DLAMI every launch.

---

## Non-Goals

- **Removing public IPs / NAT gateway** — nothing listens publicly and Tailscale needs no ingress; a NAT gateway costs more (~$37/mo) than the residual risk justifies.
- **Tailscale workload-identity federation** (EC2 joins tailnet via IAM identity, no stored key) — the right end-state, but plan-dependent; the tagged-ephemeral key in 93.3 captures most of the benefit. Revisit once verified available on the current Tailscale plan.
- **S3 path-scoped GPU policy** — refinement of 93.3; needs a key-layout audit, and no-Delete is the 80% win.
- **GuardDuty runtime/S3/malware protection tiers** — cost disproportionate for a single-admin, tailnet-gated stack.
- **Tailscale SSH session recording** — beta, plan-gated, records secrets; add only if an audit requirement appears.
- **fail2ban** — no internet-facing SSH exists once 93.4 lands.
- **Region migration (Stockholm)** — separate cost decision, orthogonal to hardening.

---

## Deployment

Ordering constraints:

1. **93.4 policy before enforcement**: write tag definitions + Grants (including SSH grants) in the admin console *before* enabling `--ssh` or removing SG port-22 rules; verify `tailscale ssh` from the Mac against both hosts *before* revoking the old rules. Keep one plain-SSH session open during the cutover.
2. **93.3 before removing the old shared role**: swap the control-plane association live, relaunch the GPU worker, confirm both boot clean (S3 read/write, tailnet join, ECR pull), then delete `dalston-instance-profile`.
3. **93.1/93.2 account defaults** are safe any time; launcher changes take effect on next `launch`/`up`.
4. **93.5** requires one control-plane stack restart (`dalston.service`) after the bootstrap change; do it together with a planned relaunch.

---

## Verification

```bash
# 93.1 — both instances require IMDSv2 (expect: required / required)
aws ec2 describe-instances --region eu-west-2 \
  --filters "Name=tag:Project,Values=dalston" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].MetadataOptions.HttpTokens"

# 93.2 — every attached volume encrypted, and exactly one root volume per GPU worker
aws ec2 describe-volumes --region eu-west-2 --query "Volumes[].{id:VolumeId,enc:Encrypted,state:State}"
aws ec2 get-ebs-encryption-by-default --region eu-west-2   # expect: true

# 93.3 — GPU role cannot delete artifacts (expect: implicitDeny)
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<acct>:role/dalston-gpu-role \
  --action-names s3:DeleteObject \
  --resource-arns "arn:aws:s3:::dalston-artifacts-<acct>/jobs/test" \
  --query "EvaluationResults[].EvalDecision"

# 93.4 — no SSH ingress anywhere; tailscale ssh works
aws ec2 describe-security-groups --region eu-west-2 \
  --query "SecurityGroups[?IpPermissions[?FromPort==\`22\`]].GroupId"   # expect: []
tailscale ssh ec2-user@dalston-control-plane -- echo OK

# 93.5 — from a non-admin vantage (or public internet): nothing answers
for p in 5432 6379 8000 3001 9090 16686 3100 4317; do nc -z -G 3 <ctrl-public-ip> $p && echo "FAIL :$p"; done
# on-host: postgres bound to 127.0.0.1, others to the 100.x address only
tailscale ssh ec2-user@dalston-control-plane -- 'sudo ss -tlnp | grep -E "5432|6379|8000|3001"'

# 93.6 — detector active
aws guardduty list-detectors --region eu-west-2

# 93.7 — release timer installed and logged
tailscale ssh ec2-user@dalston-control-plane -- \
  'systemctl list-timers | grep dalston; tail -5 /var/log/dalston-patch.log'
```

---

## Checkpoint

- [ ] `MetadataOptions` in `launch_instance()`; running GPU and regional default set to `required`
- [ ] EBS encryption-by-default enabled in eu-west-2; launcher uses AMI `RootDeviceName`; next GPU launch has a single, encrypted root volume
- [ ] `dalston-ctrl-role` / `dalston-gpu-role` split live; GPU role denies `s3:DeleteObject`; shared `dalston-instance-profile` deleted
- [ ] Tailscale auth keys split by path; GPU key is tagged + ephemeral
- [ ] Grants policy active: personal devices isolated, GPU↔ctrl limited to service ports
- [ ] Tailscale SSH verified from Mac; all port-22 SG rules removed; `_ensure_cross_region_sg()` creates zero ingress
- [ ] `dalston-aws ssh` uses `tailscale ssh` (PEM retired)
- [ ] AWS deploys publish Postgres on loopback and all other services on `tailscale0` only; local dev defaults to loopback (LAN access is opt-in via `DALSTON_BIND_IP=0.0.0.0`)
- [ ] Postgres/Grafana passwords generated once into `/dalston/ctrl/*` SSM SecureStrings, readable via `dalston-aws secrets`; hardcoded `password`/`dalston` gone from the bootstrap path
- [ ] Foundational GuardDuty enabled with severity≥4 alerting
- [ ] Monthly AL2023 release-upgrade timer present; reboot-required signal lands in the patch log
