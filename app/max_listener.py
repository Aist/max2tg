import logging
from html import escape
from typing import Awaitable, Callable

from app.max_client import MaxClient, MaxMessage
from app.resolver import ContactResolver
from app.tg_sender import TelegramSender, reply_keyboard

log = logging.getLogger(__name__)

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _header(sender_label: str, chat_label: str, is_dm: bool, account_label: str = "") -> str:
    account_part = f"👾 <b>{escape(account_label)}</b>" if account_label else ""
    if is_dm:
        return f"{account_part} | {sender_label}" if account_part else sender_label
    return f"{account_part} 💬 <b>{chat_label}</b> | {sender_label}" if account_part else f"💬 <b>{chat_label}</b> | {sender_label}"


def _extract_photo_url(attach: dict) -> str | None:
    """Extract the best available URL for a PHOTO attachment."""
    return attach.get("baseUrl") or attach.get("url")


def _extract_file_url(attach: dict) -> str | None:
    """Extract download URL for a FILE attachment (url field takes priority)."""
    url = attach.get("url")
    if url and url.startswith("http"):
        return url
    return None


def _guess_media_kind(filename: str) -> str:
    name_lower = filename.lower()
    for ext in PHOTO_EXTENSIONS:
        if name_lower.endswith(ext):
            return "photo"
    for ext in VIDEO_EXTENSIONS:
        if name_lower.endswith(ext):
            return "video"
    return "document"


async def _send_attach(
    attach: dict,
    client: MaxClient,
    sender: TelegramSender,
    tg_user_id: int,
    header_text: str,
    kb=None,
) -> bool:
    """Process and send a single attachment. Returns True if handled."""
    atype = attach.get("_type", "")
    log.info("Processing attach _type=%s keys=%s", atype, list(attach.keys()))

    if atype == "CONTROL" or atype == "WIDGET" or atype == "INLINE_KEYBOARD":
        return False

    if atype == "PHOTO":
        url = _extract_photo_url(attach)
        if not url:
            log.warning("PHOTO attach has no URL: %s", attach)
            return False
        data = await client.download_file(url)
        if data:
            await sender.send_photo(tg_user_id, data, caption=header_text, reply_markup=kb)
            return True
        await sender.send(tg_user_id, f"{header_text}\n<i>[фото — не удалось загрузить]</i>", reply_markup=kb)
        return True

    if atype == "VIDEO":
        thumb = attach.get("thumbnail")
        if thumb:
            data = await client.download_file(thumb)
            if data:
                await sender.send_photo(
                    tg_user_id, data, caption=f"{header_text}\n<i>[видео — превью]</i>", reply_markup=kb
                )
                return True
        await sender.send(tg_user_id, f"{header_text}\n<i>[видео]</i>", reply_markup=kb)
        return True

    if atype == "FILE":
        name = attach.get("name", "file")
        size = attach.get("size", 0)
        token_url = _extract_file_url(attach)
        if token_url:
            data = await client.download_file(token_url)
            if data:
                kind = _guess_media_kind(name)
                if kind == "photo":
                    await sender.send_photo(tg_user_id, data, caption=header_text, filename=name, reply_markup=kb)
                elif kind == "video":
                    await sender.send_video(tg_user_id, data, caption=header_text, filename=name, reply_markup=kb)
                else:
                    await sender.send_document(tg_user_id, data, caption=header_text, filename=name, reply_markup=kb)
                return True
        size_str = f" ({_human_size(size)})" if size else ""
        await sender.send(tg_user_id, f"{header_text}\n📎 <b>{escape(name)}</b>{size_str}", reply_markup=kb)
        return True

    if atype == "AUDIO":
        url = attach.get("url")
        if url:
            data = await client.download_file(url)
            if data:
                await sender.send_voice(tg_user_id, data, caption=header_text, reply_markup=kb)
                return True
        await sender.send(tg_user_id, f"{header_text}\n<i>[аудио]</i>", reply_markup=kb)
        return True

    if atype == "STICKER":
        url = attach.get("url")
        if url:
            data = await client.download_file(url)
            if data:
                await sender.send_sticker(tg_user_id, data, reply_markup=kb)
                return True
        await sender.send(tg_user_id, f"{header_text}\n<i>[стикер]</i>", reply_markup=kb)
        return True

    if atype == "SHARE":
        share_url = attach.get("url", "")
        title = attach.get("title", "")
        desc = attach.get("description", "")
        parts = [header_text]
        if title:
            parts.append(f"🔗 <b>{escape(title)}</b>")
        if share_url:
            parts.append(escape(share_url))
        if desc:
            parts.append(f"<i>{escape(desc[:200])}</i>")
        await sender.send(tg_user_id, "\n".join(parts), reply_markup=kb)
        return True

    if atype == "LOCATION":
        lat = attach.get("lat") or attach.get("latitude")
        lon = attach.get("lon") or attach.get("lng") or attach.get("longitude")
        if lat and lon:
            await sender.send(tg_user_id, f"{header_text}\n📍 {lat}, {lon}", reply_markup=kb)
        else:
            await sender.send(tg_user_id, f"{header_text}\n<i>[геолокация]</i>", reply_markup=kb)
        return True

    if atype == "CONTACT":
        name = attach.get("name", "")
        phone = attach.get("phone", "")
        text = f"{header_text}\n👤 {escape(name)}"
        if phone:
            text += f" — {escape(phone)}"
        await sender.send(tg_user_id, text, reply_markup=kb)
        return True

    log.info("Unknown attach type %s, sending as info", atype)
    await sender.send(tg_user_id, f"{header_text}\n<i>[вложение: {escape(atype or 'unknown')}]</i>", reply_markup=kb)
    return True


