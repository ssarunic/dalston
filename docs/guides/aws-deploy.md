# Deploying Dalston on AWS

A step-by-step guide to running Dalston on AWS using the `dalston-aws` script.
No Terraform required — just the AWS CLI.

## Prerequisites

1. **AWS CLI** configured with credentials:
   ```bash
   aws sts get-caller-identity
   # Should print your account ID. If not: aws configure
   ```

2. **The script** lives at `infra/scripts/dalston-aws`. Make it easy to run:
   ```bash
   # Option A: symlink to your PATH
   ln -s $(pwd)/infra/scripts/dalston-aws /usr/local/bin/dalston-aws

   # Option B: just use the full path
   ./infra/scripts/dalston-aws help
   ```

## What it creates

The script provisions exactly 5 AWS resources:

| Resource | What | Monthly cost (approx) |
|---|---|---|
| **S3 bucket** | Stores uploaded audio, job outputs, temp files | ~$1 (usage-based) |
| **IAM role** | Lets the EC2 instance access S3 without API keys | Free |
| **Security group** | Firewall: only SSH from Tailscale (100.64.0.0/10) | Free |
| **EC2 instance** | The server running everything | $50–$150 (see scenarios) |
| **EBS volume** | 50 GB persistent data (Postgres, Redis, models) | ~$4 |

Everything is tagged with `Project=dalston` so you can find it in the AWS console.

---

## Scenario 1: Single GPU instance (recommended start)

Best for: getting started, small-to-medium workloads, one person.

```
┌─────────────────────────────────────────────┐
│  g5.xlarge ($1.006/hr ≈ $150/mo on-demand) │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Gateway  │  │  Redis   │  │ Postgres │  │
│  │ :8000    │  │  :6379   │  │  :5432   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Orchestrator │  │ GPU Engines          │ │
│  │              │  │  transcribe (NeMo)   │ │
│  │              │  │  align (phoneme)     │ │
│  │              │  │  diarize (pyannote)  │ │
│  │              │  │  RT: NeMo, Whisper   │ │
│  └──────────────┘  └──────────────────────┘ │
│                    ┌──────────────────────┐  │
│                    │ 50 GB EBS (/data)   │  │
│                    │  postgres/  redis/   │  │
│                    │  models/             │  │
│                    └──────────────────────┘  │
└─────────────────────────────────────────────┘
         ↕ S3 (audio uploads + outputs)
```

### Steps

```bash
# 1. Create everything
dalston-aws setup
```

Output:
```
[dalston-aws] Setting up Dalston on AWS
[dalston-aws]   Region:   eu-west-2
[dalston-aws]   Scenario: gpu
[dalston-aws]   Spot:     false
[dalston-aws]   Account:  123456789012
[dalston-aws] Creating S3 bucket: dalston-artifacts-123456789012
[dalston-aws] S3 bucket created with encryption + lifecycle rules
[dalston-aws] Creating IAM role: dalston-ec2-role
[dalston-aws] IAM role + instance profile ready
[dalston-aws] Creating key pair: dalston-key
[dalston-aws] Private key saved to ~/.dalston/dalston-key.pem
[dalston-aws] Creating security group: dalston-sg
[dalston-aws] Security group sg-0abc123 created (SSH from Tailscale)
[dalston-aws] Launching GPU instance (g5.xlarge)...
[dalston-aws] Waiting for i-0def456 to be running...
[dalston-aws] Waiting for volume vol-0ghi789 to be available...
==========================================
[dalston-aws] Setup complete!
==========================================
[dalston-aws] Instance: i-0def456 (3.10.45.67)

Next steps:
  1. SSH to the instance:
     ssh -i ~/.dalston/dalston-key.pem ec2-user@3.10.45.67
  2. Set up Tailscale:
     sudo tailscale up
  3. Clone your repo to /data/dalston and start:
     sudo systemctl start dalston
```

```bash
# 2. SSH in and set up Tailscale
dalston-aws ssh
# On the instance:
sudo tailscale up
# Follow the URL to authenticate — note the Tailscale IP (e.g., 100.100.1.5)

# 3. Clone your repo and start
cd /data/dalston
git clone https://github.com/you/dalston.git .
sudo systemctl start dalston

# 4. Access the API via Tailscale
curl http://100.100.1.5:8000/health
```

