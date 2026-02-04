#!/bin/bash
set -euo pipefail

# Dalston EC2 Bootstrap Script
# This script runs on first boot to set up the instance

exec > >(tee /var/log/user-data.log) 2>&1
echo "Starting Dalston bootstrap at $(date)"

# Variables (passed from Terraform via templatefile)
DATA_DEVICE="${DATA_DEVICE}"
DATA_MOUNT="${DATA_MOUNT}"
REPO_URL="${REPO_URL}"
REPO_BRANCH="${REPO_BRANCH}"
S3_BUCKET="${S3_BUCKET}"
AWS_REGION="${AWS_REGION}"

# Install Docker
echo "Installing Docker..."
dnf update -y
dnf install -y docker git

# Start and enable Docker
systemctl start docker
systemctl enable docker

# Add ec2-user to docker group
usermod -aG docker ec2-user

# Install Docker Compose
echo "Installing Docker Compose..."
DOCKER_COMPOSE_VERSION="v2.24.0"
curl -L "https://github.com/docker/compose/releases/download/$${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose

# Wait for data volume to be attached
echo "Waiting for data volume..."
while [ ! -e "$DATA_DEVICE" ]; do
  echo "Waiting for $DATA_DEVICE..."
  sleep 5
done

# Format data volume if not already formatted
if ! blkid "$DATA_DEVICE" | grep -q ext4; then
  echo "Formatting data volume..."
  mkfs.ext4 -L dalston-data "$DATA_DEVICE"
fi

# Create mount point and mount
echo "Mounting data volume..."
mkdir -p "$DATA_MOUNT"
mount "$DATA_DEVICE" "$DATA_MOUNT"

# Add to fstab for persistence across reboots
if ! grep -q "$DATA_MOUNT" /etc/fstab; then
  echo "LABEL=dalston-data $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
fi

# Create data directories
mkdir -p "$DATA_MOUNT/postgres"
mkdir -p "$DATA_MOUNT/models"
mkdir -p "$DATA_MOUNT/dalston"
chown -R 1000:1000 "$DATA_MOUNT"

# Clone Dalston repository
echo "Cloning Dalston repository..."
if [ ! -d "$DATA_MOUNT/dalston/.git" ]; then
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$DATA_MOUNT/dalston"
else
  cd "$DATA_MOUNT/dalston"
  git fetch origin
  git reset --hard "origin/$REPO_BRANCH"
fi

# Create environment file for Docker Compose
cat > "$DATA_MOUNT/dalston/.env.aws" << EOF
S3_BUCKET=${S3_BUCKET}
AWS_REGION=${AWS_REGION}
REDIS_URL=redis://redis:6379
DATABASE_URL=postgresql://dalston:dalston@postgres:5432/dalston
HF_HOME=/data/models
EOF

# Create systemd service for Docker Compose
echo "Creating systemd service..."
cat > /etc/systemd/system/dalston.service << 'EOF'
[Unit]
Description=Dalston Transcription Server
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/data/dalston
ExecStart=/usr/bin/docker-compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml --env-file .env.aws up -d gateway orchestrator redis postgres engine-audio-prepare engine-faster-whisper engine-final-merger
ExecStop=/usr/bin/docker-compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
systemctl daemon-reload
systemctl enable dalston.service
systemctl start dalston.service

echo "Dalston bootstrap completed at $(date)"
