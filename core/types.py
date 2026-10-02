"""The Pass — core domain types (Prompt 01).

Every module in the agent imports its vocabulary from this file, and this file
imports nothing from the rest of the project.

WHY: one shared vocabulary is what keeps 30 modules coherent — if two modules
disagree on what an Ingredient is, the whole program drifts.

NOTE: all timestamps are local wall-clock strings ("YYYY-MM-DDTHH:MM:SS") and
all dates are "YYYY-MM-DD". The Jetson sits in the restaurant; local time is
the chef's time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List


def filter_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only keys that are fields of dataclass ``cls``.

    WHY: to_dict() may include computed @property values; passing those back
    into the constructor raises TypeError. Every from_dict() routes through
    this helper so the bug class is caught once, here, for everyone.
    """
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in valid}


class Role(str, Enum):
    CHEF = "chef"
    SERVER = "server"
    HOST = "host"
    AGENT = "agent"


class Urgency(str, Enum):
    ROUTINE = "routine"
    URGENT = "urgent"


class Audience(str, Enum):
    FOH = "foh"
    BOH = "boh"
    BOTH = "both"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"


class DishAction(str, Enum):
    FIRED = "fired"
    PLATED = "plated"
    PICKED_UP = "picked_up"


class Channel(str, Enum):
    URGENT = "urgent"
    NON_URGENT = "non_urgent"


class ServiceState(str, Enum):
    PRE_SERVICE = "pre_service"
    SERVICE = "service"
    POST_SERVICE = "post_service"


@dataclass(frozen=True)
class Ingredient:
    id: str
    name: str
    category: str = "general"
    unit: str = "each"
    par_level: float = 0.0
    on_hand: float = 0.0
    unit_cost: float = 0.0
    shelf_life_days: int = 0
    freshness_date: str = ""
    photo_ids: List[str] = field(default_factory=list)
    status: str = "active"  # active | proposed | eighty_six

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ingredient":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class RecipeLine:
    ingredient_id: str
    qty_per_portion: float
    unit: str = "g"
    prep_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecipeLine":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    portions: float = 1.0
    station: str = "line"
    technique_tags: List[str] = field(default_factory=list)
    plating_notes: str = ""
    menu_price: float = 0.0
    lines: List[RecipeLine] = field(default_factory=list)
    source: str = "local"  # local | global

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Recipe":
        d = filter_fields(cls, data)
        raw = d.get("lines", [])
        d["lines"] = [
            RecipeLine.from_dict(x) if isinstance(x, dict) else x for x in raw
        ]
        return cls(**d)


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    order_cutoff: str = "08:00"  # HH:MM local
    typical_delay_days: float = 1.0
    closed_days: List[int] = field(default_factory=list)  # 0=Mon .. 6=Sun
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Supplier":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class OrderLine:
    ingredient_id: str
    ingredient_name: str
    qty: float
    unit: str = "g"
    pack_size: float = 1.0
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderLine":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class Order:
    id: str
    supplier_id: str
    status: OrderStatus = OrderStatus.DRAFT
    created_by: str = "chef"
    created_at: str = ""
    service_date: str = ""
    lines: List[OrderLine] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        d = filter_fields(cls, data)
        if isinstance(d.get("status"), str):
            d["status"] = OrderStatus(d["status"])
        raw = d.get("lines", [])
        d["lines"] = [
            OrderLine.from_dict(x) if isinstance(x, dict) else x for x in raw
        ]
        return cls(**d)


@dataclass(frozen=True)
class TempZone:
    id: str
    name: str
    station: str
    min_c: float
    max_c: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TempZone":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class TempReading:
    id: int
    zone_id: str
    station: str
    sensor_id: str
    celsius: float
    ts: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TempReading":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class Reminder:
    id: str
    audience: str  # Audience value
    urgency: str  # Urgency value
    title: str
    body: str = ""
    fire_at: str = ""
    recurrence: str = "none"  # none | daily | pre_service
    status: str = "scheduled"  # scheduled | fired | acked
    ack_by: str = ""
    ack_at: str = ""
    dedupe_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Reminder":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class PassMessage:
    id: str
    channel: str  # Channel value
    sender_role: str  # Role value
    body: str
    ts: str = ""
    acked: bool = False
    ack_by: str = ""
    ack_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PassMessage":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class DishEvent:
    id: int
    dish_id: str
    action: str  # DishAction value
    ts: str
    service_date: str = ""
    photo_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DishEvent":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class KanbanCard:
    id: str
    board_id: str
    title: str
    detail: str = ""
    column: str = ""
    owner_role: str = ""
    due: str = ""
    blocked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KanbanCard":
        return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class DishReview:
    id: str
    dish_id: str
    photo_id: str
    presentation: int
    portion: int
    color: int
    execution: int
    note: str = ""
    reviewer: str = ""
    ts: str = ""
    service_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DishReview":
        return cls(**filter_fields(cls, data))
