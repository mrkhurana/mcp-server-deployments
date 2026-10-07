import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from anyio import to_thread
from kubernetes import client

from ..config import (
    require_remediation,
    validate_cpu,
    validate_image,
    validate_memory,
    validate_name,
    validate_namespace,
    validate_positive_int,
    validate_replicas,
)
from ..kubernetes_client import apps_api
from .common import k8s_tool, mutating_tool, ts
from .events import list_events

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
MAX_WAIT_SECONDS = 90


def _rollout(dep: client.V1Deployment) -> dict[str, Any]:
    """Rollout state, using the same rules as `kubectl rollout status`."""
    spec, status = dep.spec, dep.status or client.V1DeploymentStatus()
    desired = spec.replicas if spec.replicas is not None else 1
    conditions = {c.type: c for c in (status.conditions or [])}
    progressing = conditions.get("Progressing")

    if progressing is not None and progressing.reason == "ProgressDeadlineExceeded":
        state = "failed"
    elif (status.observed_generation or 0) < (dep.metadata.generation or 0):
        state = "pending"
    elif (status.updated_replicas or 0) < desired:
        state = "progressing"
    elif (status.replicas or 0) > (status.updated_replicas or 0):
        state = "progressing"  # old replicas still terminating
    elif (status.available_replicas or 0) < desired:
        state = "progressing"
    else:
        state = "complete"

    return {
        "rollout": state,
        "desired": desired,
        "ready": status.ready_replicas or 0,
        "available": status.available_replicas or 0,
        "updated": status.updated_replicas or 0,
        "revision": (dep.metadata.annotations or {}).get(REVISION_ANNOTATION),
    }


def _summary(dep: client.V1Deployment) -> dict[str, Any]:
    containers = dep.spec.template.spec.containers if dep.spec.template and dep.spec.template.spec else []
    return {
        "deployment_name": dep.metadata.name,
        **_rollout(dep),
        "images": {c.name: c.image for c in containers},
    }


def _read(namespace: str, deployment_name: str) -> client.V1Deployment:
    return apps_api().read_namespaced_deployment(name=deployment_name, namespace=namespace)


def _container_names(dep: client.V1Deployment) -> list[str]:
    return [c.name for c in dep.spec.template.spec.containers]


def _pick_container(dep: client.V1Deployment, container_name: str | None) -> str:
    names = _container_names(dep)
    if container_name is None:
        if len(names) != 1:
            raise ValueError(f"container_name is required; containers: {', '.join(names)}")
        return names[0]
    name = validate_name(container_name, "container_name")
    if name not in names:
        raise ValueError(f"container '{name}' not found; containers: {', '.join(names)}")
    return name


def _replica_sets(namespace: str, dep: client.V1Deployment) -> list[client.V1ReplicaSet]:
    sets = apps_api().list_namespaced_replica_set(namespace)
    owned = [
        rs for rs in sets.items
        if any(ref.uid == dep.metadata.uid for ref in (rs.metadata.owner_references or []))
    ]
    return sorted(owned, key=lambda rs: int((rs.metadata.annotations or {}).get(REVISION_ANNOTATION, "0")))


@k8s_tool
def list_deployments(namespace: str) -> dict[str, Any]:
    """List the deployments in a namespace with desired/ready/available replicas, rollout state and images.
    Start here to find deployment names."""
    ns = validate_namespace(namespace)
    deployments = apps_api().list_namespaced_deployment(ns)
    return {"namespace": ns, "count": len(deployments.items), "deployments": [_summary(d) for d in deployments.items]}


