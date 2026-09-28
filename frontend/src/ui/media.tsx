import { Chip, Modal } from "@heroui/react";
import { Waveform } from "@phosphor-icons/react";
import type { Media } from "../lib/run";

export function AudioRow({ a }: { a: Media }) {
  return (
    <div className="rounded-xl border border-border bg-surface px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-sm">
          <Waveform size={16} className="shrink-0 text-accent" />
          <span className="truncate">{a.label ?? a.path}</span>
        </span>
        <span className="flex shrink-0 gap-1">
          {a.duration != null && <Chip size="sm" variant="soft">{Number(a.duration).toFixed(2)} s</Chip>}
          {a.lufs != null && <Chip size="sm" variant="soft">{a.lufs} LUFS</Chip>}
          {a.chars != null && <Chip size="sm" variant="soft" color={a.cached ? "success" : "accent"}>{a.cached ? "cached" : `${a.chars} chars`}</Chip>}
        </span>
      </div>
      <audio controls preload="none" src={a.url} className="h-9 w-full" />
      {a.text && <p className="mt-1.5 line-clamp-3 text-xs text-muted">“{a.text}”</p>}
    </div>
  );
}

export function Lightbox({ media, onClose }: { media: Media | null; onClose: () => void }) {
  return (
    <Modal.Backdrop isOpen={!!media} onOpenChange={(v) => !v && onClose()}>
      <Modal.Container size="lg">
        <Modal.Dialog className="sm:max-w-[min(1100px,92vw)]">
          <Modal.CloseTrigger />
          <Modal.Header>
            <Modal.Heading className="truncate pr-8">{media?.label ?? media?.path}</Modal.Heading>
            <p className="truncate font-mono text-xs text-muted">{media?.path}</p>
          </Modal.Header>
          <Modal.Body>
            {media && (
              <a href={media.url} target="_blank" rel="noreferrer" className="checker block overflow-hidden rounded-xl border border-border">
                <img src={media.url} alt={media.label ?? ""} className="w-full" />
              </a>
            )}
          </Modal.Body>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
