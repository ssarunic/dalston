variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "eu-west-2"
}

variable "name_prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "dalston"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "g5.xlarge"
}

variable "key_name" {
  description = "Name of the SSH key pair for EC2 access"
  type        = string
}

variable "root_volume_size" {
  description = "Size of root EBS volume in GB"
  type        = number
  default     = 30
}

variable "data_volume_size" {
  description = "Size of data EBS volume in GB"
  type        = number
  default     = 50
}

variable "repo_url" {
  description = "Git repository URL for Dalston"
  type        = string
  default     = "https://github.com/your-org/dalston.git"
}

variable "repo_branch" {
  description = "Git branch to deploy"
  type        = string
  default     = "main"
}

variable "job_retention_days" {
  description = "Number of days to retain job artifacts in S3"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags for resources"
  type        = map(string)
  default     = {}
}
