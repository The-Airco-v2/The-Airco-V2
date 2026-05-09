import { Bell, Camera, FileBarChart2, GitMerge, LayoutDashboard, LogOut, PlayCircle, Users } from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/cameras", icon: Camera, label: "Camera Manager" },
  { to: "/reports", icon: FileBarChart2, label: "Reports" },
  { to: "/identity-review", icon: GitMerge, label: "Identity Review" },
  { to: "/employees", icon: Users, label: "Employees" },
  { to: "/sessions", icon: PlayCircle, label: "Sessions" },
  { to: "/alerts", icon: Bell, label: "Alerts" },
];

export function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
    toast.success("Signed out");
  };

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-zinc-800 bg-zinc-900">
      <div className="flex h-14 items-center gap-2.5 border-b border-zinc-800 px-5">
        <div className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-lg bg-zinc-950 ring-1 ring-zinc-700/80">
          <img src="/logo.png" alt="Airco Secure" className="h-full w-full object-contain p-0.5" />
        </div>
        <span className="text-sm font-semibold tracking-tight text-zinc-50">Airco Secure</span>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4">
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "border-l-2 border-sky-500 bg-zinc-800 pl-[10px] text-zinc-50"
                  : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-50",
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-zinc-800 p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-xs font-medium text-zinc-300">
            {user?.name?.charAt(0)?.toUpperCase() ?? "?"}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-zinc-100">{user?.name ?? "—"}</p>
            <p className="truncate text-xs capitalize text-zinc-500">{user?.role ?? ""}</p>
          </div>
          <button
            onClick={handleLogout}
            className="shrink-0 rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
            title="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
