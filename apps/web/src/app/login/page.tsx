"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { Button, ErrorNotice, Loading } from "@/components/primitives";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    apiFetch<{ open: boolean }>("/api/auth/registration-open")
      .then(({ open }) => setMode(open ? "register" : "login"))
      .catch(() => setMode("login"));
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const path = mode === "register" ? "/api/auth/register" : "/api/auth/login";
      const body =
        mode === "register" ? { email, password, display_name: displayName } : { email, password };
      await apiFetch(path, { method: "POST", body });
      router.replace("/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the NEXORA API. Check that the backend is running.",
      );
      setSubmitting(false);
    }
  }

  if (mode === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loading label="Contacting API" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="text-sm font-semibold tracking-[0.28em] text-base-100">NEXORA</div>
          <div className="mt-1 text-[11px] tracking-[0.22em] text-accent-500">AI AUTOPILOT</div>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-xl border border-base-700 bg-base-900/70 p-5"
        >
          <h1 className="text-sm font-medium text-base-100">
            {mode === "register" ? "Create the operator account" : "Sign in"}
          </h1>
          {mode === "register" && (
            <p className="text-xs text-base-400">
              This deployment has no account yet. The first account you create becomes the owner.
            </p>
          )}

          {mode === "register" && (
            <Field
              label="Display name"
              value={displayName}
              onChange={setDisplayName}
              autoComplete="name"
              required
            />
          )}
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            required
          />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            hint={mode === "register" ? "At least 12 characters." : undefined}
            required
          />

          {error && <ErrorNotice message={error} />}

          <Button type="submit" variant="primary" disabled={submitting} className="w-full">
            {submitting ? "Working…" : mode === "register" ? "Create account" : "Sign in"}
          </Button>
        </form>

        <p className="mt-6 text-center text-[11px] leading-relaxed text-base-500">
          NEXORA automates a YouTube workflow. It does not guarantee views, subscribers,
          monetization or revenue.
        </p>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  hint,
  ...rest
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  hint?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "type">) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs text-base-400">{label}</span>
      <input
        {...rest}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-base-600 bg-base-850 px-3 py-2 text-sm text-base-100 outline-none focus:border-accent-600"
      />
      {hint && <span className="mt-1 block text-[11px] text-base-500">{hint}</span>}
    </label>
  );
}
