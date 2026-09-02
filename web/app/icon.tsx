import { ImageResponse } from "next/og";

// Static-generated at build time (no dynamic params), works fine inside
// `output: "export"`. Mirrors the cyan-dot mark used in Nav/Footer instead
// of shipping a generic default favicon.
export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0e16",
          borderRadius: 7,
        }}
      >
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: "#22d3ee",
            boxShadow: "0 0 8px 2px rgba(34,211,238,0.65)",
          }}
        />
      </div>
    ),
    { ...size },
  );
}
