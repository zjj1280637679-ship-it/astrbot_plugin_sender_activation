const bridge = window.AstrBotPluginPage;

const elements = {
  pageTitle: document.getElementById("page-title"),
  versionLine: document.getElementById("version-line"),
  refresh: document.getElementById("refresh-button"),
  storage: document.getElementById("storage-status"),
  activations: document.getElementById("activation-count"),
  rates: document.getElementById("rate-count"),
  programs: document.getElementById("program-count"),
  heartbeats: document.getElementById("heartbeat-count"),
  accessCount: document.getElementById("access-count"),
  quarantine: document.getElementById("quarantine-count"),
  hostConfigStatus: document.getElementById("host-config-status"),
  hostConfigSource: document.getElementById("host-config-source"),
  hostConfigRows: document.getElementById("host-config-rows"),
  toolPermissionStatus: document.getElementById("tool-permission-status"),
  toolPermissionRows: document.getElementById("tool-permission-rows"),
  scopeSelect: document.getElementById("scope-select"),
  scopeRef: document.getElementById("scope-ref"),
  sessionStatus: document.getElementById("session-status"),
  recoveryRows: document.getElementById("recovery-rows"),
  recoveryCount: document.getElementById("recovery-count"),
  recoveryEmpty: document.getElementById("recovery-empty"),
  recoveryTableWrap: document.getElementById("recovery-table-wrap"),
  accessForm: document.getElementById("access-form"),
  accessAction: document.getElementById("access-action"),
  accessTargets: document.getElementById("access-targets"),
  accessDuration: document.getElementById("access-duration"),
  accessRows: document.getElementById("access-rows"),
  accessTableWrap: document.getElementById("access-table-wrap"),
  accessRowCount: document.getElementById("access-row-count"),
  accessEmpty: document.getElementById("access-empty"),
  programForm: document.getElementById("program-form"),
  programController: document.getElementById("program-controller"),
  programRecheck: document.getElementById("program-recheck"),
  programLease: document.getElementById("program-lease"),
  programGoal: document.getElementById("program-goal"),
  programWatches: document.getElementById("program-watches"),
  programAddWatch: document.getElementById("program-add-watch"),
  programNoWatch: document.getElementById("program-no-watch"),
  programRows: document.getElementById("program-rows"),
  programTableWrap: document.getElementById("program-table-wrap"),
  programRowCount: document.getElementById("program-row-count"),
  programEmpty: document.getElementById("program-empty"),
  activationForm: document.getElementById("activation-form"),
  activationAction: document.getElementById("activation-action"),
  activationTargets: document.getElementById("activation-targets"),
  activationDuration: document.getElementById("activation-duration"),
  rateForm: document.getElementById("rate-form"),
  rateAction: document.getElementById("rate-action"),
  rateTargets: document.getElementById("rate-targets"),
  rateMaximum: document.getElementById("rate-maximum"),
  rateWindow: document.getElementById("rate-window"),
  rateDuration: document.getElementById("rate-duration"),
  heartbeatForm: document.getElementById("heartbeat-form"),
  heartbeatAction: document.getElementById("heartbeat-action"),
  heartbeatIds: document.getElementById("heartbeat-ids"),
  heartbeatName: document.getElementById("heartbeat-name"),
  heartbeatCron: document.getElementById("heartbeat-cron"),
  heartbeatDuration: document.getElementById("heartbeat-duration"),
  heartbeatInstruction: document.getElementById("heartbeat-instruction"),
  heartbeatRows: document.getElementById("heartbeat-rows"),
  heartbeatTableWrap: document.getElementById("heartbeat-table-wrap"),
  heartbeatRowCount: document.getElementById("heartbeat-row-count"),
  heartbeatEmpty: document.getElementById("heartbeat-empty"),
  rows: document.getElementById("lease-rows"),
  leaseTableWrap: document.getElementById("lease-table-wrap"),
  rowCount: document.getElementById("row-count"),
  empty: document.getElementById("empty-state"),
  diagnostics: document.getElementById("diagnostics-output"),
  notice: document.getElementById("notice"),
  confirmationLayer: document.getElementById("confirmation-layer"),
  confirmationTitle: document.getElementById("confirmation-title"),
  confirmationMessage: document.getElementById("confirmation-message"),
  confirmationScope: document.getElementById("confirmation-scope"),
  confirmationTargetCount: document.getElementById("confirmation-target-count"),
  confirmationTargets: document.getElementById("confirmation-targets"),
  confirmationBatch: document.getElementById("confirmation-batch"),
  confirmationBatchAck: document.getElementById("confirmation-batch-ack"),
  confirmationBatchLabel: document.getElementById("confirmation-batch-label"),
  confirmationCancel: document.getElementById("confirmation-cancel"),
  confirmationAccept: document.getElementById("confirmation-accept"),
};

let currentPayload = null;
let noticeTimer = null;
let confirmationResolver = null;
let confirmationTrigger = null;

