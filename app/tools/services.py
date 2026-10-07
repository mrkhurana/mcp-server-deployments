from typing import Any

from kubernetes.client.exceptions import ApiException

from ..config import validate_name, validate_namespace
from ..kubernetes_client import core_api
from .common import k8s_tool


def _endpoint_counts(core, namespace: str, service_name: str) -> tuple[int, int]:
    """(ready, not_ready) endpoint addresses behind a service."""
    endpoints = core.list_namespaced_endpoints(namespace=namespace, field_selector=f"metadata.name={service_name}")
    ready = not_ready = 0
    for item in endpoints.items:
        for subset in item.subsets or []:
            ready += len(subset.addresses or [])
            not_ready += len(subset.not_ready_addresses or [])
    return ready, not_ready


@k8s_tool
def list_services(namespace: str) -> dict[str, Any]:
    """List the services in a namespace with type, ports, selector and how many ready endpoints each has.
    A service with 0 ready endpoints is not serving traffic."""
    ns = validate_namespace(namespace)
    core = core_api()
    services = core.list_namespaced_service(ns)
    items = []
    for svc in services.items:
        ready, not_ready = _endpoint_counts(core, ns, svc.metadata.name)
        items.append({
            "service_name": svc.metadata.name,
            "type": svc.spec.type,
            "ports": [f"{p.port}->{p.target_port}/{p.protocol}" for p in (svc.spec.ports or [])],
            "selector": svc.spec.selector or {},
            "ready_endpoints": ready,
            "not_ready_endpoints": not_ready,
        })
    return {"namespace": ns, "count": len(items), "services": items}


@k8s_tool
def describe_service(namespace: str, service_name: str) -> dict[str, Any]:
    """Detailed view of one service: ports, selector, the pods the selector matches and which
    endpoint addresses are ready. Use it when an app is running but not reachable."""
    ns = validate_namespace(namespace)
    name = validate_name(service_name, "service_name")
    core = core_api()
    svc = core.read_namespaced_service(name=name, namespace=ns)
    selector = svc.spec.selector or {}
    matching = []
    if selector:
        label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
        matching = [p.metadata.name for p in core.list_namespaced_pod(ns, label_selector=label_selector).items]
    ready, not_ready = _endpoint_counts(core, ns, name)

    return {
        "service_name": name,
        "namespace": ns,
        "type": svc.spec.type,
        "cluster_ip": svc.spec.cluster_ip,
        "ports": [p.to_dict() for p in (svc.spec.ports or [])],
        "selector": selector,
        "pods_matching_selector": matching,
        "ready_endpoints": ready,
        "not_ready_endpoints": not_ready,
    }


@k8s_tool
def check_app_health(namespace: str, service_name: str, path: str = "/", port: int | None = None) -> dict[str, Any]:
    """Send an HTTP GET to a service through the Kubernetes API server proxy and report the result.
    Answers 'is the app actually serving?' rather than 'is the pod Running?'."""
    ns = validate_namespace(namespace)
    name = validate_name(service_name, "service_name")
    if not path.startswith("/") or ".." in path:
        raise ValueError("path must be an absolute path such as '/' or '/healthz'")
    core = core_api()
    svc = core.read_namespaced_service(name=name, namespace=ns)
    svc_port = port or (svc.spec.ports[0].port if svc.spec.ports else 80)

    try:
        target = f"{name}:{svc_port}"
        if path == "/":
            body = core.connect_get_namespaced_service_proxy(name=target, namespace=ns, _request_timeout=10)
        else:
            body = core.connect_get_namespaced_service_proxy_with_path(
                name=target, namespace=ns, path=path.lstrip("/"), _request_timeout=10
            )
        return {"service_name": name, "port": svc_port, "path": path, "healthy": True,
                "status": 200, "body_preview": str(body)[:300]}
    except ApiException as exc:
        if exc.status == 403 and "services/proxy" in (exc.body or ""):
            raise
        return {"service_name": name, "port": svc_port, "path": path, "healthy": False,
                "status": exc.status, "body_preview": (exc.body or "")[:300]}
