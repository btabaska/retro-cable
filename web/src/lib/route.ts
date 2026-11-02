/** /admin and anything under it is the phone admin; everything else is the TV. */
export function isAdminPath(pathname: string): boolean {
  return pathname === "/admin" || pathname.startsWith("/admin/");
}
