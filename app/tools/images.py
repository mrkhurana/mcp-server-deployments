from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..config import settings, validate_positive_int
from ..kubernetes_client import ecr_client
from .common import k8s_tool, ts

MAX_TAGS = 50


@k8s_tool
def list_image_tags(repository: str, limit: int = 20) -> dict[str, Any]:
    """List the most recently pushed image tags in one of the server's allowed ECR repositories, newest first,
    with the full image URI to pass to set_image."""
    if not settings.allowed_ecr_repositories:
        raise ValueError("list_image_tags is disabled: MCP_ALLOWED_ECR_REPOSITORIES is not configured")
    repo = (repository or "").strip()
    if repo not in settings.allowed_ecr_repositories:
        raise ValueError(f"repository '{repo}' is not allowed; allowed: {', '.join(settings.allowed_ecr_repositories)}")
    count = validate_positive_int(limit, "limit", maximum=MAX_TAGS)

    ecr = ecr_client()
    try:
        uri = ecr.describe_repositories(repositoryNames=[repo])["repositories"][0]["repositoryUri"]
        images = []
        for page in ecr.get_paginator("describe_images").paginate(repositoryName=repo, filter={"tagStatus": "TAGGED"}):
            images.extend(page["imageDetails"])
    except (BotoCoreError, ClientError) as exc:
        raise ValueError(f"ECR request failed: {exc}") from exc
    images.sort(key=lambda image: image["imagePushedAt"], reverse=True)

    tags = [
        {"tag": tag, "image": f"{uri}:{tag}", "pushed_at": ts(image["imagePushedAt"]), "digest": image["imageDigest"]}
        for image in images
        for tag in image.get("imageTags", [])
    ][:count]
    return {"repository": repo, "repository_uri": uri, "count": len(tags), "tags": tags}
