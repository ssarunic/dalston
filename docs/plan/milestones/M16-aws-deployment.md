# M16: AWS Deployment (Phase 1)

| | |
|---|---|
| **Goal** | Deploy Dalston to AWS with Terraform, accessible via Tailscale VPN |
| **Duration** | 2-3 days |
| **Dependencies** | Core services working locally (M1-M5) |
| **Deliverable** | Single EC2 running all services, private access via Tailscale, S3 for artifacts |
| **Status** | In Progress |

## User Story

> *"As a developer, I can deploy Dalston to AWS with a single Terraform apply, access it securely from my MacBook via Tailscale, and later connect my other AWS-hosted projects to it."*

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  AWS Account                                                                │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  Default VPC                                                          │ │
│  │                                                                       │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │  EC2: t3.xlarge (4 vCPU, 16GB RAM)                             │ │ │
│  │  │                                                                 │ │ │
│  │  │  ┌───────────────────────────────────────────────────────────┐ │ │ │
│  │  │  │  Docker Compose                                           │ │ │ │
│  │  │  │                                                           │ │ │ │
│  │  │  │  ┌─────────┐  ┌───────────┐  ┌─────────┐  ┌─────────┐   │ │ │ │
│  │  │  │  │ Gateway │  │Orchestrat.│  │  Redis  │  │Postgres │   │ │ │ │
│  │  │  │  │  :8000  │  │           │  │  :6379  │  │  :5432  │   │ │ │ │
│  │  │  │  └─────────┘  └───────────┘  └─────────┘  └─────────┘   │ │ │ │
│  │  │  │                                                           │ │ │ │
│  │  │  │  ┌─────────┐  ┌───────────┐  ┌─────────┐                 │ │ │ │
│  │  │  │  │ Engine: │  │  Engine:  │  │ Engine: │                 │ │ │ │
│  │  │  │  │ Prepare │  │  Whisper  │  │  Merger │                 │ │ │ │
│  │  │  │  └─────────┘  └───────────┘  └─────────┘                 │ │ │ │
│  │  │  └───────────────────────────────────────────────────────────┘ │ │ │
│  │  │                                                                 │ │ │
│  │  │  + Tailscale daemon (100.x.x.x)                                │ │ │
│  │  │                                                                 │ │ │
│  │  │  Volumes:                                                       │ │ │
│  │  │  ├── /dev/xvda (root, 30GB gp3) — OS, Docker images            │ │ │
│  │  │  └── /dev/xvdf (data, 50GB gp3) — /data (Postgres, models)     │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                       │ │
│  │  Security Group: dalston-sg                                           │ │
│  │  └── Inbound: SSH (22) from 100.64.0.0/10 (Tailscale) only           │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  S3: dalston-artifacts-{account-id}                                   │ │
│  │  └── jobs/, uploads/, outputs/                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  IAM Role: dalston-ec2-role                                           │ │
│  │  └── S3 read/write to artifacts bucket                                │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ Tailscale VPN
                                    │
                            ┌───────┴───────┐
                            │  Your MacBook │
                            │  (Thestill)   │
                            └───────────────┘
```

---

## Design Principles

1. **Simplest viable deployment** — Single EC2 with Docker Compose, same as local
2. **No public exposure** — All access via Tailscale VPN, no public endpoints
3. **Future-proof URIs** — Services use Docker DNS names, not localhost
4. **Persistent storage** — Separate EBS for data survives instance stop/termination
5. **Cloud-native where sensible** — S3 replaces MinIO, IAM roles instead of credentials
6. **Infrastructure as code** — Terraform for reproducibility and versioning
7. **Pay only when using** — Stop instance when idle, ~$6/month vs ~$120/month

---

## Infrastructure Components

### EC2 Instance

| Property | Value | Notes |
|----------|-------|-------|
| Instance Type | `t3.xlarge` | 4 vCPU, 16GB RAM — sufficient for CPU transcription with large-v3 |
| AMI | Amazon Linux 2023 | Or Ubuntu 22.04 LTS |
| Root Volume | 30GB gp3 | OS, Docker images |
| Data Volume | 50GB gp3 | Mounted at `/data` — Postgres, model cache |

### Memory Budget (16GB)

| Component | Memory |
|-----------|--------|
| OS + Docker | ~1.5 GB |
| Redis + Postgres | ~1 GB |
| Gateway + Orchestrator | ~0.5 GB |
| faster-whisper (large-v3, CPU) | ~4 GB |
| whisperx-align | ~2 GB |
| pyannote (if used) | ~3 GB |
| **Headroom** | ~4 GB |

### Security Group

| Direction | Port | Source | Purpose |
|-----------|------|--------|---------|
| Inbound | 22 | 100.64.0.0/10 | SSH from Tailscale only |
| Outbound | All | 0.0.0.0/0 | Docker Hub, S3, packages |

### S3 Bucket

| Property | Value |
|----------|-------|
| Name | `dalston-artifacts-{account-id}` |
| Versioning | Disabled |
| Encryption | SSE-S3 |
| Public Access | Blocked |

### IAM Role

Policy allows:

- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on bucket objects
- `s3:ListBucket` on bucket

---

## Start/Stop Workflow

Stop the instance when not in use to minimize costs.

### Quick Commands

```bash
# Store instance ID after first deploy
export DALSTON_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx

