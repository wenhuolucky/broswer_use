# TODO: Add Toutiao Body Presence Guard

## Background

Sohu publishing exposed a class of agent reliability issue: the agent may continue to later publishing steps even when the article body was not actually written into the editor. This has now been addressed for Sohu at the prompt level by requiring a body presence check before moving to next-step, cover settings, or publish actions.

Toutiao currently still relies on its existing rich-text clipboard flow and prompt instructions. It asks the agent to check that the body entered the editor, but there is no dedicated code-level or tool-level guard that blocks publishing when the body is absent.

## Follow-up Requirement

Add a Toutiao body presence guard in a later change. The guard should:

- Keep the existing Toutiao rich-text clipboard flow unchanged unless verification fails.
- Before clicking preview, confirm the Toutiao editor visible text is non-empty.
- Confirm the editor contains a stable body probe derived from the original article content.
- Retry body write at most two times when the probe is missing.
- Return failure instead of continuing if the body remains absent.
- Log the probe, detected body length, and retry count for diagnosis.

## Reason For Deferring

This change is intentionally deferred because the current task must only modify Sohu behavior. Toutiao behavior is production-tested and should be changed in a focused follow-up with its own tests.
