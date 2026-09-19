import { PhasePlaceholder } from "@/components/phase-placeholder";

export default function Page() {
  return (
    <PhasePlaceholder
      title="Production"
      phase="Phase 4"
      description="Narration, assets, rendering, subtitles and thumbnails."
      delivers={["Voice provider abstraction with a real NOT CONFIGURED state","Asset manager that records licence provenance","FFmpeg render pipeline, SRT/VTT subtitles and thumbnails"]}
    />
  );
}
