#!/usr/bin/env bash
# Regenerate the SCIP Python bindings from the pinned proto (see backend/src/repoatlas/bench/_proto/README.md).
set -euo pipefail
cd "$(dirname "$0")/../backend/src/repoatlas/bench/_proto"
uvx --from grpcio-tools==1.85.1 python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. scip.proto
echo "Regenerated scip_pb2.py and scip_pb2.pyi"
