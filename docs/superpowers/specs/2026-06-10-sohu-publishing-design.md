# Sohu Publishing Design

## Goal

Add Sohu publishing support without changing the external API. Callers continue to use the existing publish and login endpoints and switch platforms only through the existing `platform` field.

## Requirements

- Keep the request and response API shape unchanged.
- Route `platform=toutiao` to the existing Toutiao publishing behavior.
- Route `platform=sohu` to a new Sohu publishing behavior.
- Treat publishing as successful only when an `article_url` is obtained.
- Support concurrent Toutiao and Sohu tasks with the same isolation guarantees already used by Toutiao.
- Preserve the current remote login flow and cookie storage model.

## Architecture

The current `PublishService` is functionally a Toutiao service even though the name is generic. It validates Toutiao cookies, builds a Toutiao prompt, navigates Toutiao management pages, and normalizes Toutiao URLs. Keeping Sohu logic inside the same class would mix two platform workflows in one large file.

The new structure should separate common execution mechanics from platform-specific publishing behavior:

- `BasePublishService`: shared browser, cookie, cover image, LLM, live viewer, token tracking, cleanup, and finalization mechanics.
- `PublishServiceToutiao`: existing Toutiao-specific prompt, cookie validation, URL lookup, and URL normalization.
- `PublishServiceSohu`: new Sohu-specific prompt, cookie validation, article URL extraction, and Sohu URL normalization.

For compatibility during the migration, the existing `app.publishing.service.PublishService` name can remain as an alias or thin subclass for Toutiao behavior until all imports are updated.

## Platform Routing

`PublishServiceAdapter.publish()` should accept `platform`. It chooses the concrete service by platform:

- `toutiao` -> `PublishServiceToutiao`
- `sohu` -> `PublishServiceSohu`

Unknown platforms fail early with a clear error message.

`PublishAgent._publish_with_cookie()` already has access to `request.platform`, so it should pass it to the adapter. The public request body does not change.

## Sohu Publishing Flow

The Sohu agent should:

1. Open `https://mp.sohu.com`.
2. Confirm the account is logged in.
3. Read the account display name when visible.
4. Enter the article publishing flow.
5. Fill title and body.
6. Click the next-step button.
7. Fill or confirm cover, summary, article property, and required classification fields.
8. Click publish.
9. Confirm the page indicates submission, review, or publish completion.
10. Obtain a Sohu article preview URL or mobile article URL.
11. Return success only when a normalized `article_url` is available.

Reviewing or pending-review status is acceptable for Sohu as long as a URL is available.

## Sohu URL Normalization

Use the user-provided conversion rule:

```text
https://mp.sohu.com/h5/v2/newsPreview?id=1020931946&type=article
-> https://m.sohu.com/a/1020931946_{account_id}?sec=wd
```

Already-normalized mobile URLs are returned unchanged:

```text
https://m.sohu.com/a/1020931946_122702850?sec=wd
```

`account_id` must not be permanently hard-coded in publishing logic. First implementation should support environment configuration:

- `SOHU_ACCOUNT_ID_MAP`, formatted as `user1:122702850,user2:122580788`
- `SOHU_ACCOUNT_ID`, a global fallback

Lookup priority:

1. user-specific value from `SOHU_ACCOUNT_ID_MAP`
2. global value from `SOHU_ACCOUNT_ID`
3. empty value, which leaves the preview URL unchanged or causes Sohu success validation to fail if only a preview URL was found

## Article URL Success Rule

Both platforms must keep the same success rule: no `article_url`, no success.

For Sohu:

- If a preview URL is found and an account id is available, normalize and return the mobile URL.
- If a mobile URL is found, return it directly.
- If Sohu appears submitted but no URL can be extracted, return failure with a clear reason.

## Concurrency

Sohu must reuse the same task isolation mechanics as Toutiao:

- per-task temporary browser auth file
- per-task temporary browser profile
- per-task random CDP port
- three attempts on CDP port conflict
- per-task live viewer
- per-task cover temp directory
- per-task request logger
- cookie path isolated by `platform/user_id`

No Sohu implementation should use a shared browser, shared profile, or fixed CDP port.

## Tests

Add or update tests for:

- Sohu URL normalization from preview URL.
- Sohu mobile URL passthrough.
- Sohu account id lookup by user id and global fallback.
- Adapter routing by platform.
- Unknown platform failure.
- Agent passing platform to adapter.
- API result URL normalization by platform.
- Sohu result parsing requiring URL.
- Toutiao existing URL and publishing tests remain green.

## Rollout

The first version should target Sohu submission plus URL extraction. It should not wait for review completion. Operationally, users can test by submitting `platform=sohu` with an already-saved Sohu cookie or by using the existing remote login flow with `platform=sohu`.
