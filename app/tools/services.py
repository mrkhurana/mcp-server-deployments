from typing import Any

from kubernetes import client

from ..config import validate_namespace
from ..kubernetes_client import get_api_client


def describe_service(namespace: str, service_name: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not service_name or not service_name.strip():
        raise ValueError("service_name is required")

    core = client.CoreV1Api(api_client=get_api_client())
    service = core.read_namespaced_service(name=service_name, namespace=ns)
    endpoints = core.list_namespaced_endpoints(namespace=ns, field_selector=f"metadata.name={service_name}")

    return {
        "service_name": service.metadata.name,
        "namespace": ns,
        "type": service.spec.type,
        "ports": [port.to_dict() for port in (service.spec.ports or [])],
        "selector": service.spec.selector or {},
        "cluster_ip": service.spec.cluster_ip,
        "endpoints": [
            {
                "name": item.metadata.name,
                "addresses": [address.to_dict() for address in (item.subsets[0].addresses if item.subsets else [])],
            }
            for item in endpoints.items
        ],
    }
