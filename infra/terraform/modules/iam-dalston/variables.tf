variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "dalston"
}

variable "s3_bucket_arn" {
  description = "ARN of the S3 bucket for artifacts"
  type        = string
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}
