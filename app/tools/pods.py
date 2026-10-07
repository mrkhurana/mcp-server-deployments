from typing import Any

from kubernetes import client

from ..config import require_remediation, validate_name, validate_namespace
from ..kubernetes_client import apps_api, core_api
from .common import container_state, k8s_tool, mutating_tool, ts


def _ready_count(pod: client.V1Pod) -> str:
    statuses = (pod.status.container_statuses or []) if pod.status else []
    total = len(pod.spec.containers) if pod.spec else 0
    return f"{sum(1 for cs in statuses if cs.ready)}/{total}"


def _pod_summary(pod: client.V1Pod) -> dict[str, Any]:
    status = pod.status or client.V1PodStatus()
    return {
        "pod_name": pod.metadata.name,
        "phase": status.phase,
        "ready": _ready_count(pod),
        "restarts": sum(cs.restart_count or 0 for cs in (status.container_statuses or [])),
        "containers": [
            {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count, **container_state(cs.state)}
            for cs in (status.container_statuses or [])
        ],
        "reason": status.reason,
        "message": status.message,
        "node_name": pod.spec.node_name if pod.spec else None,
        "pod_ip": status.pod_ip,
        "created": ts(pod.metadata.creation_timestamp),
    }


def _owner(pod: client.V1Pod) -> dict[str, str] | None:
    for ref in pod.metadata.owner_references or []:
        if ref.controller:
            return {"kind": ref.kind, "name": ref.name}
    return None


def _probe(probe: client.V1Probe | None) -> dict[str, Any] | None:
    if probe is None:
        return None
    data: dict[str, Any] = {
        "initial_delay_seconds": probe.initial_delay_seconds,
        "period_seconds": probe.period_seconds,
        "failure_threshold": probe.failure_threshold,
    }
    if probe.http_get:
        data["http_get"] = {"path": probe.http_get.path, "port": probe.http_get.port}
    if probe.tcp_socket:
        data["tcp_socket"] = {"port": probe.tcp_socket.port}
    if probe._exec:
        data["exec"] = probe._exec.command
    return data


@k8s_tool
def get_pods(namespace: str) -> dict[str, Any]:
    """List every pod in a namespace with phase, readiness, restart count and container states
    (e.g. waiting: CrashLoopBackOff / ImagePullBackOff). Use it to see which pods are unhealthy."""
    ns = validate_namespace(namespace)
    pods = core_api().list_namespaced_pod(ns)
    return {"namespace": ns, "count": len(pods.items), "pods": [_pod_summary(p) for p in pods.items]}


@k8s_tool
def describe_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    """Detailed view of one pod: conditions, container states, images, resource requests/limits,
    readiness/liveness probes, owning controller and the pod's own recent events.
    Use it after get_pods to find out why a specific pod is not Ready."""
    ns = validate_namespace(namespace)
    name = validate_name(pod_name, "pod_name")
    core = core_api()
    pod = core.read_namespaced_pod(name=name, namespace=ns)
    status = pod.status or client.V1PodStatus()
    events = core.list_namespaced_event(namespace=ns, field_selector=f"involvedObject.name={name}")

    return {
        **_pod_summary(pod),
        "namespace": ns,
        "labels": pod.metadata.labels or {},
        "owner": _owner(pod),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ],
        "spec_containers": [
            {
                "name": c.name,
                "image": c.image,
                "ports": [p.container_port for p in (c.ports or [])],
                "requests": (c.resources.requests or {}) if c.resources else {},
                "limits": (c.resources.limits or {}) if c.resources else {},
                "readiness_probe": _probe(c.readiness_probe),
                "liveness_probe": _probe(c.liveness_probe),
            }
            for c in (pod.spec.containers if pod.spec else [])
        ],
        "events": [
            {
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
                "last_seen": ts(e.last_timestamp or e.event_time or e.metadata.creation_timestamp),
            }
            for e in sorted(
                events.items,
                key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp,
                reverse=True,
            )[:20]
        ],
    }


@mutating_tool
def restart_pod(namespace: str, pod_name: str, dry_run: bool = False) -> dict[str, Any]:
    """Restart one pod by deleting it so its ReplicaSet creates a fresh replacement.
    Only works for pods owned by a controller (ReplicaSet/StatefulSet/DaemonSet); bare pods are refused.
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(pod_name, "pod_name")
    core = core_api()
    pod = core.read_namespaced_pod(name=name, namespace=ns)
    owner = _owner(pod)
    if owner is None:
        raise ValueError(f"pod '{name}' has no controller owner; deleting it would not restart it")

    deployment = None
    if owner["kind"] == "ReplicaSet":
        rs = apps_api().read_namespaced_replica_set(name=owner["name"], namespace=ns)
        deployment = next((r.name for r in rs.metadata.owner_references or [] if r.kind == "Deployment"), None)
    require_remediation(deployment)

    result = {"namespace": ns, "pod_name": name, "owner": owner, "deployment": deployment, "dry_run": dry_run}
    if dry_run:
        return {**result, "would": f"delete pod {name}; {owner['kind']} {owner['name']} recreates it"}

    core.delete_namespaced_pod(name=name, namespace=ns, grace_period_seconds=30)
    return {**result, "status": "deleted; replacement pod is being created"}
