"use strict";

function pageItem(page) {
  const facts = [
    plural(page.keys, "key"),
    plural(page.walker_findings, "walker finding"),
    plural(page.model_findings, "model finding"),
  ].join(", ");
  return el(
    "li",
    {},
    el("a", { href: `page.html?id=${encodeURIComponent(page.id)}` }, page.title),
    " ",
    badge(page.label, page.set),
    el("span", { class: "facts" }, facts),
    page.superseded_by
      ? el(
          "span",
          { class: "facts" },
          `superseded by ${page.superseded_by} (page revised before any model run; not scored)`,
        )
      : null,
    page.task ? el("span", { class: "facts" }, `keyboard navigator, task "${page.task}"`) : null,
    page.source_url ? el("span", { class: "source" }, `Original: ${page.source_url}`) : null,
    page.notice ? el("span", { class: "notice" }, page.notice) : null,
  );
}

async function main() {
  const index = await loadJson("data/index.json");
  const lists = {
    w3c: document.getElementById("w3c-pages"),
    builder: document.getElementById("builder-pages"),
  };
  for (const page of index.pages) lists[page.set].append(pageItem(page));
}

main().catch(showError);
