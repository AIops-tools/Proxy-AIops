# Agent guardrails — running proxy-aiops with a smaller / local model

If you drive these tools with a local model (Llama, Qwen, Mistral … via Goose,
Ollama, LM Studio, or any OpenAI-compatible runtime), you will get noticeably
better results with a short system prompt. This page gives you one, and — more
importantly — tells you which guardrails you **no longer need to write**, because
the tool now enforces them itself.

The distinction matters. A guardrail in a prompt is a request. A guardrail in the
harness is a guarantee. Anything below that we could move into the harness, we did.

## What the tool now enforces — do not waste prompt budget on these

| You might be tempted to prompt | Why you don't need to |
|---|---|
| "Work read-only, never change the proxy config" | Set `PROXY_READ_ONLY=1`. The six write tools (`set_config_value`, `delete_config_path`, `load_config`, `set_server_state`, `set_server_weight`, `undo_apply`) are then **not registered at all** — they never appear in the tool list, so the model cannot call one even if it tries. The `@governed_tool` harness independently refuses writes, so the CLI is covered too. |
| "Don't invent a value when a field is missing" | Traefik, Caddy and HAProxy express the same concepts differently, so a field one platform has and another does not comes back as `null`, never as `""`. A Caddy route's `raw` rule string is `null` — Caddy matches on a match list and has no such string — rather than a misleading empty rule. |
| "Tell me if the output was cut off" | `search_config`, `traffic_stats` and `error_counters` return `{"matches"/"services": [...], "returned": N, "limit": L, "truncated": true/false}` — one convention across the repo. Truncation is measured (the config walk deliberately overshoots by one) and not guessed from the count reaching the cap. |
| "Preserve the ordering / tell me what's most urgent" | `backend_health_rca`, `error_rate_rca`, `route_conflict_analysis` and `cert_expiry_sweep` rank findings worst-first with the measured number attached. Priority is in the payload, not implied by list position. |
| "Confirm before anything destructive" | `delete_config_path` and `load_config` require a `--dry-run`-able preview plus double confirmation at the CLI, and a named approver (`PROXY_AUDIT_APPROVED_BY`) for high-risk tiers. Config writes capture the prior value so the undo token can restore it. |
| "Log what you did" | Every governed call is audited to `~/.proxy-aiops/audit.db` regardless of what the model says it did. |

## What still needs a prompt

These are model-behaviour problems the harness cannot fix from the outside.
Copy this into your agent's system prompt:

```text
You operate a Traefik, Caddy or HAProxy reverse proxy through the proxy-aiops
MCP tools.

TOOL USE
- Before answering any question about the current proxy, you MUST call a tool.
  Never answer from memory or assumption.
- Actually invoke the tool. Do not describe the call you would make, and do not
  emit an example JSON response in place of calling it.
- If a tool call fails, report the real error verbatim. Never fill the gap with
  a plausible-sounding answer.

READING RESULTS
- Read the whole result before concluding. If a result contains a "truncated"
  field that is true, say so and narrow the query instead of treating the
  partial result as complete.
- A null field means this platform does not express that concept, or did not
  report it. Report it as "not available" — never infer it.
- An "unsupported" field is a capability statement about the platform, not an
  error and not a finding. Say the platform does not expose it; do not report
  it as a problem with the proxy.
- Report values exactly as returned. Traffic counters are cumulative since the
  proxy started — compare rates, never quote a raw total as "requests today".

SCOPE
- Separate observation from interpretation. State what the tools returned, then
  any interpretation, clearly marked as such.
- Do not claim a backend is down unless a health/upstream result says so. A
  route existing does not mean it resolves.
- Do not confuse a route with a service, a service with an upstream server, or
  an entrypoint with a route. One route names one service; one service has many
  upstream servers.
- The three platforms differ. The target's platform is in every result — do not
  suggest a Traefik router rule on Caddy, or a Caddy config path on HAProxy.
- On Traefik, /api/rawdata is the merged read-only view. Config changes belong
  to the provider (the Docker labels, the file provider), not to this tool —
  do not offer to edit what the tool cannot write.
```

## Recommended setup for a local model

```bash
# Read-only until you trust the setup — this is enforced, not advisory.
export PROXY_READ_ONLY=1
proxy-aiops doctor
```

Then, when you are ready to allow writes, unset it and set an approver so the
high-risk tier has an accountable name on it:

```bash
unset PROXY_READ_ONLY
export PROXY_AUDIT_APPROVED_BY="your.name@example.com"
export PROXY_AUDIT_RATIONALE="draining web-02 for maintenance"
```

Read-only is a reasonable default at the edge: a proxy is the one component
where a bad config change takes down everything behind it at once, and
`load_config` replaces the whole tree.

## If your model still struggles

Some behaviours are model-capacity limits rather than prompt problems:

- **Multi-tool workflows time out or drift.** Prefer the RCA tools —
  `backend_health_rca` and `error_rate_rca` do the correlation inside one call,
  so the model does not have to chain `list_services`, `list_upstreams` and
  `error_counters` and keep service names straight.
- **The model ignores later tool results in a long context.** The config
  snapshot is the big payload here. Prefer `search_config` with a narrow query
  over pulling the whole tree and asking the model to find things in it.
- **The model describes calls instead of making them.** This is usually a
  runtime/tool-calling-format mismatch, not a prompt problem — check that your
  client advertises the tools in the format your model was trained on.

Feedback on running this with a specific local model is genuinely useful —
open an issue at
[github.com/AIops-tools/Proxy-AIops](https://github.com/AIops-tools/Proxy-AIops/issues)
with the model, runtime, and what went wrong.
