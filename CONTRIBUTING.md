# Contributing to AquaPulse

Thanks for your interest! This project follows its [SRS](docs/SRS.md): every change should trace back to a requirement ID (FR-xx or NFR-xx).

## Workflow

1. Open an issue (or pick one) and mention the requirement IDs.
2. Create a branch: `git switch -c feat/fr-24-valve-lifecycle`.
3. Make your change with tests. Run `make lint` (and `make test` once it exists).
4. Commit using [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(api): valve command lifecycle (FR-24)`.
5. Open a pull request and fill in the template.

## Rules

- Never commit secrets. Use `.env` (git-ignored); document new settings in `.env.example`.
- Money is always `Decimal`, never `float`.
- Anything shown in the README or demo must really work.
- Be kind: see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
