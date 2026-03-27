const els = {
  tabBtnSearch: document.getElementById("tabBtnSearch"),
  tabBtnCustom: document.getElementById("tabBtnCustom"),
  tabSearchPanel: document.getElementById("tabSearchPanel"),
  tabCustomPanel: document.getElementById("tabCustomPanel"),

  searchInput: document.getElementById("searchInput"),
  searchLimit: document.getElementById("searchLimit"),
  searchBtn: document.getElementById("searchBtn"),
  searchMeta: document.getElementById("searchMeta"),
  searchResults: document.getElementById("searchResults"),
  searchSourceChembl: document.getElementById("searchSourceChembl"),
  searchSourcePubchem: document.getElementById("searchSourcePubchem"),
  customNameInput: document.getElementById("customNameInput"),
  customSmilesInput: document.getElementById("customSmilesInput"),
  addCustomBtn: document.getElementById("addCustomBtn"),
  customMeta: document.getElementById("customMeta"),

  builderQueue: document.getElementById("builderQueue"),
  rangeStart: document.getElementById("rangeStart"),
  rangeEnd: document.getElementById("rangeEnd"),
  generateOligomersBtn: document.getElementById("generateOligomersBtn"),
  addToLigandsBtn: document.getElementById("addToLigandsBtn"),
  dockingDbMeta: document.getElementById("dockingDbMeta"),
  dockingDbList: document.getElementById("dockingDbList"),
  builderStatus: document.getElementById("builderStatus"),
  generatedFiles: document.getElementById("generatedFiles"),
  generatedSelectionMeta: document.getElementById("generatedSelectionMeta"),
  clearGeneratedSelectionBtn: document.getElementById("clearGeneratedSelectionBtn"),

  previewModal: document.getElementById("previewModal"),
  previewTitle: document.getElementById("previewTitle"),
  previewViewport: document.getElementById("previewViewport"),
  closePreviewBtn: document.getElementById("closePreviewBtn"),
};

let currentSearchResults = [];
let queueItems = [];
let generatedFiles = [];
let generatedSelection = new Set();
let dockingDbLigands = [];
let nglStage = null;
const ROOT_PATH = String(document.body?.dataset.rootPath || "").replace(/\/+$/, "");

function withRoot(path) {
  const raw = String(path || "").trim();
  if (!raw) return ROOT_PATH || "/";
  if (raw.startsWith("#")) return raw;
  if (/^https?:\/\//i.test(raw)) return raw;
  const normalized = raw.startsWith("/") ? raw : `/${raw}`;
  return `${ROOT_PATH}${normalized}`;
}

function apiUrl(path) {
  const tail = String(path || "").trim();
  const normalized = tail.startsWith("/") ? tail : `/${tail}`;
  return withRoot(`/api${normalized}`);
}

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_err) {
    data = { detail: text || "Request failed." };
  }
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

