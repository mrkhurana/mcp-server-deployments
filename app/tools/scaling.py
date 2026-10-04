from typing import Any

from .deployments import scale_deployment
from .pods import restart_pod

__all__ = ["scale_deployment", "restart_pod"]
