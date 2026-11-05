/** Capture group scope when creating a request; server preference never routes it. */
let selected: "A" | "B" | undefined;
export function selectApiGroup(group: "A" | "B"): void { selected = group; }
export function groupUrl(path: string, group = selected): string {
  return group && path.startsWith("/api/") && !path.startsWith("/api/groups")
    ? `/api/groups/${group}${path.slice(4)}` : path;
}
export function groupFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(groupUrl(path), init);
}
