# AWS Deployment Tutorial

Step-by-step guide to deploy Dalston on AWS with Terraform and Tailscale VPN access.

## Prerequisites

- macOS with Homebrew
- AWS account
- GitHub account (for Tailscale auth)
- Dalston repository (public or with access token)

## 1. Install Required Tools

```bash
# AWS CLI
brew install awscli

# Terraform
brew install terraform

# Tailscale (for VPN access)
brew install --cask tailscale
```

## 2. Configure AWS CLI

### Create IAM User

1. Go to AWS Console → IAM → Users → Create user
2. User name: `your-name-cli`
3. Add user to group → Create group:
   - Group name: `admins`
   - Attach policy: `AdministratorAccess`
4. Create user → Security credentials → Create access key
5. Select "Command Line Interface (CLI)"
6. Copy both keys

### Configure CLI

```bash
aws configure
```

Enter:

- AWS Access Key ID: `<paste access key>`
- AWS Secret Access Key: `<paste secret key>`
- Default region: `eu-west-2` (or your preferred region)
- Default output format: `json`

## 3. Create SSH Key Pair

```bash
aws ec2 create-key-pair \
  --region eu-west-2 \
  --key-name dalston-dev \
  --query 'KeyMaterial' \
  --output text > ~/.ssh/dalston-dev.pem

chmod 400 ~/.ssh/dalston-dev.pem
```

## 4. Configure Terraform

```bash
cd infra/terraform/environments/dev

# Create your configuration
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
key_name    = "dalston-dev"
aws_region  = "eu-west-2"
repo_url    = "https://github.com/ssarunic/dalston.git"
repo_branch = "main"
```

## 5. Deploy Infrastructure

```bash
# Initialize Terraform
terraform init

# Preview changes
terraform plan

# Deploy (type 'yes' when prompted)
terraform apply
```

Note the outputs:

- `instance_id`: e.g., `i-05c2428b930fc1712`
- `public_ip`: e.g., `3.8.28.219`
- `s3_bucket`: e.g., `dalston-artifacts-178457246645`

The bootstrap script automatically:

- Installs Docker and Docker Compose
- Installs Tailscale (requires manual authentication)
- Mounts the data volume at `/data`
- Clones the Dalston repository
- Creates `/data/dalston/.env.aws` with S3/region config
- Configures NVIDIA container runtime if a GPU is detected
- Creates a systemd service for `docker-compose.yml` + AWS override with `--profile prod` and optional `--profile gpu` (disabled by default)

## 6. Setup Tailscale on EC2

### Temporarily allow SSH from your IP

```bash
# Get your public IP
curl -4 https://api.ipify.org

# Add temporary SSH access
aws ec2 authorize-security-group-ingress \
  --region eu-west-2 \
  --group-id <security_group_id from terraform output> \
  --protocol tcp \
  --port 22 \
  --cidr <your-ip>/32
```

### SSH and configure Tailscale

```bash
ssh -i ~/.ssh/dalston-dev.pem ec2-user@<public_ip>
```

On the EC2 instance (Tailscale is pre-installed by the bootstrap script):

```bash
# Authenticate Tailscale
sudo tailscale up

# Follow the auth link in your browser
# Note the Tailscale IP (100.x.x.x)
tailscale ip -4
```

Exit SSH:

```bash
exit
```

### Remove temporary SSH access

```bash
aws ec2 revoke-security-group-ingress \
  --region eu-west-2 \
  --group-id <security_group_id> \
  --protocol tcp \
  --port 22 \
  --cidr <your-ip>/32
```

## 7. Setup Tailscale on Mac

1. Open Tailscale from Applications
2. Sign in with the same account used on EC2
3. Verify connection: `ping 100.x.x.x` (use your Tailscale IP)

## 8. Add Shell Aliases

Add to `~/.zshrc`:

```bash
# Dalston AWS deployment
export DALSTON_INSTANCE_ID="i-xxxxxxxxxxxxx"
export DALSTON_TAILSCALE_IP="100.x.x.x"
alias dalston-up="aws ec2 start-instances --region eu-west-2 --instance-ids \$DALSTON_INSTANCE_ID"
alias dalston-down="aws ec2 stop-instances --region eu-west-2 --instance-ids \$DALSTON_INSTANCE_ID"
alias dalston-status="aws ec2 describe-instances --region eu-west-2 --instance-ids \$DALSTON_INSTANCE_ID --query 'Reservations[0].Instances[0].State.Name' --output text"
alias dalston-ssh="ssh -i ~/.ssh/dalston-dev.pem ec2-user@\$DALSTON_TAILSCALE_IP"
```

Reload:

```bash
source ~/.zshrc
```

## 9. Start Dalston Services

SSH to the instance:

