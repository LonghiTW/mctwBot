"""Rendering helpers for relay message content, embeds, and attachments."""
import re

import aiohttp
from discord import Embed, Message

_DISCORD_MSG_LIMIT = 2000
_MAX_EMBEDS = 10

# Regex to detect Klipy GIF URLs that Discord didn't auto-embed
_KLiPY_RE = re.compile(r'https?://(?:www\.)?klipy\.com/gifs/\S+', re.IGNORECASE)


def build_reply_embed(replied: Message | None, link: str | None = None, deleted: bool = False) -> Embed:
    if deleted or replied is None:
        return Embed(color=0xB0B8C6, description="*↰ original message was deleted*")

    if replied.message_snapshots:
        snap = replied.message_snapshots[0]
        reply_text = f"↱ {format_referenced_message_text(snap.content, snap.attachments)}"[:1000]
    else:
        reply_text = format_referenced_message_text(replied.content, replied.attachments)[:1000]
    if replied.edited_at:
        reply_text += " *(edited)*"

    reply_embed = Embed(color=0xB0B8C6, description=reply_text)
    reply_embed.set_author(
        name=f"Replying to {replied.author.display_name}",
        url=link,
        icon_url=replied.author.display_avatar.url,
    )
    return reply_embed


def format_referenced_message_text(content: str | None, attachments) -> str:
    text = (content or "").strip()
    if attachments:
        return f"🔗 {text}" if text else "🔗 click to see attachment"
    return text or "*(No text)*"


def strip_embed_urls_from_content(content: str, embeds: list) -> str:
    """Remove bare URLs from content that are already represented as rich embeds."""
    embed_urls: set[str] = set()
    for emb in embeds:
        if emb.url:
            embed_urls.add(emb.url.rstrip("/"))
        if emb.image and emb.image.url:
            embed_urls.add(emb.image.url.rstrip("/"))
        if emb.thumbnail and emb.thumbnail.url:
            embed_urls.add(emb.thumbnail.url.rstrip("/"))
    if not embed_urls:
        return content
    for url in sorted(embed_urls, key=len, reverse=True):
        if _KLiPY_RE.fullmatch(url):
            continue
        escaped = re.escape(url)
        content = re.sub(rf"\s*{escaped}\s*", " ", content).strip()
        content = re.sub(r"\s+", " ", content)
    return content


def append_attachment_previews(content: str, embeds: list, attachments) -> tuple[str, list]:
    """Return (content, image_files).

    Image attachments are returned as a list of download items for multipart
    upload (grid layout). Non-image attachments and overflow are appended
    as plain URLs in content.
    """
    image_files: list[dict] = []
    overflow: list[str] = []
    for att in attachments:
        if is_image_attachment(att) and len(image_files) < 10:
            image_files.append({
                "filename": att.filename,
                "url": att.url,  # full signed URL for download
                "content_type": att.content_type or "image/png",
            })
            continue

        line = f"\n{att.url.split('?')[0]}"
        if len(content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
            content += line
        else:
            overflow.append(att.filename)

    if overflow:
        content += f"\n*(Note: {len(overflow)} file(s) too large: {', '.join(overflow)})*"
    return content, image_files


async def resolve_klipy_urls(content: str, embeds: list) -> tuple[str, list]:
    """Find Klipy GIF URLs in content, fetch the actual GIF, add as embeds.

    Discord's GIF picker sometimes sends Klipy links without an embed.
    This fetches the og:image from the Klipy page so we can embed it.
    """
    urls = _KLiPY_RE.findall(content)
    if not urls:
        return content, embeds

    # Build set of already-embedded image URLs to avoid dupes
    existing: set[str] = set()
    for e in embeds:
        img = getattr(e, "image", None)
        if img and img.url:
            existing.add(img.url.rstrip("/"))

    new_embeds = list(embeds)
    resolved: set[str] = set()
    async with aiohttp.ClientSession() as session:
        for url in urls:
            clean_url = url.rstrip("/")
            if clean_url in existing:
                resolved.add(clean_url)
                continue
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    gif_url = None
                    match = re.search(
                        r'<meta\s+property="og:image"\s+content="([^"]+)"',
                        html, re.IGNORECASE,
                    )
                    if match:
                        gif_url = match.group(1)
                    else:
                        match = re.search(
                            r'<meta\s+content="([^"]+)"\s+property="og:image"',
                            html, re.IGNORECASE,
                        )
                        if match:
                            gif_url = match.group(1)
                    if gif_url and len(new_embeds) < _MAX_EMBEDS:
                        # Klipy sometimes serves og:image as .mp4 — Discord
                        # can't auto-play MP4 in an embed image field.
                        # Try to find a static image version instead.
                        if gif_url.lower().endswith('.mp4'):
                            found = False
                            for ext in ('.gif', '.png', '.webp'):
                                test_url = re.sub(r'\.mp4$', ext, gif_url, flags=re.IGNORECASE)
                                try:
                                    async with session.head(
                                        test_url,
                                        timeout=aiohttp.ClientTimeout(total=3),
                                    ) as tresp:
                                        if tresp.status == 200:
                                            gif_url = test_url
                                            found = True
                                            break
                                except Exception:
                                    continue
                            if not found:
                                continue
                        embed = Embed(color=0x2B2D31)
                        embed.set_image(url=gif_url)
                        new_embeds.append(embed)
                        existing.add(gif_url.rstrip("/"))
                        resolved.add(clean_url)
            except Exception:
                pass

    # Only strip Klipy URLs that were successfully resolved
    for url in urls:
        clean_url = url.rstrip("/")
        if clean_url in resolved:
            content = content.replace(url, "").strip()
    content = re.sub(r"\s+", " ", content).strip()
    return content, new_embeds


def is_image_attachment(attachment) -> bool:
    content_type = getattr(attachment, "content_type", None) or ""
    if content_type.startswith("image/"):
        return True
    filename = getattr(attachment, "filename", "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
