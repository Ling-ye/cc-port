export function Banner({ tone, text }: { tone: "success" | "danger"; text: string }) {
  return <div className={`banner ${tone}`}>{text}</div>;
}

