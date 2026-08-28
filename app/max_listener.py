import logging
import time
from datetime import datetime
from html import escape
from typing import Any

from app.max_client import MaxClient, MaxMessage, OpCode
from app.resolver import ContactResolver
from app.tg_sender import ScopedSender, SendStatus, TelegramSender, reply_keyboard

log = logging.getLogger(__name__)

AUTH_ALERT_INTERVAL_SEC = 3600

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _header(msg: MaxMessage, sender_label: str, chat_label: str, is_dm: bool) -> str:
    if is_dm:
        return f"✉ <b>{sender_label}</b>"
    if chat_label:
        return f"💬 <b>{chat_label}</b> | {sender_label}"
    return f"💬 <b>{sender_label}</b>"


def _log_delivery(status: SendStatus, what: str) -> None:
    """Log the real outcome of a forward — never claim a delivery that did not happen."""
    if status is SendStatus.OK:
        log.info("Forwarded %s → TG", what)
    elif status is SendStatus.QUEUED:
        log.warning("NOT delivered to TG yet, queued for retry: %s", what)
    else:
        log.error("LOST, Telegram rejected: %s", what)


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
    sender: TelegramSender | ScopedSender,
    header_text: str,
    chat_id: Any,
    message_id: Any,
    kb=None,
) -> SendStatus | None:
    """Process and send a single attachment. Returns its send status, or None if skipped."""
    atype = attach.get("_type", "")
    log.info("Processing attach _type=%s keys=%s", atype, list(attach.keys()))

    if atype == "CONTROL" or atype == "WIDGET" or atype == "INLINE_KEYBOARD":
        return None

    if atype == "PHOTO":
        url = _extract_photo_url(attach)
        if not url:
            log.warning("PHOTO attach has no URL: %s", attach)
            return None
        data = await client.download_file(url)
        if data:
            return await sender.send_photo(data, caption=header_text, reply_markup=kb)
        return await sender.send(f"{header_text}\n<i>[фото — не удалось загрузить]</i>", reply_markup=kb)

    if atype == "VIDEO":
        thumb = attach.get("thumbnail")
        if thumb:
            data = await client.download_file(thumb)
            if data:
                return await sender.send_photo(data, caption=f"{header_text}\n<i>[видео — превью]</i>", reply_markup=kb)
        return await sender.send(f"{header_text}\n<i>[видео]</i>", reply_markup=kb)

    if atype == "FILE":
        name = attach.get("name", "file")
        size = attach.get("size", 0)
        token_url = _extract_file_url(attach)
        file_id = attach.get("fileId")
        if not token_url and file_id and chat_id and message_id:
            log.info("Get url by fileId chatId=%s fileId=%s messageId=%s", chat_id, file_id, message_id)
            resp = await client.cmd(
                OpCode.GET_FILE_URL,
                {
                    "chatId": chat_id,
                    "fileId": file_id,
                    "messageId": message_id,
                },
            )
            token_url = resp.get("url")
            log.info("Got url by fileId: %s", token_url)
        if token_url:
            data = await client.download_file(token_url)
            if data:
                kind = _guess_media_kind(name)
                if kind == "photo":
                    return await sender.send_photo(data, caption=header_text, filename=name, reply_markup=kb)
                if kind == "video":
                    return await sender.send_video(data, caption=header_text, filename=name, reply_markup=kb)
                return await sender.send_document(data, caption=header_text, filename=name, reply_markup=kb)
        size_str = f" ({_human_size(size)})" if size else ""
        return await sender.send(f"{header_text}\n📎 <b>{escape(name)}</b>{size_str}", reply_markup=kb)

    if atype == "AUDIO":
        url = attach.get("url")
        if url:
            data = await client.download_file(url)
            if data:
                return await sender.send_voice(data, caption=header_text, reply_markup=kb)
        return await sender.send(f"{header_text}\n<i>[аудио]</i>", reply_markup=kb)

    if atype == "STICKER":
        url = attach.get("url")
        if url:
            data = await client.download_file(url)
            if data:
                return await sender.send_sticker(data, reply_markup=kb)
        return await sender.send(f"{header_text}\n<i>[стикер]</i>", reply_markup=kb)

    if atype == "SHARE":
        return await sender.send(header_text, reply_markup=kb)

    if atype == "LOCATION":
        lat = attach.get("lat") or attach.get("latitude")
        lon = attach.get("lon") or attach.get("lng") or attach.get("longitude")
        if lat and lon:
            return await sender.send(f"{header_text}\n📍 {lat}, {lon}", reply_markup=kb)
        return await sender.send(f"{header_text}\n<i>[геолокация]</i>", reply_markup=kb)

    if atype == "CONTACT":
        name = attach.get("name", "")
        phone = attach.get("phone", "")
        text = f"{header_text}\n👤 {escape(name)}"
        if phone:
            text += f" — {escape(phone)}"
        return await sender.send(text, reply_markup=kb)

    if atype == "POLL":
        title = attach.get("title", "Опрос")
        options = [
            answer.get("text") for answer in attach.get("answers", [])
            if isinstance(answer, dict) and answer.get("text")
        ]
        if len(options) >= 2:
            return await sender.send_poll(f"{header_text}\n{escape(title)}", options, reply_markup=kb)
        log.warning("POLL attach has too few valid options: %s", attach)
        return await sender.send(f"{header_text}\n📊 <b>{escape(title)}</b>\n<i>[опрос — не удалось переслать]</i>", reply_markup=kb)

    log.info("Unknown attach type %s, sending as info", atype)
    return await sender.send(f"{header_text}\n<i>[вложение: {escape(atype or 'unknown')}]</i>", reply_markup=kb)


