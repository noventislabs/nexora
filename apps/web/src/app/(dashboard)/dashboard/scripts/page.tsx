import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Scripts"
      phase="Phase 3"
      description="Versioned scripts with source references and fact-check results."
      delivers={["Hook, introduction, sections, evidence, conclusion and CTA","Every version stored immutably","Claim-level fact check with PASS / REVIEW / FAIL"]}
    />
  );
}
