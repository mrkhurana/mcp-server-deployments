from typing import Any

from kubernetes import client

from ..config import settings, validate_namespace, validate_replicas
from ..kubernetes_client import get_api_client


def describe_deployment(namespace: str, deployment_name: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not deployment_name or not deployment_name.strip():
        raise ValueError("deployment_name is required")

    api = client.AppsV1Api(api_client=get_api_client())
    deployment = api.read_namespaced_deployment(name=deployment_name, namespace=ns)
    spec = deployment.spec or client.V1DeploymentSpec()
    status = deployment.status or client.V1DeploymentStatus()

    return {
        "deployment_name": deployment.metadata.name,
        "namespace": ns,
        "replicas": spec.replicas,
        "ready_replicas": status.ready_replicas,
        "available_replicas": status.available_replicas,
        "updated_replicas": status.updated_replicas,
        "containers": [
            {
                "name": container.name,
                "image": container.image,
                "ports": [port.to_dict() for port in (container.ports or [])],
                "resources": container.resources.to_dict() if container.resources else {},
                "env_vars": [
                    {
                        "name": env.name,
                        "value": env.value,
                        "value_from": env.value_from.to_dict() if env.value_from else None,
                    }
                    for env in (container.env or [])
                ],
            }
            for container in (spec.template.spec.containers if spec.template and spec.template.spec else [])
        ],
        "selector": spec.selector.to_dict() if spec.selector else {},
        "labels": deployment.metadata.labels or {},
    }


def scale_deployment(namespace: str, deployment_name: str, replicas: int | str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not deployment_name or not deployment_name.strip():
        raise ValueError("deployment_name is required")

    count = validate_replicas(replicas)
    api = client.AppsV1Api(api_client=get_api_client())
    deployment = api.read_namespaced_deployment(name=deployment_name, namespace=ns)
    deployment.spec.replicas = count
    updated = api.replace_namespaced_deployment(name=deployment_name, namespace=ns, body=deployment)

    return {
        "namespace": ns,
        "deployment_name": deployment_name,
        "replicas": updated.spec.replicas,
        "status": "updated",
    }