function esc(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function truncateText(text, maxLen = 80) {
  const value = String(text || "");
  if (value.length <= maxLen) return value;
  return `${value.slice(0, maxLen - 3)}...`;
}

function renderSmiles(smiles, maxLen = 42) {
  const full = String(smiles || "-");
  return `<span class="smiles-pill" title="${esc(full)}">${esc(truncateText(full, maxLen))}</span>`;
}

function getOligomerRange() {
  let start = Math.max(1, Math.min(10, Math.round(Number(els.rangeStart?.value || 1))));
  let end = Math.max(1, Math.min(10, Math.round(Number(els.rangeEnd?.value || 1))));
  if (start > end) {
    const tmp = start;
    start = end;
    end = tmp;
    if (els.rangeStart) els.rangeStart.value = String(start);
    if (els.rangeEnd) els.rangeEnd.value = String(end);
  }
  const counts = [];
  for (let n = start; n <= end; n += 1) {
    counts.push(n);
  }
  return { start, end, counts };
}

function getSourcesOrDefault() {
  const selected = [];
  if (els.searchSourceChembl?.checked) selected.push("chembl");
  if (els.searchSourcePubchem?.checked) selected.push("pubchem");
  if (selected.length > 0) return selected;
  if (els.searchSourceChembl) els.searchSourceChembl.checked = true;
  return ["chembl"];
}

function getPrimaryId(row) {
  const direct = String(row?.primary_id || "").trim();
  if (direct) return direct;
  const chembl = String(row?.chembl_id || "").trim();
  if (chembl) return chembl;
  const cid = String(row?.pubchem_cid || "").trim();
  if (cid) return `CID ${cid}`;
  return "-";
}

function sourceSummary(data) {
  const count = Number(data?.count || 0);
  const by = data?.count_by_source || {};
  const chembl = Number(by.chembl || 0);
  const pubchem = Number(by.pubchem || 0);
  return `Total ${count} (ChEMBL: ${chembl}, PubChem: ${pubchem})`;
}

function setActiveInputTab(tabName) {
  const useCustom = String(tabName || "").toLowerCase() === "custom";
  if (els.tabBtnSearch) {
    els.tabBtnSearch.classList.toggle("active", !useCustom);
    els.tabBtnSearch.setAttribute("aria-selected", useCustom ? "false" : "true");
  }
  if (els.tabBtnCustom) {
    els.tabBtnCustom.classList.toggle("active", useCustom);
    els.tabBtnCustom.setAttribute("aria-selected", useCustom ? "true" : "false");
  }
  if (els.tabSearchPanel) {
    els.tabSearchPanel.classList.toggle("active", !useCustom);
  }
  if (els.tabCustomPanel) {
    els.tabCustomPanel.classList.toggle("active", useCustom);
  }
}

function renderSearchTable(rows, { container }) {
  if (!container) return;
  if (!Array.isArray(rows) || rows.length === 0) {
    container.innerHTML = '<div class="helper">No results found.</div>';
    return;
  }
  const body = rows
    .map((row, idx) => {
      const source = String(row?.source || "-").toUpperCase();
      const detailUrl = String(row?.detail_url || "");
      const detailLink = detailUrl
        ? `<a class="download" href="${esc(detailUrl)}" target="_blank" rel="noopener">Open</a>`
        : "-";
      return `
        <tr>
          <td>${idx + 1}</td>
          <td>${esc(source)}</td>
          <td><code>${esc(getPrimaryId(row))}</code></td>
          <td>${esc(row?.name || "-")}</td>
          <td>${renderSmiles(row?.smiles || "-", 40)}</td>
          <td>${detailLink}</td>
          <td>
            <button class="secondary row-add" type="button" data-idx="${idx}">Add</button>
          </td>
        </tr>
      `;
    })
    .join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Source</th>
          <th>ID</th>
          <th>Name</th>
          <th>SMILES</th>
          <th>Record</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function bindSearchActions(rows, container) {
  if (!container) return;
  Array.from(container.querySelectorAll(".row-add")).forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.idx || -1);
      if (!Number.isInteger(idx) || idx < 0 || idx >= rows.length) return;
      addRowToQueue(rows[idx]);
    });
  });
}

async function runSearch() {
  const q = String(els.searchInput.value || "").trim();
  const limit = Number(els.searchLimit.value || 5);
  const sources = getSourcesOrDefault();
  if (q.length < 2) {
    els.searchMeta.textContent = "Type at least 2 characters.";
    els.searchResults.innerHTML = "";
    return;
  }
  els.searchMeta.textContent = `Searching (${sources.join(", ")})...`;
  try {
    const qs = new URLSearchParams({
      q,
      limit: String(limit),
      sources: sources.join(","),
    });
    const data = await fetchJSON(`${apiUrl("/search")}?${qs.toString()}`);
    currentSearchResults = Array.isArray(data.results) ? data.results : [];
    els.searchMeta.textContent = `${sourceSummary(data)} for "${q}".`;
    renderSearchTable(currentSearchResults, { container: els.searchResults });
    bindSearchActions(currentSearchResults, els.searchResults);
  } catch (err) {
    els.searchMeta.textContent = `Search error: ${err.message}`;
    els.searchResults.innerHTML = "";
  }
}

