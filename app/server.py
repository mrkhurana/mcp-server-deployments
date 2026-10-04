import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import settings
from .tools.deployments import describe_deployment, scale_deployment
from .tools.events import get_events
from .tools.logs import get_pod_logs
from .tools.pods import describe_pod, get_pods, restart_pod
from .tools.services import describe_service

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("aiops-mcp")

mcp = FastMCP(
    "aiops-mcp",
    transport_security=TransportSecuritySettings(
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    ),
)

mcp.tool()(get_pods)
mcp.tool()(get_pod_logs)
mcp.tool()(get_events)
mcp.tool()(describe_pod)
mcp.tool()(describe_deployment)
mcp.tool()(describe_service)
mcp.tool()(scale_deployment)
mcp.tool()(restart_pod)

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="AI Ops MCP Server", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "aiops-mcp",
        "allowed_namespaces": settings.allowed_namespaces,
    }


app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
