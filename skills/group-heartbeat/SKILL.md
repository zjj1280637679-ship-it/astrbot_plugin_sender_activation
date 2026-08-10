---
name: group-heartbeat
description: Use when a user explicitly asks AstrBot to wake on a schedule for a finite period and inspect the current group context before deciding whether to act. Use this for time-based periodic checking, not for following a particular sender whenever that person speaks.
---

# Manage finite group heartbeat leases

Use `manage_heartbeat_lease` to create, renew, inspect, or stop finite scheduled opportunities for the native AstrBot main Agent to reassess the current group context.

## Distinguish time triggers from sender triggers

Use this Skill when the trigger is time-based, for example “未来一小时每五分钟看一下这个群” or “今晚每半小时检查一次讨论进展”.

If the request instead means “张三一说话就看见”, use the sender activation capability rather than heartbeat. If both time-based inspection and sender-based tracking are explicitly needed, the two lease types may coexist.

Do not create heartbeat state for one-shot replies, feature discussion, quoted requests, hypotheticals, or vague statements that do not request a future scheduled state.

## Choose the operation

- Use `create` for a new finite heartbeat lease.
- Use `renew` to extend or deliberately modify an existing lease.
- Use `disable` when the user cancels it, the objective is complete, or the context clearly no longer fits.
- Use `list` for explicit state inspection or when an existing lease ID must be resolved before a safe change.

For `create`, provide a short `name`, a five-field `cron_expression`, a useful `instruction`, and a finite `duration_seconds` (or the plugin default when the user intentionally leaves duration to the configured default). AstrBot native Cron has minute-level granularity; do not pretend to support second-level schedules.

For `renew`, provide the current lease ID. Preserve existing values when the user did not ask to change them.

## Write instructions for the native Agent

The heartbeat `instruction` should describe the recurring objective and decision boundary, not force a message every time. A heartbeat supplies an Agent judgment opportunity; it does not guarantee a reply and does not itself fetch QQ history.

If the task depends on information outside the currently available group context, let the native Agent combine appropriate installed search or context tools rather than pretending the heartbeat contains that information.

## Respect authorization and tool results

The plugin enforces current-group scope and AstrBot-admin/plugin-operator authorization. Do not claim a heartbeat exists, changed, or stopped until the tool returns `status=ok`. Follow `outcome`, `effect_state`, and `recovery_action`; if the result is indeterminate, inspect state before claiming success or failure.

## End no-value proactive turns silently

When a heartbeat wakes the Agent and there is no independent public value to add, use `yield_current_turn` rather than sending visible maintenance narration such as “我继续观察” or “本轮无需发言”.

A quiet heartbeat occurrence does not by itself mean the lease should be deleted. Disable the lease only when the objective is complete, a human withdraws it, or the context clearly invalidates the recurring task. After disabling, use `yield_current_turn` if there is still nothing useful to say publicly.
