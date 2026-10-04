variable "aws_region" {
  description = "AWS region in which the ECR repository will be created."
  type        = string
  default     = "us-east-1"
}

variable "repository_name" {
  description = "ECR repository name for the AI Ops MCP server."
  type        = string
  default     = "mcp-server"
}

variable "image_tag_mutability" {
  description = "Whether image tags can be overwritten."
  type        = string
  default     = "MUTABLE"
}

variable "scan_images_on_push" {
  description = "Enable ECR image scanning."
  type        = bool
  default     = true
}

variable "force_delete" {
  description = "Allow Terraform to delete repository contents when destroying the repo."
  type        = bool
  default     = true
}
