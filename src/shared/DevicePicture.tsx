import { deviceArtwork } from "./device-artwork";

export function DevicePicture({
  modelId,
  name,
  size = 120,
}: {
  modelId?: string | null;
  name: string;
  size?: number;
}) {
  const artwork = modelId ? deviceArtwork(modelId) : undefined;
  return artwork ? (
    <img
      src={artwork.src}
      alt={artwork.alt}
      width={size}
      height={size}
      draggable={false}
      style={{ objectFit: "contain", maxWidth: "100%", flexShrink: 0 }}
    />
  ) : (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 100"
      role="img"
      aria-label={`${name}: no model illustration available`}
      style={{ maxWidth: "100%", flexShrink: 0 }}
    >
      <rect
        x="22"
        y="27"
        width="76"
        height="48"
        rx="19"
        fill="#273b40"
        stroke="#729b94"
        strokeWidth="2"
      />
      <path
        d="M35 51h20m-10-10v20"
        stroke="#b5f0cb"
        strokeWidth="4"
        strokeLinecap="round"
      />
      <circle cx="78" cy="45" r="4" fill="#b5f0cb" />
      <circle cx="88" cy="57" r="4" fill="#a5b9b7" />
    </svg>
  );
}
