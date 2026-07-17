export const Page = () => (
  <AuthGuard>
    <WidgetList />
  </AuthGuard>
);
export function gate(user: User) {
  return hasPermission(user, "widget:read");   // authz check
}
