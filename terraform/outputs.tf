output "repository_name" {
  description = "ECR repository name."
  value       = aws_ecr_repository.mcp_server.name
}

output "repository_url" {
  description = "Repository URI without a tag, ready for docker build and push commands."
  value       = aws_ecr_repository.mcp_server.repository_url
}

output "registry_id" {
  description = "AWS account registry ID for the ECR repository."
  value       = aws_ecr_repository.mcp_server.registry_id
}