### With spot pricing (~65% cheaper)

```bash
dalston-aws setup --spot
# Same g5.xlarge but ~$0.35/hr instead of $1.00/hr
# AWS may reclaim the instance with 2 min warning — it auto-stops (not terminates)
```

---

## Scenario 2: CPU-only instance

Best for: testing the pipeline without GPU costs, or if your workload is small
enough that CPU transcription speed is acceptable.

```
┌─────────────────────────────────────────────┐
│  t3.xlarge ($0.166/hr ≈ $25/mo on-demand)  │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Gateway  │  │  Redis   │  │ Postgres │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Orchestrator │  │ CPU Engines          │ │
│  │              │  │  faster-whisper      │ │
│  │              │  │  align (CPU)         │ │
│  │              │  │  diarize (CPU, slow) │ │
│  │              │  │  pii-detect, merge   │ │
│  │              │  │  RT: Whisper, NeMo   │ │
│  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────┘
```

```bash
dalston-aws setup --cpu
```

CPU engines are ~5-10x slower than GPU for transcription and diarization, but
the pipeline works identically. Good enough for a few files per day.

---

## Scenario 3: Start small, add GPU later

This is the recommended path if you're not sure about costs yet. Start CPU-only,
then add a GPU worker when you need more throughput.

```bash
# Start with CPU
dalston-aws setup --cpu

# ... use it for a while, transcription is slow ...

# Add a GPU worker (spot = cheap)
dalston-aws add-gpu --spot
```

This creates a second instance:

```
┌─────────────────────────┐     ┌──────────────────────────┐
│  t3.xlarge (control)    │     │  g5.xlarge (GPU worker)  │
│                         │     │                          │
│  Gateway    Orchestrator│     │  GPU Engines             │
│  Redis      Postgres    │◄────│   transcribe (NeMo)     │
│                         │     │   align (phoneme)        │
│  CPU engines (prepare,  │     │   diarize (NeMo MSDD)    │
│   merge, pii-detect)    │     │   RT: NeMo, Whisper      │
└─────────────────────────┘     └──────────────────────────┘
     ↕ S3                             ↕ S3
```

The GPU worker connects to the control plane's Redis and Postgres over the
private VPC network. The security group is configured automatically to allow
ports 6379 (Redis) and 5432 (Postgres) between the two instances.

```bash
# SSH to the GPU worker to set up Tailscale
dalston-aws ssh gpu
sudo tailscale up
sudo systemctl start dalston-gpu

# Done — GPU engines start polling Redis queues immediately
```

### Removing the GPU worker

```bash
dalston-aws remove-gpu
# Confirms, then terminates instance + deletes volume + security group
# State reverts to single-instance CPU
```

---

## Accessing the control plane over HTTPS

The control-plane bootstrap automatically runs `tailscale serve` to expose the
gateway at `https://<node>.<tailnet>.ts.net/` with a real Let's Encrypt
certificate. Traffic stays on the Tailscale overlay interface (`tailscale0`),
so there are **no security group changes** and **no public port 443
exposure** — the Dalston SG is unchanged from the default "SSH from Tailscale
only" setup.

### One-time tailnet setup

Before the control plane can produce a working HTTPS URL, enable MagicDNS
HTTPS certificates **once** in your tailnet admin console. This is a
tailnet-wide toggle and cannot be set from code.

1. Go to <https://login.tailscale.com/admin/dns>.
2. Enable **MagicDNS**.
3. Enable **HTTPS Certificates**.

If the toggle is already on, skip this step.

### Finding your HTTPS URL

After the control plane finishes bootstrapping, SSH in and print the FQDN:

```bash
dalston-aws ssh
tailscale status --json | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'])"
# → dalston-control-plane.<your-tailnet>.ts.net.
```

The short MagicDNS name `dalston-control-plane` **won't work** for HTTPS —
browsers need the full `.ts.net` FQDN because that's what the Let's Encrypt
cert is issued for. From any device on your tailnet:

