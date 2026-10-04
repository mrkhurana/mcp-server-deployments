from typing import Any

from kubernetes import client

from ..config import settings, validate_namespace, validate_positive_int
from ..kubernetes_client import get_api_client


def _core_api() -> client.CoreV1Api:
    return client.CoreV1Api(api_client=get_api_client())


def _pod_summary(pod: client.V1Pod) -> dict[str, Any]:
    status = pod.status or client.V1PodStatus()
    ready_count = 0
    total_count = 0
    for c in pod.spec.containers if pod.spec else []:
        total_count += 1
        for cs in (status.container_statuses or []):
            if cs.name == c.name and cs.ready:
                ready_count += 1
                break

    return {
        "namespace": pod.metadata.namespace,
        "pod_name": pod.metadata.name,
        "phase": status.phase,
        "ready": f"{ready_count}/{total_count}",
        "container_states": [
            {
                "name": cs.name,
                "state": {
                    key: value
                    for key, value in (
                        getattr(cs.state, "waiting", None)
                        or getattr(cs.state, "running", None)
                        or getattr(cs.state, "terminated", None)
                    ).to_dict().items()
                    if value is not None
                }
                if cs.state
                else {},
                "restart_count": cs.restart_count,
            }
            for cs in (status.container_statuses or [])
        ],
        "reason": status.reason,
        "message": status.message,
        "node": status.host_ip,
        "pod_ip": status.pod_ip,
    }


def get_pods(namespace: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    pods = _core_api().list_namespaced_pod(ns)
    return {
        "namespace": ns,
        "count": len(pods.items),
        "pods": [_pod_summary(p) for p in pods.items],
    }


def describe_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not pod_name or not pod_name.strip():
        raise ValueError("pod_name is required")

    pod = _core_api().read_namespaced_pod(name=pod_name, namespace=ns)
    status = pod.status or client.V1PodStatus()

    return {
        "metadata": {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "labels": pod.metadata.labels or {},
            "annotations": pod.metadata.annotations or {},
        },
        "node": status.host_ip,
        "status": {
            "phase": status.phase,
            "reason": status.reason,
            "message": status.message,
            "conditions": [
                {
                    "type": condition.type,
                    "status": condition.status,
                    "reason": condition.reason,
                    "message": condition.message,
                }
                for condition in (status.conditions or [])
            ],
        },
        "container_states": [
            {
                "name": cs.name,
                "state": {k: v for k, v in cs.state.to_dict().items() if v is not None} if cs.state else {},
                "restart_count": cs.restart_count,
                "ready": cs.ready,
                "image": cs.image,
            }
            for cs in (status.container_statuses or [])
        ],
        "image": [
            {
                "name": container.name,
                "image": container.image,
                "resources": container.resources.to_dict() if container.resources else {},
            }
            for container in (pod.spec.containers if pod.spec else [])
        ],
        "resource_configuration": [
            {
                "name": container.name,
                "requests": container.resources.requests if container.resources else {},
                "limits": container.resources.limits if container.resources else {},
            }
            for container in (pod.spec.containers if pod.spec else [])
        ],
    }


def restart_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    if not pod_name or not pod_name.strip():
        raise ValueError("pod_name is required")

    _core_api().delete_namespaced_pod(name=pod_name, namespace=ns, grace_period_seconds=30)
    return {
        "status": "deleted",
        "namespace": ns,
        "pod_name": pod_name,
    }
