# MCP Server Deployments

This repository owns the Python MCP application and the ECR image publishing workflow for the AI Ops workshop.

## Components

- Python MCP server using the official MCP SDK
- Kubernetes investigation and remediation toolset
- Container image build and publish pipeline
- Terraform ECR repository definition

## Deployment order

This repository is step 1 of 3:

```bash
# 1. Build and push the MCP server image to ECR (this repository)
./deploy.sh v0.1.0

# 2. Create the AWS infrastructure and the nginx workload
cd ../mcp-server-terraform && ./deploy.sh

# 3. Verify the MCP server and connect Claude Desktop
cd ../mcp-client && ./deploy.sh
```

## Build and publish

```bash
./deploy.sh v0.1.0
```

This creates the ECR repositories with Terraform if they don't exist, then builds a `linux/amd64` image and pushes it with both tags:

```text
<account>.dkr.ecr.<region>.amazonaws.com/mcp-server:<version>
<account>.dkr.ecr.<region>.amazonaws.com/mcp-server:latest
```

It also mirrors the workshop's nginx image into a private `nginx` ECR repository, because the EKS subnets have no internet route. By default only `1.27-alpine`, the deployed version, is mirrored. To give `set_image` another real version to switch to, mirror more tags, for example `NGINX_TAGS="1.27-alpine 1.26-alpine" ./deploy.sh v0.2.0`. Tags already in ECR are skipped.

`mcp-server-terraform/terraform.tfvars` points ECS at `:latest`. ECS doesn't pick up a new push to the same tag by itself, so after pushing, force a new deployment of the ECS service or switch the tfvars to the version tag.

Requires `terraform`, `aws`, and `docker` with `buildx`.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | ALB and container health check, returns `{"status":"ok",...}` |
| `/mcp` | MCP Streamable HTTP endpoint used by Claude Desktop. Requires `Authorization: Bearer <token>` when `MCP_AUTH_TOKEN` is set. |

The server listens on `0.0.0.0:8080`.

## Tools

Investigation tools are annotated read-only. Tools that change the cluster are annotated destructive, so Claude Desktop asks for approval before each call.

| Stage | Tool | What it does |
|---|---|---|
| Detect | `get_namespace_health` | One call: deployments not fully available, unready pods with reasons, services without endpoints, recent warnings |
| Investigate | `list_deployments`, `list_services`, `get_pods` | Inventory with replica counts, endpoints, pod states |
| Investigate | `describe_pod`, `describe_deployment`, `describe_service` | Conditions, probes, resources, owner, the pod's own events. Env var values are never returned |
| Investigate | `get_pod_logs` | Last N lines (default 100, max 500). `previous=true` reads the crashed container |
| Investigate | `get_events` | Newest first, filter by object or `warnings_only` |
| Investigate | `get_rollout_status` | Rollout state plus revision history (ReplicaSets and images) |
| Investigate | `check_app_health` | HTTP GET to a service through the API server proxy |
| Investigate | `list_image_tags` | Recent tags in an allowed ECR repository |
| Remediate | `scale_deployment` | Set replicas within bounds (default 1 to 5) |
| Remediate | `restart_pod` | Delete one controller-owned pod so it is recreated |
| Remediate | `rollout_restart_deployment` | Gradual restart of all pods |
| Remediate | `rollback_deployment` | Back to a previous revision |
| Remediate | `update_resources` | Change CPU and memory requests or limits, within bounds |
| Operate | `set_image` | Deploy a new tag, only from the allowed ECR repositories, never `:latest` |
| Verify | `wait_for_deployment_ready` | Wait up to 90s for the rollout to complete, then report |

Every state-changing tool:
- checks the namespace, the resource names (DNS-1123), `MCP_ENABLE_REMEDIATION` and `MCP_ALLOWED_DEPLOYMENTS`
- supports `dry_run=true` to preview the change
- writes one JSON audit line (tool, arguments, outcome) to the container log, which lands in CloudWatch

The server doesn't expose kubectl, shell or exec, arbitrary deletes, secrets, or manifest apply.

The server also offers an MCP prompt, `investigate_workload`, which walks Claude through detect, investigate, explain, propose (with dry run), wait for approval, remediate and verify. Pick it from the **+** menu in Claude Desktop.

## Configuration

Set as environment variables on the ECS task (`mcp-server-terraform/modules/ecs`).

| Variable | Default | Purpose |
|---|---|---|
| `EKS_CLUSTER_NAME` | none (required) | Cluster to authenticate to |
| `AWS_REGION` | `us-east-1` | Region of the cluster and ECR |
| `ALLOWED_NAMESPACES` | `application` | Comma-separated namespace allowlist (`K8S_NAMESPACE_ALLOWLIST` also accepted) |
| `MCP_AUTH_TOKEN` | none | Bearer token required on `/mcp`. Injected from SSM Parameter Store by ECS. Unset disables auth (local development only) |
| `MCP_ALLOWED_HOSTS` | none | Extra `host:port` values accepted in the Host header (the ALB DNS name). `localhost` is always allowed |
| `MCP_ENABLE_REMEDIATION` | `true` | `false` makes every state-changing tool refuse |
| `MCP_ALLOWED_DEPLOYMENTS` | none (all) | Comma-separated deployments that may be changed |
| `MCP_MIN_REPLICAS` / `MCP_MAX_REPLICAS` | `1` / `5` | Bounds for `scale_deployment` |
| `MCP_MAX_CPU_MILLICORES` / `MCP_MAX_MEMORY_MIB` | `1000` / `1024` | Upper bounds for `update_resources` |
| `MCP_ALLOWED_IMAGE_PREFIXES` | none (disabled) | Comma-separated image repositories `set_image` may deploy, e.g. `<account>.dkr.ecr.<region>.amazonaws.com/nginx` |
| `MCP_ALLOWED_ECR_REPOSITORIES` | none (disabled) | ECR repository names `list_image_tags` may read |
| `PORT` / `MCP_PORT` | `8080` | Listen port |
| `LOG_LEVEL` | `INFO` | Python log level |

## Authentication to EKS

There is no kubeconfig and no stored Kubernetes token. The server:

1. Calls `eks:DescribeCluster` once with the ECS task role to get the endpoint and CA.
2. On each tool call, builds an EKS bearer token from a presigned STS `GetCallerIdentity` URL (the same mechanism `aws eks get-token` uses), signed locally.
3. Connects with the official Kubernetes Python client.

What the task role can do inside the cluster is decided by its EKS Access Entry and the Kubernetes RBAC in `mcp-server-terraform`.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
EKS_CLUSTER_NAME=<cluster> AWS_REGION=us-east-1 python -m app.server
curl http://localhost:8080/health
```

Without `MCP_AUTH_TOKEN` the server logs a warning and accepts unauthenticated requests. Set it to test auth locally.

Kubernetes calls use your local AWS credentials, so that principal needs its own EKS access entry.
