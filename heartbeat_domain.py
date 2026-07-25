


from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .domain import DomainError, normalize_scope

HEARTBEAT_TAG = "_sender_activation_heartbeat"
HEARTBEAT_KIND = "finite_native_agent_heartbeat"
HEARTBEAT_SCHEMA_VERSION = 1
MAX_HEARTBEATS_PER_OPERATION = 50

_CRON_TOKEN = re.compile(r"^[A-Za-z0-9*/?,#L\-]+$")
_LEASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class HeartbeatLimits:
    default_duration_seconds: int = 3600
    max_duration_seconds: int = 24 * 60 * 60
    max_per_scope: int = 10
    max_total: int = 100


@dataclass(frozen=True)
class HeartbeatLease:
    lease_id: str
    scope: str
    name: str
    cron_expression: str
    instruction: str
    created_at: float
    expires_at: float
    created_by: str
    source: str
    enabled: bool
    plugin_suspended: bool
    status: str
    last_error: str | None = None
    last_run_at: float | None = None
    next_run_at: float | None = None

    def remaining_seconds(self, now: float) -> int:
        return max(0, math.ceil(self.expires_at - now))

    def public(self, now: float, scope_ref: str) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "scope_ref": scope_ref,
            "name": self.name,
            "cron_expression": self.cron_expression,
            "instruction": self.instruction,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "remaining_seconds": self.remaining_seconds(now),
            "created_by": self.created_by,
            "source": self.source,
            "enabled": self.enabled,
            "plugin_suspended": self.plugin_suspended,
            "status": self.status,
            "last_error": self.last_error,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
        }


def normalize_lease_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise DomainError("invalid_lease_ids", "lease_ids 必须是字符串数组。")
    if len(value) > MAX_HEARTBEATS_PER_OPERATION:
        raise DomainError(
            "too_many_lease_ids",
            f"单次最多处理 {MAX_HEARTBEATS_PER_OPERATION} 个心跳租约。",
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        lease_id = str(item or "").strip()
        if not _LEASE_ID.fullmatch(lease_id):
            raise DomainError(
                "invalid_lease_id",
                "lease_ids 只能包含 1 至 64 位字母、数字、下划线或连字符。",
            )
        if lease_id not in seen:
            seen.add(lease_id)
            result.append(lease_id)
    return result


def normalize_heartbeat_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return "临时心跳"
    if len(name) > 80 or any(ord(char) < 32 for char in name):
        raise DomainError(
            "invalid_heartbeat_name", "心跳名称必须为 1 至 80 个可见字符。"
        )
    return name


def normalize_instruction(value: Any) -> str:
    instruction = str(value or "").strip()
    if not instruction:
        raise DomainError(
            "missing_heartbeat_instruction",
            "必须说明每次心跳唤醒后需要主 Agent 结合语境判断的目标。",
        )
    if len(instruction) > 4000 or "\x00" in instruction:
        raise DomainError(
            "invalid_heartbeat_instruction",
            "心跳目标说明不能超过 4000 字且不能包含空字符。",
        )
    return instruction


def normalize_cron_expression(value: Any) -> str:
    expression = " ".join(str(value or "").strip().split())
    fields = expression.split(" ")
    if len(fields) != 5 or any(not _CRON_TOKEN.fullmatch(field) for field in fields):
        raise DomainError(
            "invalid_cron_expression",
            "cron_expression 必须是 AstrBot 使用的五段 Cron 表达式（分 时 日 月 周）。",
        )
    if len(expression) > 128:
        raise DomainError("invalid_cron_expression", "Cron 表达式过长。")
    return expression


def normalize_heartbeat_duration(
    value: Any,
    limits: HeartbeatLimits,
) -> int:
    try:
        duration = int(value or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError("invalid_duration", "duration_seconds 必须是整数。") from exc
    if duration == 0:
        duration = limits.default_duration_seconds
    if duration < 1:
        raise DomainError("invalid_duration", "心跳租约有效期必须大于 0 秒。")
    if duration > limits.max_duration_seconds:
        raise DomainError(
            "duration_too_long",
            f"心跳租约最长为 {limits.max_duration_seconds} 秒。",
        )
    return duration


def timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def heartbeat_payload(
    *,
    scope: str,
    instruction: str,
    name: str,
    created_at: float,
    expires_at: float,
    created_by: str,
    source: str,
    sender_id: str,
    plugin_suspended: bool = False,
) -> dict[str, Any]:
    return {
        "session": normalize_scope(scope),
        "sender_id": str(sender_id or "").strip(),
        "origin": (
            "api" if source == "dashboard" else "astrbot_plugin_sender_activation"
        ),
        "note": instruction,
        HEARTBEAT_TAG: {
            "kind": HEARTBEAT_KIND,
            "schema_version": HEARTBEAT_SCHEMA_VERSION,
            "name": name,
            "instruction": instruction,
            "created_at": created_at,
            "expires_at": expires_at,
            "created_by": created_by,
            "source": source,
            "plugin_suspended": plugin_suspended,
        },
    }


def is_owned_heartbeat_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    tag = payload.get(HEARTBEAT_TAG)
    return isinstance(tag, Mapping) and tag.get("kind") == HEARTBEAT_KIND


def heartbeat_from_job(job: Any) -> HeartbeatLease:
    payload = getattr(job, "payload", None)
    if not is_owned_heartbeat_payload(payload):
        raise DomainError("not_owned_heartbeat", "该任务不属于本插件心跳租约。")
    tag = payload[HEARTBEAT_TAG]
    if tag.get("schema_version") != HEARTBEAT_SCHEMA_VERSION:
        raise DomainError(
            "unsupported_heartbeat_schema",
            "心跳租约 Schema 版本不受支持。",
        )
    lease_id = str(getattr(job, "job_id", "") or "").strip()
    if not _LEASE_ID.fullmatch(lease_id):
        raise DomainError("invalid_heartbeat_job_id", "原生 Cron 任务 ID 无效。")
    scope = normalize_scope(payload.get("session"))
    name = normalize_heartbeat_name(tag.get("name"))
    instruction = normalize_instruction(tag.get("instruction"))
    cron_expression = normalize_cron_expression(getattr(job, "cron_expression", ""))
    created_at = timestamp(tag.get("created_at"))
    expires_at = timestamp(tag.get("expires_at"))
    if created_at is None or expires_at is None or expires_at <= created_at:
        raise DomainError(
            "invalid_heartbeat_expiry",
            "心跳租约缺少合法的创建或失效时间。",
        )
    last_error = job.last_error if hasattr(job, "last_error") else None
    return HeartbeatLease(
        lease_id=lease_id,
        scope=scope,
        name=name,
        cron_expression=cron_expression,
        instruction=instruction,
        created_at=created_at,
        expires_at=expires_at,
        created_by=str(tag.get("created_by") or "unknown"),
        source=str(tag.get("source") or "unknown"),
        enabled=bool(getattr(job, "enabled", False)),
        plugin_suspended=bool(tag.get("plugin_suspended", False)),
        status=str(getattr(job, "status", "unknown") or "unknown"),
        last_error=str(last_error) if last_error else None,
        last_run_at=timestamp(getattr(job, "last_run_at", None)),
        next_run_at=timestamp(getattr(job, "next_run_time", None)),
    )
