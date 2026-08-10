---
name: sender-activation
description: Use when a user explicitly wants AstrBot to keep watching a specific QQ member's future group messages without requiring @/wake each time, to renew or stop that tracking, or to rate-limit an already tracked sender. Do not use for one-shot replies, capability discussion, hypotheticals, quoted requests, or periodic time-based monitoring.
---

# Manage sender activation

Use the plugin's existing tools to change future message reachability without changing the normal AstrBot agent lifecycle.

## Choose the operation

- Use `manage_sender_activation` with `enable` when an authorized user explicitly asks to start finite future tracking for one or more QQ IDs.
- Use `renew` when an existing tracking lease should continue longer.
- Use `disable` when the user withdraws the request, the tracked objective is complete, or the context clearly no longer fits.
- Use `list` only when the user asks about current state or state inspection is needed to resolve an operation safely.
- Use `manage_sender_activation_rate` only to limit extra activations created by this plugin for a sender that already has an activation lease. Use `set`, `clear`, or `list` as appropriate.

## Require a real future-state request

Treat all three as required before creating or renewing a sender activation lease:

1. The user wants a state that persists beyond the current turn.
2. A concrete sender or set of senders is intended.
3. Their future messages may arrive without native `@`, reply, wake word, or command activation.

Do not create state merely because words such as “关注”“追踪”“盯着” appear. Negation, quotation, hypotheticals, implementation discussion, capability questions, and requests that only concern the current reply override keyword matches.

## Resolve identities safely

Pass only verified numeric QQ IDs in `target_ids`. Never invent IDs or pass placeholders such as `me`, `current_sender`, or a nickname. When the user says “我”“本人” or refers to a nickname, use the identity information already supplied by AstrBot. If no verifiable numeric ID is available, ask for it or ask the administrator to enable AstrBot user identification rather than guessing.

The scope always comes from the current event. Do not fabricate cross-group scope.

## Respect authorization and tool results

The plugin tools enforce AstrBot-admin and plugin-operator authorization. Do not claim that a lease changed until the tool returns `status=ok`. Follow `outcome`, `effect_state`, and `recovery_action` in the returned structure. If `effect_state=indeterminate`, reload or inspect state before making a success/failure claim.

An activation lease only gives future ordinary messages another chance to reach the native main Agent. It does not itself generate a reply and does not guarantee a reply.

## Rate-limit only plugin-added activations

For `manage_sender_activation_rate`, provide an explicit `max_activations`, `window_seconds`, and finite `duration_seconds` when setting a limit. Do not describe this as changing AstrBot's native `@`, reply, command, wake-word, or platform rate limiting.

## Handle plugin-proactive turns without chatter

When the current Agent turn exists only because of this plugin's sender activation and there is no independent public value to add, finish with `yield_current_turn` instead of sending a visible “保持沉默” or control-plane explanation.

Continuing a lease and speaking in the current turn are separate decisions. Lack of useful content in one turn is not by itself a reason to delete the lease. If the objective is actually complete or explicitly withdrawn, disable the lease first; if there is then no independent public value, use `yield_current_turn` as the final action.
