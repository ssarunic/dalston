#!/usr/bin/env bash

# start-stt-onnx.sh — Launch or terminate a spot GPU instance running the stt-onnx container
#
# Usage:
#   start-stt-onnx.sh [start] [TAG]   # Launch instance (default command)
#   start-stt-onnx.sh stop             # Terminate running instance
set -euo pipefail

# --- 1. Configuration ---
REGION="eu-west-2"
ROLE_NAME="dalston-gpu"
KEY_NAME="dalston-dev"
INSTANCE_TYPE="g4dn.xlarge"
SSH_KEY_PATH="$HOME/.ssh/dalston-dev-london.pem"
REPO="dalston/stt-onnx"
INSTANCE_TAG="dalston-stt-onnx"

# --- Subcommand routing ---
COMMAND="${1:-start}"
case "$COMMAND" in
  stop)
    echo "--- Terminating stt-onnx instance ---"
    INSTANCE_ID=$(aws ec2 describe-instances \
      --filters "Name=tag:Name,Values=$INSTANCE_TAG" \
                "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].InstanceId' --output text \
      --region "$REGION")

    if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
      echo "No running instance found with tag Name=$INSTANCE_TAG"
      exit 0
    fi

    echo "Terminating instance: $INSTANCE_ID"
    aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "$REGION" --output text
    aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" --region "$REGION"
    echo "Instance $INSTANCE_ID terminated."
    exit 0
    ;;
  start)
    TAG="${2:-latest}"
    ;;
  *)
    # Treat unknown arg as TAG for backward compat (e.g. ./start-stt-onnx.sh v1.2)
    TAG="$COMMAND"
    ;;
esac

echo "--- Initialising Environment ---"

# Fetch Account & ECR details
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

# Get local Tailscale IP for Redis connection
MAC_TS_IP=$(tailscale ip -4)
[[ -z "$MAC_TS_IP" ]] && { echo "ERROR: Could not get Tailscale IPv4 address. Is Tailscale running?"; exit 1; }
echo "Mac Tailscale IP: $MAC_TS_IP"

# 2. Get Latest GPU AMI
GPU_AMI=$(aws ssm get-parameter \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query 'Parameter.Value' --output text --region "$REGION")

# 3. Network Setup
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region "$REGION")
[[ "$VPC_ID" == "None" || -z "$VPC_ID" ]] && { echo "ERROR: No default VPC found in $REGION"; exit 1; }

# Fetch all subnets (one per AZ) — we try each when launching spot
SUBNET_IDS=()
while IFS= read -r sid; do
  [[ -n "$sid" && "$sid" != "None" ]] && SUBNET_IDS+=("$sid")
