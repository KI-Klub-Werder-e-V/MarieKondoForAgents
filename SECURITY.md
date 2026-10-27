# Security policy

The KonMari-Kit handles permissions and personal data, so security reports get priority.

**Do not open a public issue for a vulnerability.** Use
[private vulnerability reporting](https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents/security/advisories/new)
or email admin@ki-klub-werder.de. We acknowledge reports within 7 days. The project is run by
volunteers, so we cannot promise a fixed remediation time, but we will keep you updated.

Of particular interest:

- an agent seeing data through the MCP server that its policy or the source permissions deny
- gaps in PII masking
- audit-trail tampering that `konmari audit verify` does not detect
- secrets (passwords, tokens) ending up in the catalog or in logs

Only the latest `main` is supported.
