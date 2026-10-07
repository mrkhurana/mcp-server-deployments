from typing import Any

from ..config import validate_name, validate_namespace, validate_positive_int
from ..kubernetes_client import core_api
from .common import k8s_tool, ts

MAX_EVENTS = 100


def list_events(namespace: str, involved_object_name: str | None = None,
                warnings_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    selectors = []
    if involved_object_name:
        selectors.append(f"involvedObject.name={involved_object_name}")
    if warnings_only:
        selectors.append("type=Warning")
    events = core_api().list_namespaced_event(namespace=namespace, field_selector=",".join(selectors) or None)

    def when(event):
        return event.last_timestamp or event.event_time or event.metadata.creation_timestamp

    return [
        {
            "type": e.type,
            "reason": e.reason,
            "message": e.message,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "count": e.count,
            "last_seen": ts(when(e)),
        }
        for e in sorted(events.items, key=when, reverse=True)[:limit]
    ]


@k8s_tool
def get_events(
    namespace: str,
    involved_object_name: str | None = None,
    warnings_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Kubernetes events in a namespace, newest first: scheduling failures, image pull errors,
    probe failures, OOM kills, scaling. Filter to one object (pod, deployment, replicaset)
    with involved_object_name, or to problems only with warnings_only=true."""
    ns = validate_namespace(namespace)
    name = validate_name(involved_object_name, "involved_object_name") if involved_object_name else None
    count = validate_positive_int(limit, "limit", maximum=MAX_EVENTS)
    items = list_events(ns, name, warnings_only, count)
    return {"namespace": ns, "count": len(items), "events": items}
