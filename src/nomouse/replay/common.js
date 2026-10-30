"use strict";

// Strings become text nodes, so run data is never parsed as HTML.
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined) node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function badge(text, kind) {
  return el("span", { class: `badge ${kind}` }, text);
}

async function loadJson(path) {
  const reply = await fetch(path);
  if (!reply.ok) throw new Error(`${path}: HTTP ${reply.status}`);
  return reply.json();
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function showError(error) {
  document
    .getElementById("main")
    .append(el("p", { class: "error", role: "alert" }, `Could not load the replay: ${error.message}`));
}