function addRowToQueue(row) {
  const source = String(row?.source || "").toLowerCase();
  const key = source === "custom"
    ? `custom:${String(row?.name || row?.primary_id || "").trim().toLowerCase()}:${String(row?.smiles || "").replace(/\s+/g, "")}`
    : `${source}:${String(getPrimaryId(row) || "")}`;
  const existing = queueItems.find((item) => item.key === key);
  if (existing) {
    renderQueue();
    return;
  }
  queueItems.push({
    key,
    source: String(row?.source || ""),
    primary_id: String(getPrimaryId(row) || ""),
    name: String(row?.name || getPrimaryId(row) || "ligand"),
    smiles: String(row?.smiles || ""),
  });
  renderQueue();
}

function addCustomSmilesToQueue() {
  const smiles = String(els.customSmilesInput?.value || "").trim();
  const nameRaw = String(els.customNameInput?.value || "").trim();
  if (!smiles) {
    if (els.customMeta) els.customMeta.textContent = "SMILES is required.";
    return;
  }
  const defaultName = nameRaw || `custom_${queueItems.length + 1}`;
  addRowToQueue({
    source: "custom",
    primary_id: defaultName,
    name: defaultName,
    smiles,
  });
  if (els.customMeta) els.customMeta.textContent = `Added "${defaultName}" to queue.`;
  if (els.customNameInput) els.customNameInput.value = "";
  if (els.customSmilesInput) els.customSmilesInput.value = "";
}

function renderQueue() {
  if (!els.builderQueue) return;
  if (!queueItems.length) {
    els.builderQueue.innerHTML = '<div class="helper">No selected structures yet. Add rows from database search or custom SMILES tab.</div>';
    return;
  }
  const body = queueItems
    .map((item, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${esc(String(item.source || "").toUpperCase())}</td>
        <td><code>${esc(item.primary_id || "-")}</code></td>
        <td>${esc(item.name || "-")}</td>
        <td>${renderSmiles(item.smiles || "-", 34)}</td>
        <td>
          <button class="secondary queue-delete-btn" type="button" data-key="${esc(item.key)}">Delete</button>
        </td>
      </tr>
    `)
    .join("");

  els.builderQueue.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Source</th>
          <th>ID</th>
          <th>Name</th>
          <th>SMILES</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;

  Array.from(els.builderQueue.querySelectorAll(".queue-delete-btn")).forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = String(btn.dataset.key || "");
      queueItems = queueItems.filter((item) => item.key !== key);
      renderQueue();
    });
  });
}

function formatBytes(sizeBytes) {
  const size = Number(sizeBytes || 0);
  if (size <= 0) return "0 B";
  if (size < 1024) return `${size} B`;
  const kb = size / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatTime(epochSeconds) {
  const dt = new Date(Number(epochSeconds || 0) * 1000);
  if (Number.isNaN(dt.getTime())) return "-";
  return dt.toLocaleString();
}

function updateGeneratedSelectionMeta() {
  if (els.generatedSelectionMeta) {
    els.generatedSelectionMeta.textContent = `${generatedSelection.size} selected`;
  }
  if (els.clearGeneratedSelectionBtn) {
    els.clearGeneratedSelectionBtn.disabled = generatedSelection.size === 0;
  }
}

function clearGeneratedSelection() {
  generatedSelection = new Set();
  renderGeneratedFiles();
}

function notifyDockingLigandsUpdated(payload = {}) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(
        {
          type: "docking:ligands-updated",
          payload,
        },
        window.location.origin
      );
    }
  } catch (_err) {
    // no-op
  }
}

function renderDockingDatabase() {
  if (!els.dockingDbList) return;
  if (!dockingDbLigands.length) {
    els.dockingDbList.innerHTML = '<div class="helper">Docking ligand database is empty.</div>';
    return;
  }
  const body = dockingDbLigands
    .map((name, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td><code>${esc(name)}</code></td>
        <td>
          <button class="secondary docking-db-delete-btn" type="button" data-name="${esc(name)}" style="color:#b91c1c;border-color:#fecaca;">Delete</button>
        </td>
      </tr>
    `)
    .join("");

  els.dockingDbList.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Ligand</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;

  Array.from(els.dockingDbList.querySelectorAll(".docking-db-delete-btn")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = String(btn.dataset.name || "");
      if (!name) return;
      await deleteDockingLigand(name);
    });
  });
}