done < <(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'Subnets[].SubnetId' --output text --region "$REGION" | tr '\t' '\n')
[[ ${#SUBNET_IDS[@]} -eq 0 ]] && { echo "ERROR: No subnets found in VPC $VPC_ID"; exit 1; }
echo "Found ${#SUBNET_IDS[@]} subnets across AZs"

# 4. Security Group Logic
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=dalston-gpu" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" \
    --region "$REGION" --output text)

if [ "$SG_ID" == "None" ] || [ -z "$SG_ID" ]; then
  echo "Creating Security Group..."
  SG_ID=$(aws ec2 create-security-group \
    --group-name dalston-gpu \
    --description "Dalston GPU - Tailscale Managed" \
    --vpc-id "$VPC_ID" --region "$REGION" --query 'GroupId' --output text)
else
  echo "Using Security Group: $SG_ID"
fi

# 5. IAM Instance Profile
INSTANCE_PROFILE_NAME=$(aws iam list-instance-profiles-for-role --role-name "$ROLE_NAME" \
  --query 'InstanceProfiles[0].InstanceProfileName' --output text)
[[ "$INSTANCE_PROFILE_NAME" == "None" || -z "$INSTANCE_PROFILE_NAME" ]] && { echo "ERROR: No instance profile found for role $ROLE_NAME"; exit 1; }

# 6. Prepare User Data
#    NOTE: Tailscale auth key is fetched from SSM inside the instance (not baked into metadata).
#    The instance role must have ssm:GetParameter permission for /dalston/tailscale-auth-key.
USER_DATA=$(cat <<'USERDATA'
#!/bin/bash
set -euxo pipefail

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Fetch auth key from SSM (not baked into user-data)
TS_AUTH_KEY=$(aws ssm get-parameter \
  --name "/dalston/tailscale-auth-key" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text --region DALSTON_REGION)
tailscale up --authkey="$TS_AUTH_KEY" --hostname=DALSTON_HOSTNAME

# The deep learning AMI already has Docker and NVIDIA drivers.
# Just ensure nvidia-container-toolkit is installed and configured.
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# Pull and run the container
aws ecr get-login-password --region DALSTON_REGION | docker login --username AWS --password-stdin DALSTON_ECR
docker pull DALSTON_ECR/DALSTON_REPO:DALSTON_TAG
docker run -d --name stt-onnx --gpus all --restart unless-stopped \
  -p 9000:9000 \
  -e DALSTON_DEVICE=cuda \
  -e DALSTON_ENGINE_ID=onnx \
  -e REDIS_URL=redis://DALSTON_MAC_TS_IP:6379 \
  -e DALSTON_S3_BUCKET=dalston-artifacts \
  -e DALSTON_S3_ENDPOINT_URL=http://DALSTON_MAC_TS_IP:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin \
  -e AWS_SECRET_ACCESS_KEY=minioadmin \
  DALSTON_ECR/DALSTON_REPO:DALSTON_TAG
USERDATA
)

# Substitute placeholders (heredoc is single-quoted so variables aren't expanded)
USER_DATA="${USER_DATA//DALSTON_REGION/$REGION}"
USER_DATA="${USER_DATA//DALSTON_ECR/$ECR}"
USER_DATA="${USER_DATA//DALSTON_REPO/$REPO}"
USER_DATA="${USER_DATA//DALSTON_TAG/$TAG}"
USER_DATA="${USER_DATA//DALSTON_HOSTNAME/$INSTANCE_TAG}"
USER_DATA="${USER_DATA//DALSTON_MAC_TS_IP/$MAC_TS_IP}"

# base64-encode for run-instances
USER_DATA_B64=$(echo "$USER_DATA" | base64)

# 7. Launch Instance — try each AZ until spot capacity is found
echo "--- Launching Spot Instance ---"
INSTANCE_ID=""
for SUBNET_ID in "${SUBNET_IDS[@]}"; do
  AZ=$(aws ec2 describe-subnets --subnet-ids "$SUBNET_ID" \
    --query 'Subnets[0].AvailabilityZone' --output text --region "$REGION")
  echo "Trying AZ $AZ (subnet $SUBNET_ID)..."
  INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$GPU_AMI" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --subnet-id "$SUBNET_ID" \
    --security-group-ids "$SG_ID" \
    --iam-instance-profile Name="$INSTANCE_PROFILE_NAME" \
    --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}' \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3"}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_TAG}]" \
    --user-data "$USER_DATA_B64" \
    --query 'Instances[0].InstanceId' --output text \
    --region "$REGION" 2>&1) && break
  echo "No spot capacity in $AZ, trying next..."
  INSTANCE_ID=""
done
[[ -z "$INSTANCE_ID" ]] && { echo "ERROR: No spot capacity available in any AZ"; exit 1; }

echo "Instance ID: $INSTANCE_ID"
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
echo "Instance is running."

# 8. Wait for container to be ready (poll via SSH instead of fixed sleep)
echo "--- Waiting for stt-onnx container to start ---"
for i in {1..30}; do
  if ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
       ubuntu@$INSTANCE_TAG "docker ps --format '{{.Names}}' 2>/dev/null | grep -q stt-onnx" 2>/dev/null; then
    echo "Container is running."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "WARNING: Timed out waiting for container after 150s. Connecting anyway..."
  fi
  sleep 5
done

# 9. Follow Logs
echo "--- Connecting to stream logs ---"
ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_TAG "docker logs -f stt-onnx"
