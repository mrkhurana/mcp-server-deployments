import base64
import tempfile

import boto3
from botocore.signers import RequestSigner
from kubernetes import client

from .config import settings


def get_api_client() -> client.ApiClient:
    if not settings.eks_cluster_name:
        raise RuntimeError("EKS_CLUSTER_NAME is required to authenticate to the cluster")

    session = boto3.Session(region_name=settings.aws_region)
    eks = session.client("eks", region_name=settings.aws_region)
    cluster = eks.describe_cluster(name=settings.eks_cluster_name)["cluster"]

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
    token = "k8s-aws-v1." + base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip("=")

    ca_data = cluster["certificateAuthority"]["data"]
    endpoint = cluster["endpoint"]

    config = client.Configuration()
    config.host = endpoint
    config.verify_ssl = True
    config.api_key_prefix["authorization"] = ""
    config.api_key["authorization"] = f"Bearer {token}"

    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as cert_file:
        cert_file.write(base64.b64decode(ca_data))
        config.ssl_ca_cert = cert_file.name

    return client.ApiClient(config)
