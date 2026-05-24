# AnyContent Vault Backend

Local FastAPI service that powers the [AnyContent Vault Importer](https://github.com/vancoder4-cyber/anycontent-obsidian-importer) Obsidian plugin.

Given a URL from any of four platforms, it returns a JSON payload containing the cleaned title, author, transcript / body text, image references, and platform metadata. The Obsidian plugin renders that payload into a Markdown note.

## Supported sources

| Source | Method | Requires API key |
| --- | --- | --- |
| Douyin / TikTok **video** | Audio extraction → SiliconFlow SenseVoice ASR | Yes |
| Douyin **image post** (图文) | Per-image OCR via SiliconFlow Qwen3-VL | Yes |
| WeChat Official Account article | HTML scrape with BeautifulSoup (mobile UA) | No |
| YouTube video | oEmbed + `youtube-transcript-api` | No |

## Requirements

- Python 3.10+
- `uv` (preferred) or `pip`
- A free [SiliconFlow](https://cloud.siliconflow.cn/) API key (only needed for Douyin/TikTok ASR and Douyin image OCR)

## Install

```bash
git clone https://github.com/vancoder4-cyber/anycontent-obsidian-backend
cd anycontent-obsidian-backend
uv sync
```

On macOS, `lxml` occasionally needs `libxml2` / `libxslt` system libraries:

```bash
brew install libxml2 libxslt
uv sync
```

## Run

```bash
export API_KEY=sk-your-siliconflow-key
uv run python web/app.py
# Uvicorn running on http://0.0.0.0:8080
```

The server listens on port 8080 by default. Point the Obsidian plugin's *Backend URL* setting at `http://127.0.0.1:8080`.

If you only plan to import WeChat / YouTube content, you can skip the `API_KEY` — the relevant endpoints will still work, and the Douyin endpoints will return a clear error.

## API

### `GET /api/health`

Quick health check. Returns `{"status": "ok", "api_key_configured": <bool>}`.

### `POST /api/video/extract`

Request body:

```json
{ "url": "<source URL>", "api_key": "<optional override>" }
```

Response (relevant fields):

```json
{
  "success": true,
  "platform": "douyin|tiktok|wechat|youtube",
  "post_type": "video|tuwen|article",
  "title": "...",
  "author": "...",
  "account": "...",              // WeChat publisher account, if different from author
  "text": "...",                  // transcript (video) or body (article)
  "images": ["url1", "url2"],    // image post / article
  "images_markdown": "...",      // per-image OCR result (image posts)
  "download_url": "...",         // no-watermark MP4 (Douyin/TikTok video)
  "publish_time": "...",
  "thumbnail": "..."
}
```

Errors return `{"success": false, "error": "<message>"}` with HTTP 200 (the plugin parses `success` to decide).

### `GET /api/video/download?url=&filename=`

Streams an MP4 from the underlying CDN. Used by the plugin when *Save no-watermark video locally* is enabled.

## Privacy / Network

- All HTTP traffic between the Obsidian plugin and this backend is localhost-only.
- The backend itself reaches out to:
  - The source page of whatever URL you imported (Douyin / TikTok / WeChat / YouTube).
  - SiliconFlow's API endpoints, but **only** when handling a Douyin / TikTok video (ASR) or a Douyin image post (OCR). WeChat and YouTube imports do not call SiliconFlow.
- No analytics, no telemetry. The backend logs to stdout only.

## Docker

A minimal Dockerfile is included for convenience:

```bash
docker build -t anycontent-obsidian-backend .
docker run --rm -p 8080:8080 -e API_KEY=sk-... anycontent-obsidian-backend
```

## License

Apache License 2.0 — see `LICENSE`.

This project is a fork of [yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) with additional extractors for WeChat Official Accounts and YouTube. Upstream attribution is recorded in `NOTICE`.

## Acknowledgements

- [yzfly/douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) — the Douyin extraction core.
- [SiliconFlow](https://cloud.siliconflow.cn/) — SenseVoice ASR and Qwen3-VL OCR.
- [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api) — YouTube captions without OAuth.
