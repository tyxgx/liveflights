/** @type {import('next').NextConfig} */
const nextConfig = {
  // react-leaflet's MapContainer does not support React 18 StrictMode's
  // dev-only double-invoke of effects cleanly — remounting throws "Map
  // container is already initialized." This only manifests under `next
  // dev`; production builds never double-invoke effects. Disabling
  // StrictMode here is the standard fix used across react-leaflet +
  // Next.js projects rather than working around Leaflet's internals.
  reactStrictMode: false,

  // Static export to web/out/, deployed as-is to S3 (static website hosting,
  // no CloudFront — see docs/aws-architecture.md). All pages are client
  // components hitting the API Gateway URL directly, so no Next.js server
  // runtime is needed.
  output: "export",
  images: { unoptimized: true }, // next/image's optimizer needs a server; static export has none
};

module.exports = nextConfig;
