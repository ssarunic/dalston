variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "dalston"
}

variable "job_retention_days" {
  description = "Number of days to retain job artifacts"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}
