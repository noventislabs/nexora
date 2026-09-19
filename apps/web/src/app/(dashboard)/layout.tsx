import Link from "next/link";
import { SideNav } from "@/components/nav";
import { SessionGate } from "@/components/session-gate";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionGate>
      <div className="min-h-screen lg:grid lg:grid-cols-[228px_1fr]">
        <aside className="border-b border-base-800 bg-base-900/60 px-4 py-4 lg:sticky lg:top-0 lg:h-screen lg:overflow-y-auto lg:border-r lg:border-b-0">
          <Link href="/dashboard" className="mb-5 block">
            <div className="text-[13px] font-semibold tracking-[0.22em] text-base-100">NEXORA</div>
            <div className="text-[10px] tracking-[0.2em] text-accent-500">AI AUTOPILOT</div>
          </Link>
          <SideNav />
        </aside>
        <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </SessionGate>
  );
}
