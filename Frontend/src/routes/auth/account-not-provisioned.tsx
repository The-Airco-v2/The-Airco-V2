import { ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function AccountNotProvisionedPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-6 text-center">
      <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-500/10">
        <ShieldAlert className="h-7 w-7 text-amber-400" />
      </div>
      <h1 className="text-xl font-semibold text-zinc-50">Account not provisioned</h1>
      <p className="mt-2 max-w-sm text-sm text-zinc-400">
        Your account exists but hasn&apos;t been set up yet. Contact your system administrator to get access.
      </p>
      <Button asChild variant="outline" className="mt-8 border-zinc-700 text-zinc-300 hover:bg-zinc-800">
        <Link to="/login">Sign in with a different account</Link>
      </Button>
    </div>
  );
}
