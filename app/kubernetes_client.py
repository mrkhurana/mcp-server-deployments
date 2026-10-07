import base64
import tempfile
import threading
from functools import lru_cache

import boto3
from botocore.signers import RequestSigner
from kubernetes import client

from .config import settings

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _session() -> boto3.Session:
    return boto3.Session(region_name=settings.aws_region)


@lru_cache(maxsize=1)
def _cluster() -> tuple[str, str]:
    """Return (endpoint, CA file path), looked up once per process."""
    if not settings.eks_cluster_name:
        raise RuntimeError("EKS_CLUSTER_NAME is required to authenticate to the cluster")

    eks = _session().client("eks", region_name=settings.aws_region)
    cluster = eks.describe_cluster(name=settings.eks_cluster_name)["cluster"]

    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".crt") as cert_file:
        cert_file.write(base64.b64decode(cluster["certificateAuthority"]["data"]))
    return cluster["endpoint"], cert_file.name


def _token() -> str:
    """EKS bearer token: a presigned STS GetCallerIdentity URL, signed locally with the task role."""
    session = _session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials are required to authenticate to EKS")

    sts = session.client("sts", region_name=settings.aws_region)
    signer = RequestSigner(
        sts.meta.service_model.service_id,
        settings.aws_region,
        "sts",
        "v4",
        credentials,
        session._session.get_component("event_emitter"),
    )
    request = {
        "method": "GET",
        "url": f"https://sts.{settings.aws_region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": settings.eks_cluster_name},
        "context": {},
    }
    presigned_url = signer.generate_presigned_url(
        request,
        region_name=settings.aws_region,
        expires_in=60,
        operation_name="",
    )
    return "k8s-aws-v1." + base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip("=")


def get_api_client() -> client.ApiClient:
    with _lock:
        endpoint, ca_file = _cluster()

    config = client.Configuration()
    config.host = endpoint
    config.verify_ssl = True
    config.ssl_ca_cert = ca_file
    config.api_key_prefix["authorization"] = ""
    config.api_key["authorization"] = f"Bearer {_token()}"
    return client.ApiClient(config)


def core_api() -> client.CoreV1Api:
    return client.CoreV1Api(api_client=get_api_client())


def apps_api() -> client.AppsV1Api:
    return client.AppsV1Api(api_client=get_api_client())


def ecr_client():
    return _session().client("ecr", region_name=settings.aws_region)