function t(key, fallback) {
  return bridge.t(`pages.state.${key}`, fallback);
}

function notify(message, type = "success") {
  clearTimeout(noticeTimer);
  elements.notice.textContent = message;
  elements.notice.className = `notice visible ${type}`;
  noticeTimer = setTimeout(() => {
    elements.notice.className = "notice";
  }, 3600);
}

function closeConfirmation(accepted) {
  if (!confirmationResolver) return;
  const resolve = confirmationResolver;
  confirmationResolver = null;
  elements.confirmationLayer.hidden = true;
  elements.confirmationBatchAck.checked = false;
  elements.confirmationAccept.disabled = false;
  if (confirmationTrigger instanceof HTMLElement) confirmationTrigger.focus();
  confirmationTrigger = null;
  resolve(accepted);
}

function requestConfirmation({ message, scope, targets, destructive = false }) {
  if (confirmationResolver) return Promise.resolve(false);
  const uniqueTargets = [...new Set(targets.map((target) => String(target)))];
  const batch = uniqueTargets.length > 1;
  confirmationTrigger = document.activeElement;
  elements.confirmationTitle.textContent = t(
    "confirmationTitle",
    "确认操作",
  );
  elements.confirmationMessage.textContent = message;
  elements.confirmationScope.textContent = scope || "--";
  elements.confirmationTargetCount.textContent = String(uniqueTargets.length);
  elements.confirmationTargets.textContent = uniqueTargets.join(", ") || "--";
  elements.confirmationBatch.hidden = !batch;
  elements.confirmationBatchAck.checked = false;
  elements.confirmationBatchLabel.textContent = t(
    "batchAcknowledge",
    "我已确认这是对多个目标的批量操作。",
  );
  elements.confirmationCancel.textContent = t("cancel", "取消");
  elements.confirmationAccept.textContent = t("confirm", "确认");
  elements.confirmationAccept.classList.toggle("danger", destructive);
  elements.confirmationAccept.disabled = batch;
  elements.confirmationLayer.hidden = false;
  elements.confirmationCancel.focus();
  return new Promise((resolve) => {
    confirmationResolver = resolve;
  });
}

function parseTargets(value) {
  return [...new Set(value.split(/[\s,，]+/).map((item) => item.trim()))].filter(
    Boolean,
  );
}

function parseDelimitedValues(value) {
  return [...new Set(value.split(/[,，\n]+/).map((item) => item.trim()))].filter(
    Boolean,
  );
}

function selectedScope() {
  return elements.scopeSelect.value;
}

function requireScope() {
  const scope = selectedScope();
  if (!scope) {
    throw new Error(t("scopeRequired", "请选择或填写操作作用域。"));
  }
  return scope;
}

function secondsLabel(value) {
  const seconds = Math.max(0, Number(value) || 0);
  if (seconds >= 86400) {
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
  }
  if (seconds >= 3600) {
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }
  if (seconds >= 60) {
    return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  }
  return `${Math.ceil(seconds)}s`;
}

function scopeRef(scope) {
  return scope || "--";
}

function outcomeMessage(result, fallback) {
  const labels = {
    activation_enabled: t("activationEnabled", "激活租约已建立。"),
    activation_renewed: t("activationRenewed", "激活租约已续期。"),
    activation_disabled: t("activationDisabled", "激活租约已撤销。"),
    activation_already_absent: t(
      "activationAlreadyAbsent",
      "目标原本没有激活租约。",
    ),
    rate_limit_set: t("rateSet", "额外激活限频已设置。"),
    rate_limit_cleared: t("rateCleared", "额外激活限频已清除。"),
    rate_limit_already_absent: t(
      "rateAlreadyAbsent",
      "目标原本没有额外激活限频。",
    ),
    attention_program_created: t("programCreated", "Attention Program 已建立。"),
    attention_program_updated: t("programUpdated", "Attention Program 已更新。"),
    attention_programs_cancelled: t("programCancelled", "Attention Program 已结束。"),
    listener_started: t("listenerStarted", "兼容监听已建立。"),
    listeners_cancelled: t("listenersCancelled", "兼容监听已取消。"),
    heartbeat_created: t("heartbeatCreated", "心跳租约已建立。"),
    heartbeat_renewed: t("heartbeatRenewed", "心跳租约已续期。"),
    heartbeat_disabled: t("heartbeatDisabled", "心跳租约已终止。"),
    heartbeat_already_absent: t(
      "heartbeatAlreadyAbsent",
      "指定心跳租约原本不存在。",
    ),
    operator_access_granted: t("operatorAccessGranted", "插件操作员授权已建立。"),
    operator_access_renewed: t("operatorAccessRenewed", "插件操作员授权已续期。"),
    operator_access_revoked: t("operatorAccessRevoked", "插件操作员授权已撤销。"),
    operator_access_already_absent: t(
      "operatorAccessAlreadyAbsent",
      "目标原本没有插件操作员授权。",
    ),
  };
  return labels[result.outcome] || fallback;
}

