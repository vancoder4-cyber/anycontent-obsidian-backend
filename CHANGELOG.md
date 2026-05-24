# Changelog

All notable changes to this backend are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-05-24

### Fixed

- ASR path (`transcribe_single_audio`) now validates the API key is ASCII before calling SiliconFlow. Previously a non-ASCII key (e.g. a Chinese placeholder accidentally left in `export API_KEY=...`) leaked through and surfaced as a cryptic `'latin-1' codec can't encode characters in position N-M` from inside `urllib3`. Now produces the same actionable error as the OCR path.

## [1.0.0] - 2026-05-24

First public release as `anycontent-obsidian-backend`, forked from
[yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server).

### Added

- WeChat Official Account article extractor (`web/wechat_extractor.py`).
- YouTube transcript + metadata extractor (`web/youtube_extractor.py`).
- URL-based routing in `web/app.py` to dispatch to the right extractor.
- Image-post (Douyin 图文) handling with per-image OCR via SiliconFlow Qwen3-VL.
- `images_markdown` field in the extract response so OCR results are
  serialised inline for the plugin to render.
- Fallback chain across SiliconFlow VL model IDs to survive model deprecations.
- Validation of the API key encoding to surface friendly errors when a
  non-ASCII placeholder leaks into `$API_KEY`.
- Apache-2.0 `LICENSE` and `NOTICE` files documenting upstream attribution.

### Changed

- Project metadata renamed (`pyproject.toml`, console script name).
- `ExtractResponse` extended with `platform`, `post_type`, `author`, `account`,
  `publish_time`, `thumbnail`, and `images_markdown`.

[1.0.0]: https://github.com/vancoder4-cyber/anycontent-obsidian-backend/releases/tag/v1.0.0
[1.0.1]: https://github.com/vancoder4-cyber/anycontent-obsidian-backend/releases/tag/v1.0.1