```
https://dalston-control-plane.<your-tailnet>.ts.net/console
wss://dalston-control-plane.<your-tailnet>.ts.net/v1/audio/transcriptions/stream
```

The cert is real Let's Encrypt — no browser warnings, and `getUserMedia`
works in the secure context for in-browser mic capture.

### Enabling HTTPS certs after the instance is already running

The bootstrap runs `tailscale serve` once on first boot. If MagicDNS HTTPS
certs were disabled at that time, the `dalston-tailscale-serve` unit logged
a warning to `/var/log/user-data.log` and exited 0 (so the boot succeeded).
To apply the config without rebooting, toggle the admin console setting, then:

```bash
dalston-aws ssh
sudo systemctl restart dalston-tailscale-serve
sudo systemctl status dalston-tailscale-serve
tailscale serve status
```

### Persistence limitation: cert re-issue on spot rotation

`tailscale serve` keeps its config and Let's Encrypt cert under
`/var/lib/tailscale/`, which lives on the **ephemeral root volume** — not
the `/data` EBS volume. When a spot instance is replaced, the new node
reclaims the `dalston-control-plane` hostname and requests a **new** cert
from Let's Encrypt on first boot.

For typical usage this is fine. Let's Encrypt's duplicate-certificate rate
limit is 5 identical certs per 168 hours per exact hostname, and normal spot
rotation (minutes to hours of downtime per week at most) stays well below
it. You'd have to rotate the control plane more than 5 times a week to hit
the limit.

If you rotate aggressively (e.g. chaos testing, frequent redeploys), the
symptoms are:

- `tailscale serve status` shows the config applied.
- Browsers get a cert error, or the TLS handshake hangs.
- `/var/log/user-data.log` (or `journalctl -u dalston-tailscale-serve`)
  shows Let's Encrypt rate-limit errors.

Workarounds: either wait for the weekly window to roll over, or bind-mount
`/var/lib/tailscale/` onto the persistent `/data` EBS volume so cert state
survives rotation. The bind mount is **not** currently automated by
`dalston-aws`; if you need it, add it manually on the instance after first
boot and restart `tailscaled`.

### Other notes

- **Gateway port is unchanged.** Port 8000 is still the backend. HTTPS on 443
  is a Tailscale-terminated reverse proxy; the gateway itself speaks plain
  HTTP on localhost.
- **WebSocket upgrades work transparently.** `tailscale serve` forwards
  `Upgrade: websocket` headers without extra config — good for the realtime
  streaming endpoints.
- **Public 443 stays closed.** Don't add a public 443 ingress rule to the
  Dalston security group. `tailscale serve` binds to `tailscale0`, not the
  public ENI; a public 443 rule would expose nothing useful and just widen
  your attack surface.
- **If you later front this with an ALB** (for non-tailnet access), bump the
  idle timeout from the default 60s — long-lived WebSocket sessions will
  otherwise be killed mid-stream.

---

## Day-to-day operations

### Check what's running

```bash
dalston-aws status
```

```
[dalston-aws] Dalston AWS Deployment
[dalston-aws]   Scenario: split
[dalston-aws]   Region:   eu-west-2
[dalston-aws]   S3:       dalston-artifacts-123456789012

[dalston-aws]   Control plane: i-0def456  state=running  ip=3.10.45.67
[dalston-aws]   GPU worker:    i-0abc789  state=running  ip=3.10.45.89
```

### Stop to save money (keep data)

```bash
dalston-aws down
# Stops instance(s) — EBS volume preserved, no compute charges
# ~$4/month for the 50 GB EBS volume while stopped
```

### Start back up

```bash
dalston-aws up
# Boots instance(s) — same EBS data, new public IP
# Dalston auto-starts via systemd
```

### SSH access

```bash
dalston-aws ssh          # Main instance
dalston-aws ssh gpu      # GPU worker (split mode only)
```

### Delete everything

```bash
dalston-aws teardown
```

This terminates instances, deletes EBS volumes, security groups, and IAM role.
The S3 bucket is **not** deleted (it may contain your transcription data).
You'll get a command to delete it manually if you want to.

