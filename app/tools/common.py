import functools
import inspect
import json
import logging
from datetime import datetime
from typing import Any, Callable

from kubernetes.client.exceptions import ApiException
from mcp.server.fastmcp.exceptions import ToolError

audit_logger = logging.getLogger("aiops-mcp.audit")


def _api_error_message(exc: ApiException) -> str:
    reason = ""
    try:
        reason = json.loads(exc.body or "{}").get("message", "")
    except (TypeError, ValueError):
        reason = exc.body or ""
    if exc.status == 403:
        return f"Kubernetes RBAC denied this request: {reason}"
    if exc.status == 404:
        return f"Not found: {reason}"
    if exc.status == 409:
        return f"Conflict, the object changed meanwhile; retry: {reason}"
    if exc.status == 422:
        return f"Kubernetes rejected the change as invalid: {reason}"
    return f"Kubernetes API error {exc.status}: {reason or exc.reason}"


def k8s_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turn validation and Kubernetes API errors into readable tool errors."""

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except ApiException as exc:
                raise ToolError(_api_error_message(exc)) from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ApiException as exc:
            raise ToolError(_api_error_message(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def mutating_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """k8s_tool plus one JSON audit line per call (lands in CloudWatch via stdout)."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        record: dict[str, Any] = {"audit": True, "tool": func.__name__, "args": kwargs}
        try:
            result = k8s_tool(func)(*args, **kwargs)
            record["outcome"] = "dry_run" if kwargs.get("dry_run") else "applied"
            record["result"] = result
            return result
        except ToolError as exc:
            record["outcome"] = "rejected"
            record["error"] = str(exc)
            raise
        finally:
            audit_logger.info(json.dumps(record, default=str))

    return wrapper


def ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def container_state(state: Any) -> dict[str, Any]:
    """Flatten V1ContainerState into {'state': 'waiting', 'reason': ..., ...}."""
    if state is None:
        return {}
    for name in ("waiting", "running", "terminated"):
        detail = getattr(state, name, None)
        if detail is not None:
            data = {k: v for k, v in detail.to_dict().items() if v is not None}
            return {"state": name, **{k: ts(v) if isinstance(v, datetime) else v for k, v in data.items()}}
    return {}
