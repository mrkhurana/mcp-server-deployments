resource "aws_ecr_repository" "mcp_server" {
  name                 = var.repository_name
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = var.scan_images_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  force_delete = var.force_delete

  tags = {
    Name        = var.repository_name
    Project     = "aiops-mcp"
    Environment = "workshop"
  }
}

resource "aws_ecr_lifecycle_policy" "default" {
  repository = aws_ecr_repository.mcp_server.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the most recent 20 images and prune older ones."
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v"]
          countType     = "imageCountMoreThan"
          countNumber   = 20
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
