import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Research"
      phase="Phase 3"
      description="Source-backed research with claims classified before any script is written."
      delivers={["Source collection and key-fact extraction","FACT / CLAIM / ANALYSIS / OPINION / UNKNOWN classification","Preserved conflicts and recorded uncertainty"]}
    />
  );
}
