"use strict";

const SVG = "http://www.w3.org/2000/svg";
const QUESTIONS = {
  inside: "focus inside what opened",
  returned: "focus back on the trigger after Escape",
};

let page = null;
let current = 0;

function svg(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

// Walk steps count from 0; the viewer shows the state before any key as 0,
// so step s is key s + 1.
function keyLabel(step) {
  return `key ${step + 1}`;
}

const DOCUMENT_TAGS = new Set(["BODY", "HTML"]);

// Focus on the document has the whole page as its box; only an element's box is drawn.
function elementBox(focus) {
  return focus.tag && !DOCUMENT_TAGS.has(focus.tag) ? focus.box : null;
}

function describeFocus(focus) {
  if (!elementBox(focus)) return "focus on the document (no element box)";
  const name = focus.name ? ` "${focus.name}"` : "";
  return `focus on ${focus.role || "element"}${name} (${focus.tag})`;
}

function showButton(step) {
  if (step === null || step === undefined) return null;
  const button = el("button", { type: "button", class: "show" }, `Show ${keyLabel(step)}`);
  button.addEventListener("click", () => {
    show(step + 1);
    document.getElementById("captures").focus();
  });
  return button;
}

function center(frame, scrollX, scrollY) {
  const box = elementBox(frame.focus);
  return [
    Math.round(box.x + frame.scroll_x - scrollX + box.width / 2),
    Math.round(box.y + frame.scroll_y - scrollY + box.height / 2),
  ];
}

function drawPanel(frame) {
  const panel = document.getElementById("panel");
  panel.replaceChildren(svg("image", { href: frame.screenshot, width: 1024, height: 768 }));
  const stops = page.frames
    .slice(1, current + 1)
    .filter((f) => f.key === "Tab" && elementBox(f.focus))
    .map((f) => center(f, frame.scroll_x, frame.scroll_y));
  if (stops.length) {
    const points = stops.map((p) => p.join(",")).join(" ");
    panel.append(svg("polyline", { class: "tab-path-halo", points }));
    panel.append(svg("polyline", { class: "tab-path", points }));
    stops.forEach(([x, y]) => panel.append(svg("circle", { class: "tab-stop", cx: x, cy: y, r: 4 })));
  }
  const box = elementBox(frame.focus);
  if (box) {
    const rect = {
      x: Math.round(box.x),
      y: Math.round(box.y),
      width: Math.round(box.width),
      height: Math.round(box.height),
    };
    panel.append(svg("rect", { ...rect, class: "focus-box-halo" }));
    panel.append(svg("rect", { ...rect, class: "focus-box" }));
  }
  return stops.length;
}

function show(index) {
  const last = page.frames.length - 1;
  current = Math.max(0, Math.min(last, index));
  const frame = page.frames[current];
  const status =
    current === 0 ? `Before the first key (0 of ${last})` : `Key ${current} of ${last}: ${frame.key}`;
  document.getElementById("step-status").textContent = status;
  document.getElementById("key-slider").value = String(current);
  document.getElementById("prev").disabled = current === 0;
  document.getElementById("next").disabled = current === last;
  const capture = document.getElementById("capture");
  capture.src = frame.screenshot;
  capture.alt = `Page capture, ${status.toLowerCase()}`;
  const stops = drawPanel(frame);
  const appeared = frame.appeared ? ` ${plural(frame.appeared, "element")} appeared.` : "";
  document.getElementById("focus-desc").textContent =
    `Panel: ${describeFocus(frame.focus)}; Tab path of ${plural(stops, "stop")}.${appeared}`;
}

function setUpViewer() {
  const slider = document.getElementById("key-slider");
  slider.max = String(page.frames.length - 1);
  slider.addEventListener("input", () => show(Number(slider.value)));
  document.getElementById("prev").addEventListener("click", () => show(current - 1));
  document.getElementById("next").addEventListener("click", () => show(current + 1));
  const moves = {
    ArrowLeft: () => current - 1,
    ArrowRight: () => current + 1,
    Home: () => 0,
    End: () => page.frames.length - 1,
  };
  document.getElementById("captures").addEventListener("keydown", (event) => {
    if (!(event.key in moves) || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    show(moves[event.key]());
  });
  document.getElementById("viewer").hidden = false;
  show(0);
}

function about() {
  const box = document.getElementById("about");
  box.append(el("p", { class: "label" }, badge(page.label, page.set), ` ${page.page}`));
  if (page.set === "w3c") {
    box.append(
      el(
        "div",
        { class: "provenance" },
        el("p", {}, "Original page: ", el("a", { href: page.source_url }, page.source_url)),
        el("p", { class: "notice" }, page.notice),
        el("p", {}, "Shown unmodified; the tool's drawing is a separate layer in the panel."),
      ),
    );
  } else {
    box.append(el("p", { class: "provenance" }, "builder-made: written and labelled by the builder (MIT)"));
  }
  if (page.superseded_by) {
    box.append(
      el("p", { class: "facts" }, `Superseded by ${page.superseded_by}: the page was revised before any model run, and this run is not scored.`),
    );
  }
}

function findingItem(finding) {
  const element = finding.element;
  const name = element.name ? ` "${element.name}"` : "";
  return el(
    "li",
    {},
    badge(finding.status, "verified"),
    finding.best_practice ? badge("best practice", "practice") : null,
    ` ${finding.rule}: ${element.role || element.tag}${name}, ${finding.detail}`,
    finding.step === null ? null : ` (at ${keyLabel(finding.step)}) `,
    showButton(finding.step),
  );
}

function verdicts() {
  const section = document.getElementById("verdicts");
  const frozen = page.verdicts;
  section.append(
    el(
      "p",
      {},
      badge("VERIFIED", "verified"),
      ` Walker verdicts frozen ${frozen.frozen_at}, before any model call. SHA-256 `,
      el("code", { class: "hash" }, frozen.sha256),
    ),
  );
  for (const walker of frozen.walkers) {
    const checked = Object.entries(walker.checked)
      .map(([rule, count]) => `${rule} ${count}`)
      .join(", ");
    section.append(el("h3", {}, walker.walker), el("p", { class: "facts" }, `Checked: ${checked}.`));
    section.append(
      walker.findings.length
        ? el("ul", { class: "findings" }, walker.findings.map(findingItem))
        : el("p", {}, "No finding."),
    );
  }
  section.hidden = false;
}

function answerItem(instance, question) {
  const answer = instance[question];
  return el(
    "li",
    {},
    answer.status ? badge(answer.status, "review") : null,
    ` ${QUESTIONS[question]}: ${answer.answer}. ${answer.reason}`,
  );
}

function reviews() {
  const section = document.getElementById("reviews");
  section.hidden = false;
  if (!page.reviews.length) {
    section.append(el("p", {}, "No model review for this page."));
    return;
  }
  section.append(
    el("p", {}, "A model finding is marked NEEDS REVIEW: a person decides whether it is a bug."),
  );
  for (const review of page.reviews) {
    section.append(
      el("h3", {}, `Run ${review.run}`),
      el("p", { class: "facts" }, `${review.model}, reasoning effort ${review.reasoning_effort}, prompt SHA-256 `, el("code", { class: "hash" }, review.prompt_sha256)),
    );
    for (const instance of review.instances) {
      const enter = instance.steps.enter;
      section.append(
        el("p", {}, `After Enter at ${keyLabel(enter)}: opened ${instance.opened}. `, showButton(enter)),
        el("ul", { class: "findings" }, Object.keys(QUESTIONS).map((q) => answerItem(instance, q))),
      );
    }
  }
  if (page.reviews.length > 1) {
    const apart = page.disagreements.map((d) => `after Enter at ${keyLabel(d.enter)}, ${QUESTIONS[d.question]}`);
    section.append(
      el("p", { class: "agreement" }, apart.length ? `The runs disagree on: ${apart.join("; ")}.` : "The runs agree on every answer."),
    );
  }
}

function packetItem(packet) {
  return el(
    "li",
    {},
    badge(packet.status, packet.status === "VERIFIED" ? "verified" : "review"),
    packet.best_practice ? badge("best practice", "practice") : null,
    ` ${packet.sentence} `,
    showButton(packet.step),
  );
}

function packets() {
  const section = document.getElementById("packets");
  if (!page.packets.length) return;
  for (const file of page.packets) {
    section.append(
      el("h3", {}, `Packets of run ${file.run}`),
      file.packets.length
        ? el("ul", { class: "findings" }, file.packets.map(packetItem))
        : el("p", {}, "No packet."),
      el("p", { class: "disclaimer" }, file.disclaimer),
    );
  }
  section.hidden = false;
}

async function main() {
  const id = new URLSearchParams(location.search).get("id");
  if (!id) throw new Error("no page id in the address");
  page = await loadJson(`data/pages/${encodeURIComponent(id)}.json`);
  document.title = `${page.title} (${page.label}) - NoMouse replay`;
  document.getElementById("title").textContent = page.title;
  about();
  setUpViewer();
  verdicts();
  reviews();
  packets();
}

main().catch(showError);
