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
  };

  if (!els.toggle || !els.panel || !els.input || !els.summary || !els.matches) return;

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
    activeLabel: "",
  };

  function normalizeText(value) {
    return String(value || "").trim().toUpperCase().replace(/[^A-Z]/g, "");
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
      window.requestAnimationFrame(() => {
        els.input.focus();
        els.input.select();
      });
    }
  }

  function loadResidues() {
    state.contextKey = contextKey();
    state.residues = bridge.getResidueCatalog ? bridge.getResidueCatalog() : [];
  }

  function renderEmpty(message) {
    els.matches.innerHTML = `<div class="grid-selection-search-empty">${message}</div>`;
  }

  function resetPanel({ preserveInput = false } = {}) {
    state.activeLabel = "";
    if (!preserveInput) {
      state.query = "";
      els.input.value = "";
    }
    els.summary.textContent = "No residue search selected.";
    els.summary.classList.remove("is-active");
    renderEmpty("Type a residue code or amino-acid name.");
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

  function bestCandidate(query) {
    const scored = RESIDUE_TYPES
      .map((candidate) => ({ candidate, score: scoreCandidate(query, candidate) }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score || a.candidate.code.localeCompare(b.candidate.code));
    return scored.length ? scored[0].candidate : null;
  }

  function buildPayload(candidate, query) {
    const residues = (state.residues || []).filter((row) => row.resname === candidate.code);
    if (!residues.length) return null;

    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;

    residues.forEach((row) => {
      const bbox = row.bbox || {};
      minX = Math.min(minX, Number(bbox.minX));
      minY = Math.min(minY, Number(bbox.minY));
      minZ = Math.min(minZ, Number(bbox.minZ));
      maxX = Math.max(maxX, Number(bbox.maxX));
      maxY = Math.max(maxY, Number(bbox.maxY));
      maxZ = Math.max(maxZ, Number(bbox.maxZ));
    });

    return {
      query,
      label: `${candidate.code}_ALL`,
      selection: residues.map((row) => row.selection).filter(Boolean).join(" or "),
      residues: residues.map((row) => ({
        chain: row.chain,
        resno: row.resno,
        resname: row.resname,
        atomCount: row.atomCount,
      })),
      bbox: { minX, minY, minZ, maxX, maxY, maxZ },
    };
  }

  function renderMatches(payload, candidate) {
    const residues = payload?.residues || [];
    const ctx = bridge.getSelectedContext ? bridge.getSelectedContext() : { chain: "all" };
    const chainText = String(ctx?.chain || "all") === "all" ? "all chains" : `chain ${ctx.chain}`;
    els.summary.textContent = `${payload.label} · ${residues.length} residues in ${chainText}`;
    els.summary.classList.add("is-active");
    els.matches.innerHTML = residues.map((row) => `
      <div class="grid-selection-search-match">
        <strong>${row.chain} ${row.resno}</strong>
        <span>${row.resname} · ${candidate.names[0].toLowerCase()}</span>
        <span>${row.atomCount} atoms</span>
      </div>
    `).join("");
  }

  function applyQuery(rawValue) {
    state.query = rawValue;
    const query = normalizeText(rawValue);

    if (!query) {
      bridge.clearResidueSelection?.({ reason: "query-empty" });
      resetPanel({ preserveInput: false });
      return;
    }

    if (query.length < 2) {
      bridge.clearResidueSelection?.({ reason: "query-short" });
      els.summary.textContent = "Keep typing to search residue families.";
      els.summary.classList.remove("is-active");
      renderEmpty("Search starts after 2 characters.");
      return;
    }

    const candidate = bestCandidate(query);
    if (!candidate) {
      bridge.clearResidueSelection?.({ reason: "query-no-match" });
      els.summary.textContent = "No amino-acid family matched this query.";
      els.summary.classList.remove("is-active");
      renderEmpty("Try a 3-letter code like TRP or a full name like tryptophan.");
      return;
    }

    const payload = buildPayload(candidate, query);
    if (!payload) {
      bridge.clearResidueSelection?.({ reason: "query-empty-result" });
      els.summary.textContent = `${candidate.code}_ALL is not present in the current receptor view.`;
      els.summary.classList.remove("is-active");
      renderEmpty("Change chain selection or search another residue type.");
      return;
    }

    state.activeLabel = payload.label;
    bridge.setResidueSelection?.(payload);
    renderMatches(payload, candidate);
  }

  function syncFromBridgeSelection(reason) {
    const selection = bridge.getResidueSelection ? bridge.getResidueSelection() : null;
    if (selection?.label) {
      state.activeLabel = selection.label;
      if (selection.query && !els.input.value.trim()) {
        els.input.value = selection.query;
      }
      return;
    }
    if (["atom", "ligand", "pocket", "receptor-change", "chain-change", "viewer-chain-change", "reset-gridbox"].includes(String(reason || ""))) {
      resetPanel({ preserveInput: false });
    }
  }

  els.toggle.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setPanelOpen(!state.open);
  });

  els.input.addEventListener("input", (event) => {
    applyQuery(event.target.value || "");
  });

  document.addEventListener("click", (event) => {
    if (!state.open) return;
    if (els.panel.contains(event.target) || els.toggle.contains(event.target)) return;
    setPanelOpen(false);
  });

  window.addEventListener("dockup:grid-selection-context", (event) => {
    const previousKey = state.contextKey;
    const nextKey = contextKey();
    const reason = String(event?.detail?.reason || "");
    if (nextKey !== previousKey || reason === "viewer-refresh") {
      loadResidues();
    }
    if (nextKey !== previousKey || ["no-receptor", "viewer-error", "viewer-refresh", "receptor-change", "chain-change", "viewer-chain-change"].includes(reason)) {
      resetPanel({ preserveInput: false });
    }
  });

  window.addEventListener("dockup:residue-search-selection", (event) => {
    syncFromBridgeSelection(event?.detail?.reason || "");
  });

  resetPanel({ preserveInput: false });
})();
