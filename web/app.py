#!/usr/bin/env python3
"""
AnyContent Vault Backend — FastAPI service.

Dispatches inbound URLs to the right extractor:
    Douyin / TikTok  → douyin_downloader (ASR for videos, OCR for image posts)
    WeChat           → wechat_extractor
    YouTube          → youtube_extractor

Run:
    cd anycontent-obsidian-backend
    export API_KEY="sk-..."          # SiliconFlow key, only needed for Douyin/TikTok
    uv run python web/app.py         # listens on :8080
"""

import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "douyin-video" / "scripts"))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import requests

# 导入抖音处理模块
from douyin_downloader import get_video_info, extract_text, HEADERS

# 导入其他平台的提取器（Phase 2: WeChat / YouTube）
sys.path.insert(0, str(Path(__file__).parent))
from wechat_extractor import is_wechat_url, extract_wechat
from youtube_extractor import is_youtube_url, extract_youtube

app = FastAPI(title="多平台内容提取器", version="2.0.0")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class VideoRequest(BaseModel):
    """视频请求模型"""
    url: str
    api_key: str = ""  # 可选，从前端传入


class VideoInfoResponse(BaseModel):
    """视频信息响应"""
    success: bool
    video_id: str = ""
    title: str = ""
    download_url: str = ""
    error: str = ""


class ExtractResponse(BaseModel):
    """文案提取响应（多平台统一 schema）"""
    success: bool
    video_id: str = ""
    title: str = ""
    text: str = ""
    download_url: str = ""
    error: str = ""
    # 区分内容类型: video（抖音视频/YouTube）/ tuwen（抖音图文）/ article（微信公众号）
    post_type: str = "video"
    images: list[str] = []
    image_count: int = 0
    desc: str = ""
    images_markdown: str = ""
    # Phase 2 新增字段（不同平台填不同子集）
    platform: str = "douyin"   # douyin / wechat / youtube / tiktok
    author: str = ""           # 作者名
    account: str = ""          # 微信公众号名 / YouTube 频道名（同 author 时为空）
    publish_time: str = ""     # 发布时间
    thumbnail: str = ""        # YouTube 缩略图等


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/health")
async def health_check():
    """健康检查"""
    api_key = os.getenv("API_KEY", "")
    return {
        "status": "ok",
        "api_key_configured": bool(api_key)
    }


@app.post("/api/video/info", response_model=VideoInfoResponse)
async def get_info(req: VideoRequest):
    """获取视频信息（无需 API_KEY）"""
    try:
        info = get_video_info(req.url)
        return VideoInfoResponse(
            success=True,
            video_id=info["video_id"],
            title=info["title"],
            download_url=info["url"]
        )
    except Exception as e:
        return VideoInfoResponse(success=False, error=str(e))


def _route_extract(url: str, api_key: str) -> dict:
    """
    URL 路由：根据 URL 判断该用哪个 extractor。
    返回 douyin extract_text 兼容的 {video_info, text, output_path} 结构。
    """
    if is_wechat_url(url):
        # 微信公众号文章——不需要 api_key
        return extract_wechat(url)
    if is_youtube_url(url):
        # YouTube——不需要 api_key（用 YouTube 自己的字幕 API）
        return extract_youtube(url, with_timestamps=True)
    # 默认走抖音 / TikTok 路径
    return extract_text(url, api_key=api_key, show_progress=False)


@app.post("/api/video/extract", response_model=ExtractResponse)
async def extract_transcript(req: VideoRequest):
    """
    多平台提取——根据 URL 自动路由：
    - 抖音视频：API_KEY 跑 ASR
    - 抖音图文：API_KEY 跑 OCR（可选；无 key 时仅返回 desc + 图片 URL）
    - 微信公众号：无需 API_KEY，直接抓 HTML + 解析
    - YouTube：无需 API_KEY，用 YouTube 字幕 API
    """
    api_key = req.api_key or os.getenv("API_KEY", "")

    try:
        result = _route_extract(req.url, api_key)
        vi = result["video_info"]
        post_type = vi.get("post_type", "video")
        platform = vi.get("platform", "douyin")

        return ExtractResponse(
            success=True,
            video_id=vi.get("video_id", ""),
            title=vi.get("title", ""),
            text=result["text"],
            download_url=vi.get("url", ""),
            post_type=post_type,
            images=vi.get("images", []) if post_type in ("tuwen", "article") else [],
            image_count=vi.get("image_count", 0) if post_type in ("tuwen", "article") else 0,
            desc=vi.get("desc", ""),
            images_markdown=vi.get("images_markdown", "") if post_type == "tuwen" else "",
            platform=platform,
            author=vi.get("author", ""),
            account=vi.get("account", ""),
            publish_time=vi.get("publish_time", ""),
            thumbnail=vi.get("thumbnail", ""),
        )
    except ValueError as e:
        return ExtractResponse(success=False, error=str(e))
    except Exception as e:
        return ExtractResponse(success=False, error=str(e))


@app.get("/api/video/download")
async def download_video(url: str, filename: str = "video.mp4"):
    """代理下载视频（解决跨域和请求头问题）"""
    print(f"[Download] URL: {url}")
    print(f"[Download] Filename: {filename}")
    try:
        # 完整的请求头，模拟浏览器访问
        download_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.douyin.com/',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'identity',
            'Connection': 'keep-alive',
        }

        response = requests.get(url, headers=download_headers, stream=True, allow_redirects=True)
        print(f"[Download] Response status: {response.status_code}")
        print(f"[Download] Final URL: {response.url}")
        response.raise_for_status()

        content_length = response.headers.get("content-length", "")

        def iter_content():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        if content_length:
            headers["Content-Length"] = content_length

        return StreamingResponse(
            iter_content(),
            media_type="video/mp4",
            headers=headers
        )
    except requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"下载失败: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main():
    """启动服务"""
    port = int(os.getenv("PORT", "8080"))
    print(f"🚀 启动文案提取器 WebUI: http://localhost:{port}")
    print(f"📝 API_KEY 配置状态: {'已配置' if os.getenv('API_KEY') else '未配置'}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
