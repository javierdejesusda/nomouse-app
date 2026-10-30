"use strict";

function table(caption, headers, rows) {
  return el(
    "table",
    {},
    el("caption", {}, caption),
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col" }, h)))),
    el(
      "tbody",
      {},
      rows.map(([arm, ...cells]) =>
        el("tr", {}, el("th", { scope: "row" }, arm), cells.map((c) => el("td", {}, String(c)))),
      ),
    ),
  );
}

function of(count, total) {
  return `${count} of ${total}`;
}

function corrected(audit, arm, counts) {
  const value = audit.false_flags_strict[arm];
  return value === undefined ? `${counts.false_flags_strict} (no correction)` : value;
}

async function main() {
  const index = await loadJson("data/index.json");
  const box = document.getElementById("eval");
  if (!index.eval) {
    box.append(el("p", {}, "No eval report in this export."));
    return;
  }
  const report = await loadJson("data/eval.json");
  const arms = Object.entries(report.arms);
  const audit = report.audit || null;
  box.append(
    table(
      "Labelled barriers found and false flags, all pages",
      [
        "Arm",
        "Barriers found (strict)",
        "VERIFIED",
        "NEEDS REVIEW",
        "Found (loose)",
        "False flags on clean controls (loose)",
        "False flags (strict)",
        ...(audit ? ["False flags (strict), audit-corrected"] : []),
        "Clean pages with any finding",
      ],
      arms.map(([arm, c]) => [
        arm,
        of(c.found, c.failures),
        c.verified,
        c.needs_review,
        c.found_loose,
        of(c.false_flags, c.clean),
        c.false_flags_strict,
        ...(audit ? [corrected(audit, arm, c)] : []),
        of(c.clean_pages_any_finding, c.clean_pages),
      ]),
    ),
    table(
      "By label set",
      ["Arm", "Builder found", "W3C found", "Builder false flags", "W3C false flags"],
      arms.map(([arm, c]) => [
        arm,
        of(c.builder.found, c.builder.failures),
        of(c.w3c.found, c.w3c.failures),
        of(c.builder.false_flags, c.builder.clean),
        of(c.w3c.false_flags, c.w3c.clean),
      ]),
    ),
  );
  if (audit) {
    box.append(
      el(
        "p",
        { id: "audit", class: "audit" },
        `Audit-corrected strict false flags (${audit.date}, ${audit.source}): ${audit.reason}`,
        " The recorded counts are kept; the corrected ones are shown beside them.",
      ),
    );
  }
  const facts = [
    `Reporting bar (form (i), pre-registered): ${report.bar.met ? "met" : "not met"} over runs ${report.runs.join(" and ")}.`,
    ...Object.entries(report.model_only).map(
      ([run, ids]) => `Model-only catches over W1, ${run}: ${ids.join(", ") || "none"}.`,
    ),
    ...Object.entries(report.disagreements).map(
      ([arm, ids]) => `Disagreements between runs, ${arm}: ${ids.join(", ") || "none"}.`,
    ),
    `Labels SHA-256: builder ${report.labels_sha256.builder}, W3C ${report.labels_sha256.w3c}.`,
  ];
  box.append(el("ul", { class: "facts-list" }, facts.map((f) => el("li", {}, f))));
}

main().catch(showError);
