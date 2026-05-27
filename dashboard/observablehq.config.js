// observablehq.config.js — Observable Framework config
// Docs: https://observablehq.com/framework/config

export default {
  root: "src",
  title: "Watching a bot fact-check",

  // Show in the sidebar
  pages: [
    { name: "Dashboard", path: "/" },
    { name: "How it works", path: "/methodology" },
    { name: "About the bot", path: "/about" },
  ],

  // Top-level header / footer
  header: `<div style="font-weight: 600;">Watching a bot fact-check</div>
           <div style="opacity: .7; font-size: 14px;">An open audit of @alexcnotes, an AI Community Notes writer</div>`,
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
