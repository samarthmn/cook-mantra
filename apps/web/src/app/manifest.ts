import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Cook Mantra",
    short_name: "Cook Mantra",
    description: "Confirm what you have, then cook something practical.",
    start_url: "/",
    display: "standalone",
    background_color: "#f3f2f2",
    theme_color: "#ec3013",
    icons: [
      { src: "/brand/cook-mantra-192.png", sizes: "192x192", type: "image/png" },
      { src: "/brand/cook-mantra-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
