// Small progressive enhancements. Every page works without JS except uploads and live progress.
const csrf = document.querySelector('meta[name="csrf"]')?.content || "";
const api = (url, opts = {}) =>
  fetch(url, { ...opts, headers: { "X-CSRF-Token": csrf, ...(opts.headers || {}) } });

// Copy buttons: data-copy="text" or data-copy-target="#id"
document.querySelectorAll("[data-copy], [data-copy-target]").forEach((b) => {
  b.addEventListener("click", () => {
    const t = b.dataset.copyTarget ? document.querySelector(b.dataset.copyTarget).textContent : b.dataset.copy;
    navigator.clipboard.writeText(t.trim());
    const old = b.textContent;
    b.textContent = "Copied";
    setTimeout(() => (b.textContent = old), 1500);
  });
});

document.querySelectorAll("[data-autosubmit]").forEach((s) => s.addEventListener("change", () => s.form.submit()));
document.querySelectorAll("form[data-confirm]").forEach((f) =>
  f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

// Slow buttons (AI calls): show that something is happening and stop double clicks.
document.querySelectorAll("button[data-busy]").forEach((b) => b.form.addEventListener("submit", () => {
  setTimeout(() => { b.disabled = true; b.textContent = b.dataset.busy; }, 0);
}));

// Run progress: a step list that updates live (status, per-step time), then reloads when the run settles.
const when = (iso) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
const span = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; };
document.querySelectorAll("[data-run]").forEach((box) => {
  const id = box.dataset.run, began = when(box.dataset.runStarted);
  const elapsed = box.querySelector("[data-run-elapsed]");
  const paint = () => {
    box.querySelectorAll(".step").forEach((li) => {
      const t = li.querySelector("[data-time]"), st = li.dataset.started, fin = li.dataset.finished;
      t.textContent = st ? span((fin ? when(fin) : new Date()) - when(st)) : "";
    });
    const running = box.querySelector(".step.running, .step.waiting");
    elapsed.textContent = `Running for ${span(new Date() - began)}` + (running ? `. Now: ${running.querySelector("strong").textContent}.` : ".");
  };
  const tick = async () => {
    try {
      const r = await api(`/api/runs/${id}`);
      if (!r.ok) return setTimeout(tick, 5000);
      const run = await r.json();
      box.querySelector("[data-run-status]").textContent = `${run.status} ${run.progress}`;
      const [done, total] = run.progress.split("/").map(Number);
      box.querySelector("[data-run-bar]").style.width = `${Math.round((done / (total || 1)) * 100)}%`;
      run.steps.forEach((s) => {
        const li = box.querySelector(`.step[data-step="${s.step}"]`);
        if (!li) return;
        li.className = `step ${s.status}`;
        li.dataset.started = s.started_at || "";
        li.dataset.finished = s.finished_at || "";
        let err = li.querySelector(".err-text");
        if (s.error && !err) { err = document.createElement("p"); err.className = "err-text"; li.lastElementChild.append(err); }
        if (err) err.textContent = s.error || "";
      });
      paint();
      if (["done", "failed", "skipped"].includes(run.status)) {
        const url = new URL(location.href);
        url.searchParams.delete("run");
        url.searchParams.delete("m");
        return setTimeout(() => location.assign(url), 1200);
      }
    } catch (_) { /* server restarting: try again */ }
    setTimeout(tick, 3000);
  };
  paint();
  setInterval(paint, 1000);
  setTimeout(tick, 1000);
});

// Hook scoring in the script editor
document.querySelectorAll("[data-hook-score]").forEach((b) => b.addEventListener("click", async () => {
  const input = document.querySelector("[data-hook-input]");
  const out = document.querySelector("[data-hook-result]");
  const r = await api("/api/hooks/score", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hooks: [input.value] }) });
  const data = await r.json();
  const s = data.scores?.[0];
  out.textContent = s ? `Hook panel score ${s.panel_score} (${s.band.toLowerCase()}, ${s.formula}). Weakest: ${s.weakest.toLowerCase()}: ${s.fix}. ${data.note}` : "Type a hook first.";
}));

