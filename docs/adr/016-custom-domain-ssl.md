# ADR-016: Custom Domain Support with Let's Encrypt SSL

## Status

Superseded by [ADR-027](027-universal-tls.md)

## Context

The deployment platform previously used nip.io wildcard DNS exclusively for all deployments. The `domain` workflow input existed but was reserved for future use. Users who wanted to serve their application under a real domain name with TLS had no supported path.

Additionally, `forward_headers` was set to `true` in the kamal-proxy configuration. Since VMs in this platform receive traffic directly from the internet via static NAT (1:1) with no upstream load balancers or CDN, forwarding headers is incorrect -- there are no upstream proxy headers to forward.

## Decision

1. **Custom domain support**: When the `domain` workflow input is set, the Kamal proxy configuration uses the domain value as `proxy.host` and enables `ssl: true`. kamal-proxy then automatically provisions a Let's Encrypt certificate for the domain. When `domain` is empty (default), the existing nip.io behavior is preserved.

2. **forward_headers always false**: Set `forward_headers: false` unconditionally, since the web VM sits directly on the internet behind static NAT with no upstream proxies.

## Consequences

**Positive:**

- Users can deploy applications under their own domain with automatic TLS.
- Let's Encrypt certificates are provisioned and renewed automatically by kamal-proxy.
- The nip.io fallback remains for quick deployments without DNS configuration.
- Correct `forward_headers` setting prevents potential header spoofing.

**Negative:**

- The user must configure a DNS A record pointing the domain to the web VM's public IP before deployment (or immediately after, before the first HTTPS request). Let's Encrypt HTTP-01 challenge requires the domain to resolve to the server.
- Only single-server deployments are supported for automatic SSL (a kamal-proxy constraint).
