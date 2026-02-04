output "instance_id" {
  description = "ID of the EC2 instance"
  value       = aws_instance.dalston.id
}

output "private_ip" {
  description = "Private IP address of the instance"
  value       = aws_instance.dalston.private_ip
}

output "public_ip" {
  description = "Public IP address of the instance (if available)"
  value       = aws_instance.dalston.public_ip
}

output "security_group_id" {
  description = "ID of the security group"
  value       = aws_security_group.dalston.id
}

output "data_volume_id" {
  description = "ID of the data EBS volume"
  value       = aws_ebs_volume.data.id
}

output "availability_zone" {
  description = "Availability zone of the instance"
  value       = aws_instance.dalston.availability_zone
}
