# ADR 0001: One repository for all parts (monorepo)

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

AquaPulse has seven parts: contracts, simulator, backend, web app, Android app, firmware and infrastructure. They must always agree on the same MQTT contract and API.

## Decision

Keep everything in one GitHub repository, with one folder per part.

## Consequences

- ✅ One pull request can change the contract, simulator and backend together, and CI tests them together.
- ✅ One place for recruiters to see the whole system.
- ➖ CI must only run the jobs for folders that changed (path filters), to stay fast. Added as parts appear.
