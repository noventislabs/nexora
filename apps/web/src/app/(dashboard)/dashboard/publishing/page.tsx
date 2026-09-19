import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Publishing"
      phase="Phase 5"
      description="Quality gates, approval and the YouTube upload pipeline."
      delivers={["Quality, copyright and metadata checks before upload","Explicit human approval or autopilot authorization","Idempotent upload with bounded retries and verification"]}
    />
  );
}
