"""Resolve numeric Max IDs to human-readable names via WebSocket RPC."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.max_client import MaxClient

log = logging.getLogger(__name__)


class ContactResolver:
    def __init__(self, client: MaxClient | None = None):
        self.chats: dict[Any, str] = {}
        self.chat_types: dict[Any, str] = {}
        self.users: dict[Any, str] = {}
        self._client = client
        self._fetch_failed: set = set()
        self._my_id: Any = None

    def chat_name(self, chat_id: Any) -> str:
        return self.chats.get(chat_id, str(chat_id))

    def is_dm(self, chat_id: Any) -> bool:
        return self.chat_types.get(chat_id) == "DIALOG"

    def user_name(self, user_id: Any) -> str:
        return self.users.get(user_id, str(user_id))

    async def resolve_user(self, user_id: Any) -> str:
        if user_id in self.users:
            return self.users[user_id]
        if user_id in self._fetch_failed:
            return str(user_id)

        await self._ws_fetch_contacts([user_id])

        if user_id in self.users:
            return self.users[user_id]
        self._fetch_failed.add(user_id)
        return str(user_id)

    async def resolve_users_batch(self, user_ids: list) -> None:
        """Pre-fetch a batch of unknown user IDs in one WS call."""
        unknown = [uid for uid in user_ids if uid not in self.users and uid not in self._fetch_failed]
        if unknown:
            await self._ws_fetch_contacts(unknown)

    # ── populate from AUTH_SNAPSHOT ────────────────────────────────

    def load_snapshot(self, snapshot: dict) -> list:
        profile = snapshot.get("profile", {})
        self._my_id = profile.get("id")
        names = profile.get("names", [])
        if names and self._my_id:
            n = names[0]
            first = n.get("firstName", "")
            last = n.get("lastName", "")
            self.users[self._my_id] = f"{first} {last}".strip() or n.get("name", "")

        all_participant_ids: set[int] = set()

        for chat in snapshot.get("chats", []):
            cid = chat.get("id")
            ctype = chat.get("type")
            title = chat.get("title")

            if cid is None:
                continue

            if ctype:
                self.chat_types[cid] = ctype

            if title:
                self.chats[cid] = title

            participants = chat.get("participants", {})
            for uid_str in participants:
                try:
                    all_participant_ids.add(int(uid_str))
                except (ValueError, TypeError):
                    pass

            if not title and ctype == "DIALOG" and self._my_id:
                peer_id = None
                for uid in participants:
                    try:
                        uid_int = int(uid)
                    except (ValueError, TypeError):
                        continue
                    if uid_int != self._my_id:
                        peer_id = uid_int
                        break
                if peer_id:
                    self.chats[cid] = f"DM:{peer_id}"

        log.info(
            "Snapshot parsed: %d chats, my_id=%s, %d participant IDs to resolve",
            len(self.chats), self._my_id, len(all_participant_ids),
        )
        return list(all_participant_ids)

    # ── WebSocket contact fetch ────────────────────────────────────

    async def _ws_fetch_contacts(self, user_ids: list) -> None:
        if not self._client:
            return
        try:
            resp = await self._client.fetch_contacts(user_ids)
            self._parse_contacts_response(resp)
        except Exception:
            log.exception("Failed to fetch contacts via WS")

    def _parse_contacts_response(self, resp: dict) -> None:
        """Parse the response from opcode 32 (CONTACT_GET)."""
        if not resp:
            return

        contacts = resp.get("contacts") or resp.get("users") or []
        if isinstance(contacts, dict):
            contacts = contacts.values()

        for c in contacts:
            if not isinstance(c, dict):
                continue
            uid = c.get("id") or c.get("userId")
            name = self._extract_name_from_contact(c)
            if uid is not None and name:
                self.users[uid] = name
                log.info("Resolved contact %s → %s", uid, name)

        # Maybe the response IS the contact (single user)
        if not contacts and resp.get("id"):
            uid = resp.get("id")
            name = self._extract_name_from_contact(resp)
            if uid and name:
                self.users[uid] = name
                log.info("Resolved contact %s → %s", uid, name)

        # Walk the entire response for any name-bearing objects
        self._deep_extract(resp, depth=0)

    def _deep_extract(self, obj: Any, depth: int) -> None:
        if depth > 5:
            return
        if isinstance(obj, dict):
            uid = obj.get("id") or obj.get("userId")
            name = self._extract_name_from_contact(obj)
            if uid is not None and name and uid not in self.users:
                self.users[uid] = name
                log.info("Deep-resolved contact %s → %s", uid, name)
            for v in obj.values():
                self._deep_extract(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._deep_extract(item, depth + 1)

    @staticmethod
    def _extract_name_from_contact(c: dict) -> str:
        # Max stores names in a "names" array: [{firstName, lastName, name, type}]
        names_list = c.get("names")
        if isinstance(names_list, list) and names_list:
            n = names_list[0]
            first = n.get("firstName", "")
            last = n.get("lastName", "")
            if first or last:
                return f"{first} {last}".strip()
            if n.get("name"):
                return str(n["name"])

        first = c.get("firstName") or c.get("first_name") or ""
        last = c.get("lastName") or c.get("last_name") or ""
        if first or last:
            return f"{first} {last}".strip()

        return str(c.get("friendly") or c.get("displayName") or c.get("name") or "")
