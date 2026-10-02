"""The Pass — the Hermes / Telegram bridge (Prompt 28).

NOTE: the bridge is a translator, not a brain. It turns a message from a phone
into the same module call the CLI and the web app make, and turns the answer
back into words a cook can read while plating.

THE INVARIANT: nothing here approves, sends, or commits money. "Approve" is
answered with instructions, never executed — the human gate is a person looking
at the actual order, not a text message at 19:40.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.types import Channel, Role

HELP_TEXT = """I run the pass. Things you can say to me:

- 86 the scallops            — pull an ingredient and warn the floor
- how's the pit              — dishwashing backlog and throughput
- order status               — what is waiting for your approval
- show me tonight            — the service plan and shortfalls
- reminders                  — what is due or unacknowledged
- temps                      — every zone, right now
- journal                    — tonight's service report
- michelin                   — the five criteria and the weakest one
- ack <id>                   — acknowledge an urgent call

I draft and I report. You decide: approvals happen in the review app or the CLI.
"""


class HermesBridge:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self._intents: List[Tuple[str, "re.Pattern", Callable]] = [
            ("help", re.compile(r"\b(help|what can you do)\b", re.I), self._help),
            ("ack", re.compile(r"^\s*ack\s+(?P<id>[\w-]+)\s*$", re.I), self._ack),
            ("approve", re.compile(r"\bapprove\b", re.I), self._approve),
            ("eighty_six", re.compile(r"^\s*(?:86|eighty[ -]?six)\s+(?P<item>.+?)\s*$", re.I),
             self._eighty_six),
            ("pit", re.compile(r"\bpit\b|dish ?wash", re.I), self._pit),
            ("orders", re.compile(r"\border(s)?\b.*(status|pending)|pending order", re.I),
             self._orders),
            ("tonight", re.compile(r"\b(tonight|service plan|show me the plan)\b", re.I),
             self._tonight),
            ("reminders", re.compile(r"\breminders?\b", re.I), self._reminders),
            ("temps", re.compile(r"\btemps?\b|temperature", re.I), self._temps),
            ("journal", re.compile(r"\bjournal\b|service report", re.I), self._journal),
            ("michelin", re.compile(r"\bmichelin\b|\bstar(s)?\b", re.I), self._michelin),
        ]

    # -- entry point -------------------------------------------------------
    def handle(self, update: Dict[str, Any]) -> Dict[str, Any]:
        text = (update or {}).get("text", "") or ""
        for name, pattern, handler in self._intents:
            match = pattern.search(text)
            if match:
                reply = handler(match)
                return {"ok": True, "intent": name, "text": reply}
        return {"ok": True, "intent": "unknown",
                "text": "I did not catch that.\n\n" + HELP_TEXT}

    # -- intents -----------------------------------------------------------
    def _help(self, match) -> str:
        return HELP_TEXT

    def _ack(self, match) -> str:
        message_id = match.group("id")
        comms = self.agent.get("comms")
        if comms is None:
            return "The comms line is not running."
        message = comms.get(message_id)
        if message is None:
            return f"No message with id {message_id}."
        if message.acked:
            return f"{message_id} was already acknowledged by {message.ack_by or 'someone'}."
        comms.ack(message_id, by=update_actor(match))
        return f"Acknowledged {message_id}: {message.body}"

    def _approve(self, match) -> str:
        """Deliberately NOT an approval path. Report, never act."""
        pending = self.agent.get("review_queue").pending()
        if not pending:
            return "Nothing is waiting for approval right now."
        lines = ["I cannot approve anything from chat — the gate is a person",
                 "looking at the actual order. Here is what is waiting:"]
        for item in pending:
            lines.append(f"- {item.id}: {item.summary} (due {item.deadline})")
        lines.append("")
        lines.append("Approve it in the review app or with: "
                     "the-pass orders --approve <id>")
        return "\n".join(lines)

    def _eighty_six(self, match) -> str:
        # NOTE: "86 the scallops" is how a cook talks; the article is noise.
        name = re.sub(r"^(?:the|a|an)\s+", "", match.group("item").strip(), flags=re.I)
        inventory = self.agent.get("inventory")
        ingredient = inventory.by_name(name)
        if ingredient is None:
            return f"I do not have '{name}' in the inventory — nothing to 86."
        if inventory.is_eighty_six(ingredient.id):
            return f"{ingredient.name} was already 86'd."
        inventory.flag_eighty_six(ingredient.id, source="chef")
        foh = self.agent.get("reminders_foh")
        if foh is not None:
            foh.eighty_six_warning(ingredient)
        comms = self.agent.get("comms")
        if comms is not None:
            comms.send(Role.CHEF.value, Channel.URGENT.value,
                       comms.template("86", item=ingredient.name,
                                      note="off the menu"))
        return (f"86'd {ingredient.name}. The floor has been warned and it is on "
                "the urgent line.")

    def _pit(self, match) -> str:
        wash = self.agent.get("wash_counter")
        summary = wash.summary()
        return (f"Pit: {summary['open_racks']} rack(s) open, "
                f"{summary['racks_done']} done, "
                f"{summary['throughput_per_hour']:g}/hour, "
                f"peak {summary['peak'].get('hour') or '—'}:00")

    def _orders(self, match) -> str:
        queue = self.agent.get("review_queue")
        ordering = self.agent.get("ordering")
        pending = queue.pending()
        drafts = ordering.orders(status="draft")
        lines = [f"{len(drafts)} draft order(s), {len(pending)} waiting on you."]
        for item in pending:
            lines.append(f"- {item.summary} (due {item.deadline})")
        return "\n".join(lines)

    def _tonight(self, match) -> str:
        plan = self.agent.get("menu_planner").latest_plan()
        if plan is None:
            return "No service plan yet — plan one with: the-pass plan --covers ..."
        lines = [f"Service {plan.service_date}: "
                 f"{int(sum(plan.covers.values()))} covers across "
                 f"{len(plan.covers)} dish(es)."]
        if plan.shortfalls:
            lines.append("Short:")
            for short in plan.shortfalls:
                lines.append(f"- {short.name}: {short.deficit:g} {short.unit} short")
        else:
            lines.append("Nothing short.")
        if plan.use_first:
            lines.append("Use first: " +
                         ", ".join(u["name"] for u in plan.use_first))
        return "\n".join(lines)

    def _reminders(self, match) -> str:
        engine = self.agent.get("reminders_boh").engine
        pending = engine.pending()
        if not pending:
            return "Nothing outstanding."
        lines = [f"{len(pending)} outstanding:"]
        for reminder in pending[:10]:
            mark = "URGENT" if reminder.urgency == "urgent" else "      "
            lines.append(f"- [{mark}] {reminder.title} (due {reminder.fire_at})")
        return "\n".join(lines)

    def _temps(self, match) -> str:
        statuses = self.agent.get("templog").zone_status()
        if not statuses:
            return "No temperature zones defined yet."
        lines = []
        for status in statuses:
            if status.get("current") is None:
                lines.append(f"- {status['zone']}: no readings")
                continue
            flag = "OK" if status.get("in_bounds") else "BREACH"
            lines.append(f"- [{flag}] {status['zone']}: {status['current']}C"
                         f" (streak {status.get('streak', 0)})")
        return "\n".join(lines)

    def _journal(self, match) -> str:
        journal = self.agent.get("journal")
        report = journal.get() or journal.compile_service()
        text = report.markdown_report()
        return text if len(text) < 3500 else text[:3500] + "\n… (truncated)"

    def _michelin(self, match) -> str:
        michelin = self.agent.get("michelin")
        note = michelin.star_readiness_note()
        focus = michelin.focus()
        if focus.get("criterion"):
            note += f"\nFocus this week: {focus['criterion']}."
        return note

    # -- pushes (the gateway calls these) ---------------------------------
    def due_reminders_push(self) -> List[Dict[str, Any]]:
        """Reminders the gateway should deliver as messages right now."""
        engine = self.agent.get("reminders_boh").engine
        out = []
        for reminder in engine.pending():
            if reminder.status != "fired":
                continue
            out.append({"id": reminder.id, "audience": reminder.audience,
                        "urgency": reminder.urgency, "title": reminder.title,
                        "body": reminder.body,
                        "text": f"[{reminder.urgency.upper()}] {reminder.title}"
                                + (f" — {reminder.body}" if reminder.body else "")})
        return out

    def urgent_messages_push(self) -> List[Dict[str, Any]]:
        """Unacknowledged urgent calls, for redelivery on the phone."""
        comms = self.agent.get("comms")
        return [{"id": m.id, "text": f"URGENT from {m.sender_role}: {m.body}"}
                for m in comms.unacked_urgent()]


def update_actor(match) -> str:
    """Who acted. The gateway supplies the identity; the bridge only labels it."""
    return "chef"
