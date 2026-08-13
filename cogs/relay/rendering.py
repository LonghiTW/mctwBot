"""Rendering helpers for relay message content, embeds, and attachments."""
import re
from discord import Embed, Message

_DISCORD_MSG_LIMIT = 2000

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


def is_image_attachment(attachment) -> bool:
    content_type = getattr(attachment, "content_type", None) or ""
    if content_type.startswith("image/"):
        return True
    filename = getattr(attachment, "filename", "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
