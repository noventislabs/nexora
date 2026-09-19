import { Card } from "@/components/primitives";

/**
 * An honest placeholder.
 *
 * This is NOT a mock screen: it renders no invented rows, counts or charts. It states
 * plainly which build phase delivers the section, so the navigation is complete
 * without any screen implying a capability that does not exist yet.
 */
export function PhasePlaceholder({
  title,
  phase,
  description,
  delivers,
}: {
  title: string;
  phase: string;
  description: string;
  delivers: string[];
}) {
  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-base-100">{title}</h1>
        <p className="mt-0.5 text-sm text-base-400">{description}</p>
      </header>
      <Card title={`Not built yet · ${phase}`}>
        <p className="text-sm text-base-300">
          This section has no data to show because the feature is not implemented in this
          build yet. Nothing here is simulated.
        </p>
        <ul className="mt-3 space-y-1.5">
          {delivers.map((item) => (
            <li key={item} className="flex gap-2 text-sm text-base-400">
              <span className="text-base-600" aria-hidden>
                ·
              </span>
              {item}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
