#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

command -v terraform >/dev/null 2>&1 || { echo "terraform is required" >&2; exit 1; }
command -v aws >/dev/null 2>&1 || { echo "aws CLI is required" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }

cd terraform
terraform init
terraform apply -auto-approve
ECR_REPO_URL="$(terraform output -raw repository_url)"
ECR_REGISTRY="$(echo "$ECR_REPO_URL" | cut -d'/' -f1)"

aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" | docker login --username AWS --password-stdin "$ECR_REGISTRY"

cd "$SCRIPT_DIR"
IMAGE_URI="${ECR_REPO_URL}:${VERSION}"
LATEST_URI="${ECR_REPO_URL}:latest"

docker buildx build \
  --platform linux/amd64 \
  --tag "$IMAGE_URI" \
  --tag "$LATEST_URI" \
  --push .

printf '\nMCP image:\n%s\n' "$IMAGE_URI"
printf 'Latest tag:\n%s\n' "$LATEST_URI"
