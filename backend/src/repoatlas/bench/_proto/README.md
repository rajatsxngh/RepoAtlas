# SCIP bindings

`scip.proto` is pinned to [sourcegraph/scip](https://github.com/sourcegraph/scip) **v0.10.0**
(commit `1c2b6db7e560d5233c944f36e4ac1377cc6963fc`), unchanged.
`scip_pb2.py` and `scip_pb2.pyi` are generated from it and committed.

Generated with `grpcio-tools` 1.85.1 (protoc gencode 7.35.1; the runtime is the `protobuf`
package in `backend/uv.lock`). To regenerate, from the repository root:

```bash
scripts/gen_scip_bindings.sh
```

The indexers the benchmark uses (scip-python 0.6.8, scip-typescript 0.6.2) write older SCIP
versions of the same schema; protobuf fields are only ever added, so this proto reads them.
`tests/unit/bench/test_scip.py` checks the proto's sha256 and that the module was generated from it.