function leaseRows(payload) {
  const activations = payload.state.activation_leases;
  const rates = new Map(
    payload.state.rate_leases.map((lease) => [
      `${lease.scope_ref}\u0000${lease.target_id}`,
      lease,
    ]),
  );
  return activations.map((activation) => ({
    activation,
    rate:
      rates.get(`${activation.scope_ref}\u0000${activation.target_id}`) || null,
  }));
}

function renderScopes(payload) {
  const existing = elements.scopeSelect.value;
  const scopes = payload.state.known_scopes || [];
  elements.scopeSelect.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = t("selectScope", "请选择作用域");
  elements.scopeSelect.append(placeholder);
  for (const scope of scopes) {
    const option = document.createElement("option");
    option.value = scope;
    option.textContent = scope;
    elements.scopeSelect.append(option);
  }
  if (scopes.includes(existing)) {
    elements.scopeSelect.value = existing;
  }
}

function button(label, className, handler) {
  const control = document.createElement("button");
  control.type = "button";
  control.className = className;
  control.textContent = label;
  control.addEventListener("click", handler);
  return control;
}

function renderRows(payload) {
  const rows = leaseRows(payload);
  elements.rows.replaceChildren();
  for (const { activation, rate } of rows) {
    const row = document.createElement("tr");
    const values = [
      activation.scope_ref,
      activation.target_id,
      secondsLabel(activation.remaining_seconds),
      rate ? `${rate.max_activations}/${rate.window_seconds}s` : "--",
      rate ? secondsLabel(rate.remaining_seconds) : "--",
      `${activation.created_by} / ${rate ? rate.source : "--"}`,
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const actions = document.createElement("td");
    actions.className = "row-actions";
    if (rate) {
      actions.append(
        button(t("clearRate", "清除限频"), "small secondary", async () => {
          const confirmed = await requestConfirmation({
            message: t("confirmClearRate", "确认清除此限频租约？"),
            scope: activation.scope_ref,
            targets: [activation.target_id],
            destructive: true,
          });
          if (!confirmed) return;
          await postRate("clear", activation.scope_ref, [activation.target_id]);
        }),
      );
    }
    actions.append(
      button(t("disable", "撤销"), "small danger", async () => {
        const confirmed = await requestConfirmation({
          message: t("confirmDisable", "确认撤销此激活租约？"),
          scope: activation.scope_ref,
          targets: [activation.target_id],
          destructive: true,
        });
        if (!confirmed) return;
        await postActivation(
          "disable",
          activation.scope_ref,
          [activation.target_id],
          0,
        );
      }),
    );
    row.append(actions);
    elements.rows.append(row);
  }
  elements.rowCount.textContent = `${rows.length} ${t("rows", "条")}`;
  elements.empty.style.display = rows.length ? "none" : "block";
  elements.leaseTableWrap.style.display = rows.length ? "block" : "none";
}

function recoveryTargetLabel(target) {
  const activation = secondsLabel(target.activation_remaining_seconds);
  if (target.rate_max_activations == null) {
    return `${target.target_id}: 激活 ${activation}，无限频`;
  }
  return `${target.target_id}: 激活 ${activation}，${target.rate_max_activations}/${target.rate_window_seconds}s，限频 ${secondsLabel(target.rate_remaining_seconds)}`;
}

function renderRecoveryReports(payload) {
  const reports = payload.state.recovery_reports || [];
  elements.recoveryRows.replaceChildren();
  for (const report of reports) {
    const row = document.createElement("tr");
    const values = [
      report.report_id,
      report.reason,
      secondsLabel(report.remaining_seconds),
      report.targets.map(recoveryTargetLabel).join("\n"),
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    elements.recoveryRows.append(row);
  }
  elements.recoveryCount.textContent = `${reports.length} ${t("rows", "条")}`;
  elements.recoveryEmpty.style.display = reports.length ? "none" : "block";
  elements.recoveryTableWrap.style.display = reports.length ? "block" : "none";
}

function watchKindLabel(kind) {
  return {
    sender: "指定用户",
    keyword: "关键词",
    any_message: "任意消息",
  }[kind] || kind;
}

function createOption(value, label, selectedValue) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  option.selected = value === selectedValue;
  return option;
}

function syncWatchValues(card) {
  const kind = card.querySelector(".watch-kind").value;
  const input = card.querySelector(".watch-values");
  const needsValues = kind === "sender" || kind === "keyword";
  input.disabled = !needsValues;
  input.required = needsValues;
  input.placeholder =
    kind === "sender"
      ? "真实 QQ ID；多个用逗号分隔"
      : kind === "keyword"
        ? "关键词；多个用逗号分隔"
        : "任意消息无需填写";
  if (!needsValues) input.value = "";
}

function addWatchEditor(initial = {}) {
  const card = document.createElement("div");
  card.className = "watch-card";

  const kindLabel = document.createElement("label");
  const kindTitle = document.createElement("span");
  kindTitle.textContent = "关注什么";
  const kind = document.createElement("select");
  kind.className = "watch-kind";
  const initialKind = initial.match_kind || initial.kind || "sender";
  kind.append(
    createOption("sender", "指定用户", initialKind),
    createOption("keyword", "关键词", initialKind),
    createOption("any_message", "任意消息", initialKind),
  );
  kindLabel.append(kindTitle, kind);

  const valuesLabel = document.createElement("label");
  valuesLabel.className = "watch-values-label";
  const valuesTitle = document.createElement("span");
  valuesTitle.textContent = "条件值";
  const values = document.createElement("input");
  values.className = "watch-values";
  values.type = "text";
  values.autocomplete = "off";
  values.value = (initial.match_values || initial.values || []).join(", ");
  valuesLabel.append(valuesTitle, values);

  const quantifierLabel = document.createElement("label");
  const quantifierTitle = document.createElement("span");
  quantifierTitle.textContent = "多少变化再看";
  const quantifier = document.createElement("select");
  quantifier.className = "watch-quantifier";
  const quantifierCount = Number(initial.quantifier_count || 1);
  const quantifierValue =
    quantifierCount === 3 ? "every_3" : quantifierCount === 10 ? "every_10" : "each";
  quantifier.append(
    createOption("each", "每次", quantifierValue),
    createOption("every_3", "每 3 次", quantifierValue),
    createOption("every_10", "每 10 次", quantifierValue),
  );
  quantifierLabel.append(quantifierTitle, quantifier);

  const settleLabel = document.createElement("label");
  const settleTitle = document.createElement("span");
  settleTitle.textContent = "安静多久再看";
  const settle = document.createElement("select");
  settle.className = "watch-settle";
  const settleSeconds = Number(initial.settle_seconds ?? 1);
  const settleValue =
    settleSeconds === 0
      ? "immediate_0s"
      : settleSeconds === 3
        ? "settle_3s"
        : "normal_1s";
  settle.append(
    createOption("immediate_0s", "立即 · 0 秒", settleValue),
    createOption("normal_1s", "普通 · 1 秒", settleValue),
    createOption("settle_3s", "等这一波说完 · 3 秒", settleValue),
  );
  settleLabel.append(settleTitle, settle);

  const removeWrap = document.createElement("div");
  removeWrap.className = "watch-remove";
  const remove = button("移除", "small danger", () => {
    card.remove();
    elements.programNoWatch.hidden = elements.programWatches.children.length > 0;
  });
  removeWrap.append(remove);

  card.append(kindLabel, valuesLabel, quantifierLabel, settleLabel, removeWrap);
  kind.addEventListener("change", () => syncWatchValues(card));
  syncWatchValues(card);
  elements.programWatches.append(card);
  elements.programNoWatch.hidden = true;
}

function collectWatches() {
  const watches = [];
  for (const card of elements.programWatches.querySelectorAll(".watch-card")) {
    const kind = card.querySelector(".watch-kind").value;
    const rawValues = card.querySelector(".watch-values").value;
    const values =
      kind === "sender"
        ? parseTargets(rawValues)
        : kind === "keyword"
          ? parseDelimitedValues(rawValues)
          : [];
    if ((kind === "sender" || kind === "keyword") && !values.length) {
      throw new Error(t("watchValuesRequired", "每个用户/关键词 Watch 都必须填写条件值。"));
    }
    watches.push({
      match: { type: kind, values },
      quantifier: card.querySelector(".watch-quantifier").value,
      settle: card.querySelector(".watch-settle").value,
    });
  }
  return watches;
}

function programWatchSummary(program) {
  const watches = program.watches || [];
  if (!watches.length) return "无 Watch · 仅 Recheck";
  return watches
    .map((watch) => {
      const values = (watch.match_values || []).join(", ");
      const suffix = values ? `：${values}` : "";
      return `${watchKindLabel(watch.match_kind)}${suffix} · ${watch.quantifier_count}次 · quiet ${watch.settle_seconds}s`;
    })
    .join("\n");
}

function programRecheckLabel(program) {
  if (program.recheck_seconds == null) return t("off", "关闭");
  const runtime = program.runtime || {};
  const remaining = runtime.recheck_remaining_seconds;
  return remaining == null
    ? secondsLabel(program.recheck_seconds)
    : `${secondsLabel(program.recheck_seconds)} / 下次 ${secondsLabel(remaining)}`;
}

function renderProgramRows(payload) {
  const programs = payload.state.programs || [];
  elements.programRows.replaceChildren();
  for (const program of programs) {
    const runtime = program.runtime || {};
    const row = document.createElement("tr");
    const generation =
      `${runtime.dirty_generation || 0} / ${runtime.reconciled_generation || 0}` +
      (runtime.dirty ? " · DIRTY" : "");
    const values = [
      program.program_id,
      programWatchSummary(program),
      programRecheckLabel(program),
      secondsLabel(program.remaining_seconds),
      generation,
      program.goal,
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value || "--";
      row.append(cell);
    }
    const actions = document.createElement("td");
    actions.className = "row-actions";
    actions.append(
      button(t("cancelProgram", "结束"), "small danger", async () => {
        const confirmed = await requestConfirmation({
          message: t("confirmProgramCancel", "确认结束这个 Attention Program？"),
          scope: program.scope_ref,
          targets: [program.program_id],
          destructive: true,
        });
        if (!confirmed) return;
        await postProgram("cancel", program.scope_ref, {
          programIds: [program.program_id],
        });
      }),
    );
    row.append(actions);
    elements.programRows.append(row);
  }
  elements.programRowCount.textContent =
    `${programs.length} ${t("rows", "条")}`;
  elements.programEmpty.style.display = programs.length ? "none" : "block";
  elements.programTableWrap.style.display = programs.length ? "block" : "none";
}

function renderHeartbeatRows(payload) {
  const leases = payload.state.heartbeat_leases || [];
  elements.heartbeatRows.replaceChildren();
  for (const lease of leases) {
    const row = document.createElement("tr");
    const values = [
      lease.lease_id,
      lease.name,
      lease.cron_expression,
      secondsLabel(lease.remaining_seconds),
      lease.enabled ? lease.status : t("heartbeatDisabledState", "已暂停"),
      lease.instruction,
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value || "--";
      row.append(cell);
    }
    const actions = document.createElement("td");
    actions.className = "row-actions";
    actions.append(
      button(t("disable", "终止"), "small danger", async () => {
        const confirmed = await requestConfirmation({
          message: t("confirmHeartbeatDisable", "确认终止此心跳租约？"),
          scope: lease.scope_ref,
          targets: [lease.lease_id],
          destructive: true,
        });
        if (!confirmed) return;
        await postHeartbeat("disable", lease.scope_ref, [lease.lease_id]);
      }),
    );
    row.append(actions);
    elements.heartbeatRows.append(row);
  }
  elements.heartbeatRowCount.textContent =
    `${leases.length} ${t("rows", "条")}`;
  elements.heartbeatEmpty.style.display = leases.length ? "none" : "block";
  elements.heartbeatTableWrap.style.display = leases.length ? "block" : "none";
}

function renderAccessRows(payload) {
  const grants = payload.state.operator_grants || [];
  elements.accessRows.replaceChildren();
  for (const grant of grants) {
    const row = document.createElement("tr");
    for (const value of [
      grant.operator_id,
      secondsLabel(grant.remaining_seconds),
      grant.created_by,
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value || "--";
      row.append(cell);
    }
    const actions = document.createElement("td");
    actions.className = "row-actions";
    actions.append(
      button(t("revoke", "撤销"), "small danger", async () => {
        const confirmed = await requestConfirmation({
          message: t("confirmAccessRevoke", "确认收回此成员的插件使用权？"),
          scope: grant.scope_ref,
          targets: [grant.operator_id],
          destructive: true,
        });
        if (!confirmed) return;
        await postAccess("revoke", grant.scope_ref, [grant.operator_id], 0);
      }),
    );
    row.append(actions);
    elements.accessRows.append(row);
  }
  elements.accessRowCount.textContent = `${grants.length} ${t("rows", "条")}`;
  elements.accessEmpty.style.display = grants.length ? "none" : "block";
  elements.accessTableWrap.style.display = grants.length ? "block" : "none";
}

function renderHostConfig(payload) {
  const report = payload.host_config || {
    status: "unknown",
    source: "default",
    checks: [],
  };
  const overallLabels = {
    ready: t("hostConfigReady", "已满足"),
    attention: t("hostConfigAttention", "需要注意"),
    blocked: t("hostConfigBlocked", "存在阻断项"),
    unknown: t("hostConfigUnknown", "无法确认"),
  };
  const sourceLabels = {
    default: t("hostConfigDefault", "默认配置"),
    effective_scope: t("hostConfigScope", "当前作用域生效配置"),
  };
  const checkLabels = {
    provider_enabled: t("providerEnabled", "AI 能力"),
    local_agent_runner: t("localAgentRunner", "内置 Agent 执行器"),
    user_identification: t("userIdentification", "用户识别"),
    empty_provider_wake_prefix: t(
      "emptyProviderWakePrefix",
      "LLM 额外唤醒前缀",
    ),
    shared_group_session: t("sharedGroupSession", "隔离会话"),
    hidden_tool_status_for_yield: t(
      "hiddenToolStatusForYield",
      "沉默回合隐藏工具状态",
    ),
  };
  const observedLabels = {
    enabled: t("observedEnabled", "已开启"),
    disabled: t("observedDisabled", "已关闭"),
    local: t("observedLocal", "local"),
    non_local: t("observedNonLocal", "非 local"),
    empty: t("observedEmpty", "留空"),
    configured: t("observedConfigured", "已填写"),
    different: t("observedDifferent", "值不匹配"),
    unknown: t("observedUnknown", "无法读取"),
  };
  const statusLabels = {
    pass: t("checkPass", "符合"),
    action_required: t("checkActionRequired", "需要调整"),
    unknown: t("checkUnknown", "未知"),
  };

  elements.hostConfigStatus.textContent =
    overallLabels[report.status] || overallLabels.unknown;
  elements.hostConfigStatus.className =
    `host-config-summary ${report.status || "unknown"}`;
  elements.hostConfigSource.textContent =
    sourceLabels[report.source] || sourceLabels.default;
  elements.hostConfigRows.replaceChildren();

  for (const check of report.checks || []) {
    const row = document.createElement("div");
    row.className = `host-config-row ${check.status || "unknown"}`;

    const name = document.createElement("strong");
    name.textContent = checkLabels[check.id] || check.id;

    const observed = document.createElement("span");
    observed.textContent =
      observedLabels[check.observed] || String(check.observed || "--");

    const status = document.createElement("span");
    status.className = "host-config-check-status";
    status.textContent =
      statusLabels[check.status] || statusLabels.unknown;

    row.append(name, observed, status);
    elements.hostConfigRows.append(row);
  }
}

function renderToolPermissions(payload) {
  const report = payload.tool_permissions || {
    status: "unknown",
    checks: [],
  };
  const statusLabels = {
    ready: t("toolPermissionsReady", "成员可达"),
    blocked: t("toolPermissionsBlocked", "授权成员会被阻断"),
    unknown: t("toolPermissionsUnknown", "无法确认"),
  };
  elements.toolPermissionStatus.textContent =
    statusLabels[report.status] || statusLabels.unknown;
  elements.toolPermissionStatus.className =
    `host-config-summary ${report.status || "unknown"}`;
  elements.toolPermissionRows.replaceChildren();
  for (const check of report.checks || []) {
    const row = document.createElement("div");
    row.className = `host-config-row ${
      check.delegate_reachable ? "pass" : "action_required"
    }`;
    const name = document.createElement("strong");
    name.textContent = check.tool;
    const observed = document.createElement("span");
    observed.textContent = check.effective_permission;
    const status = document.createElement("span");
    status.className = "host-config-check-status";
    status.textContent = check.delegate_reachable
      ? t("delegateReachable", "授权成员可达")
      : t("delegateBlocked", "请改为 member");
    row.append(name, observed, status);
    elements.toolPermissionRows.append(row);
  }
}

function render(payload) {
  currentPayload = payload;
  const health = payload.health;
  elements.versionLine.textContent = `v${payload.version}`;
  if (
    !health.runtime_snapshot_ready ||
    health.program_storage_ready === false
  ) {
    elements.storage.textContent = t("unavailable", "不可用");
    elements.storage.style.color = "var(--danger)";
  } else if (
    !health.storage_write_healthy ||
    health.program_storage_write_healthy === false
  ) {
    elements.storage.textContent = t("writeDegraded", "快照可用，写入异常");
    elements.storage.style.color = "var(--danger)";
  } else {
    elements.storage.textContent = t("ready", "可用");
    elements.storage.style.color = "var(--success)";
  }
  elements.activations.textContent = String(health.activation_count);
  elements.rates.textContent = String(health.rate_count);
  elements.programs.textContent = String(health.program_count || 0);
  elements.heartbeats.textContent = String(health.heartbeat_count || 0);
  elements.accessCount.textContent = String(health.access_grant_count || 0);
  elements.quarantine.textContent = String(
    (health.quarantined_count || 0) +
      (health.heartbeat_quarantined_count || 0) +
      (health.program_quarantined_count || 0),
  );
  renderHostConfig(payload);
  renderToolPermissions(payload);
  renderScopes(payload);
  renderAccessRows(payload);
  renderRows(payload);
  renderRecoveryReports(payload);
  renderProgramRows(payload);
  renderHeartbeatRows(payload);
  elements.scopeRef.textContent = scopeRef(elements.scopeSelect.value);
  const session = payload.session_status || {
    enabled: null,
    code: "scope_not_selected",
  };
  elements.sessionStatus.textContent =
    session.enabled === true
      ? t("sessionEnabled", "已启用")
      : session.enabled === false
        ? t("sessionDisabled", "已禁用，只允许清理现有租约")
        : t("sessionUnknown", "状态未确认，按原生默认开放");
  elements.sessionStatus.style.color =
    session.enabled === false
      ? "var(--danger)"
      : session.enabled === true
        ? "var(--success)"
        : "var(--muted)";
  elements.diagnostics.textContent = JSON.stringify(health, null, 2);
}

async function refresh() {
  elements.refresh.disabled = true;
  try {
    const preferredScope = elements.scopeSelect.value;
    const indexPayload = await bridge.apiGet("state");
    const scopes = indexPayload.state.known_scopes || [];
    const payload =
      preferredScope && scopes.includes(preferredScope)
        ? await bridge.apiGet("state", { scope_ref: preferredScope })
        : indexPayload;
    render(payload);
  } catch (error) {
    notify(error.message || String(error), "error");
  } finally {
    elements.refresh.disabled = false;
  }
}

async function postProgram(action, scopeRef, values = {}) {
  const result = await bridge.apiPost("program", {
    action,
    scope_ref: scopeRef,
    controller_sender_id: values.controllerSenderId || "",
    program_ids: values.programIds || [],
    goal: values.goal || "",
    watches: values.watches || [],
    recheck: values.recheck || "default_3m",
    lease: values.lease || "2h",
  });
  notify(
    outcomeMessage(
      result,
      result.changed
        ? t("programChanged", "Attention Program 已更新。")
        : t("noChange", "状态没有变化。"),
    ),
  );
  await refresh();
}

async function postActivation(action, scopeRef, targetIds, durationSeconds) {
  const result = await bridge.apiPost("activation", {
    action,
    scope_ref: scopeRef,
    target_ids: targetIds,
    duration_seconds: durationSeconds,
  });
  notify(
    outcomeMessage(
      result,
      result.changed
        ? t("activationChanged", "激活租约已更新。")
        : t("noChange", "状态没有变化。"),
    ),
  );
  await refresh();
}

async function postRate(action, scopeRef, targetIds, values = {}) {
  const result = await bridge.apiPost("rate", {
    action,
    scope_ref: scopeRef,
    target_ids: targetIds,
    max_activations: values.maximum || 0,
    window_seconds: values.window || 0,
    duration_seconds: values.duration || 0,
  });
  notify(
    outcomeMessage(
      result,
      result.changed
        ? t("rateChanged", "限频租约已更新。")
        : t("noChange", "状态没有变化。"),
    ),
  );
  await refresh();
}

async function postHeartbeat(action, scopeRef, leaseIds = [], values = {}) {
  const result = await bridge.apiPost("heartbeat", {
    action,
    scope_ref: scopeRef,
    lease_ids: leaseIds,
    name: values.name || "",
    cron_expression: values.cron || "",
    instruction: values.instruction || "",
    duration_seconds: values.duration || 0,
  });
  notify(
    outcomeMessage(
      result,
      result.changed
        ? t("heartbeatChanged", "心跳租约已更新。")
        : t("noChange", "状态没有变化。"),
    ),
  );
  await refresh();
}

async function postAccess(action, scopeRef, operatorIds, durationSeconds) {
  const result = await bridge.apiPost("access", {
    action,
    scope_ref: scopeRef,
    operator_ids: operatorIds,
    duration_seconds: durationSeconds,
  });
  notify(
    outcomeMessage(
      result,
      result.changed
        ? t("accessChanged", "插件操作员授权已更新。")
        : t("noChange", "状态没有变化。"),
    ),
  );
  await refresh();
}

elements.scopeSelect.addEventListener("change", () => {
  elements.scopeRef.textContent = scopeRef(elements.scopeSelect.value);
  refresh();
});

elements.programAddWatch.addEventListener("click", () => addWatchEditor());

elements.activationAction.addEventListener("change", () => {
  elements.activationDuration.disabled =
    elements.activationAction.value === "disable";
});

elements.accessAction.addEventListener("change", () => {
  elements.accessDuration.disabled = elements.accessAction.value === "revoke";
});

elements.rateAction.addEventListener("change", () => {
  const disabled = elements.rateAction.value === "clear";
  elements.rateMaximum.disabled = disabled;
  elements.rateWindow.disabled = disabled;
  elements.rateDuration.disabled = disabled;
});

elements.heartbeatAction.addEventListener("change", () => {
  const action = elements.heartbeatAction.value;
  const create = action === "create";
  const disable = action === "disable";
  elements.heartbeatIds.required = !create;
  elements.heartbeatName.disabled = disable;
  elements.heartbeatCron.disabled = disable;
  elements.heartbeatInstruction.disabled = disable;
  elements.heartbeatDuration.disabled = disable;
});

elements.confirmationBatchAck.addEventListener("change", () => {
  elements.confirmationAccept.disabled =
    !elements.confirmationBatch.hidden &&
    !elements.confirmationBatchAck.checked;
});

elements.confirmationCancel.addEventListener("click", () => {
  closeConfirmation(false);
});

elements.confirmationAccept.addEventListener("click", () => {
  if (elements.confirmationAccept.disabled) return;
  closeConfirmation(true);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.confirmationLayer.hidden) {
    closeConfirmation(false);
  }
});

elements.programForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const scope = requireScope();
    const controller = elements.programController.value.trim();
    if (!/^\d{5,20}$/.test(controller)) {
      throw new Error(t("programControllerRequired", "请填写真实数字 QQ ID 作为控制主体。"));
    }
    const goal = elements.programGoal.value.trim();
    if (!goal) {
      throw new Error(t("programGoalRequired", "请填写开放目标。"));
    }
    const watches = collectWatches();
    if (!watches.length && elements.programRecheck.value === "off") {
      throw new Error(
        t("programWakeSourceRequired", "没有 Watch 时必须启用无事件 Recheck。"),
      );
    }
    const targets = watches.flatMap((watch) => watch.match.values || []);
    const confirmed = await requestConfirmation({
      message: t(
        "confirmProgramCreate",
        "确认建立这个 Attention Program？Watch 只会标记 dirty，真正醒来后 AI 会重新读取当前世界。",
      ),
      scope,
      targets: targets.length ? targets : ["Recheck-only Program"],
      destructive: false,
    });
    if (!confirmed) return;
    await postProgram("create", scope, {
      controllerSenderId: controller,
      goal,
      watches,
      recheck: elements.programRecheck.value,
      lease: elements.programLease.value,
    });
  } catch (error) {
    notify(error.message || String(error), "error");
  }
});

