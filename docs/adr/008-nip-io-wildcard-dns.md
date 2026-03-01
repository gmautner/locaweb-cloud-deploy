# ADR-008: nip.io for Wildcard DNS

## Status

Accepted

## Context

Kamal 2's `kamal-proxy` reverse proxy routes incoming HTTP requests based on the `Host` header. When a request arrives without a matching host configuration, `kamal-proxy` returns a 404 response. This means accessing a deployed application by raw IP address does not work -- a hostname is required.

Freshly provisioned CloudStack VMs have only IP addresses. There are no DNS records pointing to them. Setting up DNS records requires:

- Access to a DNS provider (Route 53, Cloudflare, etc.).
- API credentials for automated record management.
- Propagation delay before records resolve.

For a self-service deployment system that provisions infrastructure on demand, requiring DNS configuration adds friction and delays.

## Decision

Use the [nip.io](https://nip.io) wildcard DNS service as the default hostname for deployed applications. nip.io resolves any address in the format `<anything>.<ip>.nip.io` to `<ip>`. For example:

- `app.10.20.30.40.nip.io` resolves to `10.20.30.40`

The generated Kamal configuration sets the proxy host to `<ip>.nip.io`, which gives `kamal-proxy` a valid `Host` header to match against.

The workflow also accepts an optional `domain` input for users who want to configure a real domain name.

## Consequences

**Positive:**

- Immediate DNS resolution without any DNS provider configuration or credentials.
- Works out of the box for development, testing, and demo environments.
- No propagation delay -- nip.io responds instantly for any IP address.
- The optional `domain` workflow input provides a path to real domain support without changing the deployment architecture.

**Negative:**

- Depends on the external nip.io service. If nip.io is down, hostname resolution fails and the application becomes inaccessible by name.
- Some corporate firewalls or DNS filters may block nip.io resolution.

**Note:** nip.io subdomains are valid public DNS names that resolve correctly, so Let's Encrypt HTTP-01 challenges work against them. TLS is enabled for nip.io deployments (see ADR-027).
