import { motion } from "framer-motion";
import { Eye, EyeOff, Loader2, Lock, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { BackendAuthResponse } from "@/types";

const ERROR_MESSAGES: Record<string, string> = {
  auth_invalid_credentials: "Invalid email or password.",
  auth_login_unavailable: "Authentication service unavailable.",
  auth_login_rate_limited: "Too many login attempts. Please try again later.",
  auth_not_configured: "Authentication is not configured.",
};

const ACCOUNT_STATE_MESSAGES: Record<string, string> = {
  not_provisioned: "This account has not been provisioned. Contact your administrator.",
  inactive_user: "Your account is inactive. Contact your administrator.",
  inactive_tenant: "Your organisation's account is inactive.",
};

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { status, refresh } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/dashboard";

  useEffect(() => {
    if (status === "authenticated") {
      navigate(next, { replace: true });
    }
  }, [status, navigate, next]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiFetch("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const detail =
          data && typeof data === "object" && "detail" in data && typeof data.detail === "object"
            ? data.detail
            : null;
        const code = detail && "code" in detail && typeof detail.code === "string" ? detail.code : null;
        const message =
          detail && "message" in detail && typeof detail.message === "string" ? detail.message : null;
        const accountState =
          data && typeof data === "object" && "accountState" in data && typeof data.accountState === "string"
            ? data.accountState
            : null;

        setError(
          (code ? ERROR_MESSAGES[code] : undefined) ??
            (accountState ? ACCOUNT_STATE_MESSAGES[accountState] : undefined) ??
            message ??
            "Login failed. Try again.",
        );
        return;
      }

      const payload = data as BackendAuthResponse | null;
      if (payload?.accountState && payload.accountState !== "authenticated") {
        setError(ACCOUNT_STATE_MESSAGES[payload.accountState] ?? payload.message ?? "Login failed. Try again.");
        return;
      }

      await refresh();
    } catch {
      setError("Network error. Check your connection and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-zinc-950">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-zinc-900 p-10 lg:flex lg:w-[45%]">
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              "linear-gradient(to right, #71717a 1px, transparent 1px), linear-gradient(to bottom, #71717a 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />
        <div className="absolute -left-20 -top-20 h-72 w-72 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="absolute -bottom-20 -right-20 h-72 w-72 rounded-full bg-violet-500/10 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-xl bg-zinc-950 ring-1 ring-zinc-700/80">
            <img src="/logo.png" alt="Airco Secure" className="h-full w-full object-contain p-0.5" />
          </div>
          <span className="text-lg font-semibold text-zinc-50">Airco Secure</span>
        </div>

        <div className="relative">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <h2 className="text-3xl font-bold leading-tight text-zinc-50">
              Workforce intelligence.
              <br />
              <span className="text-sky-400">Powered by camera.</span>
            </h2>
            <p className="mt-4 leading-relaxed text-zinc-400">
              Real-time presence, attendance, and behaviour analytics for your office — built for operators, trusted by enterprise.
            </p>
          </motion.div>
        </div>

        <div className="relative text-xs text-zinc-600">© {new Date().getFullYear()} Airco. All rights reserved.</div>
      </div>

      <div className="flex flex-1 items-center justify-center p-8">
        <motion.div
          className="w-full max-w-sm"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <div className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-lg bg-zinc-950 ring-1 ring-zinc-700/80">
              <img src="/logo.png" alt="Airco Secure" className="h-full w-full object-contain p-0.5" />
            </div>
            <span className="font-semibold text-zinc-50">Airco Secure</span>
          </div>

          <div className="mb-8">
            <h1 className="text-2xl font-bold text-zinc-50">Sign in</h1>
            <p className="mt-1 text-sm text-zinc-400">Enter your credentials to access your workspace.</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-zinc-300">
                Email
              </Label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
                <Input
                  id="email"
                  type="email"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className="border-zinc-700 bg-zinc-900 pl-9 text-zinc-50 placeholder:text-zinc-600 focus-visible:ring-sky-500"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password" className="text-zinc-300">
                Password
              </Label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  className="border-zinc-700 bg-zinc-900 pl-9 pr-10 text-zinc-50 placeholder:text-zinc-600 focus-visible:ring-sky-500"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 transition-colors hover:text-zinc-300"
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {error && (
              <Alert variant="destructive" className="border-red-800 bg-red-950/40">
                <AlertDescription className="text-sm text-red-300">{error}</AlertDescription>
              </Alert>
            )}

            <Button
              type="submit"
              disabled={loading}
              className={cn(
                "w-full bg-sky-500 font-medium text-white transition-colors hover:bg-sky-400",
                "disabled:opacity-60",
              )}
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>
        </motion.div>
      </div>
    </div>
  );
}