// Resumable master upload: 2 MB chunks, resumes from the server's offset after a drop.
const CHUNK = 2 * 1024 * 1024;
const mb = (n) => (n / 1048576 >= 1000 ? (n / 1073741824).toFixed(2) + " GB" : (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + " MB");
const eta = (s) => (s < 60 ? `${Math.max(1, Math.round(s))} sec` : `${Math.round(s / 60)} min`);
document.querySelectorAll("[data-master]").forEach((root) => {
  const $ = (n) => root.querySelector(`[data-master-${n}]`);
  const input = $("file"), btn = $("upload"), hint = $("hint"), box = $("box"), bar = $("progress");
  const pick = (f) => {
    $("name").textContent = f ? f.name : "Choose your video file";
    $("size").textContent = f ? mb(f.size) : "or drop it here. mp4, mov, mkv or webm.";
    $("drop").classList.toggle("has-file", !!f);
    btn.disabled = !f;
    hint.textContent = f ? "Ready. Press Upload file." : "Pick a file first.";
  };
  input.addEventListener("change", () => pick(input.files[0]));
  ["dragenter", "dragover"].forEach((e) => $("drop").addEventListener(e, (ev) => { ev.preventDefault(); $("drop").classList.add("drag"); }));
  ["dragleave", "drop"].forEach((e) => $("drop").addEventListener(e, () => $("drop").classList.remove("drag")));
  $("drop").addEventListener("drop", (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) { input.files = ev.dataTransfer.files; pick(input.files[0]); } });

  const show = (pct, detail, status = "", failed = false) => {
    box.hidden = false;
    box.classList.toggle("failed", failed);
    $("pct").textContent = `${pct}%`;
    $("detail").textContent = detail;
    $("status").textContent = status;
    bar.firstElementChild.style.width = `${pct}%`;
    bar.setAttribute("aria-valuenow", pct);
  };
  const warnLeave = (e) => { e.preventDefault(); e.returnValue = ""; };

  btn.addEventListener("click", async () => {
    const file = input.files[0];
    if (!file) { hint.textContent = "Pick a file first."; return; }
    btn.disabled = true;
    input.disabled = true;
    show(0, "Starting…", "Contacting the server.");
    window.addEventListener("beforeunload", warnLeave);
    try {
      const key = `master:${file.name}:${file.size}`;
      let id = null, received = 0;
      try { id = localStorage.getItem(key); } catch (_) { /* resume just won't survive a reload */ }
      if (id) {
        const r = await api(`/api/media/${id}`);
        if (r.ok) received = (await r.json()).received; else id = null;
      }
      if (!id) {
        const r = await api("/api/media", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, size: file.size }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `server said ${r.status}`);
        id = (await r.json()).id;
        try { localStorage.setItem(key, id); } catch (_) { /* see above */ }
      }
      const began = Date.now(), startedAt = received;
      let failures = 0;
      while (received < file.size) {
        try {
          const r = await api(`/api/media/${id}?offset=${received}`, { method: "PUT", body: file.slice(received, received + CHUNK) });
          const data = await r.json().catch(() => ({}));
          if (!r.ok && r.status !== 409) throw new Error(data.detail || `chunk failed (${r.status})`);
          received = data.received;
          failures = 0;
        } catch (err) {
          if (++failures > 5) throw err;
          $("status").textContent = `Connection dropped. Retrying (${failures} of 5)…`;
          await new Promise((res) => setTimeout(res, 2000 * failures));
          continue;
        }
        const pct = Math.floor((received / file.size) * 100);
        const rate = (received - startedAt) / Math.max(1, (Date.now() - began) / 1000);
        show(pct, `${mb(received)} of ${mb(file.size)} · ${mb(rate)}/s · about ${eta((file.size - received) / Math.max(rate, 1))} left`,
          "Keep this tab open until it finishes.");
      }
      show(100, "All of the file is on the server", "Saving it…");
      const done = await api(`/api/media/${id}/complete`, { method: "POST" });
      if (!done.ok) throw new Error((await done.json().catch(() => ({}))).detail || "could not finish the upload");
      try { localStorage.removeItem(key); } catch (_) { /* nothing to clean */ }
      window.removeEventListener("beforeunload", warnLeave);
      show(100, "Upload finished", "Your file is ready. Press Make Shorts next to it.");
      setTimeout(() => location.assign("/shorts?m=uploaded"), 1500);
    } catch (err) {
      window.removeEventListener("beforeunload", warnLeave);
      const pct = parseInt($("pct").textContent, 10) || 0;
      show(pct, "Upload stopped", `${err.message}. Choose the same file and press Upload file to continue from where it stopped.`, true);
      btn.disabled = false;
      input.disabled = false;
    }
  });
});

