import { html, type State, type Day, type Flight, type Cell, type FlightTemplate } from "./workbench-model";
import { handoffPreferences, bindHandoffPreferences } from "./handoff-preferences";

export function templatePositions(state: State, mode: (role: State["configuration"]["roleConfigs"][number]) => string): string {
  return state.flightLibrary.map(f => {
    const names = f.roleNames ?? state.configuration.roleConfigs.map(r => r.roleName);
    return `<details class="position-rule-group" id="template-${html(f.flightNo)}"><summary><strong>${html(f.flightNo)}</strong><span>${html(f.windowStart)}—${html(f.windowEnd)} · ${names.length} 个岗位</span><span class="chevron">⌄</span></summary><div class="template-actions"><select data-add-template-source="${html(f.flightNo)}" aria-label="${html(f.flightNo)} 新增岗位分类"><option value="">选择岗位分类（可选）</option>${state.configuration.roleConfigs.map(r=>`<option value="${html(r.roleName)}">${html(r.roleName)}</option>`).join("")}</select><input data-add-template-name="${html(f.flightNo)}" list="template-role-catalog" placeholder="输入岗位名称；不填则自动命名" aria-label="${html(f.flightNo)} 新增岗位名称" /><button data-add-template-role="${html(f.flightNo)}" class="btn btn-sm btn-outline-primary">＋ 增加岗位</button><button data-copy-template="${html(f.flightNo)}" class="btn btn-sm btn-outline-secondary">复制岗位到其他航班</button></div><p class="section-note group-note">选择分类或输入名称即可增加一行；同名自动编号。分类沿用人数、性质和资质，新增后可调整。</p><p class="section-note group-note" data-template-add-feedback="${html(f.flightNo)}" role="status" aria-live="polite"></p><div class="table-responsive"><table class="data-table position-rule-table"><thead><tr><th>岗位</th><th>人数</th><th>性质 / 工作环节</th><th>资质人员</th><th>操作</th></tr></thead><tbody>${names.map(name => state.configuration.roleConfigs.find(r => r.roleName === name)!).map(role => {
      const override = f.roles?.find(r => r.roleName === role.roleName);
      const people = state.people.filter(p => p.eligibleRoleNames.includes(role.roleName));
      return `<tr><td class="role-name">${html(role.roleName)}</td><td><input class="number-input" type="number" min="0" data-template-demand="${html(f.flightNo)}" data-template-role="${html(role.roleName)}" value="${override?.requiredCount ?? ""}" placeholder="${role.requiredCount ?? "待配置"}" aria-label="${html(f.flightNo)} ${html(role.roleName)} 人数" /></td><td>${mode(role)}${override?.workModeOverride ? `<small>本航班例外：${html(state.workModes[override.workModeOverride])}</small>` : ""}</td><td><button class="qualified-button" data-qualification="${html(role.roleName)}" data-scope="base"><span>${html(people.map(p => p.name).join("、") || "选择资质人员")}</span><small>${people.length} 人 ⌄</small></button></td><td><button class="btn btn-sm btn-outline-danger" data-remove-template-role="${html(role.roleName)}" data-template-number="${html(f.flightNo)}">移除</button></td></tr>`;
    }).join("") || '<tr><td colspan="5" class="empty-cell">本航班尚无岗位，可在上方添加。</td></tr>'}</tbody></table></div></details>`;
  }).join("") + `<datalist id="template-role-catalog">${state.configuration.roleConfigs.map(r=>`<option value="${html(r.roleName)}"></option>`).join("")}</datalist><section class="workspace-section"><h3>接机后岗位偏好 · 通用</h3>${handoffPreferences(state)}</section>`;
}