async def _handle_linked_message(
    link: dict,
    link_type: str,
    header_text: str,
    client: MaxClient,
    sender: TelegramSender,
    resolver: ContactResolver,
    tg_user_id: int,
    kb=None,
) -> None:
    """Handle FORWARD or REPLY link inside a message."""
    inner = link.get("message") or link
    fwd_sender_id = inner.get("sender") or link.get("sender")
    fwd_text = inner.get("text", "") or link.get("text", "")
    fwd_attaches = inner.get("attaches") or link.get("attaches") or []

    fwd_sender_label = ""
    if fwd_sender_id:
        fwd_sender_label = escape(await resolver.resolve_user(fwd_sender_id))

    if link_type == "FORWARD":
        prefix = "↩️ <b>Переслано</b>"
        if fwd_sender_label:
            prefix = f"↩️ <b>Переслано от {fwd_sender_label}</b>"
    else:
        prefix = "↩ <b>Ответ</b>"
        if fwd_sender_label:
            prefix = f"↩ <b>Ответ на {fwd_sender_label}</b>"

    full_header = f"{header_text}\n{prefix}"

    fwd_meaningful = [
        a for a in fwd_attaches
        if isinstance(a, dict) and a.get("_type") not in ("CONTROL", "WIDGET", "INLINE_KEYBOARD", None)
    ]

    if fwd_meaningful:
        text_sent = False
        for i, attach in enumerate(fwd_meaningful):
            if i == 0 and fwd_text:
                cap = f"{full_header}\n{escape(fwd_text)}"
                text_sent = True
            else:
                cap = full_header
            await _send_attach(attach, client, sender, tg_user_id, cap, kb=kb)

        if fwd_text and not text_sent:
            await sender.send(tg_user_id, f"{full_header}\n{escape(fwd_text)}", reply_markup=kb)
    elif fwd_text:
        await sender.send(tg_user_id, f"{full_header}\n{escape(fwd_text)}", reply_markup=kb)
    else:
        await sender.send(tg_user_id, f"{full_header}\n<i>[без содержимого]</i>", reply_markup=kb)


def _human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def create_max_client(
    account_id: int,
    tg_user_id: int,
    max_token: str,
    max_device_id: str,
    sender: TelegramSender,
    stats_callback: Callable[[str], Awaitable[None]] | None = None,
    account_label: str = "",
    debug: bool = False, reply_enabled: bool = False,
) -> MaxClient:
    client = MaxClient(token=max_token, device_id=max_device_id, debug=debug)
    resolver = ContactResolver(client=client)

    @client.on_ready
    async def handle_ready(snapshot: dict):
        participant_ids = resolver.load_snapshot(snapshot)

        if participant_ids:
            log.info("Batch-resolving %d participants...", len(participant_ids))
            await resolver.resolve_users_batch(participant_ids)
            log.info("Resolved users: %s", resolver.users)

            log.info("Known chats: %s", resolver.chats)
            log.info("Known users: %s", resolver.users)

    @client.on_message
    async def handle_message(msg: MaxMessage):
        log.info(
            "New message: chat=%s sender=%s is_self=%s attaches=%d",
            msg.chat_id,
            msg.sender_id,
            msg.is_self,
            len(msg.attaches),
        )

        if msg.is_self:
            return

        resolver.update_chat_from_event(msg.raw, msg.chat_id)
        await resolver.ensure_chat_meta(msg.chat_id)
        sender_name = await resolver.resolve_user(msg.sender_id)
        sender_label = escape(sender_name if sender_name and sender_name != "None" else "Неизвестный")
        is_dm = resolver.is_dm(msg.chat_id)
        chat_label = escape(resolver.chat_name(msg.chat_id))
        header_text = _header(sender_label, chat_label, is_dm, account_label=account_label)
        kb = reply_keyboard(account_id, msg.chat_id, is_dm) if reply_enabled else None

        if stats_callback:
            incoming_metric = "forward_dm" if is_dm else "forward_group"
            try:
                await stats_callback(incoming_metric)
            except Exception:
                log.exception("Failed to write report metric=%s", incoming_metric)

        link = msg.link
        link_type = link.get("type") if isinstance(link, dict) else None

        if link_type in ("FORWARD", "REPLY"):
            await _handle_linked_message(link, link_type, header_text, client, sender, resolver, tg_user_id, kb=kb)
            if msg.text:
                await sender.send(tg_user_id, f"{header_text}\n{escape(msg.text)}", reply_markup=kb)
            log.info("Forwarded link type=%s → TG", link_type)
            return

        meaningful_attaches = [
            a for a in msg.attaches
            if isinstance(a, dict) and a.get("_type") not in ("CONTROL", "WIDGET", "INLINE_KEYBOARD", None)
        ]

        if meaningful_attaches:
            text_sent = False
            for i, attach in enumerate(meaningful_attaches):
                if i == 0 and msg.text:
                    cap = f"{header_text}\n{escape(msg.text)}"
                    text_sent = True
                else:
                    cap = header_text
                await _send_attach(attach, client, sender, tg_user_id, cap, kb=kb)
                log.info("Forwarded attach _type=%s → TG", attach.get("_type"))

            if msg.text and not text_sent:
                await sender.send(tg_user_id, f"{header_text}\n{escape(msg.text)}", reply_markup=kb)
        else:
            body = escape(msg.text) if msg.text else "<i>[нетекстовое сообщение]</i>"
            await sender.send(tg_user_id, f"{header_text}\n{body}", reply_markup=kb)
            log.info("Forwarded text → TG")

    return client
