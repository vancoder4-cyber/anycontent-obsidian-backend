"""
YouTube 视频提取器

输入：youtube.com/watch?v=X / youtu.be/X / youtube.com/shorts/X 等 URL
输出：与 douyin_downloader extract_text 同结构的 dict
      post_type="video"
      text = YouTube 字幕拼接的 transcript
      title / author / thumbnail 通过 oEmbed 拿
"""
import re
import requests
from typing import Optional

OEMBED_URL = "https://www.youtube.com/oembed"

YT_ID_RE = re.compile(
    r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})'
)
YT_URL_PATTERN = re.compile(
    r'(?:^|/)(?:www\.)?(?:youtube\.com|youtu\.be)(?:/|$)', re.IGNORECASE
)


def is_youtube_url(url: str) -> bool:
    return bool(YT_URL_PATTERN.search(url or ""))


def extract_video_id(url: str) -> Optional[str]:
    m = YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def fetch_youtube_meta(url: str, timeout: int = 10) -> dict:
    """通过 oEmbed 拿 title / author / thumbnail。不需要 auth。"""
    try:
        r = requests.get(OEMBED_URL, params={"url": url, "format": "json"}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def fetch_youtube_transcript(video_id: str, prefer_langs=None) -> list:
    """
    用 youtube-transcript-api 取字幕。
    prefer_langs 是按优先级的语言代码列表（如 ['zh-Hans', 'zh', 'en']）。
    返回 [(text, start, duration), ...] 列表。
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        raise Exception(
            "youtube_extractor requires youtube-transcript-api. "
            "Run `uv sync` (or `pip install youtube-transcript-api`) in the backend directory."
        ) from e

    langs = prefer_langs or ['zh-Hans', 'zh-Hant', 'zh', 'en', 'en-US', 'ja']
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=langs)
    except Exception as e:
        # 没字幕 / 关闭字幕 / 私有 / 删除 等
        raise Exception(f"取字幕失败: {type(e).__name__}: {str(e)[:200]}")

    snippets = []
    for s in fetched:
        # 兼容新旧 API 形态
        text = getattr(s, 'text', None) or (s.get('text') if isinstance(s, dict) else "")
        start = getattr(s, 'start', None) or (s.get('start', 0) if isinstance(s, dict) else 0)
        duration = getattr(s, 'duration', None) or (s.get('duration', 0) if isinstance(s, dict) else 0)
        if text:
            snippets.append((text.strip(), float(start), float(duration)))
    return snippets


def _fmt_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"[{h:02d}:{m:02d}:{sec:02d}]"
    return f"[{m:02d}:{sec:02d}]"


def build_transcript_markdown(snippets: list, with_timestamps: bool = True) -> str:
    """
    把字幕 snippets 拼成 markdown 文本。
    with_timestamps=True 时每段前面带时间戳（方便回看定位）。
    """
    if not snippets:
        return "（未提取到字幕）"
    parts = []
    if with_timestamps:
        # 每 30 秒一段聚合，避免每 5 秒一行噪声大
        chunk_seconds = 30
        bucket_start = None
        bucket_text = []
        for text, start, _ in snippets:
            if bucket_start is None or start - bucket_start >= chunk_seconds:
                if bucket_text:
                    parts.append(f"{_fmt_timestamp(bucket_start)} {' '.join(bucket_text)}")
                bucket_start = start
                bucket_text = [text]
            else:
                bucket_text.append(text)
        if bucket_text and bucket_start is not None:
            parts.append(f"{_fmt_timestamp(bucket_start)} {' '.join(bucket_text)}")
        return "\n\n".join(parts)
    else:
        return " ".join(text for text, _, _ in snippets)


def fetch_youtube_full(url: str, with_timestamps: bool = True, timeout: int = 15) -> dict:
    """
    抓 + 拼一个完整的 YouTube 视频信息。返回 dict 对齐 douyin extract_text 接口。
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception(f"不像 YouTube URL: {url}")

    # 元数据
    meta = fetch_youtube_meta(url, timeout=timeout)
    title = meta.get("title", "") or f"YouTube_{video_id}"
    author = meta.get("author_name", "") or "未知频道"
    thumb = meta.get("thumbnail_url", "")

    # 字幕
    try:
        snippets = fetch_youtube_transcript(video_id)
        text_md = build_transcript_markdown(snippets, with_timestamps=with_timestamps)
        transcript_ok = True
        transcript_segments = len(snippets)
    except Exception as e:
        text_md = f"（未拿到字幕：{e}）"
        transcript_ok = False
        transcript_segments = 0

    return {
        "post_type": "video",
        "video_id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "desc": title,  # YouTube oEmbed 不返回 description，用 title 兜底
        "text": text_md,
        "author": author,
        "thumbnail": thumb,
        "platform": "youtube",
        "transcript_ok": transcript_ok,
        "transcript_segments": transcript_segments,
    }


def extract_youtube(url: str, **kwargs) -> dict:
    """app.py 调的统一入口，返回结构对齐 douyin extract_text。"""
    info = fetch_youtube_full(url, with_timestamps=kwargs.get('with_timestamps', True),
                              timeout=kwargs.get('timeout', 15))
    return {
        "video_info": info,
        "text": info["text"],
        "output_path": None,
    }
