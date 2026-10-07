#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-latest}"
# nginx versions mirrored for the EKS workload. Add more (e.g. NGINX_TAGS="1.27-alpine 1.26-alpine")
# to give set_image another real version to switch to.
NGINX_TAGS="${NGINX_TAGS:-1.27-alpine}"
# Docker Official Images mirrored on ECR Public: same images as docker.io/library, without Docker Hub's
# anonymous pull rate limit (429 Too Many Requests).
NGINX_SOURCE="${NGINX_SOURCE:-public.ecr.aws/docker/library/nginx}"
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
NGINX_REPO_URL="$(terraform output -raw workload_repository_url)"

aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
# Signed-in pulls from ECR Public get a ~10x higher rate limit than anonymous ones (429 Too Many Requests).
# ECR Public tokens are only issued in us-east-1, whatever region the rest of the deploy uses.
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws

cd "$SCRIPT_DIR"
IMAGE_URI="${ECR_REPO_URL}:${VERSION}"
LATEST_URI="${ECR_REPO_URL}:latest"

docker buildx build \
  --platform linux/amd64 \
  --tag "$IMAGE_URI" \
  --tag "$LATEST_URI" \
  --push .

# Mirror nginx into private ECR (all architectures) so EKS can pull it without internet access.
# Tags already in ECR are skipped, so re-runs don't pull from the public registry again.
for tag in $NGINX_TAGS; do
  if docker buildx imagetools inspect "${NGINX_REPO_URL}:${tag}" >/dev/null 2>&1; then
    printf 'nginx:%s is already in ECR, skipping\n' "$tag"
  else
    # Retry so a burst-rate 429 from the public registry doesn't stop the whole deploy.
    for wait in 10 20 30 0; do
      docker buildx imagetools create --tag "${NGINX_REPO_URL}:${tag}" "${NGINX_SOURCE}:${tag}" && break
      [[ "$wait" -gt 0 ]] || { echo "Mirroring nginx:${tag} failed after 4 attempts" >&2; exit 1; }
      printf 'Mirroring nginx:%s failed, retrying in %ss...\n' "$tag" "$wait"
      sleep "$wait"
    done
  fi
done

printf '\nMCP image:\n%s\n' "$IMAGE_URI"
printf 'Latest tag:\n%s\n' "$LATEST_URI"
printf 'Workload images:\n'
for tag in $NGINX_TAGS; do printf '%s\n' "${NGINX_REPO_URL}:${tag}"; done