# Stop (stops billing for compute)
aws ec2 stop-instances --instance-ids $DALSTON_INSTANCE_ID

# Start
aws ec2 start-instances --instance-ids $DALSTON_INSTANCE_ID

# Check status
aws ec2 describe-instances --instance-ids $DALSTON_INSTANCE_ID \
  --query 'Reservations[0].Instances[0].State.Name' --output text
```

### Shell Aliases (add to ~/.zshrc)

```bash
alias dalston-up="aws ec2 start-instances --instance-ids i-xxxxxxxxx"
alias dalston-down="aws ec2 stop-instances --instance-ids i-xxxxxxxxx"
alias dalston-status="aws ec2 describe-instances --instance-ids i-xxxxxxxxx --query 'Reservations[0].Instances[0].State.Name' --output text"
```

### What Happens on Start

1. Instance boots (~30s)
2. Docker starts automatically
3. Containers start (`restart: unless-stopped`)
4. Tailscale reconnects (same IP)
5. Dalston ready (~60s total)

### Cost Comparison

| State | Monthly Cost |
|-------|--------------|
| Running 24/7 | ~$120 |
| Running 8h/day weekdays | ~$35 |
| Stopped (EBS only) | ~$6 |

---

## Terraform Structure

```
infra/
├── terraform/
│   ├── environments/
│   │   └── dev/
│   │       ├── main.tf           # Root module, provider config
│   │       ├── variables.tf      # Input variables
│   │       ├── outputs.tf        # Outputs (instance ID, bucket name)
│   │       └── terraform.tfvars  # Environment-specific values
│   │
│   └── modules/
│       ├── ec2-dalston/
│       │   ├── main.tf
│       │   ├── variables.tf
│       │   └── outputs.tf
│       │
│       ├── s3-artifacts/
│       │   └── ...
│       │
│       └── iam-dalston/
│           └── ...
│
├── scripts/
│   └── user-data.sh              # EC2 bootstrap (Docker, EBS mount)
│
└── docker/
    └── docker-compose.aws.yml    # AWS-specific overrides
```

---

## Docker Compose Configuration

### Service DNS (for future separation)

All services communicate via Docker Compose service names:

| Service | Internal URL |
|---------|--------------|
| gateway | `http://gateway:8000` |
| orchestrator | `http://orchestrator:8080` |
| redis | `redis://redis:6379` |
| postgres | `postgres://postgres:5432` |

When services are later separated, only environment variables change.

### AWS Override File

```yaml
# docker-compose.aws.yml
services:
  gateway:
    environment:
      - S3_BUCKET=${S3_BUCKET}
      - AWS_REGION=${AWS_REGION}
      # No S3_ENDPOINT — SDK auto-detects on EC2
      # No credentials — uses IAM role

  postgres:
    volumes:
      - /data/postgres:/var/lib/postgresql/data

# MinIO services excluded (use S3 instead)
```

### Startup Command

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d \
  gateway orchestrator redis postgres \
  stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge
