import os
import re
from dataclasses import dataclass, field
from typing import List


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def _env_list(*names: str, default: str = "") -> List[str]:
    return [value.strip() for value in _env_first(*names, default=default).split(",") if value.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    eks_cluster_name: str = _env_first("EKS_CLUSTER_NAME", default="")
    allowed_namespaces: List[str] = field(
        default_factory=lambda: _env_list("ALLOWED_NAMESPACES", "K8S_NAMESPACE_ALLOWLIST", default="application")
    )
    port: int = int(_env_first("MCP_PORT", "PORT", default="8080"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    allowed_hosts: List[str] = field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*", *_env_list("MCP_ALLOWED_HOSTS")]
    )
    allowed_origins: List[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
    )

    # Bearer token required on /mcp. Empty disables auth (local development only).
    auth_token: str = os.getenv("MCP_AUTH_TOKEN", "")

    # Remediation and operations policy.
    enable_remediation: bool = _env_bool("MCP_ENABLE_REMEDIATION", True)
    # Empty list means every deployment in an allowed namespace may be changed.
    allowed_deployments: List[str] = field(default_factory=lambda: _env_list("MCP_ALLOWED_DEPLOYMENTS"))
    min_replicas: int = int(_env_first("MCP_MIN_REPLICAS", default="1"))
    max_replicas: int = int(_env_first("MCP_MAX_REPLICAS", default="5"))
    max_cpu_millicores: int = int(_env_first("MCP_MAX_CPU_MILLICORES", default="1000"))
    max_memory_mib: int = int(_env_first("MCP_MAX_MEMORY_MIB", default="1024"))
    # Images set_image may deploy, e.g. "123456789012.dkr.ecr.us-east-1.amazonaws.com/nginx".
    allowed_image_prefixes: List[str] = field(default_factory=lambda: _env_list("MCP_ALLOWED_IMAGE_PREFIXES"))
    # ECR repositories list_image_tags may read, e.g. "nginx,mcp-server".
    allowed_ecr_repositories: List[str] = field(default_factory=lambda: _env_list("MCP_ALLOWED_ECR_REPOSITORIES"))


settings = Settings()

# RFC 1123 label/subdomain, as Kubernetes uses for resource and container names.
_DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_CPU = re.compile(r"^(\d+)m$|^(\d+(\.\d+)?)$")
_MEMORY = re.compile(r"^(\d+)(Mi|Gi)$")


def validate_namespace(namespace: str | None) -> str:
    if not namespace or not namespace.strip():
        raise ValueError("namespace is required")
    normalized = namespace.strip()
    if normalized not in settings.allowed_namespaces:
        raise ValueError(
            f"namespace '{normalized}' is not allowed; allowed namespaces: {', '.join(settings.allowed_namespaces)}"
        )
    return normalized


def validate_name(value: str | None, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    name = value.strip()
    if len(name) > 253 or not _DNS1123.match(name):
        raise ValueError(f"{field_name} '{name}' is not a valid Kubernetes name")
    return name


def validate_positive_int(value: int | str | None, field_name: str, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")
    return number


def validate_replicas(replicas: int | str) -> int:
    try:
        count = int(replicas)
    except (TypeError, ValueError) as exc:
        raise ValueError("replicas must be an integer") from exc
    if count < settings.min_replicas or count > settings.max_replicas:
        raise ValueError(
            f"replicas must be between {settings.min_replicas} and {settings.max_replicas}"
        )
    return count


def require_remediation(deployment_name: str | None = None) -> None:
    """Raise unless state-changing operations are enabled for this deployment."""
    if not settings.enable_remediation:
        raise ValueError("remediation is disabled on this server (MCP_ENABLE_REMEDIATION=false)")
    if deployment_name and settings.allowed_deployments and deployment_name not in settings.allowed_deployments:
        raise ValueError(
            f"deployment '{deployment_name}' is not in the remediation allowlist: "
            f"{', '.join(settings.allowed_deployments)}"
        )


def validate_cpu(value: str | None, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    match = _CPU.match(value.strip())
    if not match:
        raise ValueError(f"{field_name} must look like '250m' or '0.5'")
    millicores = int(match.group(1)) if match.group(1) else int(float(match.group(2)) * 1000)
    if millicores <= 0 or millicores > settings.max_cpu_millicores:
        raise ValueError(f"{field_name} must be between 1m and {settings.max_cpu_millicores}m")
    return value.strip()


def validate_memory(value: str | None, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    match = _MEMORY.match(value.strip())
    if not match:
        raise ValueError(f"{field_name} must look like '128Mi' or '1Gi'")
    mib = int(match.group(1)) * (1024 if match.group(2) == "Gi" else 1)
    if mib <= 0 or mib > settings.max_memory_mib:
        raise ValueError(f"{field_name} must be between 1Mi and {settings.max_memory_mib}Mi")
    return value.strip()


def validate_image(image: str | None) -> str:
    if not image or not image.strip():
        raise ValueError("image is required")
    image = image.strip()
    if not settings.allowed_image_prefixes:
        raise ValueError("set_image is disabled: MCP_ALLOWED_IMAGE_PREFIXES is not configured")
    if not any(image.startswith(prefix.rstrip("/") + ":") or image.startswith(prefix.rstrip("/") + "@")
               for prefix in settings.allowed_image_prefixes):
        raise ValueError(
            f"image '{image}' is not allowed; it must be a tag or digest of: {', '.join(settings.allowed_image_prefixes)}"
        )
    if image.endswith(":latest"):
        raise ValueError("use an explicit version tag, not ':latest'")
    return image
