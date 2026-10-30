"use strict";

const POLL_MS = 1500;
const STAGES = {
  queued: "waiting to start",
  walking: "walking the page with the keyboard",
  reviewing: "verdicts frozen; the model reviewer is answering",
  "writing packets": "writing packets",
};
let pages = [];

async function api(path, options) {
  const reply = await fetch(path, options);
  let data;
  try {
    data = await reply.json();
  } catch (error) {
    data = { message: "Live mode is not running on this server. The replay of every frozen run works with no key." };
  }
  return { ok: reply.ok, status: reply.status, data };
}

function selected() {
  const id = document.getElementById("live-page").value;
  return pages.find((page) => page.id === id);
}

function showReplayLink(replayId) {
  const box = document.getElementById("replay-link");
  const page = selected();
  const id = replayId === undefined ? page && page.replay_id : replayId;
  box.replaceChildren();
  if (!page || !id) return;
  box.append(el("a", { href: `page.html?id=${encodeURIComponent(id)}` }, `Open the replay of ${page.title}`));
}

function alertMessage(text) {
  const box = document.getElementById("live-message");
  box.replaceChildren(el("p", { role: "alert", class: "error" }, text));
}

function setStatus(text) {
  document.getElementById("live-status").textContent = text;
}

function caps(status) {
  const spent = status.spent_usd === null ? "unknown" : `USD ${status.spent_usd.toFixed(2)}`;
  return (
    `Model ${status.model}, reasoning effort ${status.reasoning_effort}; at most ${status.max_keys} keys per walk;` +
    ` one run at a time; lifetime live credit cap USD ${status.cap_usd.toFixed(2)} (${spent} spent so far).`
  );
}

async function poll(runId) {
  const { ok, data } = await api(`api/live/runs/${encodeURIComponent(runId)}`);
  if (!ok) throw new Error(data.message || "the run is unknown");
  if (data.state === "done") {
    setStatus(`Live run ${runId} finished, about USD ${data.cost_usd.toFixed(4)}.`);
    const link = el("a", { href: `page.html?id=${encodeURIComponent(data.page_id)}` }, "Open the live run");
    document.getElementById("live-result").replaceChildren(link);
    link.focus();
    document.getElementById("start").disabled = false;
    return;
  }
  if (data.state === "failed") {
    setStatus(`Live run ${runId} stopped.`);
    alertMessage(data.message);
    showReplayLink(data.replay_id);
    document.getElementById("start").disabled = false;
    return;
  }
  setStatus(`Live run ${runId}: ${STAGES[data.state] || data.state}.`);
  setTimeout(() => poll(runId).catch(showError), POLL_MS);
}

async function start(event) {
  event.preventDefault();
  const page = selected();
  if (!page) return;
  document.getElementById("start").disabled = true;
  document.getElementById("live-message").replaceChildren();
  document.getElementById("live-result").replaceChildren();
  const { status, data } = await api("api/live/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page: page.id }),
  });
  if (status !== 202) {
    alertMessage(data.message);
    showReplayLink(data.replay_id);
    document.getElementById("start").disabled = false;
    return;
  }
  setStatus(`Live run ${data.run}: started.`);
  await poll(data.run);
}

async function main() {
  const { ok, data } = await api("api/live/status");
  const select = document.getElementById("live-page");
  pages = ok ? data.pages : [];
  for (const page of pages) select.append(el("option", { value: page.id }, page.title));
  select.addEventListener("change", () => showReplayLink());
  document.getElementById("live-form").addEventListener("submit", (event) => start(event).catch(showError));
  if (ok) document.getElementById("live-caps").textContent = caps(data);
  if (!ok || !data.live) {
    alertMessage(data.message);
    showReplayLink();
    const box = document.getElementById("replay-link");
    if (!box.hasChildNodes()) box.append(el("a", { href: "index.html" }, "Open the replay"));
    return;
  }
  document.getElementById("start").disabled = false;
}

main().catch(showError);
