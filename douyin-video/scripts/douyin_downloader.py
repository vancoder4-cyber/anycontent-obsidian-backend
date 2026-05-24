#!/usr/bin/env python3
"""
抖音无水印视频下载和文案提取工具

功能:
1. 从抖音分享链接获取无水印视频下载链接
2. 下载视频并提取音频
3. 使用硅基流动 API 从音频中提取文本
4. 自动保存文案到文件 (一个视频一个文件夹)

环境变量:
- API_KEY: 硅基流动 API 密钥 (用于文案提取功能)

使用示例:
  # 获取下载链接 (无需 API 密钥)
  python douyin_downloader.py --link "抖音分享链接" --action info

  # 下载视频
  python douyin_downloader.py --link "抖音分享链接" --action download --output ./videos

  # 提取文案并保存到文件 (需要 API_KEY 环境变量)
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output
"""

import os
import re
import sys
import json
import argparse
import tempfile
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime


def check_dependencies():
    """检查必要的依赖是否已安装"""
    missing = []
    try:
        import requests
    except ImportError:
        missing.append("requests")
    try:
        import ffmpeg
    except ImportError:
        missing.append("ffmpeg-python")

    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        sys.exit(1)


check_dependencies()

import requests
import ffmpeg

# 请求头，模拟移动端访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
}

# 硅基流动 API 配置
DEFAULT_API_BASE_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"

# 视觉模型 (OCR 用)
SILICONFLOW_CHAT_URL = "https://api.siliconflow.cn/v1/chat/completions"
# 默认按 小/快/便宜 优先；遇到 403 Model disabled 会自动 fallback
# 这些都是 Qwen3-VL 系列（硅基流动当前主推的代际）
DEFAULT_OCR_MODEL = os.getenv("OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
OCR_MODEL_FALLBACKS = [
    "Qwen/Qwen3-VL-8B-Instruct",         # 8B 小快便宜
    "Qwen/Qwen3-VL-30B-A3B-Instruct",    # 30B MoE，3B 激活，性价比好
    "Qwen/Qwen3-VL-32B-Instruct",        # 32B 大模型，质量最好
    "Qwen/Qwen3-Omni-30B-A3B-Instruct",  # Omni 系列兜底
]
OCR_PROMPT = """请把这张图片里的所有文字内容**完整、原样**提取出来，按图片中的阅读顺序排列。

要求：
1. 只输出文字内容本身，不要加任何说明、注释、标题、引号
2. 保留原文的段落结构（用空行分段）
3. 如果是大段文字，按句子/段落换行；如果是列表/枚举，按列表格式输出
4. 不要修改、解释、润色或翻译原文
5. 如果图片中没有任何文字，输出 (无文字) 即可"""


def _ocr_single_attempt(image_url: str, api_key: str, model: str, timeout: int) -> tuple:
    """单次尝试。返回 (success: bool, text_or_error: str, status_code: int)"""
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        "max_tokens": 4096,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(SILICONFLOW_CHAT_URL, json=payload, headers=headers, timeout=timeout)
    if r.status_code == 200:
        try:
            return True, r.json()["choices"][0]["message"]["content"].strip(), 200
        except Exception as e:
            return False, f"响应解析失败: {e}; 原文: {r.text[:200]}", 200
    return False, r.text[:300], r.status_code


# 缓存运行时确认可用的模型，避免每张图都试 fallback
_WORKING_OCR_MODEL = None


def ocr_image_url(image_url: str, api_key: str, model: str = None, timeout: int = 60) -> str:
    """
    用硅基流动 Vision API OCR 单张图片 URL，返回纯文本。
    遇到模型不可用（403 Model disabled）会自动 fallback 到下一个候选模型。
    """
    global _WORKING_OCR_MODEL

    # 提前验证 api_key 是 ASCII
    api_key = (api_key or "").strip()
    if not api_key:
        raise Exception("OCR 失败：未提供 API_KEY")
    try:
        api_key.encode('ascii')
    except UnicodeEncodeError:
        bad_chars = [c for c in api_key if ord(c) > 127][:5]
        raise Exception(
            f"API_KEY 含非 ASCII 字符（首批异常字符: {bad_chars}）。"
            f"很可能你的 export 命令里残留了中文 placeholder。"
            f"请改成: export API_KEY=sk-XXXXXXXX (真的硅基流动 key)"
        )

    # 用户显式指定 model → 不 fallback
    if model:
        ok, content, status = _ocr_single_attempt(image_url, api_key, model, timeout)
        if ok:
            return content
        raise Exception(f"VL API {status} (model={model}): {content}")

    # 用之前缓存的能跑的 model（避免每张图都重新探测）
    if _WORKING_OCR_MODEL:
        ok, content, status = _ocr_single_attempt(image_url, api_key, _WORKING_OCR_MODEL, timeout)
        if ok:
            return content
        # 缓存的 model 突然挂了，清缓存再来一遍 fallback
        _WORKING_OCR_MODEL = None

    # 按 fallback 列表依次试
    last_err = ""
    for candidate in OCR_MODEL_FALLBACKS:
        ok, content, status = _ocr_single_attempt(image_url, api_key, candidate, timeout)
        if ok:
            _WORKING_OCR_MODEL = candidate
            return content
        last_err = f"{candidate} → {status}: {content}"
        # 如果是认证错（401），所有 model 都会败，没必要继续
        if status == 401:
            break
        # 200 但解析失败也没必要换模型
        if status == 200:
            break

    raise Exception(f"所有 VL 模型都试过都不行。最后一次: {last_err}")


def ocr_images_parallel(image_urls: list, api_key: str, model: str = None,
                        max_workers: int = 5, show_progress: bool = False) -> list:
    """
    并行 OCR 多张图片。返回与 image_urls 顺序对应的文本列表。
    单张失败时返回 错误标记 而不是抛异常，保证其他图能正常处理。
    """
    from concurrent.futures import ThreadPoolExecutor
    if not image_urls:
        return []
    results = [None] * len(image_urls)

    def task(idx, url):
        try:
            text = ocr_image_url(url, api_key, model)
            if show_progress:
                print(f"  OCR [{idx + 1}/{len(image_urls)}] ✓ ({len(text)} 字)")
            return idx, text
        except Exception as e:
            if show_progress:
                print(f"  OCR [{idx + 1}/{len(image_urls)}] ✗ {e}")
            return idx, f"（OCR 失败: {e}）"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(task, i, url) for i, url in enumerate(image_urls)]
        for f in futures:
            idx, text = f.result()
            results[idx] = text

    return results


