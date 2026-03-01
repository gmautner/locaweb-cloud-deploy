# ADR-027: Universal TLS via Let's Encrypt

## Status

Accepted

## Context

Previously (ADR-016), TLS via Let's Encrypt was only enabled when a custom domain was provided. nip.io deployments used plain HTTP. The assumption was that nip.io subdomains could not have valid TLS certificates issued by public CAs.

However, nip.io subdomains are valid public DNS names that resolve correctly to the expected IP addresses. Let's Encrypt HTTP-01 challenges work against them: the challenge server can reach the VM via `<ip>.nip.io`, and Let's Encrypt can verify domain ownership by sending an HTTP request to that hostname. This means there is no technical barrier to enabling TLS for nip.io deployments.

Running without TLS -- even for preview/development environments -- means credentials, API keys, and session tokens transit in cleartext. Enabling TLS universally eliminates this risk with no additional user configuration.

## Decision

Always set `ssl: true` in the Kamal proxy configuration, regardless of whether a custom domain is provided. The proxy host is already a valid, publicly resolvable hostname in both cases (`<domain>` or `<web_ip>.nip.io`), so kamal-proxy can provision a Let's Encrypt certificate for either.

This supersedes the conditional SSL logic from ADR-016: TLS is no longer gated on the `domain` input.

## Consequences

**Positive:**

- All deployments get HTTPS by default, including preview/development environments using nip.io.
- No user action required -- TLS is automatic for both nip.io and custom domain deployments.
- Eliminates cleartext HTTP traffic for credentials and session data in development environments.
- Simplifies the configuration: `ssl` is always `true`, removing a conditional code path.

**Negative:**

- First request after deployment may be slightly slower while kamal-proxy provisions the Let's Encrypt certificate (one-time per deployment).
- Depends on Let's Encrypt availability. If Let's Encrypt is unreachable during certificate provisioning, the initial TLS setup will fail (kamal-proxy retries automatically).
- Depends on nip.io availability for DNS resolution of the hostname used in the certificate.
