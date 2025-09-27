from typing import Dict, Optional
import logging
from telethon import events, Button
from telethon.errors.rpcerrorlist import MessageNotModifiedError, QueryIdInvalidError
from telethon.tl.custom.message import Message
from quadridigest.config import settings
from quadridigest.utils import short_fp

logger = logging.getLogger(__name__)

class Publisher:
    def __init__(self, client):
        self.client = client
        self._store: Dict[str, dict] = {}

        @self.client.on(events.CallbackQuery(pattern=b"more:(.+)"))
        async def on_more(evt: events.CallbackQuery.Event):
            # Acknowledge immediately to avoid query expiration on slow operations
            await self._safe_answer(evt)
            try:
                cid = evt.pattern_match.group(1).decode()
                rec = self._store.get(cid)
                if not rec:
                    return await self._safe_answer(evt, "Не найдено", alert=True)

                ui = (settings.ui_variant or "reply").lower()
                if ui == "edit":
                    await self._toggle_edit(evt, cid, rec)
                else:
                    await self._reply_full(evt, cid, rec)
            except Exception as e:
                logger.exception("Unhandled exception on on_more: %s", e)
                await self._safe_answer(evt, "Ошибка обработки кнопки", alert=True)

    async def _safe_answer(self, evt: events.CallbackQuery.Event, text: Optional[str]=None, alert: bool=False):
        """Reply to callback safely, ignoring duplicate/expired QueryId errors."""
        try:
            await evt.answer(text=text, alert=alert)
        except QueryIdInvalidError:
            # already answered or expired; ignore
            pass
        except Exception:
            pass

    async def post_short(self, channel: str, topic: str, headline: str, summary: str, full_text: str, fingerprint: str) -> int:
        cid = short_fp(fingerprint, 20)
        self._store[cid] = {
            "topic": topic,
            "headline": (headline or "").strip(),
            "summary": (summary or "").strip(),
            "full_text": (full_text or "").strip(),
            "expanded": False,
            "channel": channel,
        }
        buttons = [[Button.inline("Подробнее", data=f"more:{cid}")]]
        msg: Message = await self.client.send_message(
            entity=channel,
            message=f"#{topic}\n\n{headline} — {summary}",
            buttons=buttons,
            link_preview=False
        )
        self._store[cid]["message_id"] = msg.id
        return msg.id

    async def _get_current_message(self, channel: str, message_id: int) -> Optional[Message]:
        try:
            msgs = await self.client.get_messages(channel, ids=message_id)
            if isinstance(msgs, list):
                return msgs[0] if msgs else None
            return msgs
        except Exception as e:
            logger.warning("Failed to fetch current message %s/%s: %s", channel, message_id, e)
            return None

    async def _toggle_edit(self, evt: events.CallbackQuery.Event, cid: str, rec: dict):
        from time import monotonic
        now = monotonic()
        last = rec.get("_last_click_ts", 0.0)
        if (now - last) < 1.0:
            return await self._safe_answer(evt, "Подождите…")
        rec["_last_click_ts"] = now

        want_expanded = not rec.get("expanded", False)

        collapsed_txt = f"#{rec['topic']}\n\n{rec['headline']} — {rec['summary']}".strip()
        expanded_txt  = f"#{rec['topic']}\n\n{rec['full_text']}".strip()

        next_txt = expanded_txt if want_expanded else collapsed_txt
        next_btns = [[Button.inline("Свернуть", data=f"more:{cid}")]] if want_expanded \
                    else [[Button.inline("Подробнее", data=f"more:{cid}")]]

        channel = rec["channel"]
        msg_id = rec.get("message_id")

        cur_msg = await self._get_current_message(channel, msg_id)
        cur_txt = (getattr(cur_msg, "message", "") or "").strip() if cur_msg else ""

        if cur_txt == next_txt:
            return await self._safe_answer(evt, "Без изменений")

        try:
            await self.client.edit_message(
                entity=channel,
                message=msg_id,
                text=next_txt,
                buttons=next_btns,
                link_preview=False
            )
            rec["expanded"] = want_expanded
            await self._safe_answer(evt)
        except MessageNotModifiedError:
            await self._safe_answer(evt, "Без изменений")
        except Exception as e:
            logger.exception("Edit failed: %s", e)
            await self._safe_answer(evt, "Не удалось изменить сообщение", alert=True)

    async def _reply_full(self, evt: events.CallbackQuery.Event, cid: str, rec: dict):
        try:
            await evt.respond(f"#{rec['topic']}\n\n{rec['full_text']}", reply_to=rec.get("message_id"))
            await self._safe_answer(evt, "Полная версия отправлена.")
        except Exception as e:
            logger.exception("Respond failed: %s", e)
            await self._safe_answer(evt, "Не удалось отправить полную версию", alert=True)
