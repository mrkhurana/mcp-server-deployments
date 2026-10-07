import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .auth import BearerTokenMiddleware
from .config import settings
from .tools.deployments import (
    describe_deployment,
    get_rollout_status,
    list_deployments,
    rollback_deployment,
    rollout_restart_deployment,
    scale_deployment,
    set_image,
    update_resources,
    wait_for_deployment_ready,
)
from .tools.events import get_events
from .tools.health import get_namespace_health
from .tools.images import list_image_tags
from .tools.logs import get_pod_logs
from .tools.pods import describe_pod, get_pods, restart_pod
from .tools.services import check_app_health, describe_service, list_services

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("aiops-mcp")

mcp = FastMCP(
    "aiops-mcp",
    transport_security=TransportSecuritySettings(
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CHANGES_CLUSTER = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

# Detect and investigate
for tool in (
    get_namespace_health,
    list_deployments,
    list_services,
    get_pods,
    describe_pod,
    get_pod_logs,
    get_events,
    describe_deployment,
    get_rollout_status,
    describe_service,
    check_app_health,
    list_image_tags,
    wait_for_deployment_ready,
):
    mcp.tool(annotations=READ_ONLY)(tool)

# Remediate and operate: every one validates namespace, name and policy, supports dry_run, and is audit-logged
for tool in (
    scale_deployment,
    restart_pod,
    rollout_restart_deployment,
    rollback_deployment,
    update_resources,
    set_image,
):
    mcp.tool(annotations=CHANGES_CLUSTER)(tool)


@mcp.prompt()
def investigate_workload(workload: str = "nginx", namespace: str = "application") -> str:
    """Investigate an unhealthy workload end to end: detect, investigate, explain, ask, remediate, verify."""
    return f"""Investigate the health of the "{workload}" workload in the "{namespace}" Kubernetes namespace.

Work through these stages and label each one in your answer:
1. Detect: call get_namespace_health and say whether anything is unhealthy.
2. Investigate: gather evidence with the read-only tools (pods, events, logs, deployment, rollout history, service).
   Base every conclusion on what the tools return; don't assume a cause.
3. Explain: state the root cause and quote the evidence that supports it.
4. Propose: recommend one remediation using the available tools, show it with dry_run=true,
   and ask me to approve before making any change. If no available tool can fix it, say so and stop.
5. Remediate: only after I approve, apply the change.
6. Verify: call wait_for_deployment_ready and check_app_health, then confirm the workload has recovered."""


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.auth_token:
        logger.warning("MCP_AUTH_TOKEN is not set: the /mcp endpoint accepts unauthenticated requests")
    if not settings.enable_remediation:
        logger.info("remediation is disabled (MCP_ENABLE_REMEDIATION=false): state-changing tools will refuse")
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AI Ops MCP Server", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "aiops-mcp",
        "allowed_namespaces": settings.allowed_namespaces,
        "remediation_enabled": settings.enable_remediation,
        "auth_required": bool(settings.auth_token),
    }


app.mount("/", mcp.streamable_http_app())

if settings.auth_token:
    app.add_middleware(BearerTokenMiddleware, token=settings.auth_token)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
