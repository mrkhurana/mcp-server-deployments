from typing import Any

from ..config import validate_name, validate_namespace, validate_positive_int
from ..kubernetes_client import core_api
from .common import k8s_tool

MAX_TAIL_LINES = 500


@k8s_tool
def get_pod_logs(
    namespace: str,
    pod_name: str,
    container_name: str | None = None,
    tail_lines: int = 100,
    previous: bool = False,
) -> dict[str, Any]:
    """Return the last log lines of a pod's container (default: its first container, 100 lines, max 500).
    Set previous=true to read the logs of the previous, crashed instance of a restarting container
    (the key evidence for CrashLoopBackOff)."""
    ns = validate_namespace(namespace)
    name = validate_name(pod_name, "pod_name")
    tail = validate_positive_int(tail_lines, "tail_lines", maximum=MAX_TAIL_LINES)
    core = core_api()

    container = validate_name(container_name, "container_name") if container_name else None
    if container is None:
        pod = core.read_namespaced_pod(name=name, namespace=ns)
        container = pod.spec.containers[0].name

    logs = core.read_namespaced_pod_log(
        name=name, namespace=ns, container=container, tail_lines=tail, previous=previous
    )
    return {
        "namespace": ns,
        "pod_name": name,
        "container_name": container,
        "previous": previous,
        "tail_lines": tail,
        "logs": logs,
    }