```bash
dalston-ssh
```

The bootstrap script has already created:

- `/data/dalston/.env.aws` - environment variables
- `/data/dalston/docker-compose.minimal.yml` - minimal compose file with gateway, web, redis, postgres

Start services using the systemd service:

```bash
sudo systemctl start dalston
```

Or start manually with docker-compose:

```bash
cd /data/dalston
sudo docker-compose -f docker-compose.minimal.yml --env-file .env.aws up -d
```

Check logs:

```bash
sudo docker-compose -f docker-compose.minimal.yml logs -f gateway
```

## 10. Verify Deployment

From your Mac:

```bash
# Health check
curl http://$DALSTON_TAILSCALE_IP:8000/health

# Should return: {"status":"healthy"}
```

Access in browser:

- API docs: `http://100.x.x.x:8000/docs`
- Health: `http://100.x.x.x:8000/health`

## 11. Setup Web Console

The web console provides a UI for monitoring jobs and engines. The `docker-compose.minimal.yml` created by the bootstrap script already includes the web service.

### Create Admin API Key

The console requires an admin API key to authenticate:

```bash
cd /data/dalston
sudo docker-compose -f docker-compose.minimal.yml exec -T gateway python -c "
import asyncio
from dalston.common.redis import get_redis
from dalston.gateway.services.auth import AuthService, Scope
from dalston.db.session import DEFAULT_TENANT_ID

async def create_key():
    redis = await get_redis()
    auth = AuthService(redis)
    key, _ = await auth.create_api_key('Console Admin', DEFAULT_TENANT_ID, [Scope.ADMIN])
    print('API Key:', key)

asyncio.run(create_key())
"
```

**Save the output key** (starts with `dk_`) - it cannot be retrieved later.

### Access the console

The web console starts automatically with the other services.

1. Open `http://<TAILSCALE_IP>:3000/login` in your browser
2. Enter the admin API key you created
3. You should now see the dashboard

## Daily Operations

### Start instance

```bash
dalston-up
# Wait ~60 seconds for boot
dalston-status  # Should show "running"
```

### Stop instance (saves ~$4/day)

```bash
dalston-down
```

### SSH to instance

```bash
dalston-ssh
```

### View logs

```bash
dalston-ssh
cd /data/dalston
sudo docker-compose -f docker-compose.minimal.yml logs -f
```

### Restart services

```bash
dalston-ssh
sudo systemctl restart dalston

# Or manually:
cd /data/dalston
sudo docker-compose -f docker-compose.minimal.yml restart
```

### Update code

```bash
dalston-ssh
cd /data/dalston
sudo git pull
sudo docker-compose -f docker-compose.minimal.yml restart gateway web
```

## Cost

| State | Monthly Cost |
|-------|--------------|
| Running 24/7 | ~$135 |
| Running 8h/day weekdays | ~$35 |
| Stopped (EBS + S3 only) | ~$6 |

## Troubleshooting

### Can't SSH via Tailscale

1. Verify Tailscale is running on both Mac and EC2
2. Check EC2 instance is running: `dalston-status`
3. Verify security group allows SSH from `100.64.0.0/10`

### Services not starting

```bash
dalston-ssh
sudo docker-compose -f docker-compose.minimal.yml logs
```

### User-data script failed

```bash
dalston-ssh
sudo cat /var/log/user-data.log
```

### Git clone failed (private repo)

Clone manually with a personal access token:

```bash
sudo git clone https://<token>@github.com/ssarunic/dalston.git /data/dalston
```

### Git pull conflicts with local changes

If `git pull` fails due to local changes:

```bash
# Discard local changes to specific file
sudo git checkout <filename>
sudo git pull

# Or force reset to match remote
sudo git fetch origin
sudo git reset --hard origin/main
```

### Console shows "Invalid API key"

1. Verify the API key was created successfully by testing directly:

   ```bash
   curl -v http://localhost:8000/auth/me -H "Authorization: Bearer dk_YOUR_KEY_HERE"
   ```

2. Make sure you copied the entire key including the `dk_` prefix

3. Restart the gateway to ensure code changes are loaded:

   ```bash
   sudo docker-compose -f docker-compose.minimal.yml restart gateway
   ```

### Console API returns 404

The gateway may need to be restarted after code updates:

```bash
sudo docker-compose -f docker-compose.minimal.yml restart gateway web
```

### Console pages show errors or empty data

All console endpoints require admin authentication. Make sure you:

1. Created an admin API key
2. Logged in at `/login` with the key
3. The browser has the key stored in session

## Destroy Infrastructure

To remove all AWS resources:

```bash
cd infra/terraform/environments/dev
terraform destroy
```

This will delete:

- EC2 instance
- EBS volumes
- S3 bucket
- IAM role
- Security group