async def _handle_forward_message(
    link: dict,
    header_text: str,
    client: MaxClient,
    sender: TelegramSender | ScopedSender,
    resolver: ContactResolver,
    kb=None,
) -> SendStatus:
    """Handle FORWARD link inside a message. Returns the worst status of its sends."""
    fwd_meaningful, fwd_sender_label, fwd_text = await _parse_link(link, resolver)

    prefix = "↩️ <b>Переслано</b>"
    if fwd_sender_label:
        prefix = f"↩️ <b>Переслано от {fwd_sender_label}</b>"

    full_header = f"{header_text}\n{prefix}"
    statuses: list[SendStatus] = []
    if fwd_meaningful:
        text_sent = False
        for i, attach in enumerate(fwd_meaningful):
            if i == 0 and fwd_text:
                cap = f"{full_header}\n{escape(fwd_text)}"
                text_sent = True
            else:
                cap = full_header
            status = await _send_attach(attach, client, sender, cap, None, None, kb=kb)
            if status is not None:
                statuses.append(status)

        if fwd_text and not text_sent:
            statuses.append(await sender.send(f"{full_header}\n{escape(fwd_text)}", reply_markup=kb))
    elif fwd_text:
        statuses.append(await sender.send(f"{full_header}\n{escape(fwd_text)}", reply_markup=kb))
    else:
        statuses.append(await sender.send(f"{full_header}\n<i>[без содержимого]</i>", reply_markup=kb))
    return SendStatus.worst(*statuses)


async def _handle_reply_message(
    link: dict,
    header_text: str,
    resolver: ContactResolver,
):
    fwd_meaningful, fwd_sender_label, fwd_text = await _parse_link(link, resolver)

    prefix = "↩ <b>Ответ</b>"
    if fwd_sender_label:
        prefix = f"↩ <b>Ответ на {fwd_sender_label}</b>"

    full_header = f"{header_text}\n{prefix}"
    attaches_str = ""
    if fwd_meaningful:
        for fwd_attach in fwd_meaningful:
            name = fwd_attach.get("name", fwd_attach.get("_type", "file").lower())
            size = fwd_attach.get("size", 0)
            size_str = f" ({_human_size(size)})" if size else ""
            attaches_str += f"📎 <b>{escape(name)}</b>{size_str}\n"
    return attaches_str, full_header, fwd_text


async def _parse_link(link: dict, resolver: ContactResolver):
    inner = link.get("message") or link
    fwd_sender_id = inner.get("sender") or link.get("sender")
    fwd_text = inner.get("text", "") or link.get("text", "")
    fwd_attaches = inner.get("attaches") or link.get("attaches") or []
    fwd_sender_label = ""
    if fwd_sender_id:
        fwd_sender_label = escape(await resolver.resolve_user(fwd_sender_id))
    if fwd_sender_label == "" and (chat_name := link.get("chatName", "")):
        fwd_sender_label = escape(chat_name)
    fwd_meaningful = [
        a for a in fwd_attaches
        if isinstance(a, dict) and a.get("_type") not in ("CONTROL", "WIDGET", "INLINE_KEYBOARD", None)
    ]
    return fwd_meaningful, fwd_sender_label, fwd_text

