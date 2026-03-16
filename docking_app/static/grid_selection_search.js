(function () {
  const bridge = window.DockUPGridSelectionSearchBridge;
  if (!bridge) return;

  const els = {
    toggle: document.getElementById("gridSelectionInfo"),
    panel: document.getElementById("gridSelectionSearchPanel"),
    input: document.getElementById("gridSelectionSearchInput"),
    hint: document.getElementById("gridSelectionSearchHint"),
    summary: document.getElementById("gridSelectionSearchSummary"),
    matches: document.getElementById("gridSelectionSearchMatches"),
    clear: document.getElementById("gridSelectionSearchClear"),
  };

  if (!els.toggle || !els.panel || !els.input || !els.summary || !els.matches || !els.clear) return;

  const RESIDUE_TYPES = [
    { code: "ALA", names: ["ALANINE", "ALANIN"] },
    { code: "ARG", names: ["ARGININE", "ARGININ"] },
    { code: "ASN", names: ["ASPARAGINE", "ASPARAGIN"] },
    { code: "ASP", names: ["ASPARTICACID", "ASPARTATE", "ASPARTAT", "ASPARTIC"] },
    { code: "CYS", names: ["CYSTEINE", "CYSTEIN"] },
    { code: "GLN", names: ["GLUTAMINE", "GLUTAMIN"] },
    { code: "GLU", names: ["GLUTAMICACID", "GLUTAMATE", "GLUTAMAT", "GLUTAMIC"] },
    { code: "GLY", names: ["GLYCINE", "GLISIN", "GLYCIN"] },
    { code: "HIS", names: ["HISTIDINE", "HISTIDIN"] },
    { code: "ILE", names: ["ISOLEUCINE", "ISOLEUCIN"] },
    { code: "LEU", names: ["LEUCINE", "LEUCIN"] },
    { code: "LYS", names: ["LYSINE", "LYSIN"] },
    { code: "MET", names: ["METHIONINE", "METHIONIN"] },
    { code: "PHE", names: ["PHENYLALANINE", "PHENYLALANIN"] },
    { code: "PRO", names: ["PROLINE", "PROLIN"] },
    { code: "SER", names: ["SERINE", "SERIN"] },
    { code: "THR", names: ["THREONINE", "THREONIN"] },
    { code: "TRP", names: ["TRYPTOPHAN", "TRYPTOPHANE", "TRYPTOFAN"] },
    { code: "TYR", names: ["TYROSINE", "TYROSIN"] },
    { code: "VAL", names: ["VALINE", "VALIN"] },
  ];

  const state = {
    open: false,
    contextKey: "",
    residues: [],
    query: "",
    queryMode: "empty",
    currentCandidate: null,
    currentMatches: [],
    selectedResidues: new Map(),
  };

  function normalizeText(value) {
    return String(value || "").trim().toUpperCase().replace(/[^A-Z]/g, "");
  }

  function normalizeDigits(value) {
    return String(value || "").trim().replace(/[^\d]/g, "");
  }

  function sortResidues(a, b) {
    const chainA = String(a?.chain || "");
    const chainB = String(b?.chain || "");
    if (chainA !== chainB) return chainA.localeCompare(chainB);
    const numA = Number.parseInt(String(a?.resno || ""), 10);
    const numB = Number.parseInt(String(b?.resno || ""), 10);
    if (Number.isFinite(numA) && Number.isFinite(numB) && numA !== numB) return numA - numB;
    return String(a?.resno || "").localeCompare(String(b?.resno || ""));
  }

  function residueKey(row) {
    return `${String(row?.chain || "_").trim()}:${String(row?.resno || "").trim()}:${String(row?.resname || "").trim().toUpperCase()}`;
  }

  function singleResidueLabel(row) {
    const resname = String(row?.resname || "").trim().toUpperCase();
    const resno = String(row?.resno || "").trim();
    const chain = String(row?.chain || "").trim();
    if (!resname || !resno) return "";
    return chain && chain !== "_" ? `${resname}_${chain}${resno}` : `${resname}_${resno}`;
  }

  function contextKey() {
    const ctx = bridge.getSelectedContext ? bridge.getSelectedContext() : { pdbId: "", chain: "all" };
    const pdbId = String(ctx?.pdbId || "").trim().toUpperCase();
    const chain = String(ctx?.chain || "all").trim() || "all";
    return `${pdbId}::${chain}`;
  }

  function setPanelOpen(open) {
    state.open = !!open;
    els.panel.hidden = !state.open;
    els.toggle.setAttribute("aria-expanded", state.open ? "true" : "false");
    if (state.open) {
      loadResidues();
      renderMatches();
      window.requestAnimationFrame(() => {
        els.input.focus();
        els.input.select();
      });
    }
  }

  function loadResidues() {
    state.contextKey = contextKey();
    state.residues = (bridge.getResidueCatalog ? bridge.getResidueCatalog() : []).sort(sortResidues);
  }

  function resetSelectedResidues() {
    state.selectedResidues = new Map();
  }

  function selectedRows() {
    return Array.from(state.selectedResidues.values()).sort(sortResidues);
  }

  function allResiduesForType(resname) {
    return (state.residues || []).filter((row) => row.resname === resname).sort(sortResidues);
  }

  function computeSelectionLabel(rows) {
    if (!rows.length) return "";
    if (rows.length === 1) return singleResidueLabel(rows[0]);

    const typeSet = new Set(rows.map((row) => row.resname));
    if (typeSet.size === 1) {
      const [singleType] = Array.from(typeSet);
      const allOfType = allResiduesForType(singleType);
      if (allOfType.length === rows.length && allOfType.every((row) => state.selectedResidues.has(residueKey(row)))) {
        return `${singleType}_ALL`;
      }
    }
    return `${rows.length} Residues`;
  }

  function buildPayloadFromRows(rows) {
    if (!rows.length) return null;

    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;

    rows.forEach((row) => {
      const bbox = row.bbox || {};
      minX = Math.min(minX, Number(bbox.minX));
      minY = Math.min(minY, Number(bbox.minY));
      minZ = Math.min(minZ, Number(bbox.minZ));
      maxX = Math.max(maxX, Number(bbox.maxX));
      maxY = Math.max(maxY, Number(bbox.maxY));
      maxZ = Math.max(maxZ, Number(bbox.maxZ));
    });

    return {
      query: String(state.query || "").trim(),
      label: computeSelectionLabel(rows),
      selection: rows.map((row) => row.selection).filter(Boolean).join(" or "),
      residues: rows.map((row) => ({
        chain: row.chain,
        resno: row.resno,
        resname: row.resname,
        atomCount: row.atomCount,
      })),
      bbox: { minX, minY, minZ, maxX, maxY, maxZ },
    };
  }

  function syncBridgeSelection(reason = "search-selection") {
    const rows = selectedRows();
    const payload = buildPayloadFromRows(rows);
    if (payload) {
      bridge.setResidueSelection?.(payload, { reason });
    } else {
      bridge.clearResidueSelection?.({ reason });
    }
  }

  function updateQueryState(rawValue) {
    state.query = String(rawValue || "");
    const alphaQuery = normalizeText(rawValue);
    const digitQuery = normalizeDigits(rawValue);

    state.currentCandidate = null;
    state.currentMatches = [];
    state.queryMode = "empty";

    if (!rawValue || !String(rawValue).trim()) return;

    if (digitQuery && !alphaQuery) {
      state.queryMode = "number";
      state.currentMatches = state.residues.filter((row) => String(row.resno || "").includes(digitQuery));
      return;
    }

    if (alphaQuery.length < 2) {
      state.queryMode = "short";
      return;
    }

    const scored = RESIDUE_TYPES
      .map((candidate) => ({
        candidate,
        score: scoreCandidate(alphaQuery, candidate),
      }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score || a.candidate.code.localeCompare(b.candidate.code));

    if (!scored.length) {
      state.queryMode = "miss";
      return;
    }

    state.queryMode = "family";
    state.currentCandidate = scored[0].candidate;
    state.currentMatches = state.residues.filter((row) => row.resname === state.currentCandidate.code);
  }

  function scoreCandidate(query, candidate) {
    if (!query) return 0;
    if (candidate.code === query) return 1000;
    if (candidate.names.includes(query)) return 950;
    if (candidate.code.startsWith(query)) return 820 - (candidate.code.length - query.length);
    for (const name of candidate.names) {
      if (name.startsWith(query)) return 700 - (name.length - query.length);
      if (name.includes(query)) return 500 - (name.length - query.length);
    }
    return 0;
  }

  function allCurrentMatchesSelected() {
    return state.currentMatches.length > 0
      && state.currentMatches.every((row) => state.selectedResidues.has(residueKey(row)));
  }

  function renderSummary() {
    const count = state.selectedResidues.size;
    if (!state.query.trim()) {
      els.summary.textContent = count ? `${count} residues selected. Search another family or residue number to add more.` : "No residue search selected.";
      els.summary.classList.toggle("is-active", count > 0);
      return;
    }

    if (state.queryMode === "short") {
      els.summary.textContent = `${count} residues selected. Keep typing to search.`;
      els.summary.classList.toggle("is-active", count > 0);
      return;
    }

    if (state.queryMode === "miss") {
      els.summary.textContent = `${count} residues selected. No amino-acid family matched this query.`;
      els.summary.classList.toggle("is-active", count > 0);
      return;
    }

    if (state.queryMode === "number") {
      els.summary.textContent = `${state.currentMatches.length} residue-number matches · ${count} selected overall.`;
      els.summary.classList.add("is-active");
      return;
    }

    if (state.queryMode === "family" && state.currentCandidate) {
      els.summary.textContent = `${state.currentCandidate.code}_ALL available · ${state.currentMatches.length} matches · ${count} selected overall.`;
      els.summary.classList.add("is-active");
      return;
    }

    els.summary.textContent = count ? `${count} residues selected.` : "No residue search selected.";
    els.summary.classList.toggle("is-active", count > 0);
  }

  function renderEmpty(message) {
    els.matches.innerHTML = `<div class="grid-selection-search-empty">${message}</div>`;
  }

  function renderMatchButtons(rows) {
    return rows.map((row, index) => {
      const activeClass = state.selectedResidues.has(residueKey(row)) ? " is-active" : "";
      return `
        <div class="grid-selection-search-match${activeClass}">
          <button type="button" data-match-index="${index}">
            <strong>${row.chain} ${row.resno}</strong>
            <span>${row.resname}</span>
            <span>${row.atomCount} atoms</span>
          </button>
        </div>
      `;
    }).join("");
  }

  function bindMatchButtons(rows) {
    els.matches.querySelectorAll("[data-match-index]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const index = Number.parseInt(button.getAttribute("data-match-index") || "-1", 10);
        const row = rows[index];
        if (!row) return;
        toggleResidue(row);
      });
    });
  }

  function renderMatches() {
    renderSummary();

    if (!state.query.trim()) {
      renderEmpty("Type a residue code, full amino-acid name, or a residue number.");
      return;
    }

    if (state.queryMode === "short") {
      renderEmpty("Search starts after 2 characters for residue names.");
      return;
    }

    if (state.queryMode === "miss") {
      renderEmpty("Try a 3-letter code like TRP, a full name like tryptophan, or a residue number like 128.");
      return;
    }

    if (!state.currentMatches.length) {
      renderEmpty("No residues matched the current query in this receptor view.");
      return;
    }

    const parts = [];
    if (state.queryMode === "family" && state.currentCandidate) {
      const allActive = allCurrentMatchesSelected() ? " is-active" : "";
      parts.push(`
        <div class="grid-selection-search-match grid-selection-search-group${allActive}">
          <button type="button" data-select-all="true">
            <strong>${state.currentCandidate.code}_ALL</strong>
            <span>${state.currentCandidate.names[0].toLowerCase()}</span>
            <span>${state.currentMatches.length} residues</span>
          </button>
        </div>
      `);
    }
    parts.push(renderMatchButtons(state.currentMatches));
    els.matches.innerHTML = parts.join("");

    const selectAllBtn = els.matches.querySelector("[data-select-all='true']");
    if (selectAllBtn) {
      selectAllBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleAllCurrentMatches();
      });
    }
    bindMatchButtons(state.currentMatches);
  }

  function toggleResidue(row) {
    const key = residueKey(row);
    if (state.selectedResidues.has(key)) {
      state.selectedResidues.delete(key);
    } else {
      state.selectedResidues.set(key, row);
    }
    syncBridgeSelection("search-toggle");
    renderMatches();
  }

  function toggleAllCurrentMatches() {
    const allSelected = allCurrentMatchesSelected();
    state.currentMatches.forEach((row) => {
      const key = residueKey(row);
      if (allSelected) {
        state.selectedResidues.delete(key);
      } else {
        state.selectedResidues.set(key, row);
      }
    });
    syncBridgeSelection(allSelected ? "search-all-off" : "search-all-on");
    renderMatches();
  }

  function clearSelection({ preserveQuery = true, reason = "search-clear" } = {}) {
    resetSelectedResidues();
    syncBridgeSelection(reason);
    if (!preserveQuery) {
      state.query = "";
      els.input.value = "";
      updateQueryState("");
    }
    renderMatches();
  }

  function loadSelectionFromBridge(reason = "") {
    const selection = bridge.getResidueSelection ? bridge.getResidueSelection() : null;
    if (!selection?.residues?.length) {
      if (["atom", "ligand", "pocket", "receptor-change", "chain-change", "viewer-chain-change", "reset-gridbox", "query-empty", "query-no-match", "query-empty-result"].includes(String(reason || ""))) {
        resetSelectedResidues();
        renderMatches();
      }
      return;
    }
    const desiredKeys = new Set(
      selection.residues.map((row) => residueKey(row)).filter(Boolean)
    );
    const nextMap = new Map();
    state.residues.forEach((row) => {
      const key = residueKey(row);
      if (desiredKeys.has(key)) {
        nextMap.set(key, row);
      }
    });
    state.selectedResidues = nextMap;
    renderMatches();
  }

  function handleContextChange(reason = "") {
    const previousKey = state.contextKey;
    const nextKey = contextKey();
    if (nextKey !== previousKey || reason === "viewer-refresh") {
      loadResidues();
    }
    if (nextKey !== previousKey || ["no-receptor", "viewer-error", "viewer-refresh", "receptor-change", "chain-change", "viewer-chain-change"].includes(reason)) {
      resetSelectedResidues();
      state.query = "";
      els.input.value = "";
      updateQueryState("");
      renderMatches();
    }
  }

  els.toggle.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setPanelOpen(!state.open);
  });

  els.input.addEventListener("input", (event) => {
    updateQueryState(event.target.value || "");
    renderMatches();
  });

  els.clear.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    clearSelection({ preserveQuery: true, reason: "search-clear-button" });
  });

  document.addEventListener("click", (event) => {
    if (!state.open) return;
    if (els.panel.contains(event.target) || els.toggle.contains(event.target)) return;
    setPanelOpen(false);
  });

  window.addEventListener("dockup:grid-selection-context", (event) => {
    handleContextChange(String(event?.detail?.reason || ""));
  });

  window.addEventListener("dockup:residue-search-selection", (event) => {
    loadSelectionFromBridge(String(event?.detail?.reason || ""));
  });

  updateQueryState("");
  renderMatches();
})();
