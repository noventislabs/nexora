import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Ideas"
      phase="Phase 3"
      description="AI-proposed topic candidates, each traceable to its evidence."
      delivers={["Topic candidates with angle, audience, why-now and risks","Approve / reject / save / regenerate decisions","Conversion of an approved idea into a content project"]}
    />
  );
}
