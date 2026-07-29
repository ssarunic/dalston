# M94: Automated Postgres Backups

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Nightly logical Postgres dumps to S3 with 30-day retention, plus daily EBS snapshots of the data volume, so a bad migration, fat-fingered delete, or volume loss costs at most 24 hours of metadata |
| **Duration**       | 1–2 days                                                     |
| **Dependencies**   | None (reuses the M91 control-plane provisioning patterns)    |
| **Deliverable**    | `dalston-aws backup` command (`--once` / `--provision`), `dalston-backup` systemd timer on the control plane, `backups/` S3 lifecycle rule, DLM daily-snapshot policy, restore runbook |
| **Status**         | Completed — live since 2026-07-29 (provisioned, first dump verified, restore drill passed) |

## User Story

> *"As the operator of a self-hosted transcription service, I want the control-plane Postgres database backed up automatically every night, so that losing the database — to a bad migration, an accidental delete, or an EBS failure — never means losing the API keys, model registry, and job history."*

---

## Outcomes

| Scenario | Current | After M94 |
| -------- | ------- | --------- |
| Bad Alembic migration or fat-fingered `DELETE` corrupts the DB | Unrecoverable — the only copy of the data is the live EBS volume | `pg_restore` last night's dump; at most 24 h of job metadata lost |
| EBS data volume or AZ is lost | Postgres, Redis state, and manual `.env` edits are all gone | Restore the volume from a ≤24 h DLM snapshot; Postgres additionally restorable from S3 independent of EBS |
| Operator wants to inspect last week's DB state | Impossible | Pull any of the last 30 nightly dumps from `s3://…/backups/postgres/` |

---

## Motivation

