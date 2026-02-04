# AWS Deployment Implementation

Implementation of M16: AWS Deployment (Phase 1) - manual deployment via Terraform.

## Structure

```
infra/
├── terraform/
│   ├── .gitignore                      # Excludes tfstate, tfvars, .terraform/
│   ├── environments/
│   │   └── dev/
│   │       ├── main.tf                 # Root module, provider config
│   │       ├── variables.tf            # Input variables
│   │       ├── outputs.tf              # Instance ID, S3 bucket, shell aliases
│   │       └── terraform.tfvars.example
│   │
│   └── modules/
│       ├── ec2-dalston/                # EC2 instance, security group, data EBS
│       ├── s3-artifacts/               # S3 bucket with encryption, lifecycle
│       └── iam-dalston/                # IAM role, S3 policy, instance profile
│
├── scripts/
│   └── user-data.sh                    # EC2 bootstrap script
│
└── docker/
    └── docker-compose.aws.yml          # AWS-specific compose overrides
```

## Components

### Terraform Modules

| Module | Resources Created |
|--------|-------------------|
| `ec2-dalston` | EC2 instance (t3.xlarge), security group (SSH from Tailscale), data EBS volume (50GB) |
| `s3-artifacts` | S3 bucket with SSE-S3 encryption, blocked public access, lifecycle rules |
| `iam-dalston` | IAM role with EC2 trust policy, S3 access policy, instance profile |

### User Data Script

The `user-data.sh` script runs on first boot and:
1. Installs Docker and Docker Compose
2. Formats and mounts the data EBS volume to `/data`
3. Adds fstab entry for persistence
4. Clones the Dalston repository
5. Creates environment file with S3/AWS config
6. Creates and enables systemd service for auto-start

### Docker Compose Override

The `docker-compose.aws.yml` override:
- Configures S3 environment variables (bucket, region)
- Maps Postgres data to `/data/postgres`
- Maps model cache to `/data/models`
- Excludes MinIO (replaced by S3)

## Usage

### Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform >= 1.0
- SSH key pair created in AWS

### Deploy

```bash
cd infra/terraform/environments/dev

# Configure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars:
#   - key_name: your AWS SSH key pair name
#   - repo_url: your Dalston repository URL

# Deploy
terraform init
terraform plan
terraform apply
```

### Outputs

After deployment, Terraform outputs:
- `instance_id`: EC2 instance ID for start/stop commands
- `public_ip`: For initial Tailscale setup
- `s3_bucket`: Artifact bucket name
- `shell_aliases`: Ready-to-use aliases for ~/.zshrc

### Post-Deployment: Tailscale Setup

```bash
# SSH to instance (temporarily allow your IP in security group)
ssh ec2-user@<public_ip>

# Install and authenticate Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the Tailscale IP (100.x.x.x)
```

### Start/Stop Instance

```bash
# Add to ~/.zshrc (from terraform output)
export DALSTON_INSTANCE_ID="i-xxxxxxxxx"
alias dalston-up="aws ec2 start-instances --instance-ids \$DALSTON_INSTANCE_ID"
alias dalston-down="aws ec2 stop-instances --instance-ids \$DALSTON_INSTANCE_ID"
alias dalston-status="aws ec2 describe-instances --instance-ids \$DALSTON_INSTANCE_ID --query 'Reservations[0].Instances[0].State.Name' --output text"

# Usage
dalston-up      # Start instance (~60s to ready)
dalston-down    # Stop instance (saves ~$4/day)
dalston-status  # Check current state
```

### Verify Deployment

```bash
# Health check (via Tailscale)
curl http://100.x.x.x:8000/health

# System status
curl http://100.x.x.x:8000/v1/system/status

# Test transcription
curl -X POST http://100.x.x.x:8000/v1/audio/transcriptions \
  -F "file=@test.mp3" \
  -F "model=faster-whisper"

# Verify S3 storage
aws s3 ls s3://<bucket-name>/
```

## Cost

| State | Monthly Cost |
|-------|--------------|
| Running 24/7 | ~$135 |
| Running 8h/day weekdays | ~$35 |
| Stopped (EBS + S3 only) | ~$6 |

## Security

- SSH access restricted to Tailscale CIDR (100.64.0.0/10)
- S3 bucket has blocked public access
- IAM role uses least-privilege S3 permissions
- EBS volumes encrypted at rest
- No public endpoints exposed
