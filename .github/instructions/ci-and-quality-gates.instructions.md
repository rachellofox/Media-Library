---
applyTo: '.github/workflows/**/*,**/*.py,requirements.txt,README.md'
description: 'CI and quality gate expectations for Python Flask repositories'
---

# CI and Quality Gate Expectations

## Minimum CI Stages

- Install dependencies.
- Run lint checks.
- Run tests configured for the project.
- Validate app startup where practical.

## Build Verification

- Fail on Python syntax errors.
- Fail on linting errors for changed modules.
- Ensure dependency install succeeds from `requirements.txt`.

## Pull Request Expectations

- Behavior changes should include tests where practical.
- Workflow or setup changes should include docs updates.
- CI must pass before merge.

## Release Baseline

- Keep release notes and changelog aligned.
- Record shipped behavior in `Common/CHANGELOG.md`.