def _human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def create_max_client(
    max_token: str, max_device_id: str, sender: TelegramSender, max_chat_ids: str | None = None,
    debug: bool = False, reply_enabled: bool = False,
) -> MaxClient:
    client = MaxClient(token=max_token, device_id=max_device_id, debug=debug, chat_ids=max_chat_ids)
    resolver = ContactResolver(client=client)

    _first_connect = True
    _notif_count = 0
    _last_notif_time: datetime | None = None
    _last_auth_alert: datetime | None = None

    def _can_notify() -> bool:
        if _last_notif_time is None:
            return True
        elapsed = (datetime.now() - _last_notif_time).total_seconds()
        if _notif_count == 1:
            return elapsed >= 3600    # 2-е: через 1 час
        if _notif_count == 2:
            return elapsed >= 10800   # 3-е: через 3 часа
        return elapsed >= 86400       # 4-е и далее: раз в сутки

    @client.on_ready
    async def handle_ready(snapshot: dict):
        nonlocal _first_connect
        participant_ids = resolver.load_snapshot(snapshot)

        if participant_ids:
            log.info("Batch-resolving %d participants...", len(participant_ids))
            await resolver.resolve_users_batch(participant_ids)
            log.info("Resolved users: %s", resolver.users)

            log.info("Known chats: %s", resolver.chats)
            log.info("Known users: %s", resolver.users)

        if not _first_connect:
            await sender.send_status("✅ <b>Max:</b> соединение восстановлено")
            # Re-read everything sent while we were away. The window covers the whole gap,
            # not just the reconnect delay; duplicates are filtered by id in MaxClient.
            now = time.time()
            gap_start = client.gap_since or (now - client.RECONNECT_SEC)
            gap_start = max(gap_start - client.CATCHUP_OVERLAP_SEC, now - client.CATCHUP_MAX_SEC)
            log.info("Catching up on %.0fs since the disconnect...", now - gap_start)
            chat_ids = client.chat_ids or [*resolver.chats, *resolver.users]
            recovered = 0
            for chat_id in chat_ids:
                resp = await client.cmd(OpCode.GET_MESSAGES, {
                    "chatId": chat_id,
                    "from": int(gap_start * 1000),
                    "forward": client.CATCHUP_LIMIT,
                    "backward": 0,
                    "getMessages": True
                })
                messages = resp.get("messages", [])
                recovered += len(messages)
                for message in messages:
                    client.process_message({"message": message, "chatId": chat_id})
            log.info("Catch-up: re-read %d message(s) from %d chat(s)", recovered, len(chat_ids))
        _first_connect = False

    @client.on_disconnect
    async def handle_disconnect():
        nonlocal _notif_count, _last_notif_time
        if not _can_notify():
            log.info("Disconnect notification suppressed (throttle)")
            return
        _notif_count += 1
        _last_notif_time = datetime.now()
        await sender.send_status("⚠️ <b>Max:</b> соединение потеряно, переподключение...")

    @client.on_auth_error
    async def handle_auth_error(payload: dict):
        nonlocal _last_auth_alert
        log.error("Max authorization failed: %s", payload)
        now = datetime.now()
        if _last_auth_alert and (now - _last_auth_alert).total_seconds() < AUTH_ALERT_INTERVAL_SEC:
            return
        _last_auth_alert = now
        reason = payload.get("error") or payload.get("message") if isinstance(payload, dict) else None
        await sender.send_status(
            "⛔️ <b>Max:</b> ошибка авторизации — обновите <code>MAX_TOKEN</code>"
            + (f"\n<i>{escape(str(reason))}</i>" if reason else "")
        )

    @client.on_message
    async def handle_message(msg: MaxMessage):
        log.info(
            "New message: chat=%s sender=%s is_self=%s text=%r attaches=%d",
            msg.chat_id,
            msg.sender_id,
            msg.is_self,
            (msg.text[:80] + "…") if len(msg.text) > 80 else msg.text,
            len(msg.attaches),
        )

        if msg.is_self:
            return

        sender_label = escape(await resolver.resolve_user(msg.sender_id))
        is_dm = resolver.is_dm(msg.chat_id)
        if len(client.chat_ids) == 1:
            chat_label = ""
        else:
            chat_label = escape(resolver.chat_name(msg.chat_id))
        header_text = _header(msg, sender_label, chat_label, is_dm)
        kb = reply_keyboard(msg.chat_id) if reply_enabled else None
        # Only recipients subscribed to this Max chat see what follows;
        # status notices keep using the unscoped sender and reach everyone.
        scoped = sender.for_max_chat(msg.chat_id)

        link = msg.link
        link_type = link.get("type") if isinstance(link, dict) else None

        if link_type == "FORWARD":
            status = await _handle_forward_message(link, header_text, client, scoped, resolver, kb=kb)
            if msg.text:
                status = SendStatus.worst(status, await scoped.send(f"{header_text}\n{escape(msg.text)}", reply_markup=kb))
            _log_delivery(status, "forwarded message")
            return

        reply = ""
        if link_type == "REPLY":
            attaches_str, full_header, fwd_text = await _handle_reply_message(link, header_text, resolver)
            header_text = full_header
            reply = f"<blockquote>{escape(fwd_text)}\n{attaches_str}</blockquote>"

        meaningful_attaches = [
            a for a in msg.attaches
            if isinstance(a, dict) and a.get("_type") not in ("CONTROL", "WIDGET", "INLINE_KEYBOARD", None)
        ]

        if meaningful_attaches:
            text_sent = False
            for i, attach in enumerate(meaningful_attaches):
                if i == 0:
                    cap = f"{header_text}\n{reply}{escape(msg.text)}"
                    text_sent = bool(msg.text)
                else:
                    cap = header_text
                status = await _send_attach(attach, client, scoped, cap, msg.chat_id, msg.message_id, kb=kb)
                if status is not None:
                    _log_delivery(status, f"attach _type={attach.get('_type')}")

            if msg.text and not text_sent:
                _log_delivery(await scoped.send(f"{header_text}\n{reply}{escape(msg.text)}", reply_markup=kb), "text")
        else:
            if msg.text:
                _log_delivery(await scoped.send(f"{header_text}\n{reply}{escape(msg.text)}", reply_markup=kb), "text")
            else:
                log.warning("Нетекстовое сообщение! %s", msg.attaches)

    return client
