import { dispositionTone, isKnownDisposition, stateTone, TONE_GLYPH, type Tone } from "@/lib/dispositions";

export function ToneChip({ tone, label, title }: { tone: Tone; label: string; title?: string }) {
  return (
    <span className={`chip t-${tone}`} title={title}>
      <span className="g" aria-hidden="true">{TONE_GLYPH[tone]}</span>
      {label}
    </span>
  );
}

/** Disposition: colour + glyph + text label. Unknown values are labelled, never hidden. */
export function DispositionChip({ value }: { value: string }) {
  const label = isKnownDisposition(value) ? value : `UNKNOWN: ${value}`;
  return <ToneChip tone={dispositionTone(value)} label={label} title={`Disposition ${label}`} />;
}

export function StateChip({ value }: { value: string }) {
  return <ToneChip tone={stateTone(value)} label={value} title={`Lifecycle state ${value}`} />;
}