---

## Re-running setup is safe

The script is idempotent. Running `dalston-aws setup` twice will:

1. Detect the existing instance is alive
2. Verify infrastructure (S3, IAM, SG) still exists — re-create if missing
3. Show current status
4. **Not** create a duplicate instance

```bash
dalston-aws setup
# [dalston-aws] Existing deployment found: i-0def456 (state=running)
# [dalston-aws] Infrastructure resources (S3, IAM, SG) will be verified.
# [dalston-aws] Instance will NOT be re-created. Use 'dalston-aws teardown' first to start fresh.
```

---

## Cost cheat sheet

| Scenario | On-demand | With `--spot` | Stopped |
|---|---|---|---|
| CPU only (t3.xlarge) | ~$120/mo | ~$40/mo | ~$4/mo |
| Single GPU (g5.xlarge) | ~$725/mo | ~$250/mo | ~$4/mo |
| Split: CPU + GPU spot | ~$120 + ~$250/mo | ~$40 + ~$250/mo | ~$8/mo |

Tip: use `dalston-aws down` nights and weekends to cut costs in half.

---

## Troubleshooting

### Where is the state stored?

```bash
cat ~/.dalston/aws-state.env
```

Contains instance IDs, volume IDs, bucket name, etc. If you delete this file,
the script loses track of your resources (but they still exist in AWS).

### User-data log (bootstrap issues)

```bash
dalston-aws ssh
sudo cat /var/log/user-data.log
```

### Docker Compose logs

```bash
dalston-aws ssh
cd /data/dalston
docker-compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws logs -f gateway
```

### Instance won't start

Check if your region has the instance type available:
```bash
aws ec2 describe-instance-type-offerings \
  --filters Name=instance-type,Values=g5.xlarge \
  --location-type availability-zone \
  --region eu-west-2
```

### Spot instance was reclaimed

AWS stops spot instances (doesn't terminate) when it needs capacity back.
Just start it again:
```bash
dalston-aws up
```

Your data on EBS is preserved. The instance gets a new public IP but Tailscale
reconnects automatically.

### HTTPS URL returns a cert error or hangs

Check the serve unit on the instance:

```bash
dalston-aws ssh
sudo systemctl status dalston-tailscale-serve
sudo journalctl -u dalston-tailscale-serve -n 50
tailscale serve status
```

Common causes:

- **MagicDNS HTTPS certs not enabled in the tailnet admin console.** The
  unit logs a warning on first boot and exits 0. Enable the toggle (see
  [Accessing the control plane over HTTPS](#accessing-the-control-plane-over-https)),
  then `sudo systemctl restart dalston-tailscale-serve`.
- **You used the short name** (`dalston-control-plane`) instead of the full
  `.ts.net` FQDN. The cert is only valid for the full name.
- **Let's Encrypt rate limit** after heavy spot rotation. See the
  persistence limitation section above.
- **Gateway isn't healthy yet.** `tailscale serve` happily accepts the
  reverse-proxy config before the backend exists, so early requests can
  return 502. Check `docker compose ... ps gateway` and
  `curl -s http://127.0.0.1:8000/health` on the instance.

---

## Quick reference

```bash
dalston-aws setup                    # GPU instance (default)
dalston-aws setup --cpu              # CPU-only instance
dalston-aws setup --spot             # GPU with spot pricing
dalston-aws setup --cpu --spot       # CPU with spot pricing
dalston-aws setup --split            # CPU control plane + GPU worker
dalston-aws setup --gpu-type p3.2xlarge  # Different GPU type

dalston-aws add-gpu                  # Add GPU worker to existing setup
dalston-aws add-gpu --spot           # Add GPU worker with spot pricing
dalston-aws remove-gpu               # Remove GPU worker

dalston-aws status                   # Show instance state
dalston-aws up                       # Start instance(s)
dalston-aws down                     # Stop instance(s)
dalston-aws ssh                      # SSH to main instance
dalston-aws ssh gpu                  # SSH to GPU worker

dalston-aws teardown                 # Delete everything (except S3 data)
```
