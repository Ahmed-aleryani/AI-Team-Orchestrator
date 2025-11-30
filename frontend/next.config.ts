/** @type {import('next').NextConfig} */
const nextConfig = {
  //output: 'export',
  //distDir: 'out',
  images: {
    unoptimized: true,
  },
  env: {
    // Use environment variable or default to localhost for development
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
  async rewrites() {
    // Use environment variable for API proxy destination
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    return [
      {
        source: '/api/:path*',
        destination: `${apiUrl}/api/:path*`,
      },
    ]
  },
};

module.exports = nextConfig;