def build_tuwen_markdown(ocr_texts: list) -> str:
    """把 OCR 结果列表拼成单一 markdown 字符串。"""
    if not ocr_texts:
        return ""
    parts = []
    for i, text in enumerate(ocr_texts):
        parts.append(f"### 图 {i + 1}")
        parts.append("")
        parts.append(text.strip() or "（无文字）")
        parts.append("")
    return "\n".join(parts).strip()


class DouyinProcessor:
    """抖音视频处理器"""

    def __init__(self, api_key: str = "", api_base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.api_base_url = api_base_url or DEFAULT_API_BASE_URL
        self.model = model or DEFAULT_MODEL
        self.temp_dir = Path(tempfile.mkdtemp())

    def __del__(self):
        """清理临时目录"""
        if hasattr(self, 'temp_dir') and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def parse_share_url(self, share_text: str) -> dict:
        """从分享文本中提取无水印视频链接"""
        # 提取分享链接
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        share_response = requests.get(share_url, headers=HEADERS, allow_redirects=True)
        final_url = share_response.url

        # 从最终 redirect URL 检测帖子类型：
        # /share/video/... = 视频帖
        # /share/note/...  = 图文帖
        # /share/slides/... = 幻灯片帖
        if '/share/note/' in final_url:
            aweme_type = 'note'
        elif '/share/slides/' in final_url:
            aweme_type = 'slides'
        else:
            aweme_type = 'video'  # 默认 + fallback

        # 从 URL 提 ID（不带查询参数）
        video_id = final_url.split("?")[0].strip("/").split("/")[-1]

        # 用正确的 canonical URL 重新请求
        canonical_url = f'https://www.iesdouyin.com/share/{aweme_type}/{video_id}'
        response = requests.get(canonical_url, headers=HEADERS)
        response.raise_for_status()

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频信息失败")

        # 解析JSON数据
        json_data = json.loads(find_res.group(1).strip())
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_video_info["item_list"][0]

        desc = data.get("desc", "").strip() or f"douyin_{video_id}"
        # 替换文件名中的非法字符
        desc_safe = re.sub(r'[\\/:*?"<>|]', '_', desc)

        # 区分视频帖 vs 图文帖
        # 关键：图文帖**也可能有 video 字段**（抖音自动生成的幻灯片版本）
        # 所以必须**先检查 images**，再 fallback 到 video
        # 另一个强信号：item["aweme_type"] == 2 是图文
        images_field = data.get("images") or []
        aweme_type_int = data.get("aweme_type")
        is_image_post = (
            (isinstance(images_field, list) and len(images_field) > 0)
            or aweme_type_int in (2, 68, 150)  # 已知的图文 aweme_type
        )

        if is_image_post and images_field:
            # 图文帖：每个 image 对象都有 url_list
            image_urls = []
            for img in images_field:
                img_urls = img.get("url_list") or []
                if img_urls:
                    image_urls.append(img_urls[0])
            return {
                "post_type": "tuwen",
                "url": "",
                "title": desc_safe,
                "video_id": video_id,
                "desc": desc,
                "images": image_urls,
                "image_count": len(image_urls),
                "aweme_type": aweme_type_int,
            }

        # 走到这里说明不是图文：要么是视频，要么是异常
        video_field = data.get("video") or {}
        play_addr = video_field.get("play_addr") or {}
        url_list = play_addr.get("url_list") or []

        if url_list:
            # 视频帖
            video_url = url_list[0].replace("playwm", "play")
            return {
                "post_type": "video",
                "url": video_url,
                "title": desc_safe,
                "video_id": video_id,
                "desc": desc,
                "aweme_type": aweme_type_int,
            }
        else:
            raise Exception(
                f"既无视频也无图文数据。aweme_type={aweme_type_int}, "
                f"data keys: {list(data.keys())}"
            )

    def download_video(self, video_info: dict, output_dir: Optional[Path] = None, show_progress: bool = True) -> Path:
        """下载视频"""
        if output_dir is None:
            output_dir = self.temp_dir
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{video_info['video_id']}.mp4"
        filepath = output_dir / filename

        if show_progress:
            print(f"正在下载视频: {video_info['title']}")

        response = requests.get(video_info['url'], headers=HEADERS, stream=True)
        response.raise_for_status()

        # 获取文件大小
        total_size = int(response.headers.get('content-length', 0))

        # 下载文件
        downloaded = 0
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if show_progress and total_size > 0:
                        progress = downloaded / total_size * 100
                        print(f"\r下载进度: {progress:.1f}%", end="", flush=True)

        if show_progress:
            print(f"\n视频下载完成: {filepath}")
        return filepath

    def extract_audio(self, video_path: Path, show_progress: bool = True) -> Path:
        """从视频文件中提取音频"""
        audio_path = video_path.with_suffix('.mp3')

        if show_progress:
            print("正在提取音频...")
        try:
            (
                ffmpeg
                .input(str(video_path))
                .output(str(audio_path), acodec='libmp3lame', q=0)
                .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
            )
            if show_progress:
                print(f"音频提取完成: {audio_path}")
            return audio_path
        except Exception as e:
            raise Exception(f"提取音频时出错: {str(e)}")

    def get_audio_info(self, audio_path: Path) -> dict:
        """获取音频文件信息（时长和大小）"""
        try:
            probe = ffmpeg.probe(str(audio_path))
            duration = float(probe['format'].get('duration', 0))
            size = audio_path.stat().st_size
            return {'duration': duration, 'size': size}
        except Exception:
            return {'duration': 0, 'size': audio_path.stat().st_size}

    def split_audio(self, audio_path: Path, segment_duration: int = 600, show_progress: bool = True) -> list:
        """
        将音频分割成多个片段

        参数:
            audio_path: 音频文件路径
            segment_duration: 每段时长（秒），默认 10 分钟
            show_progress: 是否显示进度

        返回:
            分割后的音频文件路径列表
        """
        audio_info = self.get_audio_info(audio_path)
        duration = audio_info['duration']

        if duration <= segment_duration:
            return [audio_path]

        segments = []
        segment_index = 0
        current_time = 0

        if show_progress:
            total_segments = int(duration / segment_duration) + 1
            print(f"音频时长 {duration:.0f} 秒，将分割为 {total_segments} 段...")

        while current_time < duration:
            segment_path = self.temp_dir / f"segment_{segment_index}.mp3"

            try:
                (
                    ffmpeg
                    .input(str(audio_path), ss=current_time, t=segment_duration)
                    .output(str(segment_path), acodec='libmp3lame', q=0)
                    .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
                )
                segments.append(segment_path)

                if show_progress:
                    print(f"  分割片段 {segment_index + 1}: {current_time:.0f}s - {min(current_time + segment_duration, duration):.0f}s")

            except Exception as e:
                raise Exception(f"分割音频片段 {segment_index} 时出错: {str(e)}")

            current_time += segment_duration
            segment_index += 1

        return segments

    def transcribe_single_audio(self, audio_path: Path) -> str:
        """转录单个音频文件"""
        files = {
            'file': (audio_path.name, open(audio_path, 'rb'), 'audio/mpeg'),
            'model': (None, self.model)
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            response = requests.post(self.api_base_url, files=files, headers=headers)
            response.raise_for_status()

            result = response.json()
            if 'text' in result:
                return result['text']
            else:
                return response.text

        except Exception as e:
            raise Exception(f"提取文字时出错: {str(e)}")
        finally:
            files['file'][1].close()

    def extract_text_from_audio(self, audio_path: Path, show_progress: bool = True) -> str:
        """从音频文件中提取文字（支持大文件自动分段）"""
        if not self.api_key:
            raise ValueError("未设置 API 密钥，请设置环境变量 DOUYIN_API_KEY")

        # 检查文件大小和时长
        audio_info = self.get_audio_info(audio_path)
        max_duration = 3600  # 1 小时
        max_size = 50 * 1024 * 1024  # 50MB

        # 判断是否需要分段
        need_split = audio_info['duration'] > max_duration or audio_info['size'] > max_size

        if not need_split:
            # 文件在限制范围内，直接处理
            if show_progress:
                print("正在识别语音...")
            return self.transcribe_single_audio(audio_path)

        # 需要分段处理
        if show_progress:
            print(f"音频文件较大（时长: {audio_info['duration']:.0f}秒, 大小: {audio_info['size'] / 1024 / 1024:.1f}MB）")
            print("将自动分段处理...")

        # 分割音频
        segments = self.split_audio(audio_path, segment_duration=540, show_progress=show_progress)  # 9分钟一段，留余量

        # 逐段转录
        all_texts = []
        for i, segment_path in enumerate(segments):
            if show_progress:
                print(f"正在识别第 {i + 1}/{len(segments)} 段...")

            text = self.transcribe_single_audio(segment_path)
            all_texts.append(text)

            # 清理分段文件
            if segment_path != audio_path:
                self.cleanup_files(segment_path)

        # 合并文本
        merged_text = ''.join(all_texts)

        if show_progress:
            print(f"语音识别完成，共处理 {len(segments)} 个片段")

        return merged_text

    def cleanup_files(self, *file_paths: Path):
        """清理指定的文件"""
        for file_path in file_paths:
            if file_path.exists():
                file_path.unlink()


def get_video_info(share_link: str) -> dict:
    """获取视频信息和下载链接"""
    processor = DouyinProcessor()
    return processor.parse_share_url(share_link)


def download_video(share_link: str, output_dir: str = ".") -> Path:
    """下载视频到指定目录"""
    processor = DouyinProcessor()
    video_info = processor.parse_share_url(share_link)
    return processor.download_video(video_info, Path(output_dir))


def extract_text(share_link: str, api_key: Optional[str] = None, output_dir: Optional[str] = None,
                 save_video: bool = False, show_progress: bool = True) -> dict:
    """
    从视频中提取文案并保存到文件

    返回:
        dict: 包含 video_info, text, output_path 的字典
        如果是图文帖子，video_info 含 post_type="tuwen" 和 images 列表，
        text = desc（图文本身没有音频，没有 ASR）
    """
    api_key = api_key or os.getenv('API_KEY')

    processor = DouyinProcessor(api_key or "")

    if show_progress:
        print("正在解析抖音分享链接...")
    video_info = processor.parse_share_url(share_link)

    # 图文帖：没有视频，没有音频。对每张图片做 OCR，拼成 markdown
    if video_info.get("post_type") == "tuwen":
        images = video_info.get("images", []) or []
        if show_progress:
            print(f"检测到图文帖（{len(images)} 张图）")

        images_markdown = ""
        if api_key and images:
            if show_progress:
                print(f"开始并行 OCR（最多 5 并发）...")
            ocr_texts = ocr_images_parallel(images, api_key, show_progress=show_progress)
            images_markdown = build_tuwen_markdown(ocr_texts)
            if show_progress:
                print(f"OCR 完成，{sum(len(t) for t in ocr_texts)} 字")
        elif not api_key and show_progress:
            print("未配置 API_KEY，跳过 OCR（图片 URL 仍返回，但无文字提取）")

        # text 字段：desc + OCR 拼起来，给下游 AI/查询用
        desc = video_info.get("desc", "")
        if images_markdown:
            text_combined = f"{desc}\n\n---\n\n## 图片内容\n\n{images_markdown}"
        else:
            text_combined = desc

        enriched = {**video_info, "images_markdown": images_markdown}
        return {
            "video_info": enriched,
            "text": text_combined,
            "output_path": None,
        }

    # 视频帖：原流程（需要 API_KEY 才能跑 ASR）
    if not api_key:
        raise ValueError("视频帖需要 API_KEY（图文帖不需要）。请先获取硅基流动 API 密钥")

    if show_progress:
        print("正在下载视频...")
    video_path = processor.download_video(video_info, show_progress=show_progress)

    if show_progress:
        print("正在提取音频...")
    audio_path = processor.extract_audio(video_path, show_progress=show_progress)

    if show_progress:
        print("正在从音频中提取文本...")
    text_content = processor.extract_text_from_audio(audio_path, show_progress=show_progress)

    result = {
        "video_info": video_info,
        "text": text_content,
        "output_path": None
    }

    # 保存到文件
    if output_dir:
        output_base = Path(output_dir)
        video_folder = output_base / video_info['video_id']
        video_folder.mkdir(parents=True, exist_ok=True)

        # 保存文案为 Markdown 格式
        transcript_path = video_folder / "transcript.md"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(f"# {video_info['title']}\n\n")
            f.write(f"| 属性 | 值 |\n")
            f.write(f"|------|----|\n")
            f.write(f"| 视频ID | `{video_info['video_id']}` |\n")
            f.write(f"| 提取时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n")
            f.write(f"| 下载链接 | [点击下载]({video_info['url']}) |\n\n")
            f.write(f"---\n\n")
            f.write(f"## 文案内容\n\n")
            f.write(text_content)

        result["output_path"] = str(video_folder)

        if show_progress:
            print(f"文案已保存到: {transcript_path}")

        # 保存视频 (可选)
        if save_video:
            saved_video_path = video_folder / f"{video_info['video_id']}.mp4"
            shutil.copy2(video_path, saved_video_path)
            if show_progress:
                print(f"视频已保存到: {saved_video_path}")

    # 清理临时文件
    if show_progress:
        print("正在清理临时文件...")
    processor.cleanup_files(video_path, audio_path)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="抖音无水印视频下载和文案提取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取视频信息和下载链接
  python douyin_downloader.py --link "抖音分享链接" --action info

  # 下载视频
  python douyin_downloader.py --link "抖音分享链接" --action download --output ./videos

  # 提取文案并保存到文件 (需要设置 DOUYIN_API_KEY 环境变量)
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output

  # 提取文案并同时保存视频
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output --save-video
        """
    )

    parser.add_argument("--link", "-l", required=True, help="抖音分享链接或包含链接的文本")
    parser.add_argument("--action", "-a", choices=["info", "download", "extract"],
                        default="info", help="操作类型: info(获取信息), download(下载视频), extract(提取文案)")
    parser.add_argument("--output", "-o", default="./output", help="输出目录 (默认 ./output)")
    parser.add_argument("--api-key", "-k", help="硅基流动 API 密钥 (也可通过 DOUYIN_API_KEY 环境变量设置)")
    parser.add_argument("--save-video", "-v", action="store_true", help="提取文案时同时保存视频")
    parser.add_argument("--quiet", "-q", action="store_true", help="安静模式，减少输出")

    args = parser.parse_args()

    try:
        if args.action == "info":
            info = get_video_info(args.link)
            print("\n" + "=" * 50)
            print("视频信息:")
            print("=" * 50)
            print(f"视频ID: {info['video_id']}")
            print(f"标题: {info['title']}")
            print(f"下载链接: {info['url']}")
            print("=" * 50)

        elif args.action == "download":
            video_path = download_video(args.link, args.output)
            print(f"\n视频已保存到: {video_path}")

        elif args.action == "extract":
            result = extract_text(
                args.link,
                args.api_key,
                output_dir=args.output,
                save_video=args.save_video,
                show_progress=not args.quiet
            )

            if not args.quiet:
                print("\n" + "=" * 50)
                print("提取完成!")
                print("=" * 50)
                print(f"视频ID: {result['video_info']['video_id']}")
                print(f"标题: {result['video_info']['title']}")
                if result['output_path']:
                    print(f"保存位置: {result['output_path']}")
                print("=" * 50)
                print("\n文案内容:\n")
                print(result['text'][:500] + "..." if len(result['text']) > 500 else result['text'])
                print("\n" + "=" * 50)

    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
