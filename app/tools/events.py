from typing import Any

from kubernetes import client

from ..config import validate_namespace
from ..kubernetes_client import get_api_client


def get_events(namespace: str) -> dict[str, Any]:
    ns = validate_namespace(namespace)
    api = client.CoreV1Api(api_client=get_api_client())
    events = api.list_namespaced_event(namespace=ns)

    items = []
    for event in events.items:
        items.append(
            {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "involved_object": {
                    "kind": event.involved_object.kind,
                    "name": event.involved_object.name,
                    "namespace": event.involved_object.namespace,
                },
                "namespace": event.metadata.namespace,
                "timestamp": event.event_time or event.last_timestamp or event.metadata.creation_timestamp,
            }
        )

    return {
        "namespace": ns,
        "count": len(items),
        "events": items,
    }
