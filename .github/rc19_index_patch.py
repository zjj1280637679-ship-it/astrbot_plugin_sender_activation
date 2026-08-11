from pathlib import Path

path = Path("attention_service.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    '        self._state = IgnoreState.empty()\n        self._mutation_lock = asyncio.Lock()\n',
    '        self._state = IgnoreState.empty()\n        self._policy_map: dict[IgnoreKey, IgnorePolicy] = {}\n        self._mutation_lock = asyncio.Lock()\n',
    1,
)

text = text.replace(
    '    def _metric(self, name: str) -> None:\n        with self._metrics_lock:\n            self._metrics[name] += 1\n\n',
    '    def _metric(self, name: str) -> None:\n        with self._metrics_lock:\n            self._metrics[name] += 1\n\n'
    '    def _rebuild_policy_index(self) -> None:\n'
    '        self._policy_map = {policy.key: policy for policy in self._state.policies}\n\n',
    1,
)

text = text.replace(
    '        self.storage_write_healthy = False\n        self._guard.clear_all()\n        try:\n',
    '        self.storage_write_healthy = False\n        self._guard.clear_all()\n        self._policy_map.clear()\n        try:\n',
    1,
)

text = text.replace(
    '        self._state = selected.state\n        self.loaded_from = selected_name\n',
    '        self._state = selected.state\n        self._rebuild_policy_index()\n        self.loaded_from = selected_name\n',
    1,
)

text = text.replace(
    '            self.storage_write_healthy = False\n            self._guard.clear_all()\n\n    def _require_storage',
    '            self.storage_write_healthy = False\n            self._guard.clear_all()\n            self._policy_map.clear()\n\n    def _require_storage',
    1,
)

text = text.replace(
    '            self._state = prune_ignore_state(result.state, now)\n            changed_keys = {\n',
    '            self._state = prune_ignore_state(result.state, now)\n            self._rebuild_policy_index()\n            changed_keys = {\n',
    1,
)

old_policy = '''    def _policy(self, scope: str, target_id: str) -> IgnorePolicy | None:\n        if not self.storage_ready:\n            return None\n        now = float(self._wall_clock())\n        for policy in self._state.policies:\n            if policy.scope == scope and policy.target_id == target_id and policy.expires_at > now:\n                return policy\n        return None\n'''
new_policy = '''    def _policy(self, scope: str, target_id: str) -> IgnorePolicy | None:\n        if not self.storage_ready:\n            return None\n        policy = self._policy_map.get((scope, target_id))\n        if policy is None:\n            return None\n        if policy.expires_at <= float(self._wall_clock()):\n            # Expiry is fail-open on the hot path. Persistent pruning happens on the\n            # next mutation/reload; we never perform storage I/O per event.\n            self._guard.clear({(scope, target_id)})\n            return None\n        return policy\n'''
assert old_policy in text
text = text.replace(old_policy, new_policy, 1)

path.write_text(text, encoding="utf-8")
