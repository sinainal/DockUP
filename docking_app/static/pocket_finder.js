(function () {
  const modal = document.getElementById("bindingSiteFinderModal");
  if (!modal) return;

  const els = {
    open: document.getElementById("openBindingSiteFinder"),
    close: document.getElementById("closeBindingSiteFinder"),
    loading: document.getElementById("bindingSiteFinderLoading"),
    loadingText: document.getElementById("bindingSiteFinderLoadingText"),
    subtitle: document.getElementById("bindingSiteFinderSubtitle"),
    error: document.getElementById("bindingSiteFinderError"),
    body: document.getElementById("bindingSiteFinderBody"),
    pocketMeta: document.getElementById("bindingSitePocketMeta"),
    pocketList: document.getElementById("bindingSitePocketList"),
    viewerTitle: document.getElementById("bindingSiteViewerTitle"),
    viewport: document.getElementById("bindingSiteViewport"),
    detailTable: document.getElementById("bindingSiteDetailTable"),
    residues: document.getElementById("bindingSiteResidues"),
    gridMode: document.getElementById("bindingSiteGridMode"),
    fixedSize: document.getElementById("bindingSiteFixedSize"),
    padding: document.getElementById("bindingSitePadding"),
    showGrid: document.getElementById("bindingSiteShowGrid"),
    apply: document.getElementById("bindingSiteApplyGrid"),
  };

  const state = {
    modalOpen: false,
    pdbId: "",
    pdbText: "",
    pockets: [],
    selectedIndex: 0,
    stage: null,
    proteinComponent: null,
    pocketSurfaceRep: null,
    pocketResidueRep: null,
    centerComponent: null,
    gridComponent: null,
    loadToken: 0,
  };

  function nextPaint() {
    return new Promise((resolve) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(resolve);
      });
    });
  }

  function resetGridInputs() {
    const defaultFixedSize = window.DockUPGridbox?.getDefaultFixedGridSize
      ? window.DockUPGridbox.getDefaultFixedGridSize()
      : 20;
    els.gridMode.value = "fit";
    els.fixedSize.value = String(defaultFixedSize);
    els.padding.value = "2";
    els.showGrid.checked = true;
    syncGridModeUI();
  }

  function fetchJSON(url, options) {
    return fetch(url, options).then(async (response) => {
      const text = await response.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (_err) {
        data = { detail: text || "Unknown error" };
      }
      if (!response.ok) {
        throw new Error(data.detail || data.error || `Request failed: ${response.status}`);
      }
      return data;
    });
  }

  function setLoading(message) {
    els.loading.style.display = "flex";
    els.body.style.display = "none";
    els.error.style.display = "none";
    els.error.textContent = "";
    els.loadingText.textContent = message || "Preparing binding site prediction...";
  }

  function showError(message) {
    els.error.textContent = message || "Binding site finder failed.";
    els.error.style.display = "block";
    els.loading.style.display = "none";
    els.body.style.display = "none";
  }

  function showBody() {
    els.loading.style.display = "none";
    els.error.style.display = "none";
    els.body.style.display = "grid";
  }

  function currentPocket() {
    return state.pockets[state.selectedIndex] || null;
  }

  function selectionForPocket(pocket) {
    const chainMap = new Map();
    (pocket?.residue_ids || []).forEach((token) => {
      const parts = String(token || "").split("_");
      if (parts.length !== 2) return;
      const chain = parts[0];
      const residue = parts[1];
      if (!chain || !residue) return;
      const bucket = chainMap.get(chain) || [];
      bucket.push(residue);
      chainMap.set(chain, bucket);
    });
    const pieces = [];
    chainMap.forEach((residues, chain) => {
      pieces.push(`:${chain} and (${residues.join(" or ")})`);
    });
    return pieces.join(" or ");
  }

  function createCenterShape(pocket) {
    const shape = new NGL.Shape("binding-site-center");
    shape.addSphere(
      [pocket.center.x, pocket.center.y, pocket.center.z],
      [0.329, 0.882, 0.757],
      1.1,
      `Pocket ${pocket.rank}`
    );
    return shape;
  }

  function clearRepresentation(rep) {
    if (!rep) return null;
    try {
      rep.setVisibility(false);
      rep.dispose();
    } catch (_err) {
      // stale
    }
    return null;
  }

  function clearViewerOverlays() {
    state.pocketSurfaceRep = clearRepresentation(state.pocketSurfaceRep);
    state.pocketResidueRep = clearRepresentation(state.pocketResidueRep);
    if (state.centerComponent && state.stage) {
      try {
        state.stage.removeComponent(state.centerComponent);
      } catch (_err) {
        // ignore
      }
      state.centerComponent = null;
    }
    if (state.gridComponent && state.stage) {
      try {
        state.stage.removeComponent(state.gridComponent);
      } catch (_err) {
        // ignore
      }
      state.gridComponent = null;
    }
  }

  async function ensureStage() {
    if (!state.stage) {
      state.stage = new NGL.Stage("bindingSiteViewport", {
        backgroundColor: "#02070c",
        clipNear: 0,
      });
      window.addEventListener("resize", () => {
        if (state.stage) state.stage.handleResize();
      });
    }

    if (state.proteinComponent) {
      try {
        state.stage.removeComponent(state.proteinComponent);
      } catch (_err) {
        // ignore
      }
      state.proteinComponent = null;
    }

    const blob = new Blob([state.pdbText], { type: "text/plain" });
    state.proteinComponent = await state.stage.loadFile(blob, { ext: "pdb" });
    state.proteinComponent.addRepresentation("cartoon", {
      color: "#d9e5ef",
      opacity: 0.95,
    });
    state.stage.setFocus(0);
  }

  async function refreshStageLayout() {
    if (!state.stage) return;
    await nextPaint();
    try {
      state.stage.handleResize();
      state.stage.setFocus(0);
      state.stage.autoView(800);
      state.stage.viewer?.requestRender?.();
    } catch (_err) {
      // ignore layout refresh failures
    }
  }

  function renderPocketList() {
    els.pocketMeta.textContent = `${state.pockets.length} pockets | ${state.pdbId}`;
    els.pocketList.innerHTML = state.pockets.map((pocket, index) => `
      <article class="pocket-finder-pocket-card ${index === state.selectedIndex ? "active" : ""}" data-pocket-index="${index}">
        <div class="pocket-finder-pocket-top">
          <span class="pocket-finder-pocket-rank">#${pocket.rank}</span>
          <div class="pocket-finder-pocket-prob">
            <strong>${Number(pocket.probability || 0).toFixed(3)}</strong>
            <span>Probability</span>
          </div>
        </div>
        <div class="pocket-finder-pocket-meta">
          <div>
            <span>Score</span>
            <strong>${Number(pocket.score || 0).toFixed(2)}</strong>
          </div>
          <div>
            <span>Residues</span>
            <strong>${pocket.residue_count}</strong>
          </div>
          <div>
            <span>Surface</span>
            <strong>${pocket.surface_atoms}</strong>
          </div>
        </div>
      </article>
    `).join("");

    els.pocketList.querySelectorAll(".pocket-finder-pocket-card").forEach((card) => {
      card.addEventListener("click", () => {
        state.selectedIndex = Number(card.dataset.pocketIndex || 0);
        updateSelectedPocket(true);
      });
    });
  }

  function renderDetailTable(pocket) {
    const rows = Array.isArray(pocket?.detail_rows) ? pocket.detail_rows : [];
    els.detailTable.innerHTML = rows.map((row) => `
      <div class="pocket-finder-detail-item">
        <span>${row.label}</span>
        <strong>${row.value}</strong>
      </div>
    `).join("");
  }

  function renderResidues(pocket) {
    const rows = Array.isArray(pocket?.top_residues) ? pocket.top_residues : [];
    els.residues.innerHTML = rows.length
      ? rows.map((row) => `
          <span class="pocket-finder-residue-chip">
            ${row.chain || ""} ${row.residue_label || ""} ${row.residue_name || ""}
            <strong>${Number(row.probability || 0).toFixed(3)}</strong>
          </span>
        `).join("")
      : `<span class="helper">No residue-level scores available.</span>`;
  }

  function previewGridData(pocket) {
    const mode = String(els.gridMode.value || "fit");
    const fixedSize = Math.max(Number.parseFloat(els.fixedSize.value || "20") || 20, 1);
    const padding = Math.max(Number.parseFloat(els.padding.value || "0") || 0, 0);
    if (mode === "fixed") {
      return {
        cx: Number(pocket.center.x) || 0,
        cy: Number(pocket.center.y) || 0,
        cz: Number(pocket.center.z) || 0,
        sx: fixedSize,
        sy: fixedSize,
        sz: fixedSize,
      };
    }
    const preview = pocket.box_preview || {};
    return {
      cx: Number(preview.cx) || Number(pocket.center.x) || 0,
      cy: Number(preview.cy) || Number(pocket.center.y) || 0,
      cz: Number(preview.cz) || Number(pocket.center.z) || 0,
      sx: Math.max((Number(preview.sx) || fixedSize) + (2 * padding), 1),
      sy: Math.max((Number(preview.sy) || fixedSize) + (2 * padding), 1),
      sz: Math.max((Number(preview.sz) || fixedSize) + (2 * padding), 1),
    };
  }

  function syncGridModeUI() {
    const fixedMode = String(els.gridMode.value || "fit") === "fixed";
    els.fixedSize.disabled = !fixedMode;
    els.fixedSize.closest(".field")?.classList.toggle("is-disabled", !fixedMode);
    els.padding.closest(".field")?.classList.toggle("is-disabled", fixedMode);
  }

  function renderGridPreview(grid) {
    if (!state.stage) return;
    if (state.gridComponent) {
      try {
        state.stage.removeComponent(state.gridComponent);
      } catch (_err) {
        // ignore
      }
      state.gridComponent = null;
    }
    if (!els.showGrid.checked || !grid) return;

    const { cx, cy, cz, sx, sy, sz } = grid;
    const hx = sx / 2;
    const hy = sy / 2;
    const hz = sz / 2;
    const corners = [
      [cx - hx, cy - hy, cz - hz],
      [cx + hx, cy - hy, cz - hz],
      [cx + hx, cy + hy, cz - hz],
      [cx - hx, cy + hy, cz - hz],
      [cx - hx, cy - hy, cz + hz],
      [cx + hx, cy - hy, cz + hz],
      [cx + hx, cy + hy, cz + hz],
      [cx - hx, cy + hy, cz + hz],
    ];
    const edges = [
      [0, 1], [1, 2], [2, 3], [3, 0],
      [4, 5], [5, 6], [6, 7], [7, 4],
      [0, 4], [1, 5], [2, 6], [3, 7],
    ];

    const shape = new NGL.Shape("binding-site-gridbox");
    corners.forEach((corner, index) => {
      shape.addSphere(corner, [0.24, 0.52, 0.95], 0.28, `Grid ${index + 1}`);
    });
    edges.forEach(([a, b]) => {
      shape.addCylinder(corners[a], corners[b], [0.24, 0.52, 0.95], 0.08);
    });
    state.gridComponent = state.stage.addComponentFromObject(shape);
    state.gridComponent.addRepresentation("buffer");
  }

  function updateViewerPocket(pocket, shouldAutoView) {
    if (!state.proteinComponent || !pocket) return;
    clearViewerOverlays();

    const selection = selectionForPocket(pocket);
    if (selection) {
      state.pocketSurfaceRep = state.proteinComponent.addRepresentation("surface", {
        sele: selection,
        opacity: 0.78,
        color: "#54e1c1",
        surfaceType: "av",
        useWorker: false,
      });
      state.pocketResidueRep = state.proteinComponent.addRepresentation("licorice", {
        sele: selection,
        color: "#54e1c1",
        radiusScale: 0.82,
        opacity: 0.98,
      });
      if (shouldAutoView) {
        try {
          state.proteinComponent.autoView(selection, 1000);
        } catch (_err) {
          state.stage.autoView(1000);
        }
      }
    }

    state.centerComponent = state.stage.addComponentFromObject(createCenterShape(pocket));
    state.centerComponent.addRepresentation("buffer");
    renderGridPreview(previewGridData(pocket));
  }

  function updateSelectedPocket(shouldAutoView) {
    const pocket = currentPocket();
    if (!pocket) return;
    els.viewerTitle.textContent = `Pocket #${pocket.rank} | Probability ${Number(pocket.probability || 0).toFixed(3)} | Score ${Number(pocket.score || 0).toFixed(2)}`;
    renderPocketList();
    renderDetailTable(pocket);
    renderResidues(pocket);
    updateViewerPocket(pocket, shouldAutoView);
  }

  async function loadResultsForSelectedReceptor(pdbId, token) {
    const [detail, results] = await Promise.all([
      fetchJSON(`/api/receptors/${encodeURIComponent(pdbId)}`),
      fetchJSON(`/api/pockets/results?pdb_id=${encodeURIComponent(pdbId)}`),
    ]);
    if (!state.modalOpen || token !== state.loadToken) return;
    state.pdbId = pdbId;
    state.pdbText = String(detail.pdb_text || "").trim();
    state.pockets = Array.isArray(results.pockets) ? results.pockets : [];
    state.selectedIndex = 0;
    els.subtitle.textContent = `Binding site prediction for ${pdbId}`;
    if (!state.pdbText || !state.pockets.length) {
      throw new Error("No binding site pockets were produced for the selected receptor.");
    }
    showBody();
    await nextPaint();
    await ensureStage();
    await refreshStageLayout();
    renderPocketList();
    updateSelectedPocket(true);
    await refreshStageLayout();
  }

  async function waitForPrediction(pdbId, token) {
    while (state.modalOpen && token === state.loadToken) {
      const status = await fetchJSON(`/api/pockets/status?pdb_id=${encodeURIComponent(pdbId)}`);
      if (!state.modalOpen || token !== state.loadToken) return;
      if (status.status === "done") {
        await loadResultsForSelectedReceptor(pdbId, token);
        return;
      }
      if (status.status === "error") {
        throw new Error(status.error || status.message || "Binding site prediction failed.");
      }
      setLoading(status.message || `Running binding site prediction for ${pdbId}...`);
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  }

  async function openBindingSiteFinder() {
    const selectedReceptor = window.DockUPGridbox?.getSelectedReceptorId
      ? window.DockUPGridbox.getSelectedReceptorId()
      : "";
    if (!selectedReceptor) {
      alert("Select a receptor first.");
      return;
    }

    state.modalOpen = true;
    state.loadToken += 1;
    modal.classList.add("active");
    resetGridInputs();
    setLoading(`Running binding site prediction for ${selectedReceptor}...`);

    try {
      await fetchJSON("/api/pockets/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdb_id: selectedReceptor }),
      });
      await waitForPrediction(selectedReceptor, state.loadToken);
    } catch (error) {
      showError(error.message || "Binding site finder failed.");
    }
  }

  async function closeBindingSiteFinder() {
    state.modalOpen = false;
    state.loadToken += 1;
    modal.classList.remove("active");
    state.pdbId = "";
    state.pdbText = "";
    state.pockets = [];
    state.selectedIndex = 0;
    els.pocketList.innerHTML = "";
    els.detailTable.innerHTML = "";
    els.residues.innerHTML = "";
    els.viewerTitle.textContent = "-";
    els.pocketMeta.textContent = "-";
    clearViewerOverlays();
    if (state.stage) {
      try {
        state.stage.dispose();
      } catch (_err) {
        // ignore
      }
      state.stage = null;
      state.proteinComponent = null;
    }
    setLoading("Preparing binding site prediction...");
    resetGridInputs();
    try {
      await fetchJSON("/api/pockets/clear", { method: "POST" });
    } catch (_err) {
      // ignore
    }
  }

  async function applySelectedPocket() {
    const pocket = currentPocket();
    if (!pocket) return;
    const gridMode = String(els.gridMode.value || "fit");
    const fixedSize = Number.parseFloat(els.fixedSize.value || "20") || 20;
    const padding = Number.parseFloat(els.padding.value || "2") || 0;
    try {
      const response = await fetchJSON("/api/pockets/gridbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pdb_id: state.pdbId,
          pocket_rank: pocket.rank,
          mode: gridMode,
          fixed_size: fixedSize,
          padding,
        }),
      });
      if (!window.DockUPGridbox?.applyExternalGridbox) {
        throw new Error("Gridbox bridge is not available.");
      }
      window.DockUPGridbox.applyExternalGridbox(response.grid_data, {
        pdbId: state.pdbId,
        showGrid: Boolean(els.showGrid.checked),
      });
      await closeBindingSiteFinder();
    } catch (error) {
      alert(error.message || "Failed to apply gridbox.");
    }
  }

  function bindGridPreviewControls() {
    [els.gridMode, els.fixedSize, els.padding, els.showGrid].forEach((element) => {
      element.addEventListener("input", () => {
        if (element === els.gridMode) syncGridModeUI();
        const pocket = currentPocket();
        if (pocket) renderGridPreview(previewGridData(pocket));
      });
      element.addEventListener("change", () => {
        if (element === els.gridMode) syncGridModeUI();
        const pocket = currentPocket();
        if (pocket) renderGridPreview(previewGridData(pocket));
      });
    });
  }

  els.open.addEventListener("click", () => {
    openBindingSiteFinder();
  });

  els.close.addEventListener("click", () => {
    closeBindingSiteFinder();
  });

  modal.addEventListener("click", (event) => {
    if (event.target === modal) {
      closeBindingSiteFinder();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.modalOpen) {
      closeBindingSiteFinder();
    }
  });

  els.apply.addEventListener("click", () => {
    applySelectedPocket();
  });

  bindGridPreviewControls();
  resetGridInputs();
})();
