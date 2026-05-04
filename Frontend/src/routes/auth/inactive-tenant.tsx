import { ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function InactiveTenantPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-6 text-center">
      <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl bg-red-500/10">
        <ShieldAlert className="h-7 w-7 text-red-400" />
      </div>
      <h1 className="text-xl font-semibold text-zinc-50">Organisation inactive</h1>
      <p className="mt-2 max-w-sm text-sm text-zinc-400">
        Your organisation&apos;s account is currently inactive. Contact your administrator for more information.
      </p>
      <Button asChild variant="outline" className="mt-8 border-zinc-700 text-zinc-300 hover:bg-zinc-800">
        <Link to="/login">Sign in with a different account</Link>
      </Button>
    </div>
  );
}
