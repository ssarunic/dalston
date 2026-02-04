terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local backend for initial setup
  # TODO: Migrate to S3 backend for team collaboration
  # backend "s3" {
  #   bucket         = "dalston-terraform-state"
  #   key            = "dev/terraform.tfstate"
  #   region         = "us-west-2"
  #   encrypt        = true
  #   dynamodb_table = "dalston-terraform-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "dalston"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

# Get default VPC and subnet
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# S3 bucket for artifacts
module "s3_artifacts" {
  source = "../../modules/s3-artifacts"

  name_prefix        = var.name_prefix
  job_retention_days = var.job_retention_days

  tags = var.tags
}

# IAM role for EC2
module "iam" {
  source = "../../modules/iam-dalston"

  name_prefix   = var.name_prefix
  s3_bucket_arn = module.s3_artifacts.bucket_arn

  tags = var.tags
}

# User data script
locals {
  user_data = templatefile("${path.module}/../../../scripts/user-data.sh", {
    DATA_DEVICE = "/dev/xvdf"
    DATA_MOUNT  = "/data"
    REPO_URL    = var.repo_url
    REPO_BRANCH = var.repo_branch
    S3_BUCKET   = module.s3_artifacts.bucket_name
    AWS_REGION  = var.aws_region
  })
}

# EC2 instance
module "ec2" {
  source = "../../modules/ec2-dalston"

  name_prefix               = var.name_prefix
  instance_type             = var.instance_type
  key_name                  = var.key_name
  vpc_id                    = data.aws_vpc.default.id
  subnet_id                 = data.aws_subnets.default.ids[0]
  iam_instance_profile_name = module.iam.instance_profile_name
  root_volume_size          = var.root_volume_size
  data_volume_size          = var.data_volume_size
  user_data                 = base64encode(local.user_data)

  tags = var.tags
}
