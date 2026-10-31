/** View-only filtering. Never rebuild controls or change configuration. */
const beforeSearch = new Map<string, boolean>();
export function bindRulesSearch(root: HTMLElement, query: string, changed: (value: string) => void): void {
  const input = root.querySelector<HTMLInputElement>("#rules-search")!;
  const groups = [...root.querySelectorAll<HTMLDetailsElement>(".rule-group")];
  let searching = !!query;
  const filter = () => {
    const term = input.value.trim().toLocaleLowerCase();
    if (term && !searching) groups.forEach(group => beforeSearch.set(group.id, group.open));
    root.querySelectorAll("mark.rule-match").forEach(mark => mark.replaceWith(document.createTextNode(mark.textContent ?? "")));
    let matches = 0;
    for (const group of groups) {
      group.normalize();
      const matched = !term || (group.textContent ?? "").toLocaleLowerCase().includes(term);
      group.hidden = !matched;
      if (matched) matches++;
      if (term && matched) {
        group.open = true;
        const walker = document.createTreeWalker(group, NodeFilter.SHOW_TEXT);
        const nodes: Text[] = [];
        while (walker.nextNode()) nodes.push(walker.currentNode as Text);
        for (const node of nodes) {
          const index = node.data.toLocaleLowerCase().indexOf(term);
          if (index < 0 || node.parentElement?.closest("button, option, textarea, script")) continue;
          const after = node.splitText(index), rest = after.splitText(term.length);
          const mark = document.createElement("mark"); mark.className = "rule-match";
          after.replaceWith(mark); mark.append(after); void rest;
        }
      } else if (!term && searching) group.open = beforeSearch.get(group.id) ?? false;
    }
    searching = !!term;
    root.querySelector<HTMLElement>("#rules-search-empty")!.hidden = matches > 0;
  };
  input.value = query; filter();
  input.oninput = () => { changed(input.value); filter(); };
}
