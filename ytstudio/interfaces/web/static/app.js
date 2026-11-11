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

// Run progress: poll until the run settles, then reload to show the results.
document.querySelectorAll("[data-run]").forEach((box) => {
  const id = box.dataset.run;
  const tick = async () => {
    const r = await api(`/api/runs/${id}`);
    if (!r.ok) return;
    const run = await r.json();
    box.querySelector("[data-run-status]").textContent = `${run.status} ${run.progress}`;
    const list = box.querySelector("[data-run-steps]");
    list.replaceChildren(...run.steps.map((s) => {
      const li = document.createElement("li");
      li.textContent = `${s.step}: ${s.status}${s.error ? " (" + s.error + ")" : ""}`;
      return li;
    }));
    if (["done", "failed", "skipped"].includes(run.status)) {
      const url = new URL(location.href);
      url.searchParams.delete("run");
      url.searchParams.delete("m");
      setTimeout(() => location.assign(url), 800);
    } else {
      setTimeout(tick, 3000);
    }
  };
  setTimeout(tick, 1500);
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

// Resumable master upload: 8 MB chunks, resumes from the server's offset after a drop.
const CHUNK = 8 * 1024 * 1024;
document.querySelectorAll("[data-master-upload]").forEach((b) => b.addEventListener("click", async () => {
  const file = document.querySelector("[data-master-file]").files[0];
  const status = document.querySelector("[data-master-status]");
  const bar = document.querySelector("[data-master-progress]");
  if (!file) { status.textContent = "Choose a file first."; return; }
  b.disabled = true;
  bar.style.display = "block";
  const key = `master:${file.name}:${file.size}`;
  let id = localStorage.getItem(key);
  let received = 0;
  try {
    if (id) {
      const r = await api(`/api/media/${id}`);
      if (r.ok) received = (await r.json()).received; else id = null;
    }
    if (!id) {
      const r = await api("/api/media", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, size: file.size }) });
      if (!r.ok) throw new Error((await r.json()).detail || "could not start upload");
      id = (await r.json()).id;
      localStorage.setItem(key, id);
    }
    let failures = 0;
    while (received < file.size) {
      const chunk = file.slice(received, received + CHUNK);
      try {
        const r = await api(`/api/media/${id}?offset=${received}`, { method: "PUT", body: chunk });
        const data = await r.json();
        if (!r.ok && r.status !== 409) throw new Error(data.detail || "chunk failed");
        received = data.received;
        failures = 0;
      } catch (err) {
        if (++failures > 5) throw err;
        status.textContent = "Connection dropped, retrying…";
        await new Promise((res) => setTimeout(res, 2000 * failures));
      }
      bar.firstElementChild.style.width = `${Math.round((received / file.size) * 100)}%`;
      status.textContent = `Uploaded ${Math.round((received / file.size) * 100)}%`;
    }
    const done = await api(`/api/media/${id}/complete`, { method: "POST" });
    if (!done.ok) throw new Error((await done.json()).detail || "could not finish upload");
    localStorage.removeItem(key);
    status.textContent = "Done.";
    location.reload();
  } catch (err) {
    status.textContent = `Upload stopped: ${err.message}. Press Upload again to resume.`;
    b.disabled = false;
  }
}));

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