// Manual publish page (pre-existing behaviour)
const form = document.getElementById("upload");
if (form) {
  const drop = document.getElementById("drop");
  const file = form.querySelector("input[type=file]");
  const warn = document.getElementById("file-warn");
  let meta = null;
  const check = () => {
    warn.hidden = true;
    if (!meta) return;
    const isShort = document.getElementById("m-short").checked;
    let msg = "";
    if (isShort && meta.w > meta.h) msg = "This video is landscape. Shorts should be vertical.";
    else if (isShort && meta.d > 180) msg = "This video is over 3 minutes, so YouTube will treat it as a normal video.";
    if (msg) { warn.textContent = msg; warn.hidden = false; }
  };
  file.addEventListener("change", () => {
    const f = file.files[0];
    drop.classList.toggle("has-file", !!f);
    document.getElementById("file-name").textContent = f ? f.name : "Drop a video here";
    meta = null;
    if (!f) return check();
    const v = document.createElement("video");
    v.preload = "metadata";
    v.onloadedmetadata = () => { meta = { w: v.videoWidth, h: v.videoHeight, d: v.duration }; URL.revokeObjectURL(v.src); check(); };
    v.src = URL.createObjectURL(f);
    if (!document.getElementById("u-title").value) document.getElementById("u-title").value = f.name.replace(/\.[^.]+$/, "");
  });
  document.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", check));

  // Fill title, description and tags from a script idea's package and SEO.
  const fill = document.querySelector("[data-fill]");
  if (fill) fill.addEventListener("change", () => {
    const o = fill.selectedOptions[0];
    if (!o || !o.value) return;
    const titles = JSON.parse(o.dataset.titles), tags = JSON.parse(o.dataset.tags);
    const put = (id, v) => { document.getElementById(id).value = v; };
    put("u-title", titles[0] || "");
    put("u-desc", JSON.parse(o.dataset.description));
    put("u-tags", tags.join(", "));
    document.querySelector("[data-title-options]").replaceChildren(...titles.slice(1).map((t) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "copy";
      b.textContent = "Use: " + t;
      b.addEventListener("click", () => put("u-title", t));
      return b;
    }));
  });
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, () => drop.classList.add("drag")));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("drag")));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = form.querySelector("button"), status = document.getElementById("status"), bar = document.getElementById("bar");
    btn.disabled = true;
    form.classList.add("uploading");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (ev) => {
      const pct = Math.round((ev.loaded / ev.total) * 100);
      bar.style.width = pct + "%";
      status.textContent = pct < 100 ? `Uploading ${pct}%` : "Sending to YouTube. Keep this tab open.";
    };
    xhr.onload = () => { document.open(); document.write(xhr.responseText); document.close(); history.replaceState(null, "", "/publish"); };
    xhr.onerror = () => { btn.disabled = false; form.classList.remove("uploading"); status.textContent = "Upload failed. Check your connection and try again."; };
    xhr.send(new FormData(form));
  });
}

// Icons (vendored Lucide), mobile menu, theme toggle, and notices that fade out by themselves.
if (window.lucide) lucide.createIcons();
const setMenu = (open) => {
  document.body.classList.toggle("menu-open", open);
  document.querySelector("[data-menu]")?.setAttribute("aria-expanded", String(open));
  const scrim = document.querySelector(".scrim");
  if (scrim) scrim.hidden = !open;
};
document.querySelector("[data-menu]")?.addEventListener("click", () => setMenu(!document.body.classList.contains("menu-open")));
document.querySelector(".scrim")?.addEventListener("click", () => setMenu(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });
document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (_) { /* keep for this visit only */ }
});
document.querySelectorAll(".notice.ok").forEach((n) => setTimeout(() => {
  n.style.transition = "opacity .4s"; n.style.opacity = "0"; setTimeout(() => n.remove(), 400);
}, 8000));
