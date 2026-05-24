"""
微信公众号文章提取器

输入：mp.weixin.qq.com/s/xxx 形式的 URL
输出：与 douyin_downloader 的 extract_text 同结构的 dict
      post_type="article"
      text = 完整正文 markdown
      author + 公众号名 + 图片 URL 列表
"""
import requests
import re
from typing import Optional

WECHAT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                  'EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1',
}

# 用于识别 URL 是不是微信公众号
WECHAT_URL_PATTERN = re.compile(r'(?:^|/)mp\.weixin\.qq\.com/s/[A-Za-z0-9_-]+', re.IGNORECASE)


def is_wechat_url(url: str) -> bool:
    """判断 URL 是否是微信公众号文章"""
    return bool(WECHAT_URL_PATTERN.search(url or ""))


def _extract_article_id(url: str) -> str:
    """从 mp.weixin.qq.com/s/XXXXX 抽出 article id"""
    m = re.search(r'mp\.weixin\.qq\.com/s/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else ""


def fetch_wechat_article(url: str, timeout: int = 15) -> dict:
    """
    抓 + 解析一篇微信公众号文章。
    返回结构对齐 douyin_downloader.extract_text 的 video_info / text 接口。

    返回 dict:
      {
        "post_type": "article",
        "video_id": "<article id>",
        "title": "...",
        "url": "<canonical url>",
        "desc": "<short summary>",
        "text": "<full article markdown>",
        "author": "<author or 公众号名>",
        "account": "<公众号名>",
        "publish_time": "<may be empty if JS rendered>",
        "images": [<url, ...>],
        "image_count": N,
      }
    """
    try:
        # Lazy import so the backend can boot even if these are missing
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise Exception(
            "wechat_extractor requires beautifulsoup4 + lxml. "
            "Run `uv sync` (or `pip install beautifulsoup4 lxml`) in the backend directory."
        ) from e

    r = requests.get(url, headers=WECHAT_HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()

    # 解决编码（微信偶尔是 GBK 但 charset 没标对）
    if r.encoding is None or r.encoding.lower() == 'iso-8859-1':
        r.encoding = r.apparent_encoding or 'utf-8'

    soup = BeautifulSoup(r.text, 'lxml')

    article_id = _extract_article_id(url)

    # --- title ---
    title = ""
    h1 = soup.find('h1', id='activity-name')
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find('meta', property='og:title')
        if og and og.get('content'):
            title = og['content'].strip()

    # --- 公众号名 ---
    account = ""
    js_name = soup.find('a', id='js_name') or soup.find('strong', id='js_name')
    if js_name:
        account = js_name.get_text(strip=True)
    if not account:
        og_site = soup.find('meta', property='og:article:author')
        if og_site and og_site.get('content'):
            account = og_site['content'].strip()

    # --- 作者（meta name=author）---
    author = ""
    author_meta = soup.find('meta', attrs={'name': 'author'})
    if author_meta and author_meta.get('content'):
        author = author_meta['content'].strip()
    if not author:
        author = account  # fallback：用公众号名当 author

    # --- publish_time (微信文章 JS 渲染，多数情况是空) ---
    publish_time = ""
    em = soup.find('em', id='publish_time')
    if em:
        publish_time = em.get_text(strip=True)
    if not publish_time:
        # 备用：尝试从 script 里抓
        script_text = '\n'.join(s.get_text() for s in soup.find_all('script') if s.string)
        m = re.search(r'var\s+ct\s*=\s*["\'](\d+)["\']', script_text)
        if m:
            import datetime as _dt
            try:
                publish_time = _dt.datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

    # --- 描述（用 og:description）---
    desc = ""
    og_desc = soup.find('meta', property='og:description')
    if og_desc and og_desc.get('content'):
        desc = og_desc['content'].strip()

    # --- 正文 ---
    content_div = (
        soup.find('div', id='js_content') or
        soup.find('div', class_='rich_media_content')
    )

    paragraphs = []
    images = []
    if content_div:
        # 用 find_all 按出现顺序遍历，保留段落 + 图片插入位置（图片用 placeholder）
        for el in content_div.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote', 'img', 'pre']):
            if el.name == 'img':
                src = el.get('data-src') or el.get('src')
                if src and src.startswith('http'):
                    images.append(src)
                    paragraphs.append(f"![图{len(images)}]({src})")
            else:
                text = el.get_text(strip=True)
                if text:
                    if el.name in ('h1', 'h2'):
                        paragraphs.append(f"## {text}")
                    elif el.name in ('h3', 'h4'):
                        paragraphs.append(f"### {text}")
                    elif el.name == 'blockquote':
                        paragraphs.append(f"> {text}")
                    elif el.name == 'li':
                        paragraphs.append(f"- {text}")
                    elif el.name == 'pre':
                        paragraphs.append(f"```\n{text}\n```")
                    else:
                        paragraphs.append(text)
    text_md = "\n\n".join(paragraphs)

    if not text_md:
        # 兜底：取整页所有 p
        all_p = [p.get_text(strip=True) for p in soup.find_all('p')]
        text_md = "\n\n".join(p for p in all_p if p)

    return {
        "post_type": "article",
        "video_id": article_id,
        "title": title or "无标题",
        "url": url,
        "desc": desc,
        "text": text_md,
        "author": author,
        "account": account,
        "publish_time": publish_time,
        "images": images,
        "image_count": len(images),
        "platform": "wechat",
    }


def extract_wechat(url: str, **kwargs) -> dict:
    """
    给 app.py 调的统一入口。返回结构和 douyin extract_text 一致：
      {"video_info": <enriched_dict>, "text": <main markdown text>, "output_path": None}
    """
    info = fetch_wechat_article(url, timeout=kwargs.get('timeout', 15))
    return {
        "video_info": info,
        "text": info["text"],
        "output_path": None,
    }
