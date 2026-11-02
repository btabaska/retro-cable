/** Focus the current admin screen's title (every h1.adm-title has tabIndex -1), so keyboard and
 * screen reader users land somewhere sensible after the screen changes (M2 D23). */
export function focusTitle(): void {
  document.querySelector<HTMLElement>("h1.adm-title")?.focus();
}
