import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Analytics"
      phase="Phase 6"
      description="Real YouTube metrics with their source and data age."
      delivers={["Channel and video metrics from the YouTube APIs","Data source and age shown on every figure","Revenue only when a supported monetization source is connected"]}
    />
  );
}
