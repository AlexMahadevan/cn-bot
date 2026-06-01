// observablehq.config.js — Observable Framework config
// Docs: https://observablehq.com/framework/config

export default {
  root: "src",
  // GitHub Pages serves the site under /cn-bot/. Setting base here ensures
  // absolute paths in the built site (CSS, JS, data) resolve correctly.
  base: process.env.OBSERVABLE_BASE_PATH || "/",
  title: "Watching a bot attempt to fact-check",

  // Show in the sidebar
  pages: [
    { name: "Dashboard", path: "/" },
    { name: "What X surfaces", path: "/pool" },
    { name: "How it compares", path: "/comparison" },
    { name: "How it works", path: "/methodology" },
    { name: "The prompts", path: "/prompts" },
  ],

  // Top-level header / footer
  header: `<div style="font-weight: 600;">Watching a bot attempt to fact-check</div>
           <div style="opacity: .7; font-size: 14px;">An open audit of Kind Raspberry Chickadee, an AI Community Notes writer</div>`,
  footer: `<div style="opacity: .6;">
             Built by <a href="https://www.poynter.org/author/alex-mahadevan/">Alex Mahadevan</a> ·
             <a href="https://github.com/AlexMahadevan/cn-bot">source</a> ·
             Updated automatically from the bot's audit log.
           </div>`,

  // Theme
  theme: "air",
  toc: true,
  sidebar: true,
  pager: true,

  // Auto-deploy hooks (filled in if/when we wire up Vercel/GitHub Pages)
  search: true,
};