elements.activationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const scope = requireScope();
    const targets = parseTargets(elements.activationTargets.value);
    if (!targets.length) throw new Error(t("targetsRequired", "请填写 QQ ID。"));
    const action = elements.activationAction.value;
    const message =
      action === "disable"
        ? t("confirmBatchDisable", "确认撤销这些目标的激活与从属限频？")
        : t("confirmActivation", "确认更新这些激活租约？");
    const confirmed = await requestConfirmation({
      message,
      scope,
      targets,
      destructive: action === "disable",
    });
    if (!confirmed) return;
    await postActivation(
      action,
      scope,
      targets,
      Number(elements.activationDuration.value || 0),
    );
  } catch (error) {
    notify(error.message || String(error), "error");
  }
});

elements.accessForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const scope = requireScope();
    const targets = parseTargets(elements.accessTargets.value);
    if (!targets.length) throw new Error(t("targetsRequired", "请填写 QQ ID。"));
    const action = elements.accessAction.value;
    const confirmed = await requestConfirmation({
      message:
        action === "revoke"
          ? t("confirmAccessRevoke", "确认收回这些成员的插件使用权？")
          : t("confirmAccessChange", "确认更新这些成员的插件使用权？"),
      scope,
      targets,
      destructive: action === "revoke",
    });
    if (!confirmed) return;
    await postAccess(
      action,
      scope,
      targets,
      Number(elements.accessDuration.value || 0),
    );
  } catch (error) {
    notify(error.message || String(error), "error");
  }
});