function renderGeneratedFiles() {
  if (!els.generatedFiles) return;
  if (!generatedFiles.length) {
    els.generatedFiles.innerHTML = '<div class="helper">No generated files yet.</div>';
    updateGeneratedSelectionMeta();
    return;
  }
  const body = generatedFiles
    .map((row, idx) => {
      const name = String(row.name || "");
      const checked = generatedSelection.has(name);
      const previewBtn = `<button class="secondary generated-preview-btn" type="button" data-name="${esc(name)}">Preview</button>`;
      const deleteBtn = `<button class="secondary generated-delete-btn" type="button" data-name="${esc(name)}" style="color:#b91c1c;border-color:#fecaca;">Delete</button>`;
      return `
        <tr>
          <td><input class="generated-select-checkbox" type="checkbox" data-name="${esc(name)}" ${checked ? "checked" : ""}></td>
          <td>${idx + 1}</td>
          <td><code>${esc(name)}</code></td>
          <td>${esc(formatBytes(row.size_bytes || 0))}</td>
          <td>${esc(formatTime(row.mtime || 0))}</td>
          <td>
            <a class="download" href="${esc(withRoot(row.download_url || "#"))}">Download</a>
            ${previewBtn}
            ${deleteBtn}
          </td>
        </tr>
      `;
    })
    .join("");

  els.generatedFiles.innerHTML = `
    <table>
      <thead>
        <tr>
          <th><input id="generatedSelectAll" type="checkbox"></th>
          <th>#</th>
          <th>File</th>
          <th>Size</th>
          <th>Modified</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;

  Array.from(els.generatedFiles.querySelectorAll(".generated-select-checkbox")).forEach((box) => {
    box.addEventListener("change", () => {
      const name = String(box.dataset.name || "");
      if (!name) return;
      if (box.checked) {
        generatedSelection.add(name);
      } else {
        generatedSelection.delete(name);
      }
      const allBoxes = Array.from(els.generatedFiles.querySelectorAll(".generated-select-checkbox"));
      const allChecked = allBoxes.length > 0 && allBoxes.every((item) => item.checked);
      const selectAll = document.getElementById("generatedSelectAll");
      if (selectAll) selectAll.checked = allChecked;
      updateGeneratedSelectionMeta();
    });
  });
  const selectAll = document.getElementById("generatedSelectAll");
  if (selectAll) {
    const allBoxes = Array.from(els.generatedFiles.querySelectorAll(".generated-select-checkbox"));
    const allChecked = allBoxes.length > 0 && allBoxes.every((item) => item.checked);
    selectAll.checked = allChecked;
    selectAll.addEventListener("change", () => {
      allBoxes.forEach((item) => {
        item.checked = selectAll.checked;
        const name = String(item.dataset.name || "");
        if (!name) return;
        if (selectAll.checked) generatedSelection.add(name);
        else generatedSelection.delete(name);
      });
      updateGeneratedSelectionMeta();
    });
  }
  updateGeneratedSelectionMeta();

  Array.from(els.generatedFiles.querySelectorAll(".generated-preview-btn")).forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = String(btn.dataset.name || "");
      if (!name) return;
      openPreview(name);
    });
  });

  Array.from(els.generatedFiles.querySelectorAll(".generated-delete-btn")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = String(btn.dataset.name || "");
      if (!name) return;
      await deleteGeneratedFiles([name]);
    });
  });
}

async function loadGeneratedFiles() {
  try {
    const data = await fetchJSON(apiUrl("/files"));
    generatedFiles = Array.isArray(data.files) ? data.files : [];
    const validNames = new Set(generatedFiles.map((item) => String(item.name || "")));
    generatedSelection = new Set(Array.from(generatedSelection).filter((name) => validNames.has(name)));
    renderGeneratedFiles();
  } catch (err) {
    els.generatedFiles.innerHTML = `<div class="helper">Failed to load files: ${esc(err.message)}</div>`;
    updateGeneratedSelectionMeta();
  }
}

async function deleteGeneratedFiles(names) {
  const fileNames = (Array.isArray(names) ? names : []).map((x) => String(x || "").trim()).filter(Boolean);
  if (!fileNames.length) return;
  try {
    const res = await fetchJSON(apiUrl("/files/delete"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_names: fileNames }),
    });
    if (Array.isArray(res.deleted)) {
      res.deleted.forEach((name) => generatedSelection.delete(String(name || "")));
    }
    els.builderStatus.textContent = `Deleted ${Number(res.deleted_count || 0)} file(s).`;
    await loadGeneratedFiles();
  } catch (err) {
    els.builderStatus.textContent = `Delete failed: ${err.message}`;
  }
}

async function buildOligomers() {
  if (!queueItems.length) {
    els.builderStatus.textContent = "Queue is empty.";
    return;
  }
  const { start, end, counts } = getOligomerRange();
  const items = [];
  queueItems.forEach((item) => {
    counts.forEach((count) => {
      items.push({
        primary_id: item.primary_id,
        name: item.name,
        smiles: item.smiles,
        count,
      });
    });
  });
  els.builderStatus.textContent = `Generating oligomer SDF files for ${queueItems.length} structure(s), range ${start}-${end}...`;
  try {
    const res = await fetchJSON(apiUrl("/oligomers/build"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    const generatedRows = Array.isArray(res.generated) ? res.generated : [];
    generatedRows.forEach((row) => {
      const name = String(row.name || "");
      if (name) generatedSelection.add(name);
    });
    els.builderStatus.textContent = `Generated ${Number(res.generated_count || 0)} file(s). ${res.errors?.length ? `Errors: ${res.errors.join(" | ")}` : ""}`;
    await loadGeneratedFiles();
  } catch (err) {
    els.builderStatus.textContent = `Build failed: ${err.message}`;
  }
}

async function addSelectedToLigands() {
  const names = Array.from(generatedSelection);
  if (!names.length) {
    els.builderStatus.textContent = "Select at least one generated SDF file.";
    return;
  }
  els.builderStatus.textContent = "Saving selected files to Docking ligand database...";
  try {
    const res = await fetchJSON(apiUrl("/ligands/add"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_names: names }),
    });
    const duplicateMsg = res.duplicates?.length ? ` Already present: ${res.duplicates.join(", ")}` : "";
    const missingMsg = res.missing?.length ? ` Missing: ${res.missing.join(", ")}` : "";
    els.builderStatus.textContent = `Saved ${res.copied_count} file(s) to Docking DB.${duplicateMsg}${missingMsg}`;
    await loadDockingDatabase();
    notifyDockingLigandsUpdated({
      copied_count: Number(res.copied_count || 0),
      copied: Array.isArray(res.copied) ? res.copied : [],
      ligands_count: Array.isArray(res.ligands) ? res.ligands.length : undefined,
    });
  } catch (err) {
    els.builderStatus.textContent = `Add failed: ${err.message}`;
  }
}

async function deleteDockingLigand(name) {
  const safeName = String(name || "").trim();
  if (!safeName) return;
  els.builderStatus.textContent = `Deleting ${safeName} from Docking DB...`;
  try {
    const res = await fetchJSON(apiUrl("/ligands/delete"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: safeName }),
    });
    await loadDockingDatabase();
    notifyDockingLigandsUpdated({
      deleted: [safeName],
      ligands_count: Number(res.count || 0),
    });
    els.builderStatus.textContent = `Deleted ${safeName} from Docking DB.`;
  } catch (err) {
    els.builderStatus.textContent = `Delete failed: ${err.message}`;
  }
}

async function loadDockingDatabase() {
  if (!els.dockingDbMeta) return;
  try {
    const res = await fetchJSON(apiUrl("/ligands/database"));
    const count = Number(res.count || 0);
    const path = String(res.ligands_path || "").trim();
    dockingDbLigands = Array.isArray(res.ligands) ? res.ligands : [];
    els.dockingDbMeta.textContent = `Docking DB: ${count} ligand(s)${path ? ` | ${path}` : ""}`;
    renderDockingDatabase();
  } catch (err) {
    dockingDbLigands = [];
    els.dockingDbMeta.textContent = `Docking DB: unavailable (${err.message})`;
    if (els.dockingDbList) {
      els.dockingDbList.innerHTML = "";
    }
  }
}

function ensureNglStage() {
  if (!els.previewViewport) return null;
  if (!window.NGL) {
    els.builderStatus.textContent = "Preview failed: NGL library is not loaded.";
    return null;
  }
  if (!nglStage) {
    nglStage = new NGL.Stage("previewViewport", { backgroundColor: "white" });
    window.addEventListener("resize", () => {
      if (nglStage) nglStage.handleResize();
    });
  }
  return nglStage;
}

async function openPreview(fileName) {
  const fileRow = generatedFiles.find((row) => String(row.name || "") === String(fileName || ""));
  const url = withRoot(fileRow?.download_url || apiUrl(`/files/${encodeURIComponent(fileName)}`));
  if (els.previewTitle) {
    els.previewTitle.textContent = `3D Preview: ${fileName}`;
  }
  if (els.previewModal) {
    els.previewModal.classList.add("active");
    els.previewModal.setAttribute("aria-hidden", "false");
  }
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  const stage = ensureNglStage();
  if (!stage) return;
  stage.handleResize();
  stage.removeAllComponents();

  try {
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Could not read file (${res.status})`);
    }
    const sdfText = await res.text();
    const blob = new Blob([sdfText], { type: "text/plain" });
    const blobUrl = URL.createObjectURL(blob);
    let component = null;
    try {
      component = await stage.loadFile(blobUrl, { ext: "sdf" });
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
    if (!component) {
      throw new Error("No preview component produced for this file.");
    }
    component.addRepresentation("ball+stick");
    component.autoView();
    await new Promise((resolve) => setTimeout(resolve, 40));
    stage.handleResize();
  } catch (err) {
    console.error("Preview error", err);
    els.builderStatus.textContent = `Preview failed: ${err.message || err}`;
  }
}

