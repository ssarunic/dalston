# IAM Role and Policy for Dalston EC2 instance

data "aws_caller_identity" "current" {}

# Trust policy allowing EC2 to assume this role
data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# IAM Role
resource "aws_iam_role" "dalston" {
  name               = "${var.name_prefix}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json

  tags = var.tags
}

# S3 access policy
data "aws_iam_policy_document" "s3_access" {
  statement {
    sid = "S3BucketAccess"
    actions = [
      "s3:ListBucket"
    ]
    resources = [var.s3_bucket_arn]
  }

  statement {
    sid = "S3ObjectAccess"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject"
    ]
    resources = ["${var.s3_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "s3_access" {
  name   = "${var.name_prefix}-s3-access"
  role   = aws_iam_role.dalston.id
  policy = data.aws_iam_policy_document.s3_access.json
}

# Instance profile
resource "aws_iam_instance_profile" "dalston" {
  name = "${var.name_prefix}-instance-profile"
  role = aws_iam_role.dalston.name

  tags = var.tags
}