export function bindTemplateConfig(root: HTMLElement, state: State, save: (action: string, payload: unknown) => Promise<boolean>, openCopy: (number: string) => void): void {
  bindHandoffPreferences(root, state, save);
  root.querySelectorAll<HTMLInputElement>("[data-template-demand]").forEach(input => input.onchange = () => {
    const flight = structuredClone(state.flightLibrary.find(f => f.flightNo === input.dataset.templateDemand)!);
    flight.roles ??= [];
    let role = flight.roles.find(r => r.roleName === input.dataset.templateRole);
    if (!role) { role = { roleName: input.dataset.templateRole!, requiredCount: null }; flight.roles.push(role); }
    role.requiredCount = input.value === "" ? null : Number(input.value);
    void save("save-flight-template", flight);
  });
  root.querySelectorAll<HTMLButtonElement>("[data-add-template-role]").forEach(button => button.onclick = async () => {
    const flightNo=button.dataset.addTemplateRole!;
    const input=[...root.querySelectorAll<HTMLInputElement>("[data-add-template-name]")].find(el=>el.dataset.addTemplateName===flightNo)!;
    const source=[...root.querySelectorAll<HTMLSelectElement>("[data-add-template-source]")].find(el=>el.dataset.addTemplateSource===flightNo)!;
    const sourceRoleName=source.value;
    const roleName=input.value;
    const previous=state.flightLibrary.find(f=>f.flightNo===flightNo)!.roleNames ?? state.configuration.roleConfigs.map(r=>r.roleName);
    const success=await save("add-template-role", {flightNo,roleName,sourceRoleName});
    const feedback=[...root.querySelectorAll<HTMLElement>("[data-template-add-feedback]")].find(el=>el.dataset.templateAddFeedback===flightNo)!;
    const currentSource=[...root.querySelectorAll<HTMLSelectElement>("[data-add-template-source]")].find(el=>el.dataset.addTemplateSource===flightNo)!;
    currentSource.value=sourceRoleName;
    if (!success) {
      [...root.querySelectorAll<HTMLInputElement>("[data-add-template-name]")].find(el=>el.dataset.addTemplateName===flightNo)!.value=roleName;
      feedback.setAttribute("role","alert"); feedback.textContent=root.querySelector(".app-message")?.textContent ?? "未增加岗位，请检查输入。"; return;
    }
    const added=[...root.querySelectorAll<HTMLInputElement>("[data-template-demand]")].find(el=>el.dataset.templateDemand===flightNo&&!previous.includes(el.dataset.templateRole!));
    if (added) {
      feedback.textContent=`已新增“${added.dataset.templateRole}”一行，请核对人数、性质和资质。${sourceRoleName === "大件行李拦截" ? "新增行独立保障；共享轮换仍由原大件岗位管理。" : ""}`;
      added.focus({preventScroll:true});
      added.closest("tr")!.scrollIntoView({block:"nearest"});
    }
  });
  root.querySelectorAll<HTMLSelectElement>("[data-add-template-source]").forEach(input => input.onchange = () => {
    const feedback=[...root.querySelectorAll<HTMLElement>("[data-template-add-feedback]")].find(el=>el.dataset.templateAddFeedback===input.dataset.addTemplateSource)!;
    feedback.setAttribute("role","status");
    feedback.textContent=input.value === "大件行李拦截" ? "新增行独立保障；共享轮换仍由原大件岗位管理。" : "";
  });
  root.querySelectorAll<HTMLInputElement>("[data-add-template-name]").forEach(input => input.onkeydown = event => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    [...root.querySelectorAll<HTMLButtonElement>("[data-add-template-role]")].find(button=>button.dataset.addTemplateRole===input.dataset.addTemplateName)?.click();
  });
  root.querySelectorAll<HTMLButtonElement>("[data-remove-template-role]").forEach(button => button.onclick = () => {
    const flight=structuredClone(state.flightLibrary.find(f=>f.flightNo===button.dataset.templateNumber)!);
    const role=button.dataset.removeTemplateRole!;
    if(!confirm(`从 ${flight.flightNo} 移除“${role}”？其他航班、人员资质和已有班表保留；需要时可再添加。`)) return;
    flight.roleNames=(flight.roleNames ?? state.configuration.roleConfigs.map(r=>r.roleName)).filter(name=>name!==role);
    void save("save-flight-template", flight);
  });
  root.querySelectorAll<HTMLButtonElement>("[data-copy-template]").forEach(button => button.onclick = () => openCopy(button.dataset.copyTemplate!));
}