```

---

## Cost Estimate

### Running

| Resource | Monthly |
|----------|---------|
| EC2 t3.xlarge (on-demand, 24/7) | ~$120 |
| EBS 30GB gp3 (root) | ~$2.40 |
| EBS 50GB gp3 (data) | ~$4.00 |
| S3 (10GB estimated) | ~$0.23 |
| Data transfer | ~$5-10 |
| **Total (24/7)** | **~$135/month** |

### Idle (stopped)

| Resource | Monthly |
|----------|---------|
| EBS 30GB gp3 | ~$2.40 |
| EBS 50GB gp3 | ~$4.00 |
| S3 (10GB) | ~$0.23 |
| **Total (stopped)** | **~$6/month** |

---

## Steps

### 16.1: Terraform Setup

- Create `infra/terraform/` directory structure
- Configure AWS provider, state backend (local initially, S3 later)
- Define input variables: region, instance type, volume sizes

### 16.2: IAM Module

- Create IAM role with EC2 trust policy
- Create policy for S3 artifact bucket access
- Create instance profile

### 16.3: S3 Module

- Create bucket with unique name (account ID suffix)
- Block public access
- Enable SSE-S3 encryption

### 16.4: EC2 Module

- Create security group (SSH from Tailscale CIDR only)
- Create EC2 instance with:
  - IAM instance profile
  - Root EBS volume (30GB gp3)
  - User data script for bootstrap
- Create and attach data EBS volume (50GB gp3)

### 16.5: User Data Script

- Install Docker and Docker Compose
- Format and mount data EBS to `/data`
- Add fstab entry for persistence across reboots
- Clone Dalston repository
- Create systemd service for Docker Compose auto-start

### 16.6: Docker Compose AWS Override

- Create `docker-compose.aws.yml`
- Configure S3 environment variables
- Map Postgres data to `/data/postgres`
- Map model cache to `/data/models`
- Remove MinIO services

### 16.7: Tailscale Setup (Manual, one-time)

- SSH to instance (temporarily allow your IP)
- Install Tailscale
- Authenticate and note Tailscale IP
- Update security group to Tailscale-only
- Add instance ID to shell aliases

### 16.8: Verification & Documentation

- Connect via Tailscale from MacBook
- Test Gateway health endpoint
- Submit test transcription job
- Verify S3 artifact storage
- Document Tailscale IP and instance ID

---

## Verification

```bash
# Deploy infrastructure
cd infra/terraform/environments/dev
terraform init
terraform plan
terraform apply

# Note the outputs
# - instance_id: i-xxxxxxxxx
# - s3_bucket: dalston-artifacts-123456789

# SSH via Tailscale (after manual Tailscale setup)
ssh ec2-user@100.x.x.x

# Check services
docker compose ps
docker compose logs gateway

# From MacBook (via Tailscale)
curl http://100.x.x.x:8000/health
curl http://100.x.x.x:8000/v1/system/status

# Test transcription
curl -X POST http://100.x.x.x:8000/v1/audio/transcriptions \
  -F "file=@test.mp3" \
  -F "model=faster-whisper"

# Verify S3 storage
aws s3 ls s3://dalston-artifacts-123456789/

# Test start/stop
dalston-down  # alias
dalston-status  # should show "stopping" then "stopped"
dalston-up
# Wait 60s
curl http://100.x.x.x:8000/health  # should work
```

---

## Checkpoint

- [ ] Terraform directory structure created
- [ ] IAM role and policy for S3 access
- [ ] S3 bucket with blocked public access
- [ ] EC2 instance with attached data EBS
- [ ] Security group restricts to Tailscale CIDR
- [ ] User data script installs Docker, mounts EBS
- [ ] docker-compose.aws.yml with S3 config
- [ ] Tailscale installed and authenticated
- [ ] Gateway accessible via Tailscale IP
- [ ] Test transcription completes successfully
- [ ] Artifacts stored in S3
- [ ] Start/stop workflow documented
- [ ] Shell aliases configured

---

## Future Phases

### Phase 2: GPU Instance

- Change instance type to `g4dn.xlarge` (~$380/month running)
- Install NVIDIA drivers in user data
- Enable GPU engines in compose
- 50-100x faster transcription

### Phase 3: Managed Services

- PostgreSQL → RDS
- Redis → ElastiCache
- Keep compute on EC2

### Phase 4: Container Orchestration

- Migrate to ECS or EKS
- Separate services into tasks
- Auto-scaling for engines

### Phase 5: Multi-region / HA

- Load balancer
- Multi-AZ deployment
- Database replication
