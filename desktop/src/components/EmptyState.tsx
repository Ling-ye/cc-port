import { HardDrive } from "lucide-react";

export function EmptyState({ text }: { text: string }) {
  return (
    <div className="empty">
      <HardDrive size={32} />
      <span>{text}</span>
    </div>
  );
}

