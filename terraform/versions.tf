terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "tf-state-backup-kay-bkp"
    key    = "aiops-mcp/ecr/terraform.tfstate"
    region = "us-east-1"
  }
}
