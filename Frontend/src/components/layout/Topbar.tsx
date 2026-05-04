import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useLocation } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

const PAGE_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/cameras": "Cameras",
  "/employees": "Employees",
  "/sessions": "Sessions",
  "/alerts": "Alerts",
};

export function Topbar() {
  const { pathname } = useLocation();
  const qc = useQueryClient();
  const title = PAGE_TITLES[pathname] ?? "Airco Secure";

  const handleRefresh = () => {
    qc.invalidateQueries();
    toast.success("Data refreshed");
  };

  return (
    <header className="fixed left-60 right-0 top-0 z-30 flex h-14 items-center justify-between border-b border-zinc-800 bg-zinc-950 px-6">
      <h1 className="text-base font-semibold text-zinc-50">{title}</h1>
      <Button
        variant="ghost"
        size="icon"
        onClick={handleRefresh}
        className="h-8 w-8 text-zinc-400 hover:text-zinc-100"
      >
        <RefreshCw className="h-4 w-4" />
      </Button>
    </header>
  );
}
