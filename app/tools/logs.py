from typing import Any

from kubernetes import client

from ..config import settings, validate_namespace, validate_positive_int
from ..kubernetes_client import get_api_client


def get_pod_logs(namespace: str, pod_name: str, container_name: str, tail_lines: int | str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not pod_name or not pod_name.strip():
        raise ValueError("pod_name is required")
    if not container_name or not container_name.strip():
        raise ValueError("container_name is required")
    tail = validate_positive_int(tail_lines, "tail_lines")

    api = client.CoreV1Api(api_client=get_api_client())
    logs = api.read_namespaced_pod_log(
        name=pod_name,
        namespace=ns,
        container=container_name,
        tail_lines=tail,
    )

    return {
        "namespace": ns,
        "pod_name": pod_name,
        "container_name": container_name,
        "tail_lines": tail,
        "logs": logs,
    }