export function prepareWithConfiguration(day: Day, state: State, newCell: (flight: Flight, role: string) => Cell, useTemplates: boolean): void {
  for (const person of state.people.filter(p => p.active)) {
    if (!day.roster.includes(person.name)) day.roster.push(person.name);
    let member = day.memberConfigs.find(m => m.memberName === person.name);
    if (!member) { member = { memberName: person.name, eligibleRoleNames: [...person.eligibleRoleNames] }; day.memberConfigs.push(member); }
    else if (useTemplates) member.eligibleRoleNames = [...person.eligibleRoleNames];
    if (!day.memberAvailability.some(a => a.memberName === person.name)) day.memberAvailability.push({ memberName: person.name, participating: false, availableFrom: null, availableUntil: null });
  }
  for (const entry of day.memberAvailability) if (!state.people.some(p => p.active && (p.status ?? "正常") === "正常" && p.name === entry.memberName)) entry.participating = false;
  const defaults = state.configuration.roleConfigs.map(r => structuredClone(state.newRoleDefaults[r.roleName] ?? r));
  day.roleConfigs = useTemplates ? defaults : [...day.roleConfigs, ...defaults.filter(r=>!day.roleConfigs.some(existing=>existing.roleName===r.roleName))];
  for (const role of day.roleConfigs) {
    if (!day.roleNames.includes(role.roleName)) day.roleNames.push(role.roleName);
    for (const flight of day.flights) if (!day.cells.some(c => c.flightId === flight.id && c.roleName === role.roleName)) {
      const cell = newCell(flight, role.roleName);
      const template = state.flightLibrary.find(f => f.flightNo === flight.flightNo);
      if (template?.roleNames && !template.roleNames.includes(role.roleName)) cell.requiredCountOverride = 0;
      day.cells.push(cell);
    }
  }
  if (!useTemplates) return;
  day.transitionMinutes = state.configuration.transitionMinutes;
  for (const flight of day.flights) {
    const template = state.flightLibrary.find(f => f.flightNo === flight.flightNo);
    if (!template) continue;
    flight.windowStart = template.windowStart; flight.windowEnd = template.windowEnd;
    for (const cell of day.cells.filter(c => c.flightId === flight.id)) {
      const role = template.roles?.find(r => r.roleName === cell.roleName);
      const included = !template.roleNames || template.roleNames.includes(cell.roleName);
      cell.requiredCountOverride = included ? role?.requiredCount ?? null : 0;
      cell.workModeOverride = included ? role?.workModeOverride ?? null : null;
      cell.workStart = included ? role?.workStart ?? null : null; cell.workEnd = included ? role?.workEnd ?? null : null;
    }
  }
}

export function templateForm(flight?: FlightTemplate): string {
  return `<form id="template-form" class="form-grid"><label>航班号<input id="template-number" value="${html(flight?.flightNo)}" ${flight ? "readonly" : ""} required /></label><label>保障开始<input id="template-start" value="${html(flight?.windowStart)}" placeholder="例如10:10" required /></label><label>保障结束<input id="template-end" value="${html(flight?.windowEnd)}" placeholder="跨日写次日01:10" required /></label></form>`;
}

export function flightLibraryHtml(state: State): string {
  return `<details id="flight-library" class="workspace-section"><summary>航班模板（${state.flightLibrary.length}）<button id="new-flight-template" class="btn btn-sm btn-primary">＋ 新增航班</button></summary><p class="section-note">长期维护航班与保障窗口。人员安排、当天出勤和固定人员不写入模板。</p><div class="flight-template-list">${state.flightLibrary.map(f=>`<article><strong>${html(f.flightNo)}</strong><span>${html(f.windowStart)}—${html(f.windowEnd)}</span><button data-edit-template="${html(f.flightNo)}" class="btn btn-sm btn-outline-secondary">编辑</button></article>`).join("")}</div></details>`;
}
