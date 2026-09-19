import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NEXORA AI AUTOPILOT",
  description:
    "Automated YouTube content research, production, publishing and analytics — transparent, controllable and based on real data.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#070a0f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-base-950 text-base-200 antialiased">{children}</body>
    </html>
  );
}
