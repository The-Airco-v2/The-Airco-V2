import { Loader2 } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { useAuth } from "@/lib/auth";

export default function AppLayout() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950">
        <Loader2 className="h-6 w-6 animate-spin text-sky-500" />
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  if (status === "not_provisioned") {
    return <Navigate to="/account-not-provisioned" replace />;
  }
  if (status === "inactive") {
    return <Navigate to="/account-inactive" replace />;
  }
  if (status === "tenant_inactive") {
    return <Navigate to="/inactive-tenant" replace />;
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      <Sidebar />
      <Topbar />
      <main className="ml-60 pt-14">
        <div className="p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
