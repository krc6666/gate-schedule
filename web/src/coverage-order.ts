import { html, type BalanceSettings, type CoveragePriority, type State } from './workbench-model';

function tierRoles(state: State, balance: BalanceSettings, tier: CoveragePriority): string[] {
  return state.configuration.roleConfigs.map(role => role.roleName)
    .filter(role => (balance.coveragePriorities?.find(item => item.roleName === role)?.priority ?? 'normal') === tier);
}

export function coverageOrderHtml(state: State): string {
  const balance = state.balanceSettings;
  const order = balance.coverageRoleOrder ?? [];
  return `<h4>同档保岗顺序</h4><p class="section-note">同一档里越靠前，缺人时越倾向保留。先满足原有专岗、航班先后和三档覆盖目标，再参考这个软顺序；不保证严格逐岗排满，也不豁免资格、固定、半休或冲突。当前班组所有航班共用，A/B各自保存。点击“启用本档排序”后上下移动，最后点“保存配置”，重新排班生效。</p>${(['protect', 'normal', 'leave-empty'] as const).map(tier => {
    const roles = tierRoles(state, balance, tier);
    if (!roles.length) return '';
    const listed = order.filter(role => roles.includes(role));
    const shown = [...listed, ...roles.filter(role => !listed.includes(role))];
    return `<div class="coverage-order-tier" data-coverage-order-group="${tier}"><div class="button-row"><strong>${html(state.coveragePolicy?.choices[tier] ?? tier)}</strong><span>${listed.length ? '已设置软顺序' : '未排序 · 沿用原行为'}</span><button class="btn btn-sm btn-outline-secondary" data-coverage-order-action="enable" data-coverage-order-tier="${tier}">启用本档排序</button><button class="btn btn-sm btn-outline-secondary" data-coverage-order-action="reset" data-coverage-order-tier="${tier}" ${listed.length ? '' : 'disabled'}>恢复本档未排序</button></div><div class="table-responsive"><table class="data-table"><thead><tr><th>顺序</th><th>岗位</th><th>调整</th></tr></thead><tbody>${shown.map((role, index) => `<tr><td>${listed.includes(role) ? listed.indexOf(role) + 1 : '未排序'}</td><th>${html(role)}</th><td><div class="button-row"><button class="btn btn-sm btn-outline-secondary" data-coverage-order-action="up" data-coverage-order-tier="${tier}" data-coverage-order-role="${html(role)}" aria-label="${html(role)} 保岗顺序上移" ${!listed.length || index === 0 ? 'disabled' : ''}>↑</button><button class="btn btn-sm btn-outline-secondary" data-coverage-order-action="down" data-coverage-order-tier="${tier}" data-coverage-order-role="${html(role)}" aria-label="${html(role)} 保岗顺序下移" ${!listed.length || index === shown.length - 1 ? 'disabled' : ''}>↓</button></div></td></tr>`).join('')}</tbody></table></div></div>`;
  }).join('')}<p class="section-note">未启用的档位保持原行为。已启用档位里，新加或未列入的岗位与同档末位并列；再次启用可将这些岗位加入排序。恢复未排序不会改三档选择，也不会改旧正式班表。顺序和提示随完整配置、归档与导出保留。</p>`;
}

export function changeCoverageOrder(state: State, balance: BalanceSettings, tier: CoveragePriority, action: string, role?: string): BalanceSettings {
  const roles = tierRoles(state, balance, tier);
  const previous = balance.coverageRoleOrder ?? [];
  const other = previous.filter(name => !roles.includes(name));
  const ordered = [...previous.filter(name => roles.includes(name)), ...roles.filter(name => !previous.includes(name))];
  if (action === 'up' || action === 'down') {
    const index = ordered.indexOf(role ?? '');
    const target = index + (action === 'up' ? -1 : 1);
    if (index < 0 || target < 0 || target >= ordered.length) return balance;
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
  }
  return { ...balance, coverageRoleOrder: [...other, ...(action === 'reset' ? [] : ordered)] };
}
