# V2 Triton Identity Model Artifacts

This directory is the Triton model repository for Airco Secure V2 identity models.

It is intentionally split into:

- committed runtime config
- uncommitted/generated runtime artifacts
- an explicit catalog of supported models

## Source of truth

- catalog: `catalog.json`
- triton runtime configs: `<model>/config.pbtxt`
- generated runtime artifact: `<model>/1/model.plan`

## Triton-served models

1. `yolo26`
2. `phone_detection`
3. `yolo26-pose`
4. `scrfd`
5. `arcface`
6. `osnet`

The detector runtime artifacts are intentionally kept in the Triton repository so they can be loaded explicitly in GPU mode.

## Artifact contract

For each Triton-served model:

1. canonical source asset must be defined in `catalog.json`
2. Triton runtime name must match the `config.pbtxt` directory
3. the generated TensorRT artifact must land at:
   - `v2/services/triton/models/<model>/1/model.plan`

The `.gitkeep` files preserve the repository layout, but they are not valid inference artifacts.

## Dev vs production expectations

- local API/frontend work should not depend on these files existing
- GPU integration mode does depend on them
- production must only use reproducible exported artifacts, not ad hoc `.pt` files mounted directly into runtime services

## Current status

Runtime configs exist for the Triton-served detector and identity models.

Until the listed `1/model.plan` artifacts exist, Triton can start as a process but cannot serve real inference for that model.
