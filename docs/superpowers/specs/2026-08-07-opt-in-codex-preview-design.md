# Opt-in Functional Codex Preview

**Status:** Approved in conversation on 2026-08-07

## Context

Cook Mantra can start the official local `codex app-server`, use its Codex-managed sign-in, inspect its model catalog, and construct structured text or vision turns. The current adapter nevertheless marks every Codex model unavailable. The installed app-server does not expose one typed setting that disables every built-in file, shell, web, MCP, and user-input tool before a turn begins. Detecting a tool event after it starts is not the same as preventing it.

The user wants a functional Codex-only setup on a trusted personal machine and accepts an explicit preview boundary. Cook Mantra must preserve the safe default for every clone that does not make that choice.

## Goals

- Add a visible, disabled-by-default Codex provider opt-in.
- Make Codex text and vision roles ready when the opt-in is enabled, the exact model exists, and discovery proves the required input modality.
- Update the adapter to the installed app-server protocol without weakening validation of responses, IDs, terminal states, or structured output.
- Keep the child in an API-owned temporary workspace with the narrowest documented filesystem and network settings.
- Reject and terminate any observed shell, file, web, MCP, collaboration, user-input, reasoning, reroute, or other unapproved activity.
- Keep Codex image generation unavailable.
- Explain that prompts, accepted photos, and responses may pass through OpenAI using the Codex-managed connection.

## Non-goals

- Generic ChatGPT proxying or direct ChatGPT credential handling.
- Codex image generation.
- A browser switch for provider configuration.
- Silent fallback to Ollama, OpenRouter, or another model.
- Production or multi-user support for the opt-in mode.
- A claim that post-event rejection provides the same guarantee as disabling a tool before execution.

## Approaches considered

### 1. Provider-level YAML opt-in — selected

Add one strict field to the Codex provider definition:

```yaml
providers:
  codex:
    command: [codex, app-server]
    allow_unverified_tool_boundary: true
```

This choice sits next to the command it affects, survives service restarts, appears in configuration review, and supports separate configuration files. The field defaults to `false` and is rejected for Ollama and OpenRouter.

### 2. Process environment flag

An environment variable would keep the risk choice outside YAML, but it would split one provider's behavior across two configuration systems and make forks harder to audit. This approach is rejected.

### 3. Remove the readiness gate globally

This approach would make Codex work without extra configuration, but it would silently accept the unverified tool boundary for every user. This approach is rejected.

## Configuration contract

`ProviderConfiguration` gains `allow_unverified_tool_boundary: bool = false`. Validation enforces these rules:

- only a Codex provider may set the field to `true`;
- Ollama and OpenRouter reject `true` rather than ignore it;
- a Codex provider still accepts only `command: [codex, app-server]`;
- secrets remain forbidden in YAML; and
- configuration remains process-lifetime, so changing the opt-in requires an API restart.

The tracked default remains Ollama with no opt-in. The redacted example shows the Codex field as `false` and explains how a trusted local user may change it. A Codex-only local configuration may declare only Codex when every role selects Codex and the image role is disabled.

## Discovery and readiness

The provider composition passes the opt-in value into `CodexAppServerClient`.

Discovery remains read-only: initialize the child, inspect account status, page the visible model catalog, and read provider capabilities. It creates no thread or turn.

The adapter must match the installed app-server schema. It may accept only documented, explicitly allowlisted non-content notifications during initialization and discovery. It must reject server requests, malformed frames, mismatched response IDs, unknown response shapes, model reroutes, and tool or content events outside an active invocation.

For each exact configured model:

- no catalog match, duplicate matches, missing modality, or failed authentication remains unavailable;
- opt-in `false` returns the current fail-closed capability result;
- opt-in `true` exposes `text` plus structured output when the catalog advertises text input;
- opt-in `true` exposes vision when the catalog advertises image input; and
- image-output capability is never added for Codex.

Required text and vision roles become ready only when their exact model advertises their full capability contract. An enabled Codex image role remains `capability_missing`; a disabled image role remains disabled.

## Invocation boundary

Each accepted request uses one temporary thread and one turn. Cook Mantra deletes the thread after success, failure, or cancellation. Cook Mantra continues to own child startup, serialization, deadlines, cleanup, and shutdown.

The adapter uses fields supported by the installed schema and applies these controls where the protocol allows them:

- API-owned empty working directory;
- read-only sandbox with restricted readable roots limited to the owned working directory and the single-use media directory, plus documented platform defaults required by the sandbox;
- disabled network access when supported for the selected sandbox policy;
- approval policy `never`;
- no Cook Mantra dynamic tools, environments, MCP roots, or workspace roots;
- exact model selection with no fallback;
- one strict JSON output schema; and
- immediate child termination and temporary-media cleanup after forbidden activity or protocol failure.

Cook Mantra rejects every observed command, file, web, MCP, collaboration, reasoning, user-input, model-reroute, or other unapproved event. The opt-in acknowledges that the app-server may begin built-in activity before emitting the event that Cook Mantra rejects. The sandbox and empty workspace reduce exposure but do not turn post-event rejection into pre-execution prevention.

## Errors and disclosure

Runtime status remains browser-safe. It reports only the role, exact provider and model, readiness, capabilities, and normalized error kind. It never exposes account data, provider commands, paths, raw frames, prompts, outputs, or credentials.

The existing remote-photo disclosure remains mandatory for a Codex ingredient-recognition role. It names Codex and the exact model before the browser creates an upload request. Declining or choosing manual input sends no photo.

Documentation must label this mode an experimental, personal-machine preview. It must say that Codex-managed sign-in can send content remotely and that the opt-in accepts an unverified built-in-tool boundary.

## Testing

Deterministic tests cover:

- default-off parsing and Codex-only opt-in parsing;
- rejection of the field on Ollama and OpenRouter;
- fail-closed discovery when the opt-in is absent;
- ready text and vision discovery when it is enabled and the catalog matches;
- permanently unavailable Codex image output;
- current documented initialization, account, catalog, capability, thread, turn, and cleanup shapes;
- explicit handling of allowlisted discovery notifications;
- rejection of server requests, tool activity, reroutes, malformed output, and cleanup failures;
- API readiness and runtime-status behavior in both modes; and
- documentation and example configuration.

After deterministic checks pass, one targeted live Codex readiness check may inspect the signed-in account and model catalog. One minimal synthetic, non-sensitive structured text turn may run only to verify this requested integration; it must use the owned empty workspace and print no raw account data, prompt, response, path, or protocol frame. No live photo or image-generation test runs.

## Success criteria

- Without the YAML opt-in, behavior remains fail-closed.
- With the opt-in, a matching Codex text or vision role can become ready and complete a validated structured turn.
- Dish Preview stays disabled or unavailable for Codex.
- No manual `codex app-server` process is required.
- The README gives an exact Codex-only example and an explicit risk disclosure.
