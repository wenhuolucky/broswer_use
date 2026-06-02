# Publish Service Markdown Image Design

## Goal

Simplify the publish-service rich text pipeline so正文图片 only come from Markdown image syntax rendered into HTML. Remove the old "upload body images separately" path completely.

## Scope

- Keep `Markdown -> HTML -> browser clipboard -> Toutiao editor paste`.
- Keep cover image upload as a separate feature.
- Remove正文图片 upload support from API, service logic, prompt text, docs, and tests.
- Support Markdown images only when the image source is a network URL.

## Behavior Changes

### New正文图片 rule

- Markdown image syntax like `![alt](https://example.com/a.png)` stays in generated HTML as `<img>`.
- These images are expected to render through the rich-text paste flow inside the Toutiao editor.
- Local image paths in Markdown are out of scope and should not be documented as supported.

### Removed behavior

- No more `body_images` request field.
- No more temporary file handling for正文图片 uploads.
- No more prompt instructions telling the agent to insert正文图片 through the toolbar.
- No more server-side stripping of `<img>` tags from generated HTML.

## Code Changes

### `publish_service/markdown_to_rich.py`

- Stop treating `<img>` as something to strip before paste.
- Remove the now-obsolete `strip_images_from_html` helper if nothing else uses it.
- Keep the HTML conversion logic that preserves links, emphasis, headings, and Markdown image tags.

### `publish_service/publish_service.py`

- Remove `body_image_paths` handling from `PublishService.publish`.
- Remove the "Step 5" collection/logging flow for正文图片 uploads.
- After Markdown conversion, keep generated HTML intact instead of stripping `<img>`.
- Simplify task generation so the agent only pastes the HTML and does not perform extra正文图片 insertion.
- Keep `available_file_paths` focused on cover upload only.

### `platforms/toutiao.py`

- Rewrite the rich-html prompt so it describes a single正文 flow:
  `Markdown -> HTML -> clipboard write -> Ctrl+V paste into editor`.
- Remove all references to `body_image_paths`, toolbar image upload, and "配图占位符" handling.
- Update validation instructions so success means the editor shows pasted rich text including links and network images.

### `publish_service/service_api.py`

- Remove `body_images` parsing and temp-file cleanup.
- Remove正文图片 field descriptions from the endpoint docs.

### `publish_service/models.py`

- Remove any response or schema text that describes正文图片 local-path upload behavior.

### Docs and test helpers

- Update `publish_service/README.md`, `publish_service/test_data/README.md`, and related examples to remove `body_images`.
- Update any test helper scripts to stop mentioning or sending正文图片 uploads.

## Testing Plan

### Unit coverage

- Add a test that confirms Markdown with a network image URL keeps `<img src="https://...">` in the generated HTML.
- Add a test that confirms the Toutiao prompt no longer mentions `body_image_paths` or toolbar-based正文图片 upload.

### Regression checks

- Verify Markdown links still become `<a href="...">`.
- Verify HTML generation still keeps normal rich text structure for headings, bold text, and lists.

## Risks

- Toutiao may still sanitize or transform pasted HTML images. This design intentionally assumes pasted network-image HTML is the only正文图片 path.
- If Toutiao strips `<img>` on paste, we will need a new single-path strategy later, but we will not keep the old upload path around.

## Out of Scope

- Supporting local-file正文图片 inside Markdown.
- Adding fallback upload logic for正文图片.
- Changing cover image behavior.
