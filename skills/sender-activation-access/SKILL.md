---
name: sender-activation-access
description: Use when an AstrBot administrator explicitly asks to grant, renew, revoke, or inspect a normal QQ member's finite permission to operate this plugin in the current group without granting global AstrBot administrator rights. Do not use this Skill merely to start tracking or heartbeat tasks.
---

# Manage scoped plugin-operator access

Use `manage_sender_activation_access` only for finite delegation of this plugin's controls inside the current group.

## Choose the operation

- Use `grant` when the current AstrBot administrator explicitly authorizes one or more QQ IDs to operate this plugin in the current group.
- Use `renew` when an existing operator grant should last longer.
- Use `revoke` when the administrator withdraws the delegation.
- Use `list` when the administrator asks to inspect current operator grants.

Pass verified numeric QQ IDs in `operator_ids`. Do not use nicknames or placeholders. For grant/renew, use the requested finite duration; a zero duration means the plugin's configured default.

## Preserve the authority boundary

This grant does not make the member an AstrBot administrator. It only permits the plugin's activation, rate-limit, and heartbeat operations within the current event scope. It does not confer access to AstrBot configuration, commands, other plugins, or other groups.

Only an AstrBot-native administrator may change the operator grant table. A plugin operator cannot delegate the permission onward.

Do not interpret discussion, hypotheticals, quoted requests, or permission design questions as authorization changes.

## Account for AstrBot native tool permissions

Even after the plugin operator grant succeeds, AstrBot's own tool permission settings may still block a normal member before the request reaches the plugin. If `manage_sender_activation`, `manage_sender_activation_rate`, or `manage_heartbeat_lease` is configured as admin-only, explain that those operation tools must be exposed to members so the plugin can enforce its finer-grained current-group operator table. Do not widen unrelated AstrBot permissions.

## Trust the structured result

Do not claim that an operator grant changed until `manage_sender_activation_access` returns `status=ok`. Follow the returned `outcome`, `effect_state`, and recovery information rather than inferring success from the natural-language request.
