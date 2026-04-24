# Documentation Index

This directory holds project-level documentation that does not belong in the user
quick start or directly beside application code.

## Current docs

- [README.md](../README.md) in the repository root is the end-user overview and deployment guide.
- [CONTRIBUTING.md](../CONTRIBUTING.md) in the repository root is the developer setup, workflow, and
  quality-gate guide.
- [repo-map.md](../repo-map.md) is the living high-level map of code ownership, runtime paths, and
  operational files.
- `docs/` is reserved for remaining cross-cutting project notes.

## Component docs live with code

Detailed component documentation is kept next to the code it describes so it changes
with that code and is easier to find during implementation. Look for README files in
areas such as:

- [app/siftarr/](../app/siftarr/README.md) for application package boundaries and runtime flow.
- [app/siftarr/routers/](../app/siftarr/routers/README.md) for HTTP/UI/API route responsibilities.
- [app/siftarr/services/](../app/siftarr/services/README.md) for business logic and integration boundaries.
- [app/siftarr/models/](../app/siftarr/models/README.md) for ORM model ownership.
- [tests/](../tests/README.md) for test layout, fixtures, async conventions, and targeted commands.
- [docker/](../docker/README.md) for image, Compose, volume, environment, and helper-script workflow.

When adding new detailed docs, prefer the closest relevant code directory. Use this
directory for broad project documents that span multiple components.
