---
applyTo: '*'
description: 'Secure coding guidance for Python, Flask, SQL, and web templates.'
---

# Secure Coding and OWASP Guidance

## Security Defaults

- Prefer the most restrictive safe default behavior.
- Do not hardcode secrets; read from environment variables.
- Validate and sanitize all untrusted input.

## Python and Flask

- Do not enable Flask debug mode in production.
- Validate route inputs and query parameters.
- Return generic error messages for unexpected failures.

## SQL and Storage

- Use parameterized SQL queries only.
- Never build SQL from string concatenation with user input.
- Validate identifiers and file paths before using them.

## Web and Templates

- Keep template auto-escaping enabled.
- Avoid rendering unsanitized user-provided HTML.
- Add security headers when applicable.

## Dependencies and Operations

- Keep dependencies updated and review vulnerabilities regularly.
- Use HTTPS endpoints for external network requests.
- Document security-impacting behavior changes in `Common/CHANGELOG.md`.
