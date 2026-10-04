# MCP Server Deployments

This repository owns the Python MCP application and the ECR image publishing workflow for the AI Ops workshop.

## Components

- Python MCP server using the official MCP SDK
- Kubernetes investigation and remediation toolset
- Container image build and publish pipeline
- Terraform ECR repository definition

## Build and publish

```bash
./deploy.sh v0.1.0
```

The image is published as:

```text
<account>.dkr.ecr.<region>.amazonaws.com/mcp-server:<version>
```
