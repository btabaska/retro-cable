type TokenGroups = Record<string, unknown>;

const kebab = (name: string) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** Flatten design/tokens.json into CSS custom properties: --rc-<group>-<name>. */
export function tokensToCssVars(tokens: TokenGroups): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [group, values] of Object.entries(tokens)) {
    if (group.startsWith("$") || typeof values !== "object" || values === null) continue;
    for (const [name, value] of Object.entries(values)) {
      if (typeof value !== "string" && typeof value !== "number") continue;
      vars[`--rc-${kebab(group)}-${kebab(name)}`] = typeof value === "number" ? `${value}px` : value;
    }
  }
  return vars;
}

export function applyTokens(tokens: TokenGroups, root: HTMLElement = document.documentElement) {
  for (const [name, value] of Object.entries(tokensToCssVars(tokens))) {
    root.style.setProperty(name, value);
  }
}