@k8s_tool
def describe_deployment(namespace: str, deployment_name: str) -> dict[str, Any]:
    """Detailed view of one deployment: replica counts, rollout state, conditions (e.g. ProgressDeadlineExceeded),
    strategy, selector, containers with image, ports, resources, probes and environment variable names.
    Environment variable values are not returned."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    dep = _read(ns, name)
    template = dep.spec.template.spec

    return {
        "namespace": ns,
        **_summary(dep),
        "strategy": dep.spec.strategy.type if dep.spec.strategy else None,
        "selector": dep.spec.selector.match_labels if dep.spec.selector else {},
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (dep.status.conditions or [])
        ],
        "containers": [
            {
                "name": c.name,
                "image": c.image,
                "ports": [p.container_port for p in (c.ports or [])],
                "requests": (c.resources.requests or {}) if c.resources else {},
                "limits": (c.resources.limits or {}) if c.resources else {},
                "readiness_probe_path": c.readiness_probe.http_get.path
                if c.readiness_probe and c.readiness_probe.http_get else None,
                "env_names": [e.name for e in (c.env or [])],
            }
            for c in template.containers
        ],
    }


@k8s_tool
def get_rollout_status(namespace: str, deployment_name: str) -> dict[str, Any]:
    """Rollout state of a deployment (complete / progressing / failed) and its revision history:
    one entry per ReplicaSet with revision number, image and replica counts. Use before rollback_deployment."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    dep = _read(ns, name)
    history = [
        {
            "revision": (rs.metadata.annotations or {}).get(REVISION_ANNOTATION),
            "replica_set": rs.metadata.name,
            "images": {c.name: c.image for c in rs.spec.template.spec.containers},
            "replicas": rs.status.replicas or 0,
            "ready": rs.status.ready_replicas or 0,
            "created": ts(rs.metadata.creation_timestamp),
        }
        for rs in _replica_sets(ns, dep)
    ]
    return {"namespace": ns, **_summary(dep), "history": history}


@mutating_tool
def scale_deployment(namespace: str, deployment_name: str, replicas: int, dry_run: bool = False) -> dict[str, Any]:
    """Set a deployment's replica count, within the server's safe bounds (default 1-5).
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    require_remediation(name)
    count = validate_replicas(replicas)
    api = apps_api()
    before = api.read_namespaced_deployment_scale(name=name, namespace=ns).spec.replicas

    result = {"namespace": ns, "deployment_name": name, "replicas_before": before, "dry_run": dry_run}
    if dry_run:
        return {**result, "would": f"scale from {before} to {count} replicas"}

    updated = api.patch_namespaced_deployment_scale(name=name, namespace=ns, body={"spec": {"replicas": count}})
    return {**result, "replicas_after": updated.spec.replicas, "status": "scaled; use wait_for_deployment_ready to verify"}


@mutating_tool
def rollout_restart_deployment(namespace: str, deployment_name: str, dry_run: bool = False) -> dict[str, Any]:
    """Restart all pods of a deployment gradually (one by one, like `kubectl rollout restart`), keeping it available.
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    require_remediation(name)
    dep = _read(ns, name)

    result = {"namespace": ns, "deployment_name": name, "revision_before": _rollout(dep)["revision"], "dry_run": dry_run}
    if dry_run:
        return {**result, "would": "replace every pod with a new one, gradually"}

    now = datetime.now(timezone.utc).isoformat()
    body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
    apps_api().patch_namespaced_deployment(name=name, namespace=ns, body=body)
    return {**result, "restarted_at": now, "status": "rollout started; use wait_for_deployment_ready to verify"}


