from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    '    indeterminate = error.code == "commit_indeterminate"\n',
    '    indeterminate = error.code in {\n'
    '        "commit_indeterminate",\n'
    '        "access_commit_indeterminate",\n'
    '        "attention_commit_indeterminate",\n'
    '        "native_cron_update_indeterminate",\n'
    '    }\n',
    1,
)

old_scope = '''    @staticmethod\n    def _scope(event: AstrMessageEvent) -> str:\n        if event.get_platform_name() != "aiocqhttp":\n            raise DomainError(\n                "unsupported_platform",\n                "本插件首版只支持 aiocqhttp。",\n            )\n        if event.is_private_chat():\n            raise DomainError(\n                "unsupported_chat_type",\n                "本插件首版只作用于群聊。",\n            )\n        return normalize_scope(event.unified_msg_origin)\n\n    @staticmethod\n    def _sender(event: AstrMessageEvent) -> str:\n        sender = str(event.get_sender_id() or "").strip()\n        if not sender:\n            raise DomainError("missing_sender", "当前事件缺少发送者 QQ ID。")\n        return sender\n'''
new_scope = '''    @staticmethod\n    def _owned_cron_payload(event: AstrMessageEvent) -> dict[str, Any] | None:\n        if event.get_platform_name() != "cron":\n            return None\n        payload = event.get_extra("cron_payload")\n        if not isinstance(payload, dict):\n            return None\n        if is_owned_echo_payload(payload) or is_owned_heartbeat_payload(payload):\n            return payload\n        return None\n\n    @classmethod\n    def _scope(cls, event: AstrMessageEvent) -> str:\n        if event.get_platform_name() == "aiocqhttp":\n            if event.is_private_chat():\n                raise DomainError(\n                    "unsupported_chat_type",\n                    "本插件首版只作用于群聊。",\n                )\n            return normalize_scope(event.unified_msg_origin)\n        payload = cls._owned_cron_payload(event)\n        if payload is not None:\n            return normalize_scope(payload.get("session"))\n        raise DomainError(\n            "unsupported_platform",\n            "本插件首版只支持 aiocqhttp 群事件及本插件自己创建的主动 Cron 回合。",\n        )\n\n    @classmethod\n    def _sender(cls, event: AstrMessageEvent) -> str:\n        payload = cls._owned_cron_payload(event)\n        if payload is not None:\n            sender = str(payload.get("sender_id") or "").strip()\n        else:\n            sender = str(event.get_sender_id() or "").strip()\n        if not sender:\n            raise DomainError("missing_sender", "当前事件缺少发送者 QQ ID。")\n        return sender\n'''
assert old_scope in text
text = text.replace(old_scope, new_scope, 1)

path.write_text(text, encoding="utf-8")