elements.rateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const scope = requireScope();
    const targets = parseTargets(elements.rateTargets.value);
    if (!targets.length) throw new Error(t("targetsRequired", "请填写 QQ ID。"));
    const action = elements.rateAction.value;
    const confirmed = await requestConfirmation({
      message: t("confirmRate", "确认更新这些限频租约？"),
      scope,
      targets,
      destructive: action === "clear",
    });
    if (!confirmed) return;
    await postRate(action, scope, targets, {
      maximum: Number(elements.rateMaximum.value || 0),
      window: Number(elements.rateWindow.value || 0),
      duration: Number(elements.rateDuration.value || 0),
    });
  } catch (error) {
    notify(error.message || String(error), "error");
  }
});

elements.heartbeatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const scope = requireScope();
    const action = elements.heartbeatAction.value;
    const leaseIds = parseTargets(elements.heartbeatIds.value);
    if (action !== "create" && !leaseIds.length) {
      throw new Error(t("heartbeatIdsRequired", "请填写心跳租约 ID。"));
    }
    if (action === "create" && !elements.heartbeatInstruction.value.trim()) {
      throw new Error(t("heartbeatInstructionRequired", "请填写语境目标。"));
    }
    const confirmed = await requestConfirmation({
      message:
        action === "disable"
          ? t("confirmHeartbeatDisable", "确认终止这些心跳租约？")
          : t("confirmHeartbeatChange", "确认更新心跳租约？"),
      scope,
      targets: leaseIds.length ? leaseIds : [elements.heartbeatName.value],
      destructive: action === "disable",
    });
    if (!confirmed) return;
    await postHeartbeat(action, scope, leaseIds, {
      name: elements.heartbeatName.value,
      cron: elements.heartbeatCron.value,
      instruction: elements.heartbeatInstruction.value,
      duration: Number(elements.heartbeatDuration.value || 0),
    });
  } catch (error) {
    notify(error.message || String(error), "error");
  }
});

elements.refresh.addEventListener("click", refresh);

function renderLocale() {
  const title = t("title", "管理员的真理捍卫器控制台");
  document.title = title;
  elements.pageTitle.textContent = title;
}

await bridge.ready();
renderLocale();
addWatchEditor();
elements.heartbeatAction.dispatchEvent(new Event("change"));
bridge.onContext(() => {
  renderLocale();
  if (currentPayload) render(currentPayload);
});
await refresh();