@mutating_tool
def rollback_deployment(
    namespace: str, deployment_name: str, to_revision: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Roll a deployment back to an earlier revision (default: the one before the current one),
    like `kubectl rollout undo`. Check get_rollout_status first.
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    require_remediation(name)
    dep = _read(ns, name)
    current = int((dep.metadata.annotations or {}).get(REVISION_ANNOTATION, "0"))
    history = _replica_sets(ns, dep)
    by_revision = {int((rs.metadata.annotations or {}).get(REVISION_ANNOTATION, "0")): rs for rs in history}

    if to_revision is None:
        older = [rev for rev in by_revision if rev < current]
        if not older:
            raise ValueError(f"no revision older than the current revision {current} to roll back to")
        target = max(older)
    else:
        target = validate_positive_int(to_revision, "to_revision")
        if target not in by_revision:
            raise ValueError(f"revision {target} not found; available: {sorted(by_revision)}")
        if target == current:
            raise ValueError(f"revision {target} is already the current revision")

    template = by_revision[target].spec.template
    images = {c.name: c.image for c in template.spec.containers}
    result = {
        "namespace": ns,
        "deployment_name": name,
        "from_revision": current,
        "to_revision": target,
        "images_after": images,
        "dry_run": dry_run,
    }
    if dry_run:
        return {**result, "would": f"replace the pod template with revision {target}'s"}

    body = client.ApiClient().sanitize_for_serialization(template)
    (body.get("metadata") or {}).get("labels", {}).pop("pod-template-hash", None)
    apps_api().patch_namespaced_deployment(
        name=name, namespace=ns, body=[{"op": "replace", "path": "/spec/template", "value": body}]
    )
    return {**result, "status": "rollback started; use wait_for_deployment_ready to verify"}


@mutating_tool
def update_resources(
    namespace: str,
    deployment_name: str,
    container_name: str | None = None,
    cpu_request: str | None = None,
    cpu_limit: str | None = None,
    memory_request: str | None = None,
    memory_limit: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Change CPU/memory requests or limits of one container in a deployment (e.g. raise memory_limit after OOMKilled).
    Values like cpu '250m' and memory '256Mi'; bounded by the server's maximums. Triggers a rolling update.
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    require_remediation(name)
    requests = {k: v for k, v in {
        "cpu": validate_cpu(cpu_request, "cpu_request"),
        "memory": validate_memory(memory_request, "memory_request"),
    }.items() if v}
    limits = {k: v for k, v in {
        "cpu": validate_cpu(cpu_limit, "cpu_limit"),
        "memory": validate_memory(memory_limit, "memory_limit"),
    }.items() if v}
    if not requests and not limits:
        raise ValueError("set at least one of cpu_request, cpu_limit, memory_request, memory_limit")

    dep = _read(ns, name)
    container = _pick_container(dep, container_name)
    spec = next(c for c in dep.spec.template.spec.containers if c.name == container)
    before = {
        "requests": (spec.resources.requests or {}) if spec.resources else {},
        "limits": (spec.resources.limits or {}) if spec.resources else {},
    }
    resources: dict[str, Any] = {}
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits

    result = {"namespace": ns, "deployment_name": name, "container_name": container,
              "before": before, "change": resources, "dry_run": dry_run}
    if dry_run:
        return {**result, "would": "patch container resources and roll out new pods"}

    body = {"spec": {"template": {"spec": {"containers": [{"name": container, "resources": resources}]}}}}
    apps_api().patch_namespaced_deployment(name=name, namespace=ns, body=body)
    return {**result, "status": "rollout started; use wait_for_deployment_ready to verify"}


@mutating_tool
def set_image(
    namespace: str, deployment_name: str, image: str, container_name: str | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Deploy a new image version to a deployment's container (rolling update). Only images from the server's
    allowed registries/repositories, with an explicit tag or digest, are accepted; use list_image_tags to see versions.
    Changes the cluster: ask the user for approval first. Use dry_run=true to preview."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    require_remediation(name)
    new_image = validate_image(image)
    dep = _read(ns, name)
    container = _pick_container(dep, container_name)
    before = next(c.image for c in dep.spec.template.spec.containers if c.name == container)

    result = {"namespace": ns, "deployment_name": name, "container_name": container,
              "image_before": before, "image_after": new_image, "dry_run": dry_run}
    if before == new_image:
        raise ValueError(f"container '{container}' already runs {new_image}")
    if dry_run:
        return {**result, "would": "update the image and roll out new pods"}

    body = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": new_image}]}}}}
    apps_api().patch_namespaced_deployment(name=name, namespace=ns, body=body)
    return {**result, "status": "rollout started; use wait_for_deployment_ready to verify"}


@k8s_tool
async def wait_for_deployment_ready(namespace: str, deployment_name: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """Verify a change: wait until the deployment's rollout is complete and all desired replicas are available,
    or until timeout_seconds (max 90). Returns the final state and any new Warning events."""
    ns = validate_namespace(namespace)
    name = validate_name(deployment_name, "deployment_name")
    timeout = validate_positive_int(timeout_seconds, "timeout_seconds", maximum=MAX_WAIT_SECONDS)

    started = time.monotonic()
    while True:
        dep = await to_thread.run_sync(_read, ns, name)
        state = _rollout(dep)
        elapsed = round(time.monotonic() - started)
        if state["rollout"] in ("complete", "failed") or elapsed >= timeout:
            break
        await asyncio.sleep(3)

    warnings = await to_thread.run_sync(lambda: list_events(ns, None, True, 10))
    return {
        "namespace": ns,
        "deployment_name": name,
        **state,
        "recovered": state["rollout"] == "complete",
        "timed_out": state["rollout"] not in ("complete", "failed"),
        "waited_seconds": elapsed,
        "recent_warnings": warnings,
    }
