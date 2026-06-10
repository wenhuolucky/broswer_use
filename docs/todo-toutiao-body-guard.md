# TODO: Publishing System Follow-ups

## Toutiao Body Presence Guard

### Background

Sohu publishing exposed a class of agent reliability issue: the agent may continue to later publishing steps even when the article body was not actually written into the editor. This has now been addressed for Sohu at the prompt level by requiring a body presence check before moving to next-step, cover settings, or publish actions.

Toutiao currently still relies on its existing rich-text clipboard flow and prompt instructions. It asks the agent to check that the body entered the editor, but there is no dedicated code-level or tool-level guard that blocks publishing when the body is absent.

### Follow-up Requirement

Add a Toutiao body presence guard in a later change. The guard should:

- Keep the existing Toutiao rich-text clipboard flow unchanged unless verification fails.
- Before clicking preview, confirm the Toutiao editor visible text is non-empty.
- Confirm the editor contains a stable body probe derived from the original article content.
- Retry body write at most two times when the probe is missing.
- Return failure instead of continuing if the body remains absent.
- Log the probe, detected body length, and retry count for diagnosis.

### Reason For Deferring

This change is intentionally deferred because the current task must only modify Sohu behavior. Toutiao behavior is production-tested and should be changed in a focused follow-up with its own tests.

## Remote Connection Model Consistency

Currently remote login and live publish viewing do not use exactly the same remote connection path. Remote login is built around the KasmVNC/noVNC style browser session exposed for user interaction, while publish-time viewing uses the live viewer flow tied to the isolated publishing browser. This split works, but it means two viewer paths must be maintained, debugged, and stabilized separately.

Follow-up evaluation should determine whether to keep the split or converge the two flows. The decision should compare:

- login usability, including QR login, password login, slider verification, and image captcha handling
- publish live-view latency and image quality
- connection stability under cloudflared/network interruption
- resource usage per session
- whether the same URL allowlist/navigation guard can protect both modes
- operational complexity when debugging user reports

If convergence is chosen, design the migration so login and publish viewing still keep independent browser profiles and task isolation. Do not trade away the existing concurrency isolation just to share the viewer implementation.

## Concurrency Capacity Testing

The current implementation isolates publish browsers by per-task temporary profile, auth file, random CDP port, live viewer, cover temp directory, and request logger. This should prevent account/session cross-talk, but the actual safe concurrency limit has not been measured.

Before production scale-up, run controlled concurrency tests for:

- multiple Toutiao tasks at the same time
- multiple Sohu tasks at the same time
- mixed Toutiao and Sohu tasks at the same time
- multiple remote-login sessions while publish tasks are running
- repeated CDP port allocation under high task creation rate
- large cover images and long article bodies

The test should record:

- maximum stable concurrent jobs on the target server
- CPU, memory, shared memory, disk, and network usage
- browser startup failure rate
- CDP port conflict retry count
- publish success/failure count by platform
- whether any task uses another task's cookie, title, cover, browser, or article URL
- live viewer availability during load

Use the result to set explicit operational limits, such as `MAX_REMOTE_LOGIN_SESSIONS`, API rate limits, worker count, or queue policy.

## Remote Login Stability

The remote login flow is known to be sensitive to connection stability. It depends on browser startup, VNC/noVNC streaming, cloudflared tunnel availability, user network quality, and the platform login page itself. When any layer becomes unstable, users may see disconnections, blank viewer screens, delayed refreshes, or login completion not being saved reliably.

Follow-up work should improve this area with better diagnostics and recovery:

- log tunnel startup time and tunnel URL availability
- detect and report cloudflared process exit
- detect stale browser/CDP sessions
- expose clearer login session status to job query responses
- add timeout reasons that distinguish user timeout, tunnel failure, browser crash, and cookie extraction failure
- evaluate reconnect support instead of requiring a new login session after transient viewer failure
- verify URL allowlist behavior still permits all legitimate login/captcha/verification pages for Toutiao and Sohu

The goal is to make remote login failure actionable from logs instead of requiring guesswork from user screenshots.

## Pre-Launch Extreme-State Testing

Before上线, test platform and account edge cases explicitly. The publish service should return clear task states instead of looping, falsely reporting success, or leaving jobs stuck.

Required scenarios include:

- account banned or suspended
- account muted / no publishing permission
- account requires re-login
- cookie expired or partially invalid
- platform asks for image captcha
- platform asks for slider verification
- platform asks for phone/email/security verification
- article content rejected by policy
- duplicate title/content warning
- cover upload fails
- body editor remains empty after write attempts
- platform publish button disabled
- platform returns submission success but no article URL
- platform returns URL while article is still under review
- network timeout during publish
- browser crash during publish
- live viewer tunnel disconnects during publish

For each scenario, verify:

- final job status is correct
- `failure_reason` uses the platform's visible text when available
- no repeated unbounded retries occur
- no article URL is fabricated
- no task uses another account's cookie or browser session
- cleanup removes temporary auth/profile/cover directories
- logs contain enough evidence to diagnose the failure