The control-plane Postgres (jobs, API keys, model registry, usage records) runs as a Docker container with its data dir bind-mounted to the EBS data volume at `/data/postgres` ([docker-compose.aws.yml:31-33](../../infra/docker/docker-compose.aws.yml#L31-L33)). There is **no backup of any kind** today: no dumps, no EBS snapshots, no bucket versioning. Every durability guarantee rests on a single EBS volume, and nothing protects against logical corruption (bad migration, accidental delete) at all — a snapshot of a corrupted DB is still corrupted, which is why the primary mechanism is a *logical* dump.

Everything needed already exists: the control-plane IAM role has bucket-wide `s3:PutObject` ([dalston-aws:723](../../infra/scripts/dalston-aws#L723)), so **no IAM changes are required**; and M91 established the exact provisioning pattern to clone — a bash provision script baked into cloud-init at launch and retrofittable over SSH ([`_autoscale_provision_script`, dalston-aws:1182](../../infra/scripts/dalston-aws#L1182), [`_autoscale_provision_remote`, dalston-aws:3355](../../infra/scripts/dalston-aws#L3355)), plus a nightly `OnCalendar` timer precedent in `dalston-patch.timer` ([dalston-aws:1543](../../infra/scripts/dalston-aws#L1543)).

---

## Architecture

```
┌─────────────────────────── CONTROL PLANE (EC2) ───────────────────────────┐
│                                                                           │
│  dalston-backup.timer (OnCalendar 04:15 UTC, Persistent=true)             │
│        │                                                                  │
│        ▼                                                                  │
│  dalston-backup.service (oneshot, EnvironmentFile=/data/dalston/.env.aws) │
│        │  python3.11 dalston-aws backup --once                            │
│        ▼                                                                  │
│  docker exec dalston-postgres-1 pg_dump -Fc  ──▶  /data/backups/ (stage)  │
│        │                                              │ boto3 upload     │
│        ▼                                              ▼                   │
│  journalctl -u dalston-backup      s3://dalston-artifacts-<acct>/         │
│                                      backups/postgres/dalston-<ts>.dump   │
│                                      (lifecycle: expire after 30 days)    │
└───────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── AWS (account-level) ──────────────────────────┐
│  DLM policy: daily 04:45 UTC snapshot of volumes tagged                   │
│  dalston:backup=daily (the /data volume) — retain 7                       │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions (fixed for this milestone)

1. **Logical `pg_dump -Fc` (custom format) is the primary mechanism** — compressed, `pg_restore`-able with `--clean`, supports table-selective restore, and protects against logical corruption, which EBS snapshots cannot.
2. **The nightly tick is `dalston-aws backup --once` in Python/boto3**, not a bash pipeline in the unit file — mirrors `autoscale --once` and the project rule that AWS scripting is Python+boto3, not bash.
3. **`backup --once` reads `DALSTON_S3_BUCKET`/`AWS_REGION` from the environment** (the unit sets `EnvironmentFile=/data/dalston/.env.aws`) and does **not** call `require_state()` — the operator state file does not exist on the control plane, same reason `autoscale --once` uses a context file.
4. **Dumps land in the existing artifacts bucket** under `backups/postgres/` — no new bucket, no IAM change; retention is a third lifecycle rule (30 days) beside the existing `temp/` (7 d) and `jobs/` (30 d) rules ([`S3_LIFECYCLE_RULES`, dalston-aws:623](../../infra/scripts/dalston-aws#L623)).
5. **Backups are default-on for every control-plane launch** — unlike opt-in `--autoscale`, the provision block is appended unconditionally in `generate_user_data`. A backup you must remember to enable is not a backup.
6. **Timer fires at 04:15 UTC** — after the 03:30 security-patch timer, in the overnight lull; `Persistent=true` catches up after downtime.
7. **DLM daily snapshots (retain 7) are the second layer, not the primary** — crash-consistent (equivalent to a power-cut; Postgres WAL recovery handles it), coarse-grained (whole volume), but also covers Redis state and manual `.env` edits. Tag-targeted (`dalston:backup=daily` on the data volume) so the policy is declarative.
8. **No RDS migration** — managed backups/PITR are not worth ~$30+/mo and an infra rebuild for a single-node self-hosted stack.

---

## Steps

### 94.1: `dalston-aws backup --once` — the dump-and-upload tick

**Files modified:**

- `infra/scripts/dalston-aws` — new `cmd_backup()` ([dalston-aws:3669](../../infra/scripts/dalston-aws#L3669)) + `_backup_once()` ([dalston-aws:3471](../../infra/scripts/dalston-aws#L3471)); `backup` subparser registered after `autoscale` ([dalston-aws:5427](../../infra/scripts/dalston-aws#L5427)); usage line added to the module docstring

**Deliverables:**

Runs *on the control plane*. Reads `DALSTON_S3_BUCKET` and `AWS_REGION` from env (fails loudly if unset). Then:

```
docker exec dalston-postgres-1 pg_dump -U dalston -Fc dalston
  → stage to /data/backups/dalston-<UTC ts>.dump
  → sanity-check size > 0
  → boto3 upload to s3://$DALSTON_S3_BUCKET/backups/postgres/dalston-<ts>.dump
  → delete local staging file
  → log outcome (journal)
```

Postgres user/db are both `dalston`; the container is `dalston-postgres-1` (Compose v2 default naming — no `container_name` is set, project name is the `/data/dalston` directory basename). `pg_dump` runs inside the container so no client tools are needed on the host.

---

### 94.2: Provision script + systemd timer, default-on at launch

**Files modified:**

- `infra/scripts/dalston-aws` — new `_backup_provision_script()` ([dalston-aws:1247](../../infra/scripts/dalston-aws#L1247)); appended unconditionally in `generate_user_data()` (same append point as the autoscale block, [dalston-aws:1588](../../infra/scripts/dalston-aws#L1588))

**Deliverables:**

Bash provision script (single source of truth, used at launch and by `--provision`), cloned from the autoscaler's:

- Idempotent `dnf install python3.11` + `pip install boto3` (the CP may not have the autoscaler installed).
- `mkdir -p /data/backups`
- **Repo-freshness guard**: mark `/data/dalston` as `safe.directory` (the mount is uid-1000-owned while provisioning runs as root — git would otherwise reject the pull as dubious ownership), `git pull --ff-only`, then verify `dalston-aws backup --help` works — refuses to install a timer whose `ExecStart` points at a pre-M94 checkout with no `backup` subcommand.
- `/etc/systemd/system/dalston-backup.service` — `Type=oneshot`, `After=docker.service dalston.service`, `EnvironmentFile=/data/dalston/.env.aws`, `ExecStart=… backup --once`, plus bounded retries for transient failures: `Restart=on-failure`, `RestartSec=10min`, `StartLimitBurst=4` in a 2 h window (oneshot+Restart is fine on AL2023's systemd 252), and `TimeoutStartSec=45min` so a hung dump/upload is killed and retried rather than blocking every later timer activation (oneshot has no start timeout by default).
- `/etc/systemd/system/dalston-backup.timer` — `OnCalendar=*-*-* 04:15:00`, `Persistent=true`, `WantedBy=timers.target`
- `systemctl daemon-reload && systemctl enable --now dalston-backup.timer`
- Boot-race guard in `backup --once` itself: waits up to 5 minutes for `pg_isready` in the postgres container before dumping — `Persistent=true` catch-up runs can otherwise fire before compose has Postgres up.

---

### 94.3: `dalston-aws backup --provision` — retrofit + bucket/DLM setup from the operator machine

**Files modified:**

- `infra/scripts/dalston-aws` — new `_backup_provision_remote()` ([dalston-aws:3631](../../infra/scripts/dalston-aws#L3631)) mirroring `_autoscale_provision_remote()` (minus the IAM-profile swap — no IAM changes needed); shared SSH-pipe helper `_pipe_provision_script_over_ssh()` extracted for both paths ([dalston-aws:3340](../../infra/scripts/dalston-aws#L3340)); `audit("backup.provision", …)`

**Deliverables:**

One command does everything for an existing deployment:

1. Ensures the `backups/` lifecycle rule on the bucket (94.4) and the DLM policy (94.5) — both idempotent boto3 calls from the operator machine.
2. Pipes the provision script (94.2) over SSH to the running control plane (base64 → `sudo bash`), resolved via Tailscale like autoscale.
3. Prints the follow-up commands (`systemctl list-timers`, `journalctl -u dalston-backup`).

New launches need nothing — 94.2 is baked into cloud-init.

---

### 94.4: S3 lifecycle rule for `backups/`

**Files modified:**

- `infra/scripts/dalston-aws` — `create_s3_bucket()` gains a third rule; shared helper so `backup --provision` can apply it to an existing bucket

**Deliverables:**

```python
{
    "ID": "cleanup-backups",
    "Status": "Enabled",
    "Filter": {"Prefix": "backups/"},
    "Expiration": {"Days": 30},
}
```

Note `put_bucket_lifecycle_configuration` **replaces** the full rule set — the helper must always write all three rules (`temp/`, `jobs/`, `backups/`), not append.

---

### 94.5: DLM daily snapshot policy for the data volume

**Files modified:**

- `infra/scripts/dalston-aws` — tag the data volume `dalston:backup=daily` at creation; `_ensure_dlm_policy()` creates the DLM service role (trust `dlm.amazonaws.com` + managed `AWSDataLifecycleManagerServiceRole` policy) and the lifecycle policy if absent; called from `backup --provision` (and tags the existing volume retroactively)

**Deliverables:**

- Policy: `ResourceTypes=[VOLUME]`, `TargetTags=[dalston:backup=daily]`, schedule daily at 04:45 UTC, `RetainRule={Count: 7}`, `CopyTags=true`.
- `_ensure_dlm_policy()` **reconciles, not just creates**: an existing policy (matched by description) is reset to ENABLED with the canonical role/tags/schedule via `update_lifecycle_policy` — a DISABLED or hand-edited policy would otherwise pass a presence check while taking no snapshots.
- **The launch path also runs the operator-side pieces**: `_launch_control_plane` tags the (possibly reattached pre-M94) data volume, ensures the bucket lifecycle rules, and ensures the DLM policy — so a fresh `setup` → `launch` gets both backup layers without ever running `backup --provision`.
- Snapshots are incremental, so the model cache on the volume adds little ongoing cost.

---

## Restore Runbook

**Postgres from a dump** (on the control plane, via `tailscale ssh` / `dalston-aws ssh`):

```bash
# 0) Load the bucket name — interactive shells don't inherit the unit's EnvironmentFile
export DALSTON_S3_BUCKET=$(grep '^DALSTON_S3_BUCKET=' /data/dalston/.env.aws | cut -d= -f2)

# 1) Quiesce writers. Leave postgres itself up — do NOT stop dalston.service,
#    its ExecStop takes the whole compose stack down, postgres included.
docker stop dalston-gateway-1 dalston-orchestrator-1 dalston-stt-prepare-1

# 2) Find the dump and restore atomically. --single-transaction makes it
#    all-or-nothing: any error rolls the whole restore back instead of
#    leaving a partially restored database (pg_restore's default is to
#    continue past errors).
aws s3 ls s3://$DALSTON_S3_BUCKET/backups/postgres/
aws s3 cp s3://$DALSTON_S3_BUCKET/backups/postgres/dalston-<ts>.dump - \
  | docker exec -i dalston-postgres-1 pg_restore -U dalston -d dalston \
      --clean --if-exists --no-owner --single-transaction

# 3) Restart the stack so writers and pooled connections re-establish
sudo systemctl restart dalston.service
```

**Volume from a DLM snapshot**: create a volume from the snapshot in the CP's AZ, stop `dalston.service`, unmount `/data`, detach the old volume, attach the new one at the same device, mount, start. (Coarse — prefer the pg_restore path unless the volume itself is gone.)

---

## Non-Goals

- **RDS migration** — evaluated and rejected: managed PITR is not worth the monthly cost and rebuild for a single-node stack (Design Decision 8).
- **WAL archiving / PITR (wal-g, pgBackRest)** — 24 h RPO is fine for job metadata; revisit only if the DB ever holds data that can't tolerate a day's loss.
- **Redis dump backups** — Redis holds transient queue/session state; the DLM snapshot covers it crash-consistently, which is proportionate.
- **Bucket versioning** — retention via lifecycle is sufficient; versioning adds cost/complexity without a concrete threat it addresses here.
- **Backup-failure alerting** — no alerting substrate to hang it on yet; `Persistent=true` + `journalctl` is the check. Revisit with the observability stack.
- **Cross-region backup copies** — single-region deployment; disaster scope is the AZ/volume, covered.
- **`backup --list` / `--restore` CLI helpers** — restore is a documented one-liner; keep the command surface minimal.

---

## Deployment

No ordering constraints for the code merge. For the live deployment:

1. Merge; then on the operator machine: `git pull && ./infra/scripts/dalston-aws backup --provision`.
2. Verify the timer fired (or run one immediately: `sudo systemctl start dalston-backup.service` on the CP) and the object landed in S3.
3. Operator-run — Claude does not touch the live control plane for this milestone.

---

## Verification

```bash
# 94.1/94.2 — timer installed and a dump landed (run on/against the live CP after --provision)
tailscale ssh ec2-user@dalston-control-plane -- systemctl list-timers dalston-backup.timer
# expect: next-fire time ~04:15 UTC

tailscale ssh ec2-user@dalston-control-plane -- sudo systemctl start dalston-backup.service
tailscale ssh ec2-user@dalston-control-plane -- journalctl -u dalston-backup -n 20 --no-pager
# expect: "backup uploaded s3://dalston-artifacts-.../backups/postgres/dalston-<ts>.dump (<size>)"

aws s3 ls s3://dalston-artifacts-<acct>/backups/postgres/
# expect: one .dump object per night, non-zero size

# 94.4 — lifecycle rule present alongside the existing two
aws s3api get-bucket-lifecycle-configuration --bucket dalston-artifacts-<acct> \
  --query "Rules[].{id:ID,prefix:Filter.Prefix,days:Expiration.Days}"
# expect: temp/=7, jobs/=30, backups/=30

# 94.5 — DLM policy active and volume tagged
aws dlm get-lifecycle-policies --query "Policies[].{id:PolicyId,state:State,desc:Description}"
# expect: one ENABLED dalston policy
aws ec2 describe-volumes --volume-ids <data-volume-id> --query "Volumes[].Tags"
# expect: dalston:backup=daily
aws ec2 describe-snapshots --owner-ids self \
  --filters "Name=tag:dalston:backup,Values=daily" --query "Snapshots[].StartTime"
# expect: entries appear after the first 04:45 UTC window

# Restore drill — prove a dump actually restores (scratch DB, non-destructive).
# pg_restore must read stdin DIRECTLY (no `sh -c … /dev/stdin` — that fails
# with "did not find magic string" on the alpine postgres image).
tailscale ssh ec2-user@dalston-control-plane -- bash -c '
  export DALSTON_S3_BUCKET=$(grep "^DALSTON_S3_BUCKET=" /data/dalston/.env.aws | cut -d= -f2)
  docker exec dalston-postgres-1 createdb -U dalston restore_test
  aws s3 cp "s3://$DALSTON_S3_BUCKET/backups/postgres/<latest>.dump" - \
    | docker exec -i dalston-postgres-1 pg_restore -U dalston -d restore_test --no-owner
  docker exec dalston-postgres-1 psql -U dalston -d restore_test -c "select count(*) from api_keys"
  docker exec dalston-postgres-1 dropdb -U dalston restore_test'
# expect: row count > 0, no errors
# (drill passed 2026-07-29: 7 api_keys, 2250 jobs restored from the first live dump)
```

---

## Checkpoint

- [x] `dalston-aws backup --once` dumps and uploads to `backups/postgres/` (fails loudly on missing env / empty dump)
- [x] `dalston-backup.timer` (04:15 UTC, `Persistent=true`) baked into cloud-init for every new control-plane launch
- [x] `dalston-aws backup --provision` retrofits a running control plane over SSH and is idempotent
- [x] `backups/` 30-day lifecycle rule written by `create_s3_bucket()` and ensured by `--provision` (all three rules preserved)
- [x] DLM daily policy (retain 7) targets the tagged data volume; service role auto-created
- [x] Fresh `setup` → `launch` gets both layers with no manual step (launch path tags volume + ensures lifecycle and DLM policy)
- [x] Unit tests cover the provision script, lifecycle rules, user-data assembly, and CLI wiring (`tests/unit/test_dalston_aws_backup.py`)
- [x] Restore drill from a real dump passes (scratch-DB `pg_restore` returns data — 7 api_keys, 2250 jobs on 2026-07-29)
- [x] Live control plane provisioned; first dump in S3 (`dalston-20260729T125435Z.dump`, 0.8 MiB); DLM policy ENABLED; timer next-fire 04:15 UTC
