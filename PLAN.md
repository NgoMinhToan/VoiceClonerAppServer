# Plan: VoiceClonerAppServer

Open work only. Git history and `CONTEXT.md` record what is already implemented.

## Current Focus

- Preserve gateway contracts used by the Android client and web studio.
- Keep worker health, capability routing, cache, and quota behavior stable.

## Backlog (candidates, not committed)

- **Unified error-code table** — centralize user-readable messages and machine codes so clients no longer map raw provider errors.
- **Abuse protection** — add request-size, burst, and clone-upload throttling beyond the per-user sliding window.
- **Branding and public metadata** — expose consistent product naming through system/status responses.
- **Dictionary and verse tags** — define a worker capability contract for supported `[verse]` tags and text-replacement rules, then surface them to clients.
- **Observability** — structured request IDs, worker latency histograms, and cache-hit SLO reporting.

## Verification Checklist

```bash
pytest tests/ -v
```

Any change to routing, cache, or quota logic must keep the singleflight, unbilled-claim, and health-routing suites green.
