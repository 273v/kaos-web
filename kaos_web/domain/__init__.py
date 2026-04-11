"""Domain intelligence: DNS, WHOIS, TLS, HTTP probing, and service detection.

Provides low-level network intelligence tools for domain profiling.
All operations are read-only — no modification, no exploitation.

Modules:

- ``models`` — Pydantic models shared across all domain tools.
- ``tcp`` — TCP port probing via ``asyncio.open_connection()``.
- ``tls`` — TLS certificate inspection via stdlib ``ssl``.
- ``http`` — HTTP header analysis and server fingerprinting.
- ``dns`` — DNS record queries and enumeration (requires ``dnspython``).
- ``whois`` — WHOIS client with built-in parsing (stdlib only).
- ``security`` — Mail authentication analysis (SPF/DKIM/DMARC).
- ``profile`` — Composite domain profiling combining all of the above.
"""
