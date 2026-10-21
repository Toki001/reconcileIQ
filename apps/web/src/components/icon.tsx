const paths = {
  overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  invoices:
    "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h5",
  orders: "M9 5H5v16h14V5h-4 M9 3h6v4H9z M8 12h8 M8 16h5",
  reconciliation: "M4 7h15l-4-4 M20 17H5l4 4 M19 7l-4 4 M5 17l4-4",
  exceptions: "M12 3 2 21h20L12 3z M12 9v5 M12 17v1",
  audit: "M3 12a9 9 0 1 0 3-6 M3 3v6h6 M12 7v5l3 2",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  arrow: "M5 12h14 M14 7l5 5-5 5",
  check: "M5 12l4 4L19 6",
  close: "M6 6l12 12 M6 18 18 6",
  chevron: "M9 5l7 7-7 7",
  shield: "M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6z M8 12l3 3 5-6",
};

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
