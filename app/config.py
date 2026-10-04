import os
from dataclasses import dataclass, field
from typing import List


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def _env_list(name: str) -> List[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    eks_cluster_name: str = _env_first("EKS_CLUSTER_NAME", default="")
    allowed_namespaces: List[str] = field(
        default_factory=lambda: [
            ns.strip()
            for ns in _env_first("ALLOWED_NAMESPACES", "K8S_NAMESPACE_ALLOWLIST", default="application").split(",")
            if ns.strip()
        ]
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
    min_replicas: int = int(_env_first("MCP_MIN_REPLICAS", default="0"))
    max_replicas: int = int(_env_first("MCP_MAX_REPLICAS", default="5"))


settings = Settings()


def validate_namespace(namespace: str | None) -> str:
    if not namespace or not namespace.strip():
        raise ValueError("namespace is required")
    normalized = namespace.strip()
    if normalized not in settings.allowed_namespaces:
        raise ValueError(
            f"namespace '{normalized}' is not allowed; allowed namespaces: {', '.join(settings.allowed_namespaces)}"
        )
    return normalized


def validate_positive_int(value: int | str | None, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
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
