# EC2 Instance for Dalston with Docker Compose

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security group - SSH from Tailscale only
resource "aws_security_group" "dalston" {
  name        = "${var.name_prefix}-sg"
  description = "Security group for Dalston EC2 instance"
  vpc_id      = var.vpc_id

  # SSH from Tailscale CIDR only
  ingress {
    description = "SSH from Tailscale"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["100.64.0.0/10"]
  }

  # Allow all outbound traffic
  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-sg"
  })
}

# EC2 Instance
resource "aws_instance" "dalston" {
  ami                    = data.aws_ami.amazon_linux_2023.id
  instance_type          = var.instance_type
  key_name               = var.key_name
  iam_instance_profile   = var.iam_instance_profile_name
  vpc_security_group_ids = [aws_security_group.dalston.id]
  subnet_id              = var.subnet_id

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  user_data = var.user_data

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-server"
  })

  lifecycle {
    ignore_changes = [ami]
  }
}

# Data EBS volume (persistent, survives instance termination)
resource "aws_ebs_volume" "data" {
  availability_zone = aws_instance.dalston.availability_zone
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-data"
  })
}

# Attach data volume to instance
resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/xvdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.dalston.id
  stop_instance_before_detaching = true
}
