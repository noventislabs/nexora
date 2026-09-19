import { UNKNOWN_PLACEHOLDER } from "@/components/primitives";

/**
 * Licence status for an asset.
 *
 * `LICENSE UNKNOWN` is the default for anything NEXORA did not create itself, and it
 * is shown as a warning rather than a neutral state — an unestablished licence blocks
 * autonomous publishing.
 */
const STYLES: Record<string, string> = {
  PERMITTED: "text-ok-500 border-ok-500/30 bg-ok-500/10",
  "LICENSE UNKNOWN": "text-warn-500 border-warn-500/30 bg-warn-500/10",
  PROHIBITED: "text-danger-500 border-danger-500/30 bg-danger-500/10",
};

export function LicenseBadge({
  status,
  licenseType,
}: {
  status: string;
  licenseType?: string | null;
}) {
  const style = STYLES[status] ?? STYLES["LICENSE UNKNOWN"];
  return (
    <span
      data-testid="license-badge"
      title={
        status === "PERMITTED"
          ? `Licence: ${licenseType ?? UNKNOWN_PLACEHOLDER}. Cleared for use.`
          : "This asset blocks autonomous publishing until its licence is established."
      }
      className={`inline-block shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] tracking-wider ${style}`}
    >
      {status}
    </span>
  );
}

/**
 * Whether a duration was measured or derived.
 *
 * Every runtime NEXORA shows for produced media comes from ffprobe. This label makes
 * that visible so a measured figure is never confused with the script's estimate.
 */
export function TimingBadge({ source }: { source: "provider" | "estimated" | null }) {
  if (source === null) {
    return <span className="font-mono text-[10px] text-base-500">TIMING {UNKNOWN_PLACEHOLDER}</span>;
  }
  const estimated = source === "estimated";
  return (
    <span
      data-testid="timing-badge"
      title={
        estimated
          ? "This voice provider returns no timings, so cue boundaries were interpolated from the measured audio duration. They are approximate."
          : "Cue times come from the synthesizer's own character-level alignment."
      }
      className={`font-mono text-[10px] tracking-wider ${estimated ? "text-warn-500" : "text-ok-500"}`}
    >
      {estimated ? "TIMING ESTIMATED" : "TIMING MEASURED"}
    </span>
  );
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return UNKNOWN_PLACEHOLDER;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return UNKNOWN_PLACEHOLDER;
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  const remainder = whole % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}
