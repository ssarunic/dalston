#!/usr/bin/env bash
# _gpu-instance-lib.sh — Shared infrastructure library for Dalston GPU spot instance scripts.
# Source this file from wrapper scripts — do not execute directly.
#
# Wrapper scripts must define:
#   INSTANCE_TAG            — EC2 Name tag for the instance
#   CONTAINERS_TO_WAIT      — bash array of container names to wait for
#   build_container_run_block()  — outputs the docker pull/run commands (with DALSTON_* placeholders)
#
# Wrapper scripts may optionally define:
#   prereq_check()          — runs before infra resolution; exit 1 to abort
#   apply_extra_substitutions()  — replaces script-specific placeholders in USER_DATA

set -euo pipefail

# --- Default configuration (override in wrapper before sourcing) ---
REGION="${REGION:-eu-west-2}"
ROLE_NAME="${ROLE_NAME:-dalston-gpu}"
KEY_NAME="${KEY_NAME:-dalston-dev}"
INSTANCE_TYPE="${INSTANCE_TYPE:-g4dn.xlarge}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/dalston-dev-london.pem}"

# --- Hook stubs ---
prereq_check() { :; }
apply_extra_substitutions() { :; }

# --- Infrastructure resolution ---
resolve_infra() {
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
  ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

  MAC_TS_IP=$(tailscale ip -4)
  [[ -z "$MAC_TS_IP" ]] && { echo "ERROR: Could not get Tailscale IPv4. Is Tailscale running?"; exit 1; }
  echo "Mac Tailscale IP: $MAC_TS_IP"

  GPU_AMI=$(aws ssm get-parameter \
    --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
    --query 'Parameter.Value' --output text --region "$REGION")

  VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
    --query 'Vpcs[0].VpcId' --output text --region "$REGION")
  [[ "$VPC_ID" == "None" || -z "$VPC_ID" ]] && { echo "ERROR: No default VPC found in $REGION"; exit 1; }

  SUBNET_IDS=()
  while IFS= read -r sid; do
    [[ -n "$sid" && "$sid" != "None" ]] && SUBNET_IDS+=("$sid")
  done < <(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
    --query 'Subnets[].SubnetId' --output text --region "$REGION" | tr '\t' '\n')
  [[ ${#SUBNET_IDS[@]} -eq 0 ]] && { echo "ERROR: No subnets found in VPC $VPC_ID"; exit 1; }
  echo "Found ${#SUBNET_IDS[@]} subnets across AZs"

  SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=dalston-gpu" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" \
    --region "$REGION" --output text)
  if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    echo "Creating Security Group..."
    SG_ID=$(aws ec2 create-security-group \
      --group-name dalston-gpu \
      --description "Dalston GPU - Tailscale Managed" \
      --vpc-id "$VPC_ID" --region "$REGION" --query 'GroupId' --output text)
  else
    echo "Using Security Group: $SG_ID"
  fi

  INSTANCE_PROFILE_NAME=$(aws iam list-instance-profiles-for-role --role-name "$ROLE_NAME" \
    --query 'InstanceProfiles[0].InstanceProfileName' --output text)
  [[ "$INSTANCE_PROFILE_NAME" == "None" || -z "$INSTANCE_PROFILE_NAME" ]] && \
    { echo "ERROR: No instance profile found for role $ROLE_NAME"; exit 1; }
}

# --- User-data header (Tailscale + NVIDIA + ECR login) ---
# Uses DALSTON_REGION, DALSTON_ECR, DALSTON_HOSTNAME placeholders — substituted by main().
userdata_header() {
  cat <<'HEADER'
#!/bin/bash
set -euxo pipefail

while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
  echo "Waiting for dpkg lock (unattended-upgrades)..."
  sleep 5
done

curl -fsSL https://tailscale.com/install.sh | sh

TS_AUTH_KEY=$(aws ssm get-parameter \
  --name "/dalston/tailscale-auth-key" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text --region DALSTON_REGION)
tailscale up --authkey="$TS_AUTH_KEY" --hostname=DALSTON_HOSTNAME

apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# Mount instance store NVMe for model cache (free 125 GB on g4dn)
NVME_DEV="/dev/nvme1n1"
if [ -b "$NVME_DEV" ]; then
  mkfs.xfs "$NVME_DEV"
  mkdir -p /data/models
  mount "$NVME_DEV" /data/models
  echo "Mounted instance store NVMe at /data/models"
else
  echo "WARNING: No instance store NVMe found, using root volume"
  mkdir -p /data/models
fi

aws ecr get-login-password --region DALSTON_REGION | docker login --username AWS --password-stdin DALSTON_ECR
HEADER
}

# --- Spot instance launch (AZ retry loop) ---
launch_spot_instance() {
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
      --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":50,"VolumeType":"gp3"}}]' \
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
}

# --- Wait for all containers in CONTAINERS_TO_WAIT ---
wait_for_containers() {
  echo "--- Waiting for containers to start: ${CONTAINERS_TO_WAIT[*]} ---"
  local all_running
  for i in {1..30}; do
    all_running=true
    for cname in "${CONTAINERS_TO_WAIT[@]}"; do
      if ! ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
           ubuntu@"$INSTANCE_TAG" "docker ps --format '{{.Names}}' 2>/dev/null | grep -q $cname" 2>/dev/null; then
        all_running=false
        break
      fi
    done
    $all_running && { echo "All containers running."; return; }
    [[ "$i" -eq 30 ]] && { echo "WARNING: Timed out waiting for containers. Connecting anyway..."; return; }
    sleep 5
  done
}

# --- Stream logs from all containers in CONTAINERS_TO_WAIT ---
follow_logs() {
  echo "--- Streaming logs (Ctrl-C to detach) ---"
  if [[ ${#CONTAINERS_TO_WAIT[@]} -eq 1 ]]; then
    ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no \
      ubuntu@"$INSTANCE_TAG" "docker logs -f ${CONTAINERS_TO_WAIT[0]}"
  else
    for cname in "${CONTAINERS_TO_WAIT[@]}"; do
      ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no \
        ubuntu@"$INSTANCE_TAG" \
        "docker logs -f $cname 2>&1 | sed 's/^/[$cname] /'" &
    done
    wait
  fi
}

# --- Terminate the instance by INSTANCE_TAG ---
stop_instance() {
  echo "--- Terminating instance (tag: $INSTANCE_TAG) ---"
  local iid
  iid=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=$INSTANCE_TAG" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' --output text \
    --region "$REGION")
  if [[ -z "$iid" || "$iid" == "None" ]]; then
    echo "No running instance found with tag Name=$INSTANCE_TAG"
    exit 0
  fi
  echo "Terminating instance: $iid"
  aws ec2 terminate-instances --instance-ids "$iid" --region "$REGION" --output text
  aws ec2 wait instance-terminated --instance-ids "$iid" --region "$REGION"
  echo "Instance $iid terminated."
}

# --- Main orchestration ---
main() {
  COMMAND="${1:-start}"
  case "$COMMAND" in
    stop)
      stop_instance
      exit 0
      ;;
    start)
      TAG="${2:-latest}"
      ;;
    *)
      # Treat unknown arg as TAG for backward compat (e.g. ./start-foo.sh v1.2)
      TAG="$COMMAND"
      ;;
  esac

  prereq_check

  echo "--- Initialising Environment ---"
  resolve_infra

  # Build user-data: common header + script-specific container block
  USER_DATA="$(userdata_header)
$(build_container_run_block)"

  # Common placeholder substitution
  USER_DATA="${USER_DATA//DALSTON_REGION/$REGION}"
  USER_DATA="${USER_DATA//DALSTON_ECR/$ECR}"
  USER_DATA="${USER_DATA//DALSTON_MAC_TS_IP/$MAC_TS_IP}"
  USER_DATA="${USER_DATA//DALSTON_HOSTNAME/$INSTANCE_TAG}"

  # Script-specific substitutions (defined in wrapper)
  apply_extra_substitutions

  USER_DATA_B64=$(echo "$USER_DATA" | base64)

  launch_spot_instance
  wait_for_containers
  follow_logs
}
