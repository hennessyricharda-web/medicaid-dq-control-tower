/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Separate build outputs (for example a mock and a live build side by side): NEXT_DIST_DIR=.next/mock.
  // Keep it under .next/ so it stays git-ignored and outside the naming guard's scan.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