function closePreview() {
  if (els.previewModal) {
    els.previewModal.classList.remove("active");
    els.previewModal.setAttribute("aria-hidden", "true");
  }
  if (nglStage) {
    nglStage.removeAllComponents();
  }
}

function bindEvents() {
  if (els.tabBtnSearch) {
    els.tabBtnSearch.addEventListener("click", () => setActiveInputTab("search"));
  }
  if (els.tabBtnCustom) {
    els.tabBtnCustom.addEventListener("click", () => setActiveInputTab("custom"));
  }
  if (els.searchBtn) els.searchBtn.addEventListener("click", runSearch);
  if (els.searchInput) {
    els.searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runSearch();
    });
  }
  if (els.addCustomBtn) {
    els.addCustomBtn.addEventListener("click", addCustomSmilesToQueue);
  }
  if (els.customSmilesInput) {
    els.customSmilesInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") addCustomSmilesToQueue();
    });
  }
  if (els.generateOligomersBtn) els.generateOligomersBtn.addEventListener("click", buildOligomers);
  if (els.addToLigandsBtn) els.addToLigandsBtn.addEventListener("click", addSelectedToLigands);
  if (els.clearGeneratedSelectionBtn) {
    els.clearGeneratedSelectionBtn.addEventListener("click", () => {
      clearGeneratedSelection();
    });
  }
  if (els.closePreviewBtn) els.closePreviewBtn.addEventListener("click", closePreview);
  if (els.previewModal) {
    els.previewModal.addEventListener("click", (event) => {
      if (event.target === els.previewModal) closePreview();
    });
  }
}

bindEvents();
setActiveInputTab("search");
renderQueue();
loadGeneratedFiles();
loadDockingDatabase();
