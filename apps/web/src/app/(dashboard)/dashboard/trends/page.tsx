import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Trends"
      phase="Phase 2"
      description="Normalized trend observations from permitted sources, scored for opportunity."
      delivers={["Pluggable trend sources (YouTube Data API, RSS, Reddit)","Normalization into one schema with sparse engagement data","Transparent Opportunity Score with a visible breakdown"]}
    />
  );
}
