#!/usr/bin/env bash

# Create the repository (first time only — safe to re-run)
aws ecr create-repository \
  --repository-name dalston/stt-onnx \
  --region $REGION 2>/dev/null || echo "Repository already exists"

echo "ECR: $ECR/dalston/stt-onnx"

# Login to ECR
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ECR

# Build
echo "Building $REPO:$TAG ..."
docker build --platform linux/amd64 \
  -t $ECR/$REPO:$TAG \
  -t $ECR/$REPO:latest \
  .

# Push
echo "Pushing ..."
docker push $ECR/$REPO:$TAG
docker push $ECR/$REPO:latest

# Restart on instance
echo "Restarting on $INSTANCE_IP ..."
ssh -i parakeet-key.pem -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP "
  aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR
  docker pull $ECR/$REPO:$TAG
  docker stop parakeet 2>/dev/null; docker rm parakeet 2>/dev/null
  docker run -d --name parakeet --gpus all --restart unless-stopped \
    -p 8000:8000 $ECR/$REPO:$TAG
"

echo "Done. Health check:"
echo "  ssh -i parakeet-key.pem -L 8000:localhost:8000 ubuntu@$INSTANCE_IP -N &"
echo "  curl localhost:8000/health"

# In another terminal:
# curl http://localhost:8000/health
# Should return: {"status":"ok","model":"stt-tdt-0.6b-v3","runtime":"onnx","gpu":false}


# SSH into the instance
ssh -i parakeet-key.pem ubuntu@${PUBLIC_IP}


# On the instance:
REGION=eu-west-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"



The dalston-gpu IAM role is missing the ssm:GetParameter permission for /dalston/tailscale-auth-key. You need to attach a policy allowing it. SSH in and fix the role, or add the permission from your local machine:


aws iam put-role-policy \
  --role-name dalston-gpu \
  --policy-name ssm-tailscale-key \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:eu-west-2:178457246645:parameter/dalston/tailscale-auth-key"
    }]
  }'
