output "instance_id" {
  description = "EC2 instance ID (use for start/stop commands)"
  value       = module.ec2.instance_id
}

output "public_ip" {
  description = "Public IP address (use for initial Tailscale setup)"
  value       = module.ec2.public_ip
}

output "private_ip" {
  description = "Private IP address"
  value       = module.ec2.private_ip
}

output "s3_bucket" {
  description = "S3 bucket name for artifacts"
  value       = module.s3_artifacts.bucket_name
}

output "security_group_id" {
  description = "Security group ID (update after Tailscale setup)"
  value       = module.ec2.security_group_id
}

output "shell_aliases" {
  description = "Shell aliases to add to ~/.zshrc"
  value       = <<-EOT
    # Add these to your ~/.zshrc:
    export DALSTON_INSTANCE_ID="${module.ec2.instance_id}"
    alias dalston-up="aws ec2 start-instances --instance-ids $DALSTON_INSTANCE_ID"
    alias dalston-down="aws ec2 stop-instances --instance-ids $DALSTON_INSTANCE_ID"
    alias dalston-status="aws ec2 describe-instances --instance-ids $DALSTON_INSTANCE_ID --query 'Reservations[0].Instances[0].State.Name' --output text"
  EOT
}
