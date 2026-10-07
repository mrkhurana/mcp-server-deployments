from typing import Any

from ..config import validate_namespace
from ..kubernetes_client import apps_api, core_api
from .common import container_state, k8s_tool
from .deployments import _rollout
from .events import list_events


@k8s_tool
def get_namespace_health(namespace: str) -> dict[str, Any]:
    """One-call health check of a namespace: deployments whose rollout isn't complete or lacks available replicas,
    pods that aren't Ready (with waiting/terminated reasons and restarts), services with no ready endpoints,
    and the latest Warning events. Use it first to detect whether anything is unhealthy."""
    ns = validate_namespace(namespace)
    core, apps = core_api(), apps_api()

    deployments = []
    for dep in apps.list_namespaced_deployment(ns).items:
        state = _rollout(dep)
        if state["rollout"] != "complete" or state["available"] < state["desired"] or state["desired"] == 0:
            deployments.append({"deployment_name": dep.metadata.name, **state})

    pods = []
    for pod in core.list_namespaced_pod(ns).items:
        statuses = pod.status.container_statuses or []
        if pod.status.phase == "Succeeded":
            continue
        if pod.status.phase != "Running" or not statuses or not all(cs.ready for cs in statuses):
            pods.append({
                "pod_name": pod.metadata.name,
                "phase": pod.status.phase,
                "reason": pod.status.reason,
                "containers": [
                    {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count, **container_state(cs.state)}
                    for cs in statuses
                ],
                "conditions": [
                    {"type": c.type, "reason": c.reason, "message": c.message}
                    for c in (pod.status.conditions or []) if c.status != "True"
                ],
            })

    services = []
    endpoints = {e.metadata.name: e for e in core.list_namespaced_endpoints(ns).items}
    for svc in core.list_namespaced_service(ns).items:
        if not svc.spec.selector:
            continue
        ep = endpoints.get(svc.metadata.name)
        ready = sum(len(s.addresses or []) for s in (ep.subsets or [])) if ep else 0
        if ready == 0:
            services.append({"service_name": svc.metadata.name, "ready_endpoints": 0, "selector": svc.spec.selector})

    warnings = list_events(ns, None, True, 15)
    healthy = not (deployments or pods or services)
    return {
        "namespace": ns,
        "healthy": healthy,
        "summary": "no problems found" if healthy else
        f"{len(deployments)} deployment(s), {len(pods)} pod(s), {len(services)} service(s) need attention",
        "unhealthy_deployments": deployments,
        "unready_pods": pods,
        "services_without_endpoints": services,
        "recent_warnings": warnings,
    }
