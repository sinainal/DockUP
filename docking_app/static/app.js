// =====================================================
// Alternative UI - NGL Viewer Application
// Simplified and fixed version with better quality
// =====================================================

const RESULTS_DOCK_ROOT = "data/dock";

const appState = {
  mode: "Docking",
  selectedReceptor: "",
  selectedLigand: "",
  selectedChain: "all",
  gridFilePath: "",
  queueCount: 0,
  runStatus: "idle",
  selectionMap: {},
  activeLigands: [],
  resultsRootPath: RESULTS_DOCK_ROOT,
  resultsView: "runs",
  resultsData: { runs: [], averages: [] },
  selectedResultDir: "",
  dockingConfig: {},
  activeRunOutRoot: "",
  runElapsedSeconds: 0,
  queueData: [],
  selectedQueueBatchId: null,
  queueEditorReceptorIds: [],
};

const DEFAULT_DOCKING_CONFIG = {
  docking_mode: "standard",
  pdb2pqr_ph: 7.4,
  pdb2pqr_ff: "AMBER",
  pdb2pqr_ffout: "AMBER",
  pdb2pqr_nodebump: true,
  pdb2pqr_keep_chain: true,
  mkrec_allow_bad_res: true,
  mkrec_default_altloc: "A",
  vina_exhaustiveness: 32,
  vina_num_modes: null,
  vina_energy_range: null,
  vina_cpu: null,
  vina_seed: null,
};

// Viewer state
let stage = null;
let comp = null;
let gridComp = null;
let runPoll = null;
let recentAutoRefreshTs = 0;
let selectedLigandData = null; // {resname, resno, chainname}
let selectedAtomData = null; // {index, atomname, resname, resno, chainname, x, y, z, selection}
let nativeLigComp = null;
let interactionResiduesByType = {};
let interactionResidueInfo = {};
let interactionReps = {};
let hoverHandlerBound = false;
let atomClickHandlerBound = false;
let resultPoseByPdb = new Map();
let currentResultPdbKey = "";
let currentResultFlexSelectionData = null;
let currentResultDockingMode = "standard";
let currentResultData = null;
let viewerPoseSyncBound = false;
let applyingProgrammaticResultPose = false;
let refreshViewerRequestId = 0;
let resultDetailRequestId = 0;
let resultStructureRequestId = 0;

const EXCLUDED_RESN = new Set([
  "HOH", "DOD", "WAT", "NA", "CL", "K", "MG", "CA", "ZN", "FE", "CU", "MN", "CO", "NI",
]);
const AMINO_ACID_RESN = new Set([
  "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
  "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
  "SEC", "PYL", "ASX", "GLX", "XLE", "XAA",
]);
const NUCLEOTIDE_RESN = new Set([
  "A", "C", "G", "T", "U", "DA", "DC", "DG", "DT", "DU",
]);

const INTERACTION_KIND_ORDER = [
  "hydrophobic_interaction",
  "hydrogen_bond",
  "salt_bridge",
  "pi_stack",
  "pi_cation_interaction",
  "halogen_bond",
  "water_bridge",
  "metal_complex",
];

const INTERACTION_KIND_LABELS = {
  hydrophobic_interaction: "Hydrophobic",
  hydrogen_bond: "H-bond",
  salt_bridge: "Salt bridge",
  pi_stack: "pi-stacking",
  pi_cation_interaction: "pi-cation",
  halogen_bond: "Halogen bond",
  water_bridge: "Water bridge",
  metal_complex: "Metal complex",
};

// Professional color palette - matches PyMOL/PLIP style colors
const PROFESSIONAL_COLORS = {
  hydrophobic_interaction: "#7e7e7e",  // Gray - hydrophobic
  hydrogen_bond: "#3498db",           // Blue - H-bond
  salt_bridge: "#e74c3c",             // Red - Salt bridge  
  pi_stack: "#2ecc71",                // Green - pi-stacking
  pi_cation_interaction: "#9b59b6",   // Purple - pi-cation
  halogen_bond: "#1abc9c",            // Teal - Halogen bond
  water_bridge: "#00bcd4",            // Cyan - Water bridge
  metal_complex: "#ff9800",           // Orange - Metal complex
};

const INTERACTION_COLORS = PROFESSIONAL_COLORS;

// Representations
let representations = {
  cartoon: null,
  surface: null,
  nativeLigand: null,
  dockedLigand: null,
  sticks: null,
  focusedLigand: null,
  selectedAtom: null,
  residueSearch: null,
  flexResidueSearch: null,
};

// Gridbox state
let gridboxData = null;
let gridDataPerReceptor = {}; // Store grid data per receptor
let gridSelectionMetaPerReceptor = {};
let externalGridSelectionData = null;
let residueSearchSelectionData = null;

// DOM Elements
const els = {};

// Initialize elements after DOM ready
function initElements() {
  els.runStatus = document.getElementById("runStatus");
  els.modeToggle = document.getElementById("modeToggle");
  els.ligandSection = document.getElementById("ligandSection");
  els.ligandUpload = document.getElementById("ligandUpload");
  els.ligandUploadName = document.getElementById("ligandUploadName");
  els.activeLigandList = document.getElementById("activeLigandList");
  els.openLigand3dPopup = document.getElementById("openLigand3dPopup");
  els.closeLigand3dPopup = document.getElementById("closeLigand3dPopup");
  els.ligand3dModal = document.getElementById("ligand3dModal");
  els.ligand3dFrame = document.getElementById("ligand3dFrame");
  els.pdbIds = document.getElementById("pdbIds");
  els.receptorUpload = document.getElementById("receptorUpload");
  els.receptorUploadName = document.getElementById("receptorUploadName");
  els.loadReceptors = document.getElementById("loadReceptors");
  els.receptorFileList = document.getElementById("receptorFileList");
  els.receptorSummary = document.getElementById("receptorSummary");
  els.ligandTable = document.getElementById("ligandTable");
  els.runCount = document.getElementById("runCount");
  els.gridPad = document.getElementById("gridPadding");
  els.gridUpload = document.getElementById("gridUpload");
  els.gridUploadName = document.getElementById("gridUploadName");
  els.gridPath = document.getElementById("gridPath");
  els.outRootPath = document.getElementById("outRootPath");
  els.outRootName = document.getElementById("outRootName");
  els.pickOutRoot = document.getElementById("pickOutRoot");
  els.outRootPicker = document.getElementById("outRootPicker");
  els.resultsDockFolderSelect = document.getElementById("resultsDockFolderSelect");
  els.resultsRootPath = document.getElementById("resultsRootPath");
  els.pickResultsRoot = document.getElementById("pickResultsRoot");
  els.resultsRootPicker = document.getElementById("resultsRootPicker");
  els.scanResults = document.getElementById("scanResults");
  els.resultsViewToggle = document.getElementById("resultsViewToggle");
  els.resultsTable = document.getElementById("resultsTable");
  els.residueTable = document.getElementById("residueTable");
  els.resultsCount = document.getElementById("resultsCount");
  els.resultDetail = document.getElementById("resultDetail");
  els.buildQueue = document.getElementById("buildQueue");
  els.clearQueueSelection = document.getElementById("clearQueueSelection");
  els.queueCount = document.getElementById("queueCount");
  els.queueEditorStatus = document.getElementById("queueEditorStatus");
  els.queueTable = document.getElementById("queueTable");
  els.openDockingConfigModal = document.getElementById("openDockingConfigModal");
  els.dockingConfigModal = document.getElementById("dockingConfigModal");
  els.closeDockingConfigModal = document.getElementById("closeDockingConfigModal");
  els.saveDockingConfigModal = document.getElementById("saveDockingConfigModal");
  els.cancelDockingConfigModal = document.getElementById("cancelDockingConfigModal");
  els.dockCfgPdb2pqrPh = document.getElementById("dockCfgPdb2pqrPh");
  els.dockCfgDockingMode = document.getElementById("dockCfgDockingMode");
  els.dockCfgPdb2pqrFf = document.getElementById("dockCfgPdb2pqrFf");
  els.dockCfgPdb2pqrFfout = document.getElementById("dockCfgPdb2pqrFfout");
  els.dockCfgPdb2pqrNodebump = document.getElementById("dockCfgPdb2pqrNodebump");
  els.dockCfgPdb2pqrKeepChain = document.getElementById("dockCfgPdb2pqrKeepChain");
  els.dockCfgMkrecAllowBadRes = document.getElementById("dockCfgMkrecAllowBadRes");
  els.dockCfgMkrecDefaultAltloc = document.getElementById("dockCfgMkrecDefaultAltloc");
  els.dockCfgVinaExhaustiveness = document.getElementById("dockCfgVinaExhaustiveness");
  els.dockCfgVinaNumModes = document.getElementById("dockCfgVinaNumModes");
  els.dockCfgVinaEnergyRange = document.getElementById("dockCfgVinaEnergyRange");
  els.dockCfgVinaCpu = document.getElementById("dockCfgVinaCpu");
  els.dockCfgVinaSeed = document.getElementById("dockCfgVinaSeed");
  els.dockCfgFlexInfo = document.getElementById("dockCfgFlexInfo");
  els.dockCfgFlexModeBlock = document.getElementById("dockCfgFlexModeBlock");
  els.runQueue = document.getElementById("runQueue");
  els.stopRunQueue = document.getElementById("stopRunQueue");
  els.runLog = document.getElementById("runLog");
  els.runLogPanel = document.getElementById("runLogPanel");
  els.runCommand = document.getElementById("runCommand");
  els.runProgressText = document.getElementById("runProgressText");
  els.runElapsed = document.getElementById("runElapsed");
  els.runProgressBar = document.getElementById("runProgressBar");
  els.runProgressPanel = document.getElementById("runProgressPanel");
  els.refreshRecentDockings = document.getElementById("refreshRecentDockings");
  els.recentDockingsMeta = document.getElementById("recentDockingsMeta");
  els.recentDockingsTable = document.getElementById("recentDockingsTable");
  els.colorScheme = document.getElementById("colorScheme");
  els.viewerChain = document.getElementById("viewerChain");
  els.showSurface = document.getElementById("showSurface");
  els.showNativeLigand = document.getElementById("showNativeLigand");
  els.showDockedLigand = document.getElementById("showDockedLigand");
  els.showInteractions = document.getElementById("showInteractions");
  els.showSticks = document.getElementById("showSticks");
  els.showFlexResidues = document.getElementById("showFlexResidues");
  els.showGrid = document.getElementById("showGrid");
  els.interactionLegend = document.getElementById("interactionLegend");
  els.interactionHover = document.getElementById("interactionHover");
  els.selectedAtomInfo = document.getElementById("selectedAtomInfo");
  els.gridSelectionInfo = document.getElementById("gridSelectionInfo");

  // Report controls
  els.reportSection = document.getElementById("reportSection");
  els.pickReportRoot = document.getElementById("pickReportRoot");
  els.reportRootPicker = document.getElementById("reportRootPicker");
  els.reportRootPath = document.getElementById("reportRootPath");
  els.reportOutputPath = document.getElementById("reportOutputPath");
  els.pickReportOutput = document.getElementById("pickReportOutput");
  els.reportOutputPicker = document.getElementById("reportOutputPicker");
  els.reportTabToggle = document.getElementById("reportTabToggle");
  els.reportMetaText = document.getElementById("reportMetaText");
  els.reportValidationBox = document.getElementById("reportValidationBox");
  els.reportSourceTableBody = document.getElementById("reportSourceTableBody");
  els.reportMetaModal = document.getElementById("reportMetaModal");
  els.reportRootEditorCard = document.getElementById("reportRootEditorCard");
  els.closeReportMetaModal = document.getElementById("closeReportMetaModal");
  els.cancelReportRootMeta = document.getElementById("cancelReportRootMeta");
  els.reportMainTypeInput = document.getElementById("reportMainTypeInput");
  els.reportReceptorAliasBody = document.getElementById("reportReceptorAliasBody");
  els.reportLigandAliasBody = document.getElementById("reportLigandAliasBody");
  els.saveReportRootMeta = document.getElementById("saveReportRootMeta");
  els.resetReportRootMeta = document.getElementById("resetReportRootMeta");
  els.reportRootMetaInfo = document.getElementById("reportRootMetaInfo");
  els.reportReceptorBody = document.getElementById("reportReceptorBody");
  els.selectAllReceptors = document.getElementById("selectAllReceptors");
  els.reportPlotBody = document.getElementById("reportPlotBody");
  els.selectAllPlots = document.getElementById("selectAllPlots");
  els.reportDpi = document.getElementById("reportDpi");

  els.reportStatTotal = document.getElementById("reportStatTotal");
  els.reportStatRendered = document.getElementById("reportStatRendered");
  els.reportStatGraphs = document.getElementById("reportStatGraphs");
  els.reportStatOther = document.getElementById("reportStatOther");

  // Report Tabs & Buttons
  els.reportImagesTab = document.getElementById("reportImagesTab");
  els.reportGraphsTab = document.getElementById("reportGraphsTab");
  els.reportDocTab = document.getElementById("reportDocTab");

  els.btnRenderPreview = document.getElementById("btnRenderPreview");
  els.deleteAllRenderImages = document.getElementById("deleteAllRenderImages");
  els.refreshReportsBtn = document.getElementById("refreshReportsBtn");

  els.btnGenerateGraphs = document.getElementById("btnGenerateGraphs");
  els.deleteAllPlotImages = document.getElementById("deleteAllPlotImages");
  els.btnGenerateReport = document.getElementById("btnGenerateReport");

  // Galleries
  els.reportImagesGallery = document.getElementById("reportImagesGallery");
  els.reportGraphsGallery = document.getElementById("reportGraphsGallery");
  els.reportDocContent = document.getElementById("reportDocContent");
  els.reportDocRootPath = document.getElementById("reportDocRootPath");
  els.pickReportDocRoot = document.getElementById("pickReportDocRoot");
  els.reportDocRootPicker = document.getElementById("reportDocRootPicker");
  els.refreshReportDocImages = document.getElementById("refreshReportDocImages");
  els.reportDocImageGallery = document.getElementById("reportDocImageGallery");
  els.selectAllDocImages = document.getElementById("selectAllDocImages");
  els.reportDocSelectedCount = document.getElementById("reportDocSelectedCount");
  els.reportFigureStartNumber = document.getElementById("reportFigureStartNumber");
  els.addReportExtraSection = document.getElementById("addReportExtraSection");
  els.saveReportDocConfig = document.getElementById("saveReportDocConfig");
  els.reportExtraSectionsEditor = document.getElementById("reportExtraSectionsEditor");

  // Render Progress
  els.renderProgressPanel = document.getElementById("renderProgressPanel");
  els.renderProgressText = document.getElementById("renderProgressText");
  els.renderElapsed = document.getElementById("renderElapsed");
  els.renderProgressBar = document.getElementById("renderProgressBar");
  els.graphProgressPanel = document.getElementById("graphProgressPanel");
  els.graphProgressText = document.getElementById("graphProgressText");
  els.graphElapsed = document.getElementById("graphElapsed");
  els.graphProgressBar = document.getElementById("graphProgressBar");
  els.fixedGridSize = document.getElementById("fixedGridSize");
  els.createGridLigand = document.getElementById("createGridLigand");
  els.gridControlsPanel = document.getElementById("gridControlsPanel");

  // Range sliders
  els.gridCx = document.getElementById("gridCx");
  els.gridCy = document.getElementById("gridCy");
  els.gridCz = document.getElementById("gridCz");
  els.gridSx = document.getElementById("gridSx");
  els.gridSy = document.getElementById("gridSy");
  els.gridSz = document.getElementById("gridSz");

  // Number inputs
  els.gridCxVal = document.getElementById("gridCxVal");
  els.gridCyVal = document.getElementById("gridCyVal");
  els.gridCzVal = document.getElementById("gridCzVal");
  els.gridSxVal = document.getElementById("gridSxVal");
  els.gridSyVal = document.getElementById("gridSyVal");
  els.gridSzVal = document.getElementById("gridSzVal");

  els.infoCenter = document.getElementById("infoCenter");
  els.infoSize = document.getElementById("infoSize");
}

// =====================================================
// Utility Functions
// =====================================================

async function fetchJSON(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const msg = await resp.text();
    throw new Error(msg || `Request failed: ${resp.status}`);
  }
  return resp.json();
}

async function resolvePathFromPicker(files, scope = "generic", pickerEl = null) {
  let rel = "";
  if (files && files.length > 0) {
    rel = files[0].webkitRelativePath || files[0].name || "";
  }
  if (!rel && pickerEl && pickerEl.webkitEntries && pickerEl.webkitEntries.length > 0) {
    const entry = pickerEl.webkitEntries[0];
    rel = (entry?.fullPath || entry?.name || "").replace(/^[/\\]+/, "");
  }
  if (!rel) return "";
  const data = await fetchJSON("/api/paths/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ relative_path: rel, scope }),
  });
  return data.path || "";
}

function setFileLabel(input, labelEl, { multiple = false } = {}) {
  if (!labelEl) return;
  const files = input.files;
  if (!files || files.length === 0) {
    labelEl.textContent = multiple ? "No files selected." : "No file selected.";
    return;
  }
  if (multiple) {
    labelEl.textContent = `${files.length} file(s) selected`;
  } else {
    labelEl.textContent = files[0].name;
  }
}

function setRunStatus(status) {
  const chip = els.runStatus;
  if (!chip) return;
  chip.textContent = status.toUpperCase();
  chip.classList.remove("status-error", "status-running", "status-stopped");
  if (status === "running") {
    chip.classList.add("status-running");
  } else if (status === "stopping" || status === "stopped") {
    chip.classList.add("status-stopped");
  } else if (status === "error") {
    chip.classList.add("status-error");
  }
}

function isRunActiveStatus(status) {
  return status === "running" || status === "stopping";
}

function normalizePathForCompare(path) {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return raw.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function extractOutRootFromCommand(command) {
  const text = String(command || "");
  if (!text) return "";
  const match = text.match(/--out[_-]root\s+(?:"([^"]+)"|'([^']+)'|(\S+))/i);
  if (!match) return "";
  return String(match[1] || match[2] || match[3] || "").trim();
}

function formatElapsed(totalSeconds) {
  const seconds = Math.max(0, Number(totalSeconds) || 0);
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  const hours = Math.floor(mins / 60);
  const mm = String(mins % 60).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  if (hours > 0) return `${hours}:${mm}:${ss}`;
  return `${mm}:${ss}`;
}

function formatElapsedSince(totalSeconds) {
  const seconds = Math.max(0, Number(totalSeconds) || 0);
  if (seconds < 60) return "0m";
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours < 24) return `${hours}h ${remMins}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h ${remMins}m`;
}

function formatNumber(val, digits = 2) {
  if (val === null || val === undefined || val === "") return "-";
  const num = Number(val);
  if (Number.isNaN(num)) return "-";
  return num.toFixed(digits);
}

function normalizeChainValue(value) {
  const chain = String(value || "").trim();
  return chain && chain.toLowerCase() !== "all" ? chain : "all";
}

function normalizeDockingMode(value) {
  const mode = String(value || "").trim().toLowerCase();
  return mode === "flexible" ? "flexible" : "standard";
}

function normalizeFlexResidueRow(raw) {
  if (!raw || typeof raw !== "object") return null;
  const chain = String(raw.chain || "").trim();
  const resno = String(raw.resno ?? raw.resid ?? "").trim();
  const resname = String(raw.resname || raw.residue_name || "").trim().toUpperCase();
  if (!chain || !resno) return null;
  return { chain, resno, resname };
}

function normalizeFlexResidueList(rawList) {
  if (typeof rawList === "string") {
    return rawList
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean)
      .map((token) => {
        const parts = token.split(":").map((part) => part.trim()).filter(Boolean);
        if (parts.length < 2) return null;
        return { chain: parts[0], resno: parts[1], resname: parts[2] ? parts[2].toUpperCase() : "" };
      })
      .filter(Boolean);
  }
  if (!Array.isArray(rawList)) return [];
  const rows = [];
  const seen = new Set();
  rawList.forEach((row) => {
    const normalized = normalizeFlexResidueRow(row);
    if (!normalized) return;
    const key = `${normalized.chain}:${normalized.resno}:${normalized.resname}`;
    if (seen.has(key)) return;
    seen.add(key);
    rows.push(normalized);
  });
  return rows;
}

function buildFlexResidueSpec(rows) {
  return normalizeFlexResidueList(rows)
    .map((row) => `${row.chain}:${row.resno}`)
    .join(",");
}

function flexResidueKey(row) {
  return `${String(row?.chain || "").trim()}:${String(row?.resno || "").trim()}:${String(row?.resname || "").trim().toUpperCase()}`;
}

function computeResidueSelectionLabel(rows) {
  const normalized = normalizeFlexResidueList(rows);
  if (!normalized.length) return "";
  if (normalized.length === 1) {
    const row = normalized[0];
    if (row.resname) {
      return row.chain ? `${row.resname}_${row.chain}${row.resno}` : `${row.resname}_${row.resno}`;
    }
    return row.chain ? `${row.chain}:${row.resno}` : `${row.resno}`;
  }
  const resnames = new Set(normalized.map((row) => row.resname).filter(Boolean));
  if (resnames.size === 1) {
    const [resname] = Array.from(resnames);
    return `${resname}_ALL`;
  }
  return `${normalized.length} Residues`;
}

function buildResidueSelectionString(rows) {
  return normalizeFlexResidueList(rows)
    .map((row) => residueSelection(row.resno, row.chain !== "_" ? row.chain : ""))
    .filter(Boolean)
    .join(" or ");
}

function buildFlexSelectionData(rows) {
  const normalized = normalizeFlexResidueList(rows);
  if (!normalized.length) return null;
  return {
    label: computeResidueSelectionLabel(normalized),
    selection: buildResidueSelectionString(normalized),
    residues: normalized.map((row) => ({ ...row })),
    spec: buildFlexResidueSpec(normalized),
  };
}

function ensureSelectionMapEntry(pdbId) {
  const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
  if (!normalizedPdbId) return null;
  if (!appState.selectionMap) appState.selectionMap = {};
  const current = appState.selectionMap[normalizedPdbId] || {};
  const next = {
    chain: normalizeChainValue(current.chain || "all"),
    ligand_resname: String(current.ligand_resname || ""),
    flex_residues: normalizeFlexResidueList(current.flex_residues || current.flex_residue_spec || []),
  };
  appState.selectionMap[normalizedPdbId] = next;
  return next;
}

function normalizeSelectionMapState(rawMap) {
  const next = {};
  if (!rawMap || typeof rawMap !== "object") return next;
  Object.entries(rawMap).forEach(([key, value]) => {
    const pdbId = String(key || "").trim().toUpperCase();
    if (!pdbId) return;
    const source = value && typeof value === "object" ? value : {};
    next[pdbId] = {
      chain: normalizeChainValue(source.chain || "all"),
      ligand_resname: String(source.ligand_resname || source.ligand || ""),
      flex_residues: normalizeFlexResidueList(source.flex_residues || source.flex_residue_spec || []),
    };
  });
  return next;
}

function getFlexResiduesForReceptor(pdbId = appState.selectedReceptor) {
  const row = ensureSelectionMapEntry(pdbId);
  return row ? normalizeFlexResidueList(row.flex_residues || []) : [];
}

function setFlexResiduesForReceptor(pdbId, residues) {
  const row = ensureSelectionMapEntry(pdbId);
  if (!row) return [];
  row.flex_residues = normalizeFlexResidueList(residues);
  return row.flex_residues;
}

function clearFlexResiduesForReceptor(pdbId) {
  return setFlexResiduesForReceptor(pdbId, []);
}

function getFlexSelectionData(pdbId = appState.selectedReceptor) {
  return buildFlexSelectionData(getFlexResiduesForReceptor(pdbId));
}

function setCurrentResultFlexSelection(result = null) {
  currentResultDockingMode = normalizeDockingMode(result?.docking_mode || "standard");
  currentResultFlexSelectionData = buildFlexSelectionData(
    result?.flex_residues || result?.flex_residue_spec || []
  );
}

function getActiveFlexSelectionData() {
  if (appState.mode === "Results" || appState.mode === "Report") {
    return currentResultFlexSelectionData;
  }
  return getFlexSelectionData(appState.selectedReceptor);
}

function getSelectedChainForReceptor(pdbId = appState.selectedReceptor) {
  const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
  if (!normalizedPdbId) return "all";
  const row = appState.selectionMap?.[normalizedPdbId] || {};
  const fallback = normalizedPdbId === String(appState.selectedReceptor || "").trim().toUpperCase()
    ? appState.selectedChain
    : "all";
  return normalizeChainValue(row.chain || fallback || "all");
}

function setSelectedChainForReceptor(pdbId, chain) {
  const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
  const normalizedChain = normalizeChainValue(chain);
  if (!normalizedPdbId) return normalizedChain;
  const current = ensureSelectionMapEntry(normalizedPdbId);
  if (!current) return normalizedChain;
  current.chain = normalizedChain;
  if (normalizedPdbId === String(appState.selectedReceptor || "").trim().toUpperCase()) {
    appState.selectedChain = normalizedChain;
  }
  return normalizedChain;
}

function getNativeLigandsForChain(ligandsByChain, chain) {
  const normalizedChain = normalizeChainValue(chain);
  const source = ligandsByChain && typeof ligandsByChain === "object" ? ligandsByChain : {};
  if (normalizedChain === "all") {
    return Array.isArray(source.all) ? [...source.all] : [];
  }
  return Array.isArray(source[normalizedChain]) ? [...source[normalizedChain]] : [];
}

function dispatchGridSelectionContext(reason = "viewer-refresh") {
  window.dispatchEvent(new CustomEvent("dockup:grid-selection-context", {
    detail: {
      reason: String(reason || "viewer-refresh"),
      pdbId: String(appState.selectedReceptor || "").trim().toUpperCase(),
      chain: getSelectedChainForReceptor(appState.selectedReceptor),
      hasStructure: !!comp?.structure,
    },
  }));
}

function dispatchFlexSelectionContext(reason = "viewer-refresh") {
  window.dispatchEvent(new CustomEvent("dockup:flex-selection-context", {
    detail: {
      reason: String(reason || "viewer-refresh"),
      pdbId: String(appState.selectedReceptor || "").trim().toUpperCase(),
      chain: getSelectedChainForReceptor(appState.selectedReceptor),
      hasStructure: !!comp?.structure,
      hasGrid: !!(gridboxData || (appState.selectedReceptor && gridDataPerReceptor[appState.selectedReceptor])),
    },
  }));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function updateRunMetrics({ command = "", totalRuns = 0, completedRuns = 0, elapsedSeconds = 0, status = "idle" }) {
  if (els.runCommand) {
    els.runCommand.textContent = command || "-";
  }
  if (els.runProgressText) {
    els.runProgressText.textContent = `${completedRuns} / ${totalRuns} runs`;
  }
  if (els.runElapsed) {
    els.runElapsed.textContent = formatElapsed(elapsedSeconds);
  }
  if (els.runProgressBar) {
    const denom = totalRuns > 0 ? totalRuns : 1;
    const pct = Math.min(100, Math.round((completedRuns / denom) * 100));
    els.runProgressBar.style.width = `${pct}%`;
  }
  if (els.runProgressPanel) {
    els.runProgressPanel.classList.toggle("running", status === "running");
  }
}

function normalizeDockingConfig(rawConfig) {
  const source = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const normalized = { ...DEFAULT_DOCKING_CONFIG };

  const asBool = (value, fallback) => {
    if (typeof value === "boolean") return value;
    const text = String(value ?? "").trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(text)) return true;
    if (["0", "false", "no", "off"].includes(text)) return false;
    return fallback;
  };
  const asInt = (value, min = null, max = null, nullable = false) => {
    const text = String(value ?? "").trim();
    if (!text) return nullable ? null : null;
    const num = Number(text);
    if (!Number.isFinite(num)) return nullable ? null : null;
    let out = Math.round(num);
    if (min !== null) out = Math.max(min, out);
    if (max !== null) out = Math.min(max, out);
    return out;
  };
  const asFloat = (value, min = null, max = null, nullable = false) => {
    const text = String(value ?? "").trim();
    if (!text) return nullable ? null : null;
    const num = Number(text);
    if (!Number.isFinite(num)) return nullable ? null : null;
    let out = num;
    if (min !== null) out = Math.max(min, out);
    if (max !== null) out = Math.min(max, out);
    return out;
  };

  normalized.docking_mode = normalizeDockingMode(source.docking_mode ?? normalized.docking_mode);
  const ph = asFloat(source.pdb2pqr_ph, 0, 14, false);
  if (ph !== null) normalized.pdb2pqr_ph = ph;
  normalized.pdb2pqr_ff = String(source.pdb2pqr_ff ?? normalized.pdb2pqr_ff).trim() || normalized.pdb2pqr_ff;
  normalized.pdb2pqr_ffout = String(source.pdb2pqr_ffout ?? normalized.pdb2pqr_ffout).trim() || normalized.pdb2pqr_ffout;
  normalized.pdb2pqr_nodebump = asBool(source.pdb2pqr_nodebump, normalized.pdb2pqr_nodebump);
  normalized.pdb2pqr_keep_chain = asBool(source.pdb2pqr_keep_chain, normalized.pdb2pqr_keep_chain);
  normalized.mkrec_allow_bad_res = asBool(source.mkrec_allow_bad_res, normalized.mkrec_allow_bad_res);
  normalized.mkrec_default_altloc = String(source.mkrec_default_altloc ?? normalized.mkrec_default_altloc).trim() || normalized.mkrec_default_altloc;

  const ex = asInt(source.vina_exhaustiveness, 1, 512, false);
  if (ex !== null) normalized.vina_exhaustiveness = ex;
  normalized.vina_num_modes = asInt(source.vina_num_modes, 1, 200, true);
  normalized.vina_energy_range = asFloat(source.vina_energy_range, 0, 1000, true);
  normalized.vina_cpu = asInt(source.vina_cpu, 1, 512, true);
  normalized.vina_seed = asInt(source.vina_seed, 0, null, true);

  return normalized;
}

function readQueueCoreValues() {
  return {
    runCount: String(document.getElementById("runCount")?.value || "10").trim(),
    gridPadding: String(document.getElementById("gridPadding")?.value || "0").trim(),
    outRootPath: String(document.getElementById("outRootPath")?.value || "data/dock").trim(),
    outRootName: String(document.getElementById("outRootName")?.value || "").trim(),
  };
}

function applyQueueCoreValues(values) {
  if (!values || typeof values !== "object") return;
  const runCountEl = document.getElementById("runCount");
  const gridPaddingEl = document.getElementById("gridPadding");
  if (runCountEl && values.runCount !== undefined) runCountEl.value = String(values.runCount);
  if (gridPaddingEl && values.gridPadding !== undefined) gridPaddingEl.value = String(values.gridPadding);
  if (els.outRootPath && values.outRootPath !== undefined) els.outRootPath.value = String(values.outRootPath);
  if (els.outRootName && values.outRootName !== undefined) els.outRootName.value = String(values.outRootName);
}

function readAdvancedDockingConfigFromModal() {
  return normalizeDockingConfig({
    docking_mode: els.dockCfgDockingMode?.value,
    pdb2pqr_ph: els.dockCfgPdb2pqrPh?.value,
    pdb2pqr_ff: els.dockCfgPdb2pqrFf?.value,
    pdb2pqr_ffout: els.dockCfgPdb2pqrFfout?.value,
    pdb2pqr_nodebump: !!els.dockCfgPdb2pqrNodebump?.checked,
    pdb2pqr_keep_chain: !!els.dockCfgPdb2pqrKeepChain?.checked,
    mkrec_allow_bad_res: !!els.dockCfgMkrecAllowBadRes?.checked,
    mkrec_default_altloc: els.dockCfgMkrecDefaultAltloc?.value,
    vina_exhaustiveness: els.dockCfgVinaExhaustiveness?.value,
    vina_num_modes: els.dockCfgVinaNumModes?.value,
    vina_energy_range: els.dockCfgVinaEnergyRange?.value,
    vina_cpu: els.dockCfgVinaCpu?.value,
    vina_seed: els.dockCfgVinaSeed?.value,
  });
}

function applyAdvancedDockingConfigToModal(config) {
  const cfg = normalizeDockingConfig(config || appState.dockingConfig || DEFAULT_DOCKING_CONFIG);

  const setSelectValue = (el, value, fallback = "") => {
    if (!el) return;
    Array.from(el.querySelectorAll("option[data-dynamic='1']")).forEach((opt) => opt.remove());
    const target = String(value ?? "").trim();
    if (target && !Array.from(el.options).some((opt) => String(opt.value) === target)) {
      const customOpt = document.createElement("option");
      customOpt.value = target;
      customOpt.textContent = `${target} (custom)`;
      customOpt.setAttribute("data-dynamic", "1");
      el.appendChild(customOpt);
    }
    el.value = target || fallback;
    if (!el.value && fallback) {
      el.value = fallback;
    }
  };

  if (els.dockCfgDockingMode) els.dockCfgDockingMode.value = cfg.docking_mode || "standard";
  if (els.dockCfgPdb2pqrPh) els.dockCfgPdb2pqrPh.value = String(cfg.pdb2pqr_ph);
  setSelectValue(els.dockCfgPdb2pqrFf, cfg.pdb2pqr_ff, "AMBER");
  setSelectValue(els.dockCfgPdb2pqrFfout, cfg.pdb2pqr_ffout, "AMBER");
  if (els.dockCfgPdb2pqrNodebump) els.dockCfgPdb2pqrNodebump.checked = Boolean(cfg.pdb2pqr_nodebump);
  if (els.dockCfgPdb2pqrKeepChain) els.dockCfgPdb2pqrKeepChain.checked = Boolean(cfg.pdb2pqr_keep_chain);
  if (els.dockCfgMkrecAllowBadRes) els.dockCfgMkrecAllowBadRes.checked = Boolean(cfg.mkrec_allow_bad_res);
  setSelectValue(els.dockCfgMkrecDefaultAltloc, cfg.mkrec_default_altloc || "A", "A");
  if (els.dockCfgVinaExhaustiveness) els.dockCfgVinaExhaustiveness.value = String(cfg.vina_exhaustiveness);
  if (els.dockCfgVinaNumModes) els.dockCfgVinaNumModes.value = cfg.vina_num_modes === null ? "" : String(cfg.vina_num_modes);
  if (els.dockCfgVinaEnergyRange) els.dockCfgVinaEnergyRange.value = cfg.vina_energy_range === null ? "" : String(cfg.vina_energy_range);
  if (els.dockCfgVinaCpu) els.dockCfgVinaCpu.value = cfg.vina_cpu === null ? "" : String(cfg.vina_cpu);
  if (els.dockCfgVinaSeed) els.dockCfgVinaSeed.value = cfg.vina_seed === null ? "" : String(cfg.vina_seed);
  syncDockingModeUI();
}

function renderDockingConfigSummary() {
  const cfg = normalizeDockingConfig(appState.dockingConfig || {});
  if (els.openDockingConfigModal) {
    const flexCount = getFlexResiduesForReceptor(appState.selectedReceptor).length;
    const flexSuffix = cfg.docking_mode === "flexible" ? ` | ${flexCount} flex residues` : "";
    els.openDockingConfigModal.title = `${cfg.docking_mode} | pH ${cfg.pdb2pqr_ph} | Exhaustiveness ${cfg.vina_exhaustiveness}${flexSuffix}`;
  }
}

function syncDockingModeUI() {
  const mode = normalizeDockingMode(els.dockCfgDockingMode?.value || appState.dockingConfig?.docking_mode || "standard");
  if (els.dockCfgFlexModeBlock) {
    els.dockCfgFlexModeBlock.style.display = mode === "flexible" ? "" : "none";
  }
  if (els.dockCfgFlexInfo) {
    const selected = getFlexSelectionData(appState.selectedReceptor);
    const valueEl = els.dockCfgFlexInfo.querySelector(".grid-selection-value");
    if (valueEl) {
      valueEl.textContent = selected?.label || "None";
    }
    els.dockCfgFlexInfo.classList.remove("is-none", "is-residue");
    els.dockCfgFlexInfo.classList.add(selected ? "is-residue" : "is-none");
  }
}

function openDockingConfigModal() {
  if (!els.dockingConfigModal) return;
  dockingConfigSnapshot = normalizeDockingConfig(appState.dockingConfig || {});
  applyAdvancedDockingConfigToModal(dockingConfigSnapshot);
  dispatchFlexSelectionContext("modal-open");
  emitFlexResidueSelectionEvent("modal-open");
  els.dockingConfigModal.classList.add("active");
}

function closeDockingConfigModal({ restore = false } = {}) {
  if (!els.dockingConfigModal) return;
  if (restore && dockingConfigSnapshot) {
    applyAdvancedDockingConfigToModal(dockingConfigSnapshot);
  }
  dockingConfigSnapshot = null;
  renderDockingConfigSummary();
  els.dockingConfigModal.classList.remove("active");
}

function saveDockingConfigModal() {
  appState.dockingConfig = readAdvancedDockingConfigFromModal();
  applyAdvancedDockingConfigToModal(appState.dockingConfig);

  closeDockingConfigModal({ restore: false });
  renderDockingConfigSummary();
  scheduleUIStateSave();
}

// =====================================================
// State Management
// =====================================================

async function loadState() {
  try {
    const data = await fetchJSON("/api/state");
    appState.mode = data.mode;
    appState.selectedReceptor = data.selected_receptor || "";
    appState.selectedLigand = data.selected_ligand || "";
    appState.selectedChain = data.selected_chain || "all";
    appState.activeLigands = Array.isArray(data.active_ligands) ? data.active_ligands : [];
    activeLigands = [...appState.activeLigands];
    if (data.selection_map && typeof data.selection_map === "object") {
      const nextSelection = {};
      Object.entries(data.selection_map).forEach(([key, value]) => {
        const pdbId = String(key || "").trim().toUpperCase();
        if (!pdbId) return;
        const row = value && typeof value === "object" ? value : {};
        nextSelection[pdbId] = {
          chain: normalizeChainValue(row.chain || "all"),
          ligand_resname: String(row.ligand_resname || ""),
          flex_residues: normalizeFlexResidueList(row.flex_residues || row.flex_residue_spec || []),
        };
      });
      appState.selectionMap = nextSelection;
    }
    appState.gridFilePath = data.grid_file_path || "";
    appState.queueCount = data.queue_count || 0;
    appState.queueData = Array.isArray(data.queue) ? data.queue : [];
    appState.runStatus = data.run_status || "idle";
    appState.activeRunOutRoot = String(data.run_out_root || appState.activeRunOutRoot || "").trim();
    appState.dockingConfig = normalizeDockingConfig(data.docking_config || appState.dockingConfig || DEFAULT_DOCKING_CONFIG);

    if (els.runCount) els.runCount.value = data.runs || 1;
    if (els.gridPad) els.gridPad.value = data.grid_pad || "";
    if (els.outRootPath) {
      els.outRootPath.value = data.out_root_path || "data/dock";
    }
    if (els.outRootName) {
      els.outRootName.value = data.out_root_name || "";
    }
    if (els.resultsRootPath) {
      els.resultsRootPath.value = RESULTS_DOCK_ROOT;
      appState.resultsRootPath = RESULTS_DOCK_ROOT;
    }
    applyAdvancedDockingConfigToModal(appState.dockingConfig);
    renderDockingConfigSummary();
    updateModeUI();
    updateGridPath();
    updateQueueCount();
    renderQueueTable(appState.queueData);
    updateQueueEditorUI();
    setRunStatus(appState.runStatus);
    updateRunMetrics({
      status: appState.runStatus,
      totalRuns: 0,
      completedRuns: 0,
      elapsedSeconds: 0,
      command: "",
    });
  } catch (e) {
    console.error("Failed to load state:", e);
  }
}

function updateModeUI() {
  if (!els.modeToggle) return;
  Array.from(els.modeToggle.querySelectorAll(".toggle")).forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === appState.mode);
  });
  document.querySelectorAll(".section[data-mode]").forEach((section) => {
    const mode = section.getAttribute("data-mode");
    if (mode === "results") {
      section.style.display = appState.mode === "Results" ? "block" : "none";
    } else if (mode === "report") {
      section.style.display = appState.mode === "Report" ? "block" : "none";
    } else {
      section.style.display = (appState.mode === "Results" || appState.mode === "Report") ? "none" : "block";
    }
  });

  const mainSplit = document.getElementById("mainSplit");
  const rightPanel = document.getElementById("rightPanel");
  if (mainSplit && rightPanel) {
    if (appState.mode === "Report") {
      mainSplit.style.gridTemplateColumns = "1fr";
      rightPanel.style.display = "none";
    } else {
      mainSplit.style.gridTemplateColumns = ""; // Clear inline style to fallback to CSS
      rightPanel.style.display = "flex";
    }
  }

  if (els.resultsViewToggle) {
    Array.from(els.resultsViewToggle.querySelectorAll(".toggle")).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === appState.resultsView);
    });
  }

  if (appState.mode !== "Report") {
    closeReportMetaModal();
  }

  const resultsOnly = appState.mode === "Results";
  if (resultsOnly) enforceResultsInteractionToggle();
  [els.showDockedLigand, els.showInteractions].forEach((el) => {
    if (!el) return;
    if (!resultsOnly) {
      el.checked = false;
    }
    el.disabled = !resultsOnly;
    const label = el.closest("label");
    if (label) {
      label.style.display = resultsOnly ? "" : "none";
      label.style.opacity = resultsOnly ? "1" : "0.55";
      label.style.pointerEvents = resultsOnly ? "auto" : "none";
    }
  });
}

function updateGridPath() {
  if (!els.gridPath) return;
  els.gridPath.textContent = appState.gridFilePath
    ? `Grid file: ${appState.gridFilePath}`
    : "No grid file selected.";
}

function updateQueueCount() {
  if (!els.queueCount) return;
  els.queueCount.textContent = `${appState.queueCount} entries`;
}

function openLigand3dModal() {
  if (!els.ligand3dModal) return;
  els.ligand3dModal.classList.add("active");
}

function closeLigand3dModal() {
  if (!els.ligand3dModal) return;
  els.ligand3dModal.classList.remove("active");
}

// =====================================================
// NGL Viewer Functions
// =====================================================

function setViewportMessage(message) {
  const viewport = document.getElementById("viewport");
  if (!viewport) return;
  viewport.innerHTML = `<div class="helper" style="padding:12px;">${message}</div>`;
}

function initViewer() {
  if (stage) return true;
  if (typeof window.NGL === "undefined" || !window.NGL.Stage) {
    setViewportMessage("NGL could not be loaded. Please refresh the page.");
    console.error("NGL is not available on window.");
    return false;
  }

  stage = new NGL.Stage("viewport", {
    backgroundColor: "white",
    quality: "high",
    impostor: true,
    workerDefault: true,
  });

  window.addEventListener("resize", () => stage.handleResize());

  if (!hoverHandlerBound) {
    stage.signals.hovered.add((pickingProxy) => {
      if (!els.interactionHover) return;
      if (pickingProxy && pickingProxy.atom) {
        const atom = pickingProxy.atom;
        const chain = atom.chainname || "_";
        const key = `${chain}:${atom.resno}:${atom.resname}`;
        const info = interactionResidueInfo[key];
        if (info && info.types && info.types.length) {
          const labels = info.types.map((t) => INTERACTION_KIND_LABELS[t] || t);
          els.interactionHover.textContent = `Hover: ${atom.resname}${atom.resno} ${chain} · ${labels.join(", ")}`;
          return;
        }
      }
      els.interactionHover.textContent = "Hover: -";
    });
    hoverHandlerBound = true;
  }

  if (!atomClickHandlerBound) {
    stage.signals.clicked.add((pickingProxy) => {
      if (!pickingProxy || !pickingProxy.atom) return;
      if (appState.mode === "Results" || appState.mode === "Report") return;
      const atom = pickingProxy.atom;
      const chainname = String(atom.chainname || "").trim();
      const atomname = String(atom.atomname || "").trim();
      const resname = String(atom.resname || "").trim();
      const resno = Number(atom.resno);
      const selection = Number.isInteger(atom.index) ? `@${atom.index}` : (
        chainname
          ? `${resno}:${chainname} and .${atomname}`
          : `${resno} and .${atomname}`
      );

      clearResidueSearchSelection({ refreshUI: false, reason: "atom" });
      selectedAtomData = {
        index: Number.isInteger(atom.index) ? atom.index : null,
        atomname,
        resname,
        resno,
        chainname,
        x: Number(atom.x) || 0,
        y: Number(atom.y) || 0,
        z: Number(atom.z) || 0,
        selection,
      };

      // Atom selection is exclusive with ligand focus highlight.
      if (representations.focusedLigand) {
        try { comp.removeRepresentation(representations.focusedLigand); } catch (e) { }
        representations.focusedLigand = null;
      }
      clearLigandTableSelection();
      selectedLigandData = null;
      externalGridSelectionData = null;
      updateSelectedAtomInfo();
      updateGridSelectionInfo();
      renderSelectedAtomHighlight();
    });
    atomClickHandlerBound = true;
  }

  if (!viewerPoseSyncBound && stage.viewerControls?.signals?.changed) {
    stage.viewerControls.signals.changed.add(() => {
      if (appState.mode !== "Results") return;
      if (applyingProgrammaticResultPose) return;
      if (!currentResultPdbKey) return;
      const pose = captureCurrentVectorPose();
      if (pose) {
        resultPoseByPdb.set(currentResultPdbKey, pose);
      }
    });
    viewerPoseSyncBound = true;
  }
  return true;
}

function updateRepresentations() {
  if (!comp) return;
  const viewerChain = String(els.viewerChain?.value || "all").trim() || "all";
  const chainFilter = viewerChain !== "all" ? ` and :${viewerChain}` : "";
  const proteinSele = `protein and not hydrogen${chainFilter}`;
  const nativeLigandSele = `not polymer and not water and not ion and not hydrogen${chainFilter}`;
  const showResultsOnlyExtras = appState.mode === "Results";
  const dockedLigandSele = showResultsOnlyExtras
    ? findDockedLigandSelection(comp, currentResultData)
    : "";

  // Clear old representations
  if (representations.cartoon) {
    try { comp.removeRepresentation(representations.cartoon); } catch (e) { }
  }
  if (representations.surface) {
    try { comp.removeRepresentation(representations.surface); } catch (e) { }
  }
  if (representations.nativeLigand) {
    try { comp.removeRepresentation(representations.nativeLigand); } catch (e) { }
  }
  if (representations.dockedLigand) {
    try { comp.removeRepresentation(representations.dockedLigand); } catch (e) { }
  }
  if (representations.selectedAtom) {
    try { comp.removeRepresentation(representations.selectedAtom); } catch (e) { }
    representations.selectedAtom = null;
  }
  if (representations.residueSearch) {
    try { comp.removeRepresentation(representations.residueSearch); } catch (e) { }
    representations.residueSearch = null;
  }
  if (representations.flexResidueSearch) {
    try { comp.removeRepresentation(representations.flexResidueSearch); } catch (e) { }
    representations.flexResidueSearch = null;
  }
  clearInteractionReps();

  const color = els.colorScheme?.value || "chainid";
  const colorScheme = color === "uniform" ? "#6366f1" : color;

  // Cartoon representation (always on)
  representations.cartoon = comp.addRepresentation("cartoon", {
    colorScheme: colorScheme,
    sele: proteinSele,
    quality: "high",
  });

  // Surface representation
  if (els.showSurface?.checked) {
    representations.surface = comp.addRepresentation("surface", {
      colorScheme: colorScheme,
      sele: proteinSele,
      opacity: 0.4,
      quality: "medium",
    });
  }

  if (els.showNativeLigand?.checked) {
    // Native ligands stay neutral gray; atom highlight overlays separately.
    representations.nativeLigand = comp.addRepresentation("ball+stick", {
      sele: nativeLigandSele,
      colorScheme: "uniform",
      colorValue: 0x9CA3AF,
      multipleBond: "symmetric",
      scale: 1.0,
    });
  } else {
    representations.nativeLigand = null;
  }

  // Docked ligand - Green color to distinguish from native.
  if (showResultsOnlyExtras && els.showDockedLigand?.checked && dockedLigandSele) {
    representations.dockedLigand = comp.addRepresentation("ball+stick", {
      sele: dockedLigandSele,
      colorScheme: "uniform",
      colorValue: 0x2ECC71,
      multipleBond: "symmetric",
      scale: 1.2,
    });
  }

  // Sticks representation
  if (els.showSticks?.checked) {
    if (!representations.sticks) {
      representations.sticks = comp.addRepresentation("licorice", {
        sele: proteinSele,
        scale: 0.5,
      });
    }
  } else {
    if (representations.sticks) {
      try { comp.removeRepresentation(representations.sticks); } catch (e) { }
      representations.sticks = null;
    }
  }

  if (showResultsOnlyExtras && els.showInteractions?.checked) {
    renderInteractionHighlights();
  } else if (els.interactionHover) {
    els.interactionHover.textContent = "Hover: -";
  }

  // Native ligand component (from original receptor PDB)
  if (nativeLigComp) {
    nativeLigComp.removeAllRepresentations();
    if (els.showNativeLigand?.checked) {
      nativeLigComp.addRepresentation("ball+stick", {
        sele: nativeLigandSele,
        colorScheme: "uniform",
        colorValue: 0x9CA3AF,
        multipleBond: "symmetric",
        scale: 1.0,
      });
    }
  }

  renderSelectedAtomHighlight();
  renderResidueSearchHighlight();
  renderFlexResidueHighlight();
}

function updateSelectedAtomInfo() {
  if (!els.selectedAtomInfo) return;
  if (!selectedAtomData) {
    els.selectedAtomInfo.textContent = "-";
    return;
  }
  const atomName = String(selectedAtomData.atomname || "?");
  const resName = String(selectedAtomData.resname || "?");
  const chain = String(selectedAtomData.chainname || "");
  const resNo = Number(selectedAtomData.resno);
  const location = chain ? `${resName}${resNo}:${chain}` : `${resName}${resNo}`;
  els.selectedAtomInfo.textContent = `${atomName} @ ${location}`;
}

function clearLigandTableSelection() {
  if (!els.ligandTable) return;
  els.ligandTable.querySelectorAll(".table-row.selected").forEach((row) => row.classList.remove("selected"));
}

function removeFocusedLigandRepresentation() {
  if (representations.focusedLigand && comp) {
    try {
      comp.removeRepresentation(representations.focusedLigand);
    } catch (e) { }
  }
  representations.focusedLigand = null;
}

function emitResidueSearchSelectionEvent(reason = "") {
  window.dispatchEvent(new CustomEvent("dockup:residue-search-selection", {
    detail: {
      active: !!residueSearchSelectionData,
      label: String(residueSearchSelectionData?.label || ""),
      reason: String(reason || ""),
    },
  }));
}

function updateGridSelectionInfo() {
  if (!els.gridSelectionInfo) return;
  const infoEl = els.gridSelectionInfo;
  const valueEl = infoEl.querySelector(".grid-selection-value");
  const setState = (state, valueText) => {
    infoEl.classList.remove("is-none", "is-ligand", "is-atom", "is-pocket", "is-residue");
    if (state === "atom") infoEl.classList.add("is-atom");
    else if (state === "ligand") infoEl.classList.add("is-ligand");
    else if (state === "pocket") infoEl.classList.add("is-pocket");
    else if (state === "residue") infoEl.classList.add("is-residue");
    else infoEl.classList.add("is-none");
    if (valueEl) {
      valueEl.textContent = valueText;
    } else {
      infoEl.textContent = `Selection: ${valueText}`;
    }
    infoEl.title = valueText === "None" ? "" : valueText;
  };

  if (selectedAtomData) {
    const atomName = String(selectedAtomData.atomname || "?");
    const resName = String(selectedAtomData.resname || "?");
    const chain = String(selectedAtomData.chainname || "");
    const resNo = Number(selectedAtomData.resno);
    const location = chain ? `${resName}${resNo}:${chain}` : `${resName}${resNo}`;
    setState("atom", `${atomName} @ ${location}`);
    return;
  }
  if (selectedLigandData) {
    const resName = String(selectedLigandData.resname || "?");
    const chain = String(selectedLigandData.chainname || "");
    const resNo = Number(selectedLigandData.resno);
    const location = chain ? `${resName}${resNo}:${chain}` : `${resName}${resNo}`;
    setState("ligand", location);
    return;
  }
  if (residueSearchSelectionData?.label) {
    setState("residue", String(residueSearchSelectionData.label));
    return;
  }
  if (externalGridSelectionData?.label) {
    setState("pocket", String(externalGridSelectionData.label));
    return;
  }
  setState("none", "None");
}

function normalizeGridSelectionMeta(meta = {}) {
  if (!meta || typeof meta !== "object") return null;
  const label = String(meta.label || "").trim();
  if (!label) return null;
  const pocketRank = Number.parseInt(meta.pocketRank, 10);
  return {
    source: String(meta.source || "binding-site"),
    label,
    pocketRank: Number.isFinite(pocketRank) && pocketRank > 0 ? pocketRank : null,
  };
}

function setExternalGridSelection(meta, pdbId = appState.selectedReceptor, { refreshUI = true } = {}) {
  const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
  const normalizedMeta = normalizeGridSelectionMeta(meta);
  if (normalizedPdbId) {
    if (normalizedMeta) gridSelectionMetaPerReceptor[normalizedPdbId] = normalizedMeta;
    else delete gridSelectionMetaPerReceptor[normalizedPdbId];
  }
  externalGridSelectionData = normalizedMeta;
  if (refreshUI) {
    updateGridSelectionInfo();
    scheduleUIStateSave();
  }
}

function restoreExternalGridSelection(pdbId = appState.selectedReceptor) {
  const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
  externalGridSelectionData = normalizedPdbId ? (gridSelectionMetaPerReceptor[normalizedPdbId] || null) : null;
  updateGridSelectionInfo();
}

function clearExternalGridSelection(pdbId = appState.selectedReceptor, { refreshUI = true } = {}) {
  setExternalGridSelection(null, pdbId, { refreshUI });
}

function buildResidueSearchCatalog() {
  const catalog = [];
  if (!comp?.structure) return catalog;
  const seen = new Set();
  comp.structure.eachResidue((res) => {
    const resname = normalizeResname(res.resname);
    if (!AMINO_ACID_RESN.has(resname)) return;
    const chain = String(res.chainname || "").trim() || "_";
    const resno = String(res.resno ?? "").trim();
    if (!resno) return;
    const key = `${chain}:${resno}:${resname}`;
    if (seen.has(key)) return;
    seen.add(key);

    let atomCount = 0;
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;

    res.eachAtom((atom) => {
      const x = Number(atom.x);
      const y = Number(atom.y);
      const z = Number(atom.z);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
      atomCount += 1;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (z < minZ) minZ = z;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      if (z > maxZ) maxZ = z;
    });

    if (!atomCount) return;
    catalog.push({
      chain,
      resno,
      resname,
      atomCount,
      selection: residueSelection(resno, chain !== "_" ? chain : ""),
      bbox: { minX, minY, minZ, maxX, maxY, maxZ },
    });
  });
  return catalog;
}

function bboxIntersectsGrid(bbox, grid) {
  if (!bbox || !grid) return true;
  const gx1 = Number(grid.cx) - Number(grid.sx) / 2;
  const gx2 = Number(grid.cx) + Number(grid.sx) / 2;
  const gy1 = Number(grid.cy) - Number(grid.sy) / 2;
  const gy2 = Number(grid.cy) + Number(grid.sy) / 2;
  const gz1 = Number(grid.cz) - Number(grid.sz) / 2;
  const gz2 = Number(grid.cz) + Number(grid.sz) / 2;
  return !(
    Number(bbox.maxX) < gx1 || Number(bbox.minX) > gx2
    || Number(bbox.maxY) < gy1 || Number(bbox.minY) > gy2
    || Number(bbox.maxZ) < gz1 || Number(bbox.minZ) > gz2
  );
}

function buildFlexibleResidueCatalog() {
  const catalog = buildResidueSearchCatalog();
  const activeGrid = gridboxData || (
    appState.selectedReceptor && gridDataPerReceptor[appState.selectedReceptor]
      ? gridDataPerReceptor[appState.selectedReceptor]
      : null
  );
  if (!activeGrid) return catalog;
  return catalog.filter((row) => bboxIntersectsGrid(row.bbox, activeGrid));
}

function clearResidueSearchSelection({ refreshUI = true, reason = "clear" } = {}) {
  residueSearchSelectionData = null;
  if (representations.residueSearch) {
    try { comp.removeRepresentation(representations.residueSearch); } catch (e) { }
    representations.residueSearch = null;
  }
  emitResidueSearchSelectionEvent(reason);
  if (refreshUI) {
    updateGridSelectionInfo();
    scheduleUIStateSave();
  }
}

function emitFlexResidueSelectionEvent(reason = "") {
  window.dispatchEvent(new CustomEvent("dockup:flex-selection-selection", {
    detail: {
      active: !!getFlexSelectionData(appState.selectedReceptor),
      label: String(getFlexSelectionData(appState.selectedReceptor)?.label || ""),
      reason: String(reason || ""),
    },
  }));
}

function clearFlexResidueSearchSelection({ refreshUI = true, reason = "clear" } = {}) {
  clearFlexResiduesForReceptor(appState.selectedReceptor);
  renderFlexResidueHighlight();
  emitFlexResidueSelectionEvent(reason);
  if (refreshUI) {
    syncDockingModeUI();
    renderDockingConfigSummary();
    scheduleUIStateSave();
  }
}

function applyFlexResidueSearchSelection(selection, { refreshUI = true, reason = "search" } = {}) {
  const rows = normalizeFlexResidueList(selection?.residues || []);
  setFlexResiduesForReceptor(appState.selectedReceptor, rows);
  renderFlexResidueHighlight();
  emitFlexResidueSelectionEvent(reason);
  if (refreshUI) {
    syncDockingModeUI();
    renderDockingConfigSummary();
    scheduleUIStateSave();
  }
}

function applyResidueSearchSelection(selection, { refreshUI = true, reason = "search" } = {}) {
  const label = String(selection?.label || "").trim();
  const bbox = selection?.bbox && typeof selection.bbox === "object" ? selection.bbox : null;
  const selectionText = String(selection?.selection || "").trim();
  if (!label || !bbox || !selectionText) {
    clearResidueSearchSelection({ refreshUI, reason: "invalid" });
    return;
  }

  residueSearchSelectionData = {
    label,
    query: String(selection?.query || "").trim(),
    selection: selectionText,
    residues: Array.isArray(selection?.residues) ? selection.residues.map((row) => ({ ...row })) : [],
    bbox: { ...bbox },
  };
  clearSelectedAtomSelection();
  removeFocusedLigandRepresentation();
  selectedLigandData = null;
  clearLigandTableSelection();
  clearExternalGridSelection(appState.selectedReceptor, { refreshUI: false });
  renderResidueSearchHighlight();
  emitResidueSearchSelectionEvent(reason);
  if (refreshUI) {
    updateGridSelectionInfo();
    scheduleUIStateSave();
  }
}

function clearSelectedAtomSelection() {
  selectedAtomData = null;
  if (representations.selectedAtom) {
    try { comp.removeRepresentation(representations.selectedAtom); } catch (e) { }
    representations.selectedAtom = null;
  }
  updateSelectedAtomInfo();
  updateGridSelectionInfo();
}

function resetGridboxState({ clearAllStored = false } = {}) {
  if (gridComp) {
    try {
      stage.removeComponent(gridComp);
    } catch (e) { }
    gridComp = null;
  }

  gridboxData = null;
  if (clearAllStored) {
    gridDataPerReceptor = {};
    gridSelectionMetaPerReceptor = {};
  } else if (appState.selectedReceptor) {
    delete gridDataPerReceptor[appState.selectedReceptor];
    delete gridSelectionMetaPerReceptor[appState.selectedReceptor];
  }
  externalGridSelectionData = null;

  if (els.gridControlsPanel) {
    els.gridControlsPanel.classList.add("grid-panel-hidden");
    els.gridControlsPanel.style.display = "none";
  }
  if (els.showGrid) {
    els.showGrid.checked = false;
  }
  if (els.infoCenter) els.infoCenter.textContent = "-";
  if (els.infoSize) els.infoSize.textContent = "-";

  clearResidueSearchSelection({ refreshUI: false, reason: "reset-gridbox" });
  clearSelectedAtomSelection();
  removeFocusedLigandRepresentation();

  selectedLigandData = null;
  clearLigandTableSelection();
  updateGridSelectionInfo();
  dispatchFlexSelectionContext("reset-gridbox");
  scheduleUIStateSave();
}

function renderSelectedAtomHighlight() {
  if (representations.selectedAtom) {
    try { comp.removeRepresentation(representations.selectedAtom); } catch (e) { }
    representations.selectedAtom = null;
  }
  if (!comp || !selectedAtomData || !selectedAtomData.selection) return;
  try {
    representations.selectedAtom = comp.addRepresentation("spacefill", {
      sele: selectedAtomData.selection,
      color: 0xFFD700,
      radiusScale: 0.33,
      opacity: 1,
    });
  } catch (err) {
    representations.selectedAtom = null;
  }
}

function renderResidueSearchHighlight() {
  if (representations.residueSearch) {
    try { comp.removeRepresentation(representations.residueSearch); } catch (e) { }
    representations.residueSearch = null;
  }
  if (!comp || !residueSearchSelectionData?.selection) return;
  try {
    representations.residueSearch = comp.addRepresentation("licorice", {
      sele: residueSearchSelectionData.selection,
      colorScheme: "uniform",
      colorValue: 0xFACC15,
      multipleBond: "symmetric",
      radiusScale: 1.15,
      opacity: 1,
    });
  } catch (err) {
    representations.residueSearch = null;
  }
}

function renderFlexResidueHighlight() {
  if (representations.flexResidueSearch) {
    try { comp.removeRepresentation(representations.flexResidueSearch); } catch (e) { }
    representations.flexResidueSearch = null;
  }
  const selection = getActiveFlexSelectionData();
  if (!comp || !selection?.selection || !els.showFlexResidues?.checked) return;
  try {
    representations.flexResidueSearch = comp.addRepresentation("licorice", {
      sele: selection.selection,
      colorScheme: "uniform",
      colorValue: 0xF97316,
      multipleBond: "symmetric",
      radiusScale: 0.95,
      opacity: 0.95,
    });
  } catch (err) {
    representations.flexResidueSearch = null;
  }
}

function normalizeResname(value) {
  return String(value || "").trim().toUpperCase();
}

function residueSelection(resid, chain) {
  const residueId = String(resid || "").trim();
  const chainId = String(chain || "").trim();
  if (!residueId && !chainId) return "";
  return chainId ? `${residueId}:${chainId}` : residueId;
}

function isLigandResidue(resname) {
  const upper = normalizeResname(resname);
  if (!upper) return false;
  if (EXCLUDED_RESN.has(upper)) return false;
  if (AMINO_ACID_RESN.has(upper)) return false;
  if (NUCLEOTIDE_RESN.has(upper)) return false;
  return true;
}

function findDockedLigandResidue(component, result) {
  if (!component || !component.structure) return null;

  const wantResname = normalizeResname(result?.ligand_resname);
  const wantChain = String(result?.ligand_chain || "").trim();
  const wantResid = String(result?.ligand_resid || "").trim();

  const candidates = [];
  component.structure.eachResidue((res) => {
    const resname = normalizeResname(res.resname);
    if (!isLigandResidue(resname)) return;
    const chain = String(res.chainname || "").trim();
    const resid = String(res.resno ?? "").trim();
    const sele = residueSelection(resid, chain);
    if (!sele) return;
    let atomCount = 0;
    res.eachAtom(() => { atomCount += 1; });
    candidates.push({ resname, chain, resid, sele, atomCount });
  });
  if (!candidates.length) return null;

  const exact = candidates.find((c) =>
    (!wantResname || c.resname === wantResname) &&
    (!wantResid || c.resid === wantResid) &&
    (!wantChain || c.chain === wantChain)
  );
  if (exact) return exact;

  const unl = candidates
    .filter((c) => c.resname === "UNL")
    .sort((a, b) => b.atomCount - a.atomCount);
  if (unl.length) return unl[0];

  candidates.sort((a, b) => b.atomCount - a.atomCount);
  return candidates[0];
}

function findDockedLigandSelection(component, result) {
  const residue = findDockedLigandResidue(component, result);
  return residue?.sele || "";
}

function isProteinAtom(atom) {
  const resname = normalizeResname(atom?.resname || "");
  return AMINO_ACID_RESN.has(resname);
}

function computeAtomCenter(component, atomFilter) {
  if (!component || !component.structure || typeof atomFilter !== "function") return null;
  let count = 0;
  let sx = 0;
  let sy = 0;
  let sz = 0;
  component.structure.eachAtom((atom) => {
    if (!atomFilter(atom)) return;
    sx += Number(atom.x) || 0;
    sy += Number(atom.y) || 0;
    sz += Number(atom.z) || 0;
    count += 1;
  });
  if (!count) return null;
  return new NGL.Vector3(sx / count, sy / count, sz / count);
}

function computeLigandCenter(component, ligandResidue) {
  if (!ligandResidue) return null;
  const wantResname = normalizeResname(ligandResidue.resname);
  const wantResid = String(ligandResidue.resid || "").trim();
  const wantChain = String(ligandResidue.chain || "").trim();
  return computeAtomCenter(component, (atom) => {
    const atomRes = normalizeResname(atom.resname);
    const atomResid = String(atom.resno ?? "").trim();
    const atomChain = String(atom.chainname || "").trim();
    return atomRes === wantResname && atomResid === wantResid && atomChain === wantChain;
  });
}

function computeProteinCenter(component) {
  const byCA = computeAtomCenter(component, (atom) => {
    if (!isProteinAtom(atom)) return false;
    const atomName = String(atom.atomname || "").trim().toUpperCase();
    return atomName === "CA";
  });
  if (byCA) return byCA;
  return computeAtomCenter(component, (atom) => isProteinAtom(atom));
}

function applyVectorPose(pose, ligandCenter) {
  if (!stage || !stage.viewerControls || !pose || !ligandCenter) return false;
  const rot = Array.isArray(pose.rotation) ? pose.rotation : [];
  if (rot.length !== 4) return false;
  const q = new NGL.Quaternion(rot[0], rot[1], rot[2], rot[3]);
  const dist = Math.max(8, Number(pose.distance) || 24);
  applyingProgrammaticResultPose = true;
  try {
    stage.viewerControls.rotate(q);
    stage.viewerControls.distance(dist);
    stage.viewerControls.center(ligandCenter);
    return true;
  } catch (err) {
    return false;
  } finally {
    applyingProgrammaticResultPose = false;
  }
}

function buildVectorPoseFromCenters(ligandCenter, receptorCenter) {
  if (!ligandCenter || !receptorCenter) return null;
  const dir = new NGL.Vector3().copy(receptorCenter).sub(ligandCenter);
  const len = dir.length();
  if (!Number.isFinite(len) || len < 1e-6) return null;
  dir.normalize();
  // Camera forward axis in NGL trackball setup; rotate scene so ligand->receptor
  // vector becomes the viewing direction.
  const forward = new NGL.Vector3(0, 0, 1);
  const q = new NGL.Quaternion().setFromUnitVectors(dir, forward);
  // Slightly more zoom-out than previous setting.
  const distance = Math.max(16, Math.min(80, len * 1.68));
  return {
    rotation: [q.x, q.y, q.z, q.w],
    distance,
  };
}

function captureCurrentVectorPose() {
  if (!stage) return null;
  const q = stage?.viewer?.rotationGroup?.quaternion;
  if (!q) return null;
  const distanceRaw = stage?.viewerControls?.getCameraDistance
    ? stage.viewerControls.getCameraDistance()
    : stage?.viewer?.cameraDistance;
  const distance = Number(distanceRaw);
  if (!Number.isFinite(distance) || distance <= 0) return null;
  return {
    rotation: [Number(q.x) || 0, Number(q.y) || 0, Number(q.z) || 0, Number(q.w) || 1],
    distance,
  };
}

function focusOnLigand(lig) {
  if (!comp) return;

  // Ligand table selection overrides any atom-picked selection.
  clearSelectedAtomSelection();
  clearResidueSearchSelection({ refreshUI: false, reason: "ligand" });
  clearExternalGridSelection(appState.selectedReceptor, { refreshUI: false });

  // Remove old focused ligand representation
  if (representations.focusedLigand) {
    try { comp.removeRepresentation(representations.focusedLigand); } catch (e) { }
    representations.focusedLigand = null;
  }

  // Build selection string
  let sele;
  if (lig.chainname) {
    sele = `${lig.resno}:${lig.chainname}`;
  } else {
    sele = `${lig.resno}`;
  }

  // Highlight selected ligand in yellow (on top of gray native representation)
  try {
    representations.focusedLigand = comp.addRepresentation("ball+stick", {
      sele: sele,
      colorScheme: "uniform",
      colorValue: 0xFFD700,
      multipleBond: "symmetric",
      scale: 1.0,
    });
  } catch (e) {
    representations.focusedLigand = null;
  }

  comp.autoView(sele, 500);
  updateGridSelectionInfo();
}

// =====================================================
// Ligand Table - Using structure parsing like script.js
// =====================================================

function populateLigandTableFromStructure(structure, chainFilter = appState.selectedChain) {
  if (!els.ligandTable) return;

  els.ligandTable.className = "table simple";
  els.ligandTable.innerHTML = "";

  const ligands = [];
  const seen = new Set();
  const normalizedChainFilter = normalizeChainValue(chainFilter);

  // Exclusion lists
  structure.eachResidue((res) => {
    const resname = normalizeResname(res.resname);
    const residueChain = String(res.chainname || "").trim() || "_";
    if (normalizedChainFilter !== "all" && residueChain !== normalizedChainFilter) return;
    const key = `${resname}_${res.resno}_${residueChain}`;

    if (seen.has(key)) return;
    if (!isLigandResidue(resname)) return;

    seen.add(key);
    ligands.push({
      resname: resname,
      resno: res.resno,
      chainname: residueChain,
    });
  });

  // Sort by chain and resno
  ligands.sort((a, b) => {
    if (a.chainname < b.chainname) return -1;
    if (a.chainname > b.chainname) return 1;
    return a.resno - b.resno;
  });

  if (ligands.length === 0) {
    els.ligandTable.innerHTML = '<div class="helper">No ligands detected.</div>';
    return;
  }

  // Header
  const header = document.createElement("div");
  header.className = "table-row header";
  header.style.gridTemplateColumns = "1fr 1fr 1fr";
  header.innerHTML = "<div>Name</div><div>Res #</div><div>Chain</div>";
  els.ligandTable.appendChild(header);

  // Rows
  ligands.forEach((lig) => {
    const row = document.createElement("div");
    row.className = "table-row";
    row.style.gridTemplateColumns = "1fr 1fr 1fr";

    if (selectedLigandData &&
      selectedLigandData.resname === lig.resname &&
      selectedLigandData.resno === lig.resno) {
      row.classList.add("selected");
    }

    row.innerHTML = `<div>${lig.resname}</div><div>${lig.resno}</div><div>${lig.chainname || "-"}</div>`;

    row.addEventListener("click", () => {
      // Update selection
      selectedLigandData = lig;
      appState.selectedLigand = lig.resname;
      appState.selectedChain = lig.chainname || "all";

      // Update table UI
      els.ligandTable.querySelectorAll(".table-row:not(.header)").forEach(r => r.classList.remove("selected"));
      row.classList.add("selected");

      // Focus on ligand in viewer
      focusOnLigand(lig);

      // In Docking mode, ligand table selection is only for gridbox/native focus.
      // Keep the queue ligand chosen from Docking Configuration untouched.
      if (appState.mode === "Redocking") {
        if (!appState.selectionMap) appState.selectionMap = {};
        if (!appState.selectionMap[appState.selectedReceptor]) {
          appState.selectionMap[appState.selectedReceptor] = {};
        }
        appState.selectionMap[appState.selectedReceptor].ligand_resname = lig.resname;
        appState.selectionMap[appState.selectedReceptor].chain = lig.chainname || "all";

        // Also notify server
        fetchJSON("/api/ligands/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pdb_id: appState.selectedReceptor,
            ligand: lig.resname,
            chain: lig.chainname || "all",
          }),
        }).catch(e => console.error(e));
      }
    });

    els.ligandTable.appendChild(row);
  });
}

// =====================================================
// Receptor & Viewer Refresh
// =====================================================

async function refreshViewer() {
  if (!appState.selectedReceptor) {
    setViewportMessage("Load a receptor to start the viewer.");
    dispatchGridSelectionContext("no-receptor");
    dispatchFlexSelectionContext("no-receptor");
    return;
  }
  if (!initViewer()) return;
  const requestId = ++refreshViewerRequestId;

  try {
    const data = await fetchJSON(`/api/receptors/${appState.selectedReceptor}`);
    if (requestId !== refreshViewerRequestId) return;
    const effectiveChain = normalizeChainValue(data.selected_chain || getSelectedChainForReceptor(appState.selectedReceptor));
    appState.selectedChain = effectiveChain;
    updateViewerChainOptions(data.chains || [], effectiveChain);

    // Load structure
    let loadPromise;
    if (data.pdb_text) {
      const blob = new Blob([data.pdb_text], { type: "text/plain" });
      loadPromise = stage.loadFile(blob, { ext: "pdb" });
    } else {
      loadPromise = stage.loadFile(`rcsb://${data.pdb_id}`);
    }

    const component = await loadPromise;
    if (requestId !== refreshViewerRequestId) {
      try { stage.removeComponent(component); } catch (e) { }
      return;
    }
    const previousComp = comp;
    const previousNativeLigComp = nativeLigComp;
    comp = component;

    // Reset viewer state only after the new structure is ready.
    representations = { cartoon: null, surface: null, nativeLigand: null, dockedLigand: null, focusedLigand: null, sticks: null, selectedAtom: null, residueSearch: null, flexResidueSearch: null };
    selectedLigandData = null;
    selectedAtomData = null;
    externalGridSelectionData = appState.selectedReceptor
      ? (gridSelectionMetaPerReceptor[appState.selectedReceptor] || null)
      : null;
    updateSelectedAtomInfo();
    updateGridSelectionInfo();
    interactionResiduesByType = {};
    interactionResidueInfo = {};
    renderInteractionLegend();
    if (previousComp) {
      try { stage.removeComponent(previousComp); } catch (e) { }
    }
    if (previousNativeLigComp) {
      try { stage.removeComponent(previousNativeLigComp); } catch (e) { }
      nativeLigComp = null;
    }

    // Add representations
    updateRepresentations();

    // Populate ligand table from actual structure
    if (comp.structure) {
      populateLigandTableFromStructure(comp.structure, effectiveChain);
    }

    // Auto view
    if (effectiveChain !== "all") {
      try {
        comp.autoView(`:${effectiveChain}`);
      } catch (_err) {
        comp.autoView();
      }
    } else {
      comp.autoView();
    }

    // Apply gridbox if exists
    applyGridbox();
    dispatchGridSelectionContext("viewer-refresh");
    dispatchFlexSelectionContext("viewer-refresh");

  } catch (e) {
    console.error("Error loading viewer:", e);
    dispatchGridSelectionContext("viewer-error");
    dispatchFlexSelectionContext("viewer-error");
  }
}

function updateViewerChainOptions(chains, preferredChain = "") {
  if (!els.viewerChain) return;
  const currentValue = normalizeChainValue(preferredChain || appState.selectedChain || els.viewerChain.value || "all");
  const options = ["all", ...chains.filter((c) => c !== "all")];
  els.viewerChain.innerHTML = "";
  options.forEach((chain) => {
    const opt = document.createElement("option");
    opt.value = chain;
    opt.textContent = chain;
    if (chain === currentValue) {
      opt.selected = true;
    }
    els.viewerChain.appendChild(opt);
  });
  if (!options.includes(currentValue)) {
    els.viewerChain.value = "all";
  }
}

function clearInteractionReps() {
  Object.values(interactionReps).forEach((rep) => {
    try {
      comp.removeRepresentation(rep);
    } catch (e) { }
  });
  interactionReps = {};
}

function buildInteractionMaps(residues) {
  interactionResiduesByType = {};
  interactionResidueInfo = {};
  if (!Array.isArray(residues)) return;

  residues.forEach((row) => {
    const chain = row.receptor_chain || "_";
    const resno = String(row.receptor_resid || "");
    const resname = row.receptor_resname || "";
    const types = Array.isArray(row.interaction_types) ? row.interaction_types : [];
    const key = `${chain}:${resno}:${resname}`;
    interactionResidueInfo[key] = { types: types };

    const ordered = INTERACTION_KIND_ORDER.filter((k) => types.includes(k));
    const dominant = ordered.length ? ordered[0] : (types[0] || "");
    if (!dominant) return;
    if (!interactionResiduesByType[dominant]) interactionResiduesByType[dominant] = [];
    interactionResiduesByType[dominant].push({ chain, resno, resname });
  });
}

function buildSelectionFromResidues(residues) {
  if (!residues || residues.length === 0) return "";
  const parts = residues.map((r) => {
    const chain = r.chain && r.chain !== "_" ? r.chain : "";
    return chain ? `${r.resno}:${chain}` : `${r.resno}`;
  });
  return parts.join(" or ");
}

function renderInteractionHighlights() {
  if (!comp) return;
  clearInteractionReps();
  const types = Object.keys(interactionResiduesByType);
  types.forEach((kind) => {
    const residues = interactionResiduesByType[kind] || [];
    const sele = buildSelectionFromResidues(residues);
    if (!sele) return;
    interactionReps[kind] = comp.addRepresentation("licorice", {
      sele: sele,
      color: INTERACTION_COLORS[kind] || "#94a3b8",
      scale: 0.7,
      opacity: 0.95,
    });
  });
}

function renderInteractionLegend() {
  if (!els.interactionLegend) return;
  const types = Object.keys(interactionResiduesByType);
  const flexSelection = getActiveFlexSelectionData();
  if (!types.length && !flexSelection) {
    els.interactionLegend.innerHTML = "";
    els.interactionLegend.style.display = "none";
    return;
  }
  els.interactionLegend.style.display = "flex";
  const ordered = INTERACTION_KIND_ORDER.filter((t) => types.includes(t));
  const extras = types.filter((t) => !ordered.includes(t));
  const all = ordered.concat(extras);
  els.interactionLegend.innerHTML = "";
  all.forEach((kind) => {
    const chip = document.createElement("div");
    chip.className = "legend-chip";
    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = INTERACTION_COLORS[kind] || "#94a3b8";
    const label = document.createElement("span");
    label.textContent = INTERACTION_KIND_LABELS[kind] || kind;
    chip.appendChild(swatch);
    chip.appendChild(label);
    els.interactionLegend.appendChild(chip);
  });
  if (flexSelection) {
    const chip = document.createElement("div");
    chip.className = "legend-chip legend-chip-flex";
    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = "#f59e0b";
    const label = document.createElement("span");
    const flexCount = Array.isArray(flexSelection.residues) ? flexSelection.residues.length : 0;
    label.textContent = flexCount > 0 ? `Flex residues (${flexCount})` : "Flex residues";
    chip.title = String(flexSelection.spec || flexSelection.label || "Flex residues");
    chip.appendChild(swatch);
    chip.appendChild(label);
    els.interactionLegend.appendChild(chip);
  }
}

// =====================================================
// Results Analysis
// =====================================================

function renderResultsDockFolderOptions(selectedPath = "") {
  if (!els.resultsDockFolderSelect) return;
  const select = els.resultsDockFolderSelect;
  select.innerHTML = "";
  const rows = Array.isArray(resultsDockFolders) ? [...resultsDockFolders] : [];
  if (!rows.length) {
    rows.push({ name: "All dock folders", path: RESULTS_DOCK_ROOT });
  }

  const normalized = String(selectedPath || "").trim();
  const hasSelected = rows.some((row) => String(row.path || "").trim() === normalized);
  if (normalized && !hasSelected) {
    rows.push({ name: normalized, path: normalized });
  }

  rows.forEach((row) => {
    const opt = document.createElement("option");
    opt.value = String(row.path || RESULTS_DOCK_ROOT);
    opt.textContent = String(row.name || row.path || RESULTS_DOCK_ROOT);
    select.appendChild(opt);
  });

  const fallback = normalized || RESULTS_DOCK_ROOT;
  select.value = fallback;
  if (!select.value && rows.length) {
    select.value = String(rows[0].path || RESULTS_DOCK_ROOT);
  }

  if (els.resultsRootPath) {
    els.resultsRootPath.value = select.value || RESULTS_DOCK_ROOT;
  }
  appState.resultsRootPath = select.value || RESULTS_DOCK_ROOT;
}

async function refreshResultsDockFolders(selectedPath = "") {
  try {
    const data = await fetchJSON("/api/results/dock-folders");
    resultsDockFolders = Array.isArray(data.folders) ? data.folders : [];
  } catch (err) {
    console.error("Failed to load dock folders:", err);
    resultsDockFolders = [{ name: "All dock folders", path: RESULTS_DOCK_ROOT }];
  }
  const desired = String(selectedPath || els.resultsRootPath?.value || appState.resultsRootPath || RESULTS_DOCK_ROOT).trim();
  renderResultsDockFolderOptions(desired || RESULTS_DOCK_ROOT);
}

async function scanResults() {
  const prevRoot = normalizePathForCompare(appState.resultsRootPath);
  const rootPath = els.resultsRootPath?.value || RESULTS_DOCK_ROOT;
  const data = await fetchJSON("/api/results/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root_path: rootPath }),
  });
  const resolvedRoot = data.root_path || rootPath;
  appState.resultsRootPath = resolvedRoot;
  if (els.resultsRootPath) {
    els.resultsRootPath.value = resolvedRoot;
  }
  renderResultsDockFolderOptions(resolvedRoot);
  await refreshResultsDockFolders(resolvedRoot);
  appState.resultsData = data || { runs: [], averages: [] };
  appState.selectedResultDir = "";
  currentResultPdbKey = "";
  currentResultData = null;
  const nextRoot = normalizePathForCompare(resolvedRoot);
  if (prevRoot !== nextRoot) {
    resultPoseByPdb = new Map();
  }
  renderResultsTable();
  renderResidueTable([]);
  renderResultDetail(null);
  interactionResiduesByType = {};
  interactionResidueInfo = {};
  renderInteractionLegend();
  updateRepresentations();
}

function renderResultsTable() {
  if (!els.resultsTable) return;
  const isAverage = appState.resultsView === "average";
  const rows = isAverage ? (appState.resultsData.averages || []) : (appState.resultsData.runs || []);
  if (els.resultsCount) {
    els.resultsCount.textContent = `${rows.length} ${isAverage ? "targets" : "results"}`;
  }
  els.resultsTable.innerHTML = "";
  if (!rows.length) {
    els.resultsTable.innerHTML = '<div class="helper">No results found.</div>';
    return;
  }

  const header = document.createElement("div");
  header.className = "table-row header";
  if (isAverage) {
    header.style.gridTemplateColumns = "1fr 0.8fr 0.5fr 0.6fr 0.6fr";
    header.innerHTML = "<div>PDB</div><div>Ligand</div><div>Runs</div><div>Avg Affinity</div><div>Avg RMSD</div>";
  } else {
    header.style.gridTemplateColumns = "0.8fr 0.5fr 0.8fr 0.6fr 0.6fr 0.6fr";
    header.innerHTML = "<div>PDB</div><div>Run</div><div>Ligand</div><div>Affinity</div><div>RMSD</div><div>Interactions</div>";
  }
  els.resultsTable.appendChild(header);

  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "table-row";
    if (isAverage) {
      item.style.gridTemplateColumns = "1fr 0.8fr 0.5fr 0.6fr 0.6fr";
      item.innerHTML = `
        <div>${row.pdb_id || "-"}</div>
        <div>${row.ligand_display_name || row.ligand_resname || "-"}</div>
        <div>${row.run_count || 0}</div>
        <div>${formatNumber(row.avg_affinity)}</div>
        <div>${formatNumber(row.avg_rmsd)}</div>
      `;
      item.addEventListener("click", () => {
        els.resultsTable.querySelectorAll(".table-row:not(.header)").forEach(r => r.classList.remove("selected"));
        item.classList.add("selected");
        setCurrentResultFlexSelection(null);
        renderResultDetail({ ...row, view: "average" });
        renderResidueTable([]);
        interactionResiduesByType = {};
        interactionResidueInfo = {};
        renderInteractionLegend();
        updateRepresentations();
      });
    } else {
      item.style.gridTemplateColumns = "0.8fr 0.5fr 0.8fr 0.6fr 0.6fr 0.6fr";
      item.innerHTML = `
        <div>${row.pdb_id || "-"}</div>
        <div>${row.run_id || "-"}</div>
        <div>${row.ligand_display_name || row.ligand_resname || "-"}</div>
        <div>${formatNumber(row.best_affinity)}</div>
        <div>${formatNumber(row.rmsd)}</div>
        <div>${row.interaction_count ?? row.residue_count ?? "-"}</div>
      `;
      if (row.result_dir && row.result_dir === appState.selectedResultDir) {
        item.classList.add("selected");
      }
      item.addEventListener("click", async () => {
        els.resultsTable.querySelectorAll(".table-row:not(.header)").forEach(r => r.classList.remove("selected"));
        item.classList.add("selected");
        appState.selectedResultDir = row.result_dir;
        await loadResultDetail(row.result_dir);
      });
    }
    els.resultsTable.appendChild(item);
  });
}

// Render interaction table - each interaction as separate row
function renderInteractionTable(interactions) {
  if (!els.residueTable) return;
  els.residueTable.innerHTML = "";
  if (!interactions || interactions.length === 0) {
    els.residueTable.innerHTML = '<div class="helper">No interaction data.</div>';
    return;
  }

  const header = document.createElement("div");
  header.className = "table-row header";
  header.style.gridTemplateColumns = "1fr 0.8fr 0.6fr 0.8fr 0.5fr";
  header.innerHTML = "<div>Residue</div><div>Type</div><div>Dist</div><div>Atoms</div><div>Chain</div>";
  els.residueTable.appendChild(header);

  interactions.forEach((inter, idx) => {
    const row = document.createElement("div");
    row.className = "table-row clickable-row";
    row.style.gridTemplateColumns = "1fr 0.8fr 0.6fr 0.8fr 0.5fr";
    row.style.cursor = "pointer";

    const kind = inter.kind || "";
    const typeColor = INTERACTION_COLORS[kind] || "#94a3b8";
    const typeLabel = INTERACTION_KIND_LABELS[kind] || kind;

    // Atoms display
    const protAtom = inter.receptor_atom || "-";
    const ligAtom = inter.ligand_atom || "-";
    const atomsStr = `${protAtom}↔${ligAtom}`;

    row.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="width:10px;height:10px;border-radius:50%;background:${typeColor};flex-shrink:0;border:1px solid rgba(0,0,0,0.2);"></span>
        ${inter.receptor_resname || "-"} ${inter.receptor_resid || ""}
      </div>
      <div style="font-size:11px;color:${typeColor};font-weight:500;">${typeLabel}</div>
      <div>${formatNumber(inter.distance, 2)}</div>
      <div style="font-size:10px;font-family:monospace;">${atomsStr}</div>
      <div>${inter.receptor_chain || "-"}</div>
    `;

    // Click handler - highlight the bond
    row.addEventListener("click", () => {
      els.residueTable.querySelectorAll(".table-row:not(.header)").forEach(r => r.classList.remove("selected"));
      row.classList.add("selected");
      highlightInteractionBond(inter, idx);
    });

    els.residueTable.appendChild(row);
  });
}

// Keep legacy function for compatibility
function renderResidueTable(residues) {
  // Convert to interaction format if needed
  if (!residues || residues.length === 0) {
    if (els.residueTable) {
      els.residueTable.innerHTML = '<div class="helper">No interaction data.</div>';
    }
    return;
  }
  // If called with residues, just show basic info
  const interactions = appState.currentInteractions || [];
  if (interactions.length > 0) {
    renderInteractionTable(interactions);
  }
}

// Bond highlighting variables
let bondShapes = [];
let selectedBondIndex = -1;

// Highlight a specific interaction bond
function highlightInteractionBond(inter, selectedIdx) {
  if (!stage || !comp) return;

  // Clear previous bond shapes
  bondShapes.forEach(shapeComp => {
    try { stage.removeComponent(shapeComp); } catch (e) { }
  });
  bondShapes = [];
  selectedBondIndex = selectedIdx;

  // Get all interactions
  const allInteractions = appState.currentInteractions || [];

  // Create shape for all bonds
  allInteractions.forEach((int, idx) => {
    if (!int.lig_coords || !int.prot_coords) return;

    const shape = new NGL.Shape("bond_" + idx);
    const isSelected = idx === selectedIdx;

    // Color: selected = gold, others = gray
    const color = isSelected ? [1.0, 0.84, 0.0] : [0.6, 0.6, 0.6];
    const radius = isSelected ? 0.15 : 0.08;

    shape.addCylinder(int.lig_coords, int.prot_coords, color, radius);

    // Add small spheres at endpoints for selected bond
    if (isSelected) {
      shape.addSphere(int.lig_coords, color, 0.25);
      shape.addSphere(int.prot_coords, color, 0.25);
    }

    const shapeComp = stage.addComponentFromObject(shape);
    shapeComp.addRepresentation("buffer");
    bondShapes.push(shapeComp);
  });

  // Zoom to selected residue using comp.autoView (more reliable)
  if (inter.receptor_chain && inter.receptor_resid) {
    const chain = inter.receptor_chain !== "_" ? inter.receptor_chain : "";
    const sele = chain ? `${inter.receptor_resid}:${chain}` : `${inter.receptor_resid}`;
    // Use autoView with larger buffer so receptor stays visible
    comp.autoView(sele, 1000); // 1000ms animation
  }
}


function renderResultDetail(result) {
  if (!els.resultDetail) return;
  if (!result) {
    currentResultData = null;
    setCurrentResultFlexSelection(null);
    els.resultDetail.innerHTML = '<div class="helper">Select a run from the table to preview details.</div>';
    return;
  }
  if (result.view === "average") {
    currentResultData = null;
    setCurrentResultFlexSelection(null);
    els.resultDetail.innerHTML = `
      <div class="result-detail-grid">
        <div class="result-detail-item">
          <div class="result-detail-label">PDB</div>
          <div class="result-detail-value">${result.pdb_id || "-"}</div>
        </div>
        <div class="result-detail-item">
          <div class="result-detail-label">Ligand</div>
          <div class="result-detail-value">${result.ligand_resname || "-"}</div>
        </div>
        <div class="result-detail-item">
          <div class="result-detail-label">Runs</div>
          <div class="result-detail-value">${result.run_count || 0}</div>
        </div>
        <div class="result-detail-item">
          <div class="result-detail-label">Avg Affinity</div>
          <div class="result-detail-value">${formatNumber(result.avg_affinity)}</div>
        </div>
        <div class="result-detail-item">
          <div class="result-detail-label">Avg RMSD</div>
          <div class="result-detail-value">${formatNumber(result.avg_rmsd)}</div>
        </div>
        <div class="result-detail-item">
          <div class="result-detail-label">Range</div>
          <div class="result-detail-value">${formatNumber(result.min_affinity)} → ${formatNumber(result.max_affinity)}</div>
        </div>
      </div>
    `;
    return;
  }
  els.resultDetail.innerHTML = `
    <div class="result-detail-grid">
      <div class="result-detail-item">
        <div class="result-detail-label">Mode</div>
        <div class="result-detail-value">${normalizeDockingMode(result.docking_mode || "standard") === "flexible" ? "Flexible" : "Standard"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">PDB</div>
        <div class="result-detail-value">${result.pdb_id || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Run</div>
        <div class="result-detail-value">${result.run_id || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Ligand</div>
        <div class="result-detail-value">${result.ligand_display_name || result.ligand_resname || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Chain</div>
        <div class="result-detail-value">${result.ligand_chain || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Ligand Resid</div>
        <div class="result-detail-value">${result.ligand_resid || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Affinity</div>
        <div class="result-detail-value">${formatNumber(result.best_affinity)}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">RMSD</div>
        <div class="result-detail-value">${formatNumber(result.rmsd)}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Residues</div>
        <div class="result-detail-value">${result.residue_count ?? "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Flex Residues</div>
        <div class="result-detail-value">${(normalizeFlexResidueList(result.flex_residues || result.flex_residue_spec || []).map((row) => row.chain ? `${row.chain}:${row.resno}` : row.resno).join(", ")) || "-"}</div>
      </div>
      <div class="result-detail-item">
        <div class="result-detail-label">Folder</div>
        <div class="result-detail-value">${result.result_dir || "-"}</div>
      </div>
    </div>
  `;
}

async function loadResultDetail(resultDir) {
  if (!resultDir) return;
  const requestId = ++resultDetailRequestId;
  enforceResultsInteractionToggle();
  const data = await fetchJSON("/api/results/detail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_dir: resultDir }),
  });
  if (requestId !== resultDetailRequestId) return;
  const result = data.result || {};
  currentResultData = result;
  setCurrentResultFlexSelection(result);
  if (currentResultFlexSelectionData && els.showFlexResidues) {
    els.showFlexResidues.checked = true;
  }
  const interactions = data.interactions || [];
  renderResultDetail(result);
  const residues = data.residues || [];

  // Store interactions globally for bond highlighting
  appState.currentInteractions = interactions;

  renderInteractionTable(interactions);
  buildInteractionMaps(residues);
  renderInteractionLegend();

  const complexPath = result.complex_path || result.pose_path || result.receptor_path || "";
  const originalReceptorPath = result.original_receptor_path || "";

  if (complexPath) {
    await loadResultStructure(result, originalReceptorPath);
  }
}

function buildResultFocusSelection(ligandSele) {
  if (!ligandSele) return "";
  return `(${ligandSele}) or (protein and within 6 of (${ligandSele}))`;
}

function getPdbResultKey(result) {
  const resultDir = String(result?.result_dir || "").trim();
  if (resultDir) return resultDir;
  const pdbId = String(result?.pdb_id || "").trim().toUpperCase();
  const runId = String(result?.run_id || "").trim();
  const ligand = String(result?.ligand_display_name || result?.ligand_resname || "").trim();
  return [pdbId, runId, ligand].filter(Boolean).join("::");
}

async function loadResultStructure(result, originalReceptorPath) {
  const path = String(result?.complex_path || result?.pose_path || result?.receptor_path || "").trim();
  if (!path) return;
  if (!initViewer()) return;
  const requestId = ++resultStructureRequestId;
  const pdbKey = getPdbResultKey(result);
  currentResultPdbKey = pdbKey || "";
  const cachedPose = pdbKey ? resultPoseByPdb.get(pdbKey) : null;
  try {
    const resp = await fetch(`/api/results/file?path=${encodeURIComponent(path)}`);
    if (!resp.ok) return;
    const text = await resp.text();
    const blob = new Blob([text], { type: "text/plain" });
    const component = await stage.loadFile(blob, { ext: "pdb" });
    if (requestId !== resultStructureRequestId) {
      try { stage.removeComponent(component); } catch (e) { }
      return;
    }
    const previousComp = comp;
    const previousNativeLigComp = nativeLigComp;
    let nextNativeLigComp = null;

    // Load ORIGINAL receptor PDB (with native ligands) for native ligand visualization
    if (originalReceptorPath) {
      try {
        const ligResp = await fetch(`/api/results/file?path=${encodeURIComponent(originalReceptorPath)}`);
        if (ligResp.ok) {
          const ligText = await ligResp.text();
          const ligBlob = new Blob([ligText], { type: "text/plain" });
          nextNativeLigComp = await stage.loadFile(ligBlob, { ext: "pdb" });
          if (requestId !== resultStructureRequestId) {
            try { stage.removeComponent(component); } catch (e) { }
            try { stage.removeComponent(nextNativeLigComp); } catch (e) { }
            return;
          }
        }
      } catch (e) {
        console.warn("Failed to load original receptor:", e);
      }
    }

    comp = component;
    nativeLigComp = nextNativeLigComp;
    representations = { cartoon: null, surface: null, nativeLigand: null, dockedLigand: null, focusedLigand: null, sticks: null, selectedAtom: null, residueSearch: null, flexResidueSearch: null, highlightedResidue: null };
    selectedAtomData = null;
    externalGridSelectionData = appState.selectedReceptor
      ? (gridSelectionMetaPerReceptor[appState.selectedReceptor] || null)
      : null;
    updateSelectedAtomInfo();
    updateGridSelectionInfo();

    if (previousComp) {
      try { stage.removeComponent(previousComp); } catch (e) { }
    }
    if (previousNativeLigComp) {
      try { stage.removeComponent(previousNativeLigComp); } catch (e) { }
    }

    // Clear any bond shapes from previous selection
    bondShapes.forEach(shapeComp => {
      try { stage.removeComponent(shapeComp); } catch (e) { }
    });
    bondShapes = [];

    updateRepresentations();

    // Ligand-focused camera: center on docked ligand and look along
    // ligand->receptor-center vector. Reuse same protein pose for angle/zoom
    // while re-centering on current ligand across different runs/ligands.
    const ligandResidue = findDockedLigandResidue(comp, result);
    const ligandSele = ligandResidue?.sele || "";
    const ligandCenter = computeLigandCenter(comp, ligandResidue);

    let focused = false;
    if (cachedPose && ligandCenter) {
      focused = applyVectorPose(cachedPose, ligandCenter);
    }

    if (!focused && ligandCenter) {
      const receptorCenter = computeProteinCenter(comp);
      const vectorPose = buildVectorPoseFromCenters(ligandCenter, receptorCenter);
      if (vectorPose) {
        focused = applyVectorPose(vectorPose, ligandCenter);
        if (focused && pdbKey) {
          resultPoseByPdb.set(pdbKey, vectorPose);
        }
      }
    }

    if (!focused) {
      const focusSele = buildResultFocusSelection(ligandSele);
      if (focusSele) {
        try {
          comp.autoView(focusSele, 0);
          focused = true;
        } catch (err) { }
      }
    }

    if (!focused) {
      const fallbackLigandSele = String(ligandSele || "resn UNL");
      if (fallbackLigandSele) {
        try {
          comp.autoView(fallbackLigandSele, 0);
          focused = true;
        } catch (err) { }
      }
    }

    if (!focused) {
      comp.autoView();
    }

    if (gridComp) {
      stage.removeComponent(gridComp);
      gridComp = null;
    }
  } catch (e) {
    console.error("Failed to load result structure:", e);
  }
}


// =====================================================
// Gridbox Functions
// =====================================================

function createGridboxForSelection() {
  if (!comp || !comp.structure) {
    alert("Please load a receptor first.");
    return;
  }

  // Priority: atom picked from viewer > ligand selected in table.
  if (selectedAtomData) {
    clearExternalGridSelection(appState.selectedReceptor, { refreshUI: false });
    const fixedSize = parseFloat(els.fixedGridSize?.value) || 20;
    gridboxData = {
      cx: Number(selectedAtomData.x) || 0,
      cy: Number(selectedAtomData.y) || 0,
      cz: Number(selectedAtomData.z) || 0,
      sx: fixedSize,
      sy: fixedSize,
      sz: fixedSize,
    };

    if (appState.selectedReceptor) {
      gridDataPerReceptor[appState.selectedReceptor] = { ...gridboxData };
      refreshReceptorSummary();
    }

    setGridboxSliders();
    showGridControlsPanel();
    applyGridbox();
    dispatchFlexSelectionContext("gridbox-update");
    return;
  }

  if (residueSearchSelectionData?.bbox) {
    clearExternalGridSelection(appState.selectedReceptor, { refreshUI: false });
    const bounds = residueSearchSelectionData.bbox;
    const sx = Math.max((Number(bounds.maxX) || 0) - (Number(bounds.minX) || 0), 1);
    const sy = Math.max((Number(bounds.maxY) || 0) - (Number(bounds.minY) || 0), 1);
    const sz = Math.max((Number(bounds.maxZ) || 0) - (Number(bounds.minZ) || 0), 1);
    gridboxData = {
      cx: ((Number(bounds.minX) || 0) + (Number(bounds.maxX) || 0)) / 2,
      cy: ((Number(bounds.minY) || 0) + (Number(bounds.maxY) || 0)) / 2,
      cz: ((Number(bounds.minZ) || 0) + (Number(bounds.maxZ) || 0)) / 2,
      sx,
      sy,
      sz,
    };

    if (appState.selectedReceptor) {
      gridDataPerReceptor[appState.selectedReceptor] = {
        cx: Number(gridboxData.cx.toFixed(1)),
        cy: Number(gridboxData.cy.toFixed(1)),
        cz: Number(gridboxData.cz.toFixed(1)),
        sx: Number(gridboxData.sx.toFixed(1)),
        sy: Number(gridboxData.sy.toFixed(1)),
        sz: Number(gridboxData.sz.toFixed(1)),
      };
      refreshReceptorSummary();
    }

    setGridboxSliders();
    showGridControlsPanel();
    applyGridbox();
    updateGridSelectionInfo();
    dispatchFlexSelectionContext("gridbox-update");
    return;
  }

  if (!selectedLigandData) {
    if (externalGridSelectionData?.label) {
      const activeGrid = gridboxData || (
        appState.selectedReceptor && gridDataPerReceptor[appState.selectedReceptor]
          ? { ...gridDataPerReceptor[appState.selectedReceptor] }
          : null
      );
      if (!activeGrid) {
        alert("No pocket gridbox is available for the current selection.");
        return;
      }
      gridboxData = { ...activeGrid };
      if (appState.selectedReceptor) {
        gridDataPerReceptor[appState.selectedReceptor] = { ...gridboxData };
        refreshReceptorSummary();
      }
      setGridboxSliders();
      showGridControlsPanel();
      applyGridbox();
      updateGridSelectionInfo();
      dispatchFlexSelectionContext("gridbox-update");
      scheduleUIStateSave();
      return;
    }

    alert("Select a ligand from table or click an atom in viewer.");
    return;
  }

  const lig = selectedLigandData;

  // Collect ligand atoms
  const ligandAtoms = [];
  comp.structure.eachAtom((atom) => {
    if (atom.resname === lig.resname && atom.resno === lig.resno) {
      if (!lig.chainname || atom.chainname === lig.chainname) {
        ligandAtoms.push({ x: atom.x, y: atom.y, z: atom.z });
      }
    }
  });

  if (ligandAtoms.length === 0) {
    alert("No atoms found for the selected ligand.");
    return;
  }

  // Find atoms within 5A of ligand atoms
  const cutoff = 5.0;
  const neighborCoords = [];

  comp.structure.eachAtom((atom) => {
    if (atom.resname === lig.resname) return;
    if (["HOH", "WAT", "DOD"].includes(atom.resname)) return;

    for (const la of ligandAtoms) {
      const dx = atom.x - la.x;
      const dy = atom.y - la.y;
      const dz = atom.z - la.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (dist < cutoff) {
        neighborCoords.push({ x: atom.x, y: atom.y, z: atom.z });
        break;
      }
    }
  });

  const allCoords = [...ligandAtoms, ...neighborCoords];

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;

  allCoords.forEach((c) => {
    if (c.x < minX) minX = c.x;
    if (c.y < minY) minY = c.y;
    if (c.z < minZ) minZ = c.z;
    if (c.x > maxX) maxX = c.x;
    if (c.y > maxY) maxY = c.y;
    if (c.z > maxZ) maxZ = c.z;
  });

  // Get fixed size from input (same for all dimensions)
  const fixedSize = parseFloat(els.fixedGridSize?.value) || 20;
  clearExternalGridSelection(appState.selectedReceptor, { refreshUI: false });

  gridboxData = {
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2,
    cz: (minZ + maxZ) / 2,
    sx: fixedSize,
    sy: fixedSize,
    sz: fixedSize,
  };

  // Save grid data for this receptor
  if (appState.selectedReceptor) {
    gridDataPerReceptor[appState.selectedReceptor] = { ...gridboxData };
    refreshReceptorSummary(); // Update table to show grid status
  }

  setGridboxSliders();
  showGridControlsPanel();
  applyGridbox();
  dispatchFlexSelectionContext("gridbox-update");
}

function setGridboxSliders() {
  if (!gridboxData) return;

  const updateSlider = (slider, input, val) => {
    if (!slider || !input) return;
    input.value = val.toFixed(1);
    // Center slider: range is [val-100, val+100]
    slider.min = (val - 100).toFixed(1);
    slider.max = (val + 100).toFixed(1);
    slider.value = val.toFixed(1);
  };

  updateSlider(els.gridCx, els.gridCxVal, gridboxData.cx);
  updateSlider(els.gridCy, els.gridCyVal, gridboxData.cy);
  updateSlider(els.gridCz, els.gridCzVal, gridboxData.cz);
  updateSlider(els.gridSx, els.gridSxVal, gridboxData.sx);
  updateSlider(els.gridSy, els.gridSyVal, gridboxData.sy);
  updateSlider(els.gridSz, els.gridSzVal, gridboxData.sz);

  updateGridboxInfo();
}

function showGridControlsPanel() {
  if (els.gridControlsPanel) {
    els.gridControlsPanel.style.display = "block";
    els.gridControlsPanel.classList.remove("grid-panel-hidden");
  }
  if (els.showGrid) {
    els.showGrid.checked = true;
  }
}

function updateGridboxInfo() {
  const cx = parseFloat(els.gridCxVal?.value) || 0;
  const cy = parseFloat(els.gridCyVal?.value) || 0;
  const cz = parseFloat(els.gridCzVal?.value) || 0;
  const sx = parseFloat(els.gridSxVal?.value) || 20;
  const sy = parseFloat(els.gridSyVal?.value) || 20;
  const sz = parseFloat(els.gridSzVal?.value) || 20;

  if (els.infoCenter) els.infoCenter.textContent = `${cx.toFixed(1)}, ${cy.toFixed(1)}, ${cz.toFixed(1)}`;
  if (els.infoSize) els.infoSize.textContent = `${sx.toFixed(1)} × ${sy.toFixed(1)} × ${sz.toFixed(1)}`;
}

function updateGridboxFromSliders() {
  // Sync number inputs from sliders
  const sync = (slider, input) => {
    if (slider && input) input.value = slider.value;
  };

  sync(els.gridCx, els.gridCxVal);
  sync(els.gridCy, els.gridCyVal);
  sync(els.gridCz, els.gridCzVal);
  sync(els.gridSx, els.gridSxVal);
  sync(els.gridSy, els.gridSyVal);
  sync(els.gridSz, els.gridSzVal);

  updateGridboxData();
}

function updateGridboxFromInputs() {
  // Sync sliders from number inputs AND re-center ranges
  const sync = (input, slider) => {
    if (input && slider) {
      const val = parseFloat(input.value) || 0;
      slider.min = (val - 100).toFixed(1);
      slider.max = (val + 100).toFixed(1);
      slider.value = val;
    }
  };

  sync(els.gridCxVal, els.gridCx);
  sync(els.gridCyVal, els.gridCy);
  sync(els.gridCzVal, els.gridCz);
  sync(els.gridSxVal, els.gridSx);
  sync(els.gridSyVal, els.gridSy);
  sync(els.gridSzVal, els.gridSz);

  updateGridboxData();
}

function updateGridboxData() {
  gridboxData = {
    cx: parseFloat(els.gridCxVal?.value) || 0,
    cy: parseFloat(els.gridCyVal?.value) || 0,
    cz: parseFloat(els.gridCzVal?.value) || 0,
    sx: parseFloat(els.gridSxVal?.value) || 20,
    sy: parseFloat(els.gridSyVal?.value) || 20,
    sz: parseFloat(els.gridSzVal?.value) || 20,
  };

  // Save to receptor
  if (appState.selectedReceptor) {
    gridDataPerReceptor[appState.selectedReceptor] = { ...gridboxData };
    refreshReceptorSummary();
  }

  updateGridboxInfo();
  applyGridbox();
  dispatchFlexSelectionContext("gridbox-update");
}

function applyGridbox() {
  // Remove old gridbox
  if (gridComp) {
    stage.removeComponent(gridComp);
    gridComp = null;
  }

  if (!els.showGrid?.checked || !gridboxData) return;

  const shape = new NGL.Shape("gridbox");
  const { cx, cy, cz, sx, sy, sz } = gridboxData;
  const hx = sx / 2, hy = sy / 2, hz = sz / 2;

  // 8 corners
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

  const color = [1, 0.5, 0]; // Orange
  const radius = 0.3;

  edges.forEach((e) => {
    shape.addCylinder(corners[e[0]], corners[e[1]], color, radius);
  });

  corners.forEach((c) => {
    shape.addSphere(c, color, radius * 1.5);
  });

  gridComp = stage.addComponentFromObject(shape);
  gridComp.addRepresentation("buffer");
}

function applyExternalGridbox(grid, options = {}) {
  const targetPdbId = String(options.pdbId || appState.selectedReceptor || "").trim().toUpperCase();
  if (!targetPdbId) {
    throw new Error("Select a receptor before applying a binding-site gridbox.");
  }

  const round1 = (value, fallback = 0) => {
    const num = Number(value);
    return Number.isFinite(num) ? Number(num.toFixed(1)) : fallback;
  };
  const normalized = {
    cx: round1(grid?.cx, 0),
    cy: round1(grid?.cy, 0),
    cz: round1(grid?.cz, 0),
    sx: Math.max(round1(grid?.sx, 0), 1),
    sy: Math.max(round1(grid?.sy, 0), 1),
    sz: Math.max(round1(grid?.sz, 0), 1),
  };

  clearResidueSearchSelection({ refreshUI: false, reason: "pocket" });
  gridboxData = { ...normalized };
  gridDataPerReceptor[targetPdbId] = { ...normalized };
  setExternalGridSelection(options.selection || null, targetPdbId, { refreshUI: false });
  void refreshReceptorSummary();
  showGridControlsPanel();
  setGridboxSliders();
  if (els.showGrid) {
    els.showGrid.checked = options.showGrid !== false;
  }
  applyGridbox();
  restoreExternalGridSelection(targetPdbId);
  dispatchFlexSelectionContext("external-gridbox");
  scheduleUIStateSave();
}

window.DockUPGridbox = {
  applyExternalGridbox,
  getSelectedReceptorId: () => String(appState.selectedReceptor || "").trim().toUpperCase(),
  getSelectedChainId: () => getSelectedChainForReceptor(appState.selectedReceptor),
  getDefaultFixedGridSize: () => Number.parseFloat(els.fixedGridSize?.value || "20") || 20,
};

window.DockUPGridSelectionSearchBridge = {
  getSelectedContext: () => ({
    pdbId: String(appState.selectedReceptor || "").trim().toUpperCase(),
    chain: getSelectedChainForReceptor(appState.selectedReceptor),
  }),
  getResidueCatalog: () => buildResidueSearchCatalog(),
  setResidueSelection: (selection, options) => applyResidueSearchSelection(selection, options || {}),
  clearResidueSelection: (options) => clearResidueSearchSelection(options || {}),
  getResidueSelection: () => (residueSearchSelectionData ? { ...residueSearchSelectionData } : null),
};

window.DockUPFlexSelectionBridge = {
  getSelectedContext: () => ({
    pdbId: String(appState.selectedReceptor || "").trim().toUpperCase(),
    chain: getSelectedChainForReceptor(appState.selectedReceptor),
    hasGrid: !!(gridboxData || (appState.selectedReceptor && gridDataPerReceptor[appState.selectedReceptor])),
  }),
  getResidueCatalog: () => buildFlexibleResidueCatalog(),
  setResidueSelection: (selection, options) => applyFlexResidueSearchSelection(selection, options || {}),
  clearResidueSelection: (options) => clearFlexResidueSearchSelection(options || {}),
  getResidueSelection: () => {
    const current = getFlexSelectionData(appState.selectedReceptor);
    return current ? { ...current } : null;
  },
};

// =====================================================
// Receptor Summary & Ligand List
// =====================================================

// Store uploaded ligands globally to populate dropdowns
let uploadedLigands = [];
let activeLigands = [];
let storedReceptorFiles = [];
let resultsDockFolders = [];
let ligandInventoryInitialized = false;
let dockingConfigSnapshot = null;

const UI_STATE_KEY = "docking_app_ui_state_v1";
let uiStateSaveTimer = null;

function toObjectOrEmpty(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value;
}

function enforceResultsInteractionToggle() {
  if (appState.mode === "Results" && els.showInteractions) {
    els.showInteractions.checked = true;
  }
}

function scheduleUIStateSave(delayMs = 180) {
  if (uiStateSaveTimer) clearTimeout(uiStateSaveTimer);
  uiStateSaveTimer = setTimeout(() => {
    uiStateSaveTimer = null;
    saveUIState();
  }, delayMs);
}

function saveUIState() {
  try {
    const payload = {
      mode: appState.mode,
      resultsView: appState.resultsView,
      selectionMap: appState.selectionMap,
      gridDataPerReceptor,
      gridSelectionMetaPerReceptor,
      dockingConfig: normalizeDockingConfig(appState.dockingConfig || DEFAULT_DOCKING_CONFIG),
      ui: {
        runCount: String(els.runCount?.value || ""),
        gridPadding: String(document.getElementById("gridPadding")?.value || ""),
        outRootPath: String(els.outRootPath?.value || ""),
        outRootName: String(els.outRootName?.value || ""),
        resultsRootPath: String(els.resultsRootPath?.value || ""),
        reportRootPath: String(els.reportRootPath?.value || ""),
        reportOutputPath: String(els.reportOutputPath?.value || ""),
        reportDocRootPath: String(els.reportDocRootPath?.value || ""),
        reportDpi: String(els.reportDpi?.value || ""),
        colorScheme: String(els.colorScheme?.value || ""),
        viewerChain: String(els.viewerChain?.value || ""),
        showSurface: Boolean(els.showSurface?.checked),
        showNativeLigand: Boolean(els.showNativeLigand?.checked),
        showDockedLigand: Boolean(els.showDockedLigand?.checked),
        showInteractions: appState.mode === "Results" ? true : Boolean(els.showInteractions?.checked),
        showSticks: Boolean(els.showSticks?.checked),
        showFlexResidues: Boolean(els.showFlexResidues?.checked),
        showGrid: Boolean(els.showGrid?.checked),
        fixedGridSize: String(els.fixedGridSize?.value || ""),
        testMode: Boolean(document.getElementById("testModeCheck")?.checked),
      },
      ts: Date.now(),
    };
    localStorage.setItem(UI_STATE_KEY, JSON.stringify(payload));
  } catch (err) {
    console.warn("Failed to save UI state:", err);
  }
}

function loadUIState() {
  try {
    const raw = localStorage.getItem(UI_STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    return parsed;
  } catch (_err) {
    return null;
  }
}

async function restoreUIState() {
  const saved = loadUIState();
  if (!saved) return;

  const ui = toObjectOrEmpty(saved.ui);
  const savedMode = String(saved.mode || "").trim();
  if (savedMode && savedMode !== appState.mode) {
    try {
      await fetchJSON("/api/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: savedMode }),
      });
      appState.mode = savedMode;
    } catch (_err) {
      // keep current mode if sync fails
    }
  }

  const savedResultsView = String(saved.resultsView || "").trim();
  if (savedResultsView) {
    appState.resultsView = savedResultsView;
  }

  const savedSelectionMap = toObjectOrEmpty(saved.selectionMap);
  if (Object.keys(savedSelectionMap).length) {
    const normalizedSelection = {};
    Object.entries(savedSelectionMap).forEach(([key, val]) => {
      const pdbId = String(key || "").trim().toUpperCase();
      if (!pdbId) return;
      const row = val && typeof val === "object" ? val : {};
      normalizedSelection[pdbId] = {
        chain: String(row.chain || "all"),
        ligand_resname: String(row.ligand_resname || ""),
        flex_residues: normalizeFlexResidueList(row.flex_residues || row.flex_residue_spec || []),
      };
    });
    appState.selectionMap = normalizedSelection;
  }

  const savedGridMap = toObjectOrEmpty(saved.gridDataPerReceptor);
  if (Object.keys(savedGridMap).length) {
    gridDataPerReceptor = savedGridMap;
  }
  const savedGridSelectionMap = toObjectOrEmpty(saved.gridSelectionMetaPerReceptor);
  if (Object.keys(savedGridSelectionMap).length) {
    gridSelectionMetaPerReceptor = savedGridSelectionMap;
  }
  appState.dockingConfig = normalizeDockingConfig(saved.dockingConfig || appState.dockingConfig || DEFAULT_DOCKING_CONFIG);

  if (els.runCount && ui.runCount !== undefined) els.runCount.value = String(ui.runCount);
  const gridPaddingEl = document.getElementById("gridPadding");
  if (gridPaddingEl && ui.gridPadding !== undefined) gridPaddingEl.value = String(ui.gridPadding);
  if (els.outRootPath && ui.outRootPath !== undefined) els.outRootPath.value = String(ui.outRootPath);
  if (els.outRootName && ui.outRootName !== undefined) els.outRootName.value = String(ui.outRootName);
  if (els.resultsRootPath) {
    els.resultsRootPath.value = RESULTS_DOCK_ROOT;
    appState.resultsRootPath = RESULTS_DOCK_ROOT;
  }
  if (els.reportRootPath && ui.reportRootPath !== undefined) els.reportRootPath.value = String(ui.reportRootPath);
  if (els.reportOutputPath && ui.reportOutputPath !== undefined) els.reportOutputPath.value = String(ui.reportOutputPath);
  if (els.reportDocRootPath && ui.reportDocRootPath !== undefined) els.reportDocRootPath.value = String(ui.reportDocRootPath);
  if (els.reportDpi && ui.reportDpi !== undefined) els.reportDpi.value = String(ui.reportDpi);
  if (els.colorScheme && ui.colorScheme !== undefined) els.colorScheme.value = String(ui.colorScheme);
  if (els.viewerChain && ui.viewerChain !== undefined) els.viewerChain.value = String(ui.viewerChain);
  if (els.showSurface && ui.showSurface !== undefined) els.showSurface.checked = Boolean(ui.showSurface);
  if (els.showNativeLigand && ui.showNativeLigand !== undefined) els.showNativeLigand.checked = Boolean(ui.showNativeLigand);
  if (els.showDockedLigand && ui.showDockedLigand !== undefined) els.showDockedLigand.checked = Boolean(ui.showDockedLigand);
  if (els.showInteractions && ui.showInteractions !== undefined) {
    els.showInteractions.checked = appState.mode === "Results" ? true : Boolean(ui.showInteractions);
  }
  if (els.showSticks && ui.showSticks !== undefined) els.showSticks.checked = Boolean(ui.showSticks);
  if (els.showFlexResidues && ui.showFlexResidues !== undefined) els.showFlexResidues.checked = Boolean(ui.showFlexResidues);
  if (els.showGrid && ui.showGrid !== undefined) els.showGrid.checked = Boolean(ui.showGrid);
  if (els.fixedGridSize && ui.fixedGridSize !== undefined) els.fixedGridSize.value = String(ui.fixedGridSize);
  const testModeEl = document.getElementById("testModeCheck");
  if (testModeEl && ui.testMode !== undefined) testModeEl.checked = Boolean(ui.testMode);

  applyAdvancedDockingConfigToModal(appState.dockingConfig);
  renderDockingConfigSummary();
  updateModeUI();
  enforceResultsInteractionToggle();
}

function normalizeReceptorIds(rawText) {
  const rows = String(rawText || "").split(/[\s,;]+/);
  const out = [];
  const seen = new Set();
  rows.forEach((row) => {
    const id = String(row || "").trim().toUpperCase();
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push(id);
  });
  return out;
}

function mergeReceptorIdsIntoInput(ids) {
  if (!els.pdbIds) return;
  const current = normalizeReceptorIds(els.pdbIds.value || "");
  const seen = new Set(current);
  (ids || []).forEach((raw) => {
    const id = String(raw || "").trim().toUpperCase();
    if (!id || seen.has(id)) return;
    seen.add(id);
    current.push(id);
  });
  els.pdbIds.value = current.join("\n");
}

function renderStoredReceptorFiles(rows) {
  if (!els.receptorFileList) return;
  els.receptorFileList.innerHTML = "";
  if (!Array.isArray(rows) || rows.length === 0) {
    els.receptorFileList.innerHTML = '<span class="helper">No receptor files found.</span>';
    return;
  }
  rows.forEach((row) => {
    const pill = document.createElement("div");
    pill.className = "pill pill-removable";
    const label = document.createElement("span");
    label.textContent = row.pdb_id || row.name || "";
    label.title = row.name || "";
    const actions = document.createElement("span");
    actions.style.display = "inline-flex";
    actions.style.gap = "6px";

    const addBtn = document.createElement("button");
    addBtn.className = "secondary";
    addBtn.type = "button";
    addBtn.style.padding = "2px 8px";
    addBtn.style.fontSize = "11px";
    addBtn.textContent = row.loaded ? "Added" : "Add";
    addBtn.disabled = Boolean(row.loaded);
    addBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      mergeReceptorIdsIntoInput([row.pdb_id]);
      if (row.loaded) return;
      const result = await fetchJSON("/api/receptors/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdb_ids: row.pdb_id || "" }),
      });
      const ignored = Array.isArray(result?.ignored_ids) ? result.ignored_ids : [];
      if (ignored.length) {
        alert(`Could not add receptor: ${ignored.join(", ")}`);
      }
      await fetchJSON("/api/receptors/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdb_id: row.pdb_id || "" }),
      });
      await refreshReceptorSummary();
      await refreshReceptorFiles();
      await refreshViewer();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "pill-x";
    delBtn.type = "button";
    delBtn.title = row.has_file === false ? "No local file to delete" : "Delete receptor file";
    delBtn.textContent = "×";
    delBtn.disabled = row.has_file === false;
    delBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (row.has_file === false) return;
      if (!confirm(`Delete receptor file ${row.name}?`)) return;
      await fetchJSON("/api/receptors/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: row.name }),
      });
      await refreshReceptorFiles();
      await refreshReceptorSummary();
      await refreshViewer();
    });

    pill.addEventListener("click", () => {
      mergeReceptorIdsIntoInput([row.pdb_id]);
      scheduleUIStateSave();
    });

    actions.appendChild(addBtn);
    actions.appendChild(delBtn);
    pill.appendChild(label);
    pill.appendChild(actions);
    els.receptorFileList.appendChild(pill);
  });
}

async function refreshReceptorFiles() {
  try {
    const data = await fetchJSON("/api/receptors/list");
    storedReceptorFiles = Array.isArray(data.receptors) ? data.receptors : [];
    renderStoredReceptorFiles(storedReceptorFiles);
  } catch (err) {
    console.error("Failed to refresh receptor files:", err);
    if (els.receptorFileList) {
      els.receptorFileList.innerHTML = `<span class="helper" style="color:var(--danger)">Failed to list receptor files: ${err.message}</span>`;
    }
  }
}

async function renderReceptorSummary(rows) {
  if (!els.receptorSummary) return;
  els.receptorSummary.innerHTML = "";

  if (!rows || rows.length === 0) {
    els.receptorSummary.innerHTML = '<div class="helper">No receptors loaded.</div>';
    return;
  }

  // Header
  const header = document.createElement("div");
  header.className = "table-row header";
  header.style.gridTemplateColumns = "0.2fr 0.6fr 0.6fr 1.0fr 0.8fr 0.8fr";
  header.innerHTML = "<div style='text-align:center;'>Remove</div><div>PDB</div><div>Chain</div><div>Target Ligand</div><div>Grid Center</div><div>Grid Size</div>";
  els.receptorSummary.appendChild(header);

  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "table-row";
    item.style.gridTemplateColumns = "0.2fr 0.6fr 0.6fr 1.0fr 0.8fr 0.8fr";
    item.style.alignItems = "center";

    if (row.status === "error") item.classList.add("error");
    if (row.pdb_id === appState.selectedReceptor) item.classList.add("selected");

    // Grid Status
    const grid = gridDataPerReceptor[row.pdb_id];
    const gridStatus = grid ? '<span style="color:var(--success);font-weight:bold;">Set</span>' : '<span style="color:var(--muted);">No</span>';

    // Chain Dropdown
    const chainSelect = document.createElement("select");
    chainSelect.style.width = "100%";
    chainSelect.style.padding = "4px";

    // Populate chains
    let chains = row.chains || ["all"];
    // Ensure chains is an array
    if (typeof chains === "string") {
      chains = chains.split(",").map(c => c.trim()).filter(c => c);
      if (chains.length === 0) chains = ["all"];
    }
    chains.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      if (appState.selectionMap && appState.selectionMap[row.pdb_id] && appState.selectionMap[row.pdb_id].chain === c) {
        opt.selected = true;
      }
      chainSelect.appendChild(opt);
    });

    chainSelect.addEventListener("click", (e) => e.stopPropagation());
    chainSelect.addEventListener("change", async (e) => {
      const previousChain = getSelectedChainForReceptor(row.pdb_id);
      const selectedChain = setSelectedChainForReceptor(row.pdb_id, e.target.value);
      const previousLigand = String(appState.selectionMap?.[row.pdb_id]?.ligand_resname || "");
      const allowedLigands = appState.mode === "Redocking"
        ? getNativeLigandsForChain(row.ligands_by_chain, selectedChain)
        : [...activeLigands];
      const nextLigand = previousLigand && previousLigand !== "all_set" && !allowedLigands.includes(previousLigand)
        ? ""
        : previousLigand;
      // Update selection map
      if (!appState.selectionMap) appState.selectionMap = {};
      if (!appState.selectionMap[row.pdb_id]) appState.selectionMap[row.pdb_id] = {};
      appState.selectionMap[row.pdb_id].chain = selectedChain;
      appState.selectionMap[row.pdb_id].ligand_resname = nextLigand;
      if (selectedChain !== previousChain) {
        clearFlexResiduesForReceptor(row.pdb_id);
      }

      // Send to backend
      await fetchJSON("/api/ligands/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pdb_id: row.pdb_id,
          chain: selectedChain,
          ligand: nextLigand
        }),
      });
      await refreshReceptorSummary();
      if (row.pdb_id === appState.selectedReceptor) {
        clearResidueSearchSelection({ refreshUI: false, reason: "chain-change" });
        renderFlexResidueHighlight();
        syncDockingModeUI();
        renderDockingConfigSummary();
        dispatchFlexSelectionContext("chain-change");
        await Promise.resolve(window.DockUPPocketFinder?.syncSelectedChain?.(row.pdb_id, selectedChain));
        await refreshViewer();
      }
      scheduleUIStateSave();
    });

    // Ligand Dropdown
    const ligSelect = document.createElement("select");
    ligSelect.style.width = "100%";
    ligSelect.style.padding = "4px";

    const defOpt = document.createElement("option");
    defOpt.value = "";
    defOpt.textContent = "Select Ligand...";
    ligSelect.appendChild(defOpt);

    const selectedChainForRow = getSelectedChainForReceptor(row.pdb_id);
    let availableLigands = [];
    if (appState.mode === "Redocking") {
      availableLigands = getNativeLigandsForChain(row.ligands_by_chain, selectedChainForRow);
    } else {
      // Docking mode: Use dock-ready ligands
      availableLigands = activeLigands;

      // Add "All Set" option only for Docking
      const allOpt = document.createElement("option");
      allOpt.value = "all_set";
      allOpt.textContent = "All Ligands (Dock All)";
      allOpt.style.fontWeight = "bold";
      if (appState.selectionMap && appState.selectionMap[row.pdb_id] && appState.selectionMap[row.pdb_id].ligand_resname === "all_set") {
        allOpt.selected = true;
      }
      ligSelect.appendChild(allOpt);
    }

    availableLigands.forEach(ligName => {
      const opt = document.createElement("option");
      opt.value = ligName;
      opt.textContent = ligName;
      if (appState.selectionMap && appState.selectionMap[row.pdb_id] && appState.selectionMap[row.pdb_id].ligand_resname === ligName) {
        opt.selected = true;
      }
      ligSelect.appendChild(opt);
    });

    ligSelect.addEventListener("click", (e) => e.stopPropagation());
    ligSelect.addEventListener("change", async (e) => {
      const selectedLig = e.target.value;

      // Auto-select chain logic
      const previousChain = chainSelect.value;
      let targetChain = chainSelect.value;

      if (selectedLig && selectedLig !== "all_set" && row.ligands_by_chain) {
        for (const [chn, ligs] of Object.entries(row.ligands_by_chain)) {
          if (chn === "all") continue;
          if (ligs.includes(selectedLig)) {
            targetChain = chn;
            break;
          }
        }
      }

      // Update UI
      chainSelect.value = targetChain;

      // Update selection map
      if (!appState.selectionMap) appState.selectionMap = {};
      if (!appState.selectionMap[row.pdb_id]) appState.selectionMap[row.pdb_id] = {};
      appState.selectionMap[row.pdb_id].ligand_resname = selectedLig;
      appState.selectionMap[row.pdb_id].chain = targetChain;
      if (targetChain !== previousChain) {
        clearFlexResiduesForReceptor(row.pdb_id);
      }

      // Send to backend
      await fetchJSON("/api/ligands/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pdb_id: row.pdb_id,
          chain: targetChain,
          ligand: selectedLig
        }),
      });
      await refreshReceptorSummary();
      if (row.pdb_id === appState.selectedReceptor) {
        clearResidueSearchSelection({ refreshUI: false, reason: "ligand-chain-change" });
        renderFlexResidueHighlight();
        syncDockingModeUI();
        renderDockingConfigSummary();
        dispatchFlexSelectionContext("chain-change");
        await Promise.resolve(window.DockUPPocketFinder?.syncSelectedChain?.(row.pdb_id, targetChain));
        await refreshViewer();
      }
      scheduleUIStateSave();

      // If Redocking, trigger preview
      // User requested to remove focus logic for now
      /*
      if (appState.mode === "Redocking" && selectedLig && selectedLig !== "all_set") {
          // ... (removed focus logic)
      }
      */
    });

    // Delete Button
    const delBtn = document.createElement("button");
    delBtn.innerHTML = "&times;"; // HTML entity for multiplication sign (x)
    delBtn.className = "secondary";
    delBtn.style.padding = "0";
    delBtn.style.width = "20px";
    delBtn.style.height = "20px";
    delBtn.style.lineHeight = "18px";
    delBtn.style.fontSize = "14px";
    delBtn.style.textAlign = "center";
    delBtn.style.color = "var(--danger)";
    delBtn.style.borderColor = "transparent";
    delBtn.style.background = "transparent";
    delBtn.style.cursor = "pointer";
    delBtn.title = "Remove Receptor";

    delBtn.addEventListener("mouseenter", () => {
      delBtn.style.background = "#fee2e2";
      delBtn.style.borderColor = "#fecaca";
    });
    delBtn.addEventListener("mouseleave", () => {
      delBtn.style.background = "transparent";
      delBtn.style.borderColor = "transparent";
    });

    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (confirm(`Remove receptor ${row.pdb_id}?`)) {
        await removeReceptor(row.pdb_id);
      }
    });

    // Columns
    const colAction = document.createElement("div");
    colAction.style.textAlign = "center";
    colAction.appendChild(delBtn);

    const colPdb = document.createElement("div"); colPdb.textContent = row.pdb_id;
    const colChain = document.createElement("div"); colChain.appendChild(chainSelect);
    const colLig = document.createElement("div"); colLig.appendChild(ligSelect);

    // Grid Details
    let gridCenter = "-";
    let gridSize = "-";
    if (grid) {
      gridCenter = `${formatNumber(grid.cx, 1)},${formatNumber(grid.cy, 1)},${formatNumber(grid.cz, 1)}`;
      gridSize = `${formatNumber(grid.sx, 1)},${formatNumber(grid.sy, 1)},${formatNumber(grid.sz, 1)}`;
    }

    const colGridC = document.createElement("div"); colGridC.innerHTML = gridCenter;
    if (grid) colGridC.style = "color:var(--success);font-family:monospace;font-size:11px;";

    const colGridS = document.createElement("div"); colGridS.innerHTML = gridSize;
    if (grid) colGridS.style = "color:var(--success);font-family:monospace;font-size:11px;";

    item.append(colAction, colPdb, colChain, colLig, colGridC, colGridS);

    item.addEventListener("click", () => selectReceptor(row.pdb_id));
    els.receptorSummary.appendChild(item);
  });
}

async function refreshReceptorSummary() {
  try {
    // Fetch ligands first to populate dropdowns
    const ligData = await fetchJSON("/api/ligands/list");
    uploadedLigands = ligData.ligands || [];
    const activeData = await fetchJSON("/api/ligands/active");
    activeLigands = Array.isArray(activeData.active_ligands) ? activeData.active_ligands : [];
    appState.activeLigands = [...activeLigands];

    const data = await fetchJSON("/api/receptors/summary");
    await renderReceptorSummary(data.summary || []);
    await refreshReceptorFiles();
  } catch (e) {
    console.error(e);
  }
}

async function removeReceptor(pdbId) {
  try {
    const data = await fetchJSON("/api/receptors/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdb_id: pdbId }),
    });
    try {
      await Promise.resolve(window.DockUPPocketFinder?.clearReceptorCache?.(pdbId));
    } catch (cacheErr) {
      console.warn("Failed to clear binding-site cache for receptor:", pdbId, cacheErr);
    }
    delete gridSelectionMetaPerReceptor[String(pdbId || "").trim().toUpperCase()];
    if (appState.selectedReceptor === pdbId) {
      externalGridSelectionData = null;
    }
    // If the removed one was selected, the backend handles re-selection logic,
    // but we need to update frontend state.
    // Ideally backend returns the new summary.
    await renderReceptorSummary(data.summary || []);
    await refreshReceptorFiles();

    // If we removed the currently selected one, we might need to refresh viewer
    if (appState.selectedReceptor === pdbId) {
      // It seems backend might have switched selection.
      // Let's re-fetch state or just refresh viewer if we have a new selection
      // But simpler is to just refresh everything.
      const state = await fetchJSON("/api/state");
      appState.selectedReceptor = state.selected_receptor;
      await refreshViewer();
    }
  } catch (e) {
    console.error("Failed to remove receptor:", e);
  }
}

async function refreshLigands() {
  try {
    const prevLigands = new Set(uploadedLigands || []);
    const data = await fetchJSON("/api/ligands/list");
    const ligands = Array.isArray(data.ligands) ? data.ligands : [];
    const activeData = await fetchJSON("/api/ligands/active");
    const activeList = Array.isArray(activeData.active_ligands) ? activeData.active_ligands : [];

    uploadedLigands = ligands;
    activeLigands = activeList.filter((name) => ligands.includes(name));
    appState.activeLigands = [...activeLigands];

    const ligandInventoryChanged = prevLigands.size !== ligands.length
      || ligands.some((name) => !prevLigands.has(name));

    if (ligandInventoryInitialized && ligandInventoryChanged) {
      // Ligand inventory changed after initial load; drop stale gridboxes.
      resetGridboxState({ clearAllStored: true });
    }

    // Remove stale selection_map ligand choices no longer in dock-ready pool.
    const activeSet = new Set(activeLigands);
    Object.keys(appState.selectionMap || {}).forEach((pdbId) => {
      const row = appState.selectionMap[pdbId] || {};
      const ligand = String(row.ligand_resname || "");
      if (!ligand || ligand === "all_set") return;
      if (!activeSet.has(ligand)) {
        appState.selectionMap[pdbId].ligand_resname = "";
      }
    });
    renderActiveLigands();
    scheduleUIStateSave();
    ligandInventoryInitialized = true;
  } catch (e) {
    console.error(e);
  }
}

function renderActiveLigands() {
  if (!els.activeLigandList) return;
  els.activeLigandList.innerHTML = "";
  if (!Array.isArray(activeLigands) || activeLigands.length === 0) {
    els.activeLigandList.innerHTML = '<span class="helper">No dock-ready ligands selected.</span>';
    return;
  }
  activeLigands.forEach((name) => {
    const pill = document.createElement("div");
    pill.className = "pill pill-removable";
    const label = document.createElement("span");
    label.textContent = name;
    const del = document.createElement("button");
    del.className = "pill-x";
    del.type = "button";
    del.title = "Remove from dock-ready ligands";
    del.textContent = "×";
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const res = await fetchJSON("/api/ligands/active/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        activeLigands = Array.isArray(res.active_ligands) ? res.active_ligands : [];
        appState.activeLigands = [...activeLigands];
        renderActiveLigands();
        await refreshReceptorSummary();
      } catch (err) {
        alert(err.message || "Failed to remove dock-ready ligand.");
      }
    });
    pill.appendChild(label);
    pill.appendChild(del);
    els.activeLigandList.appendChild(pill);
  });
}

async function selectReceptor(pdbId) {
  try {
    await fetchJSON("/api/receptors/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdb_id: pdbId }),
    });
    appState.selectedReceptor = pdbId;
    appState.selectedLigand = "";
    appState.selectedChain = getSelectedChainForReceptor(pdbId);
    selectedLigandData = null;
    clearResidueSearchSelection({ refreshUI: false, reason: "receptor-change" });

    // Restore grid data if exists
    if (gridDataPerReceptor[pdbId]) {
      gridboxData = { ...gridDataPerReceptor[pdbId] };
      setGridboxSliders();
      showGridControlsPanel();
    } else {
      gridboxData = null;
      if (els.gridControlsPanel) {
        els.gridControlsPanel.classList.add("grid-panel-hidden");
        els.gridControlsPanel.style.display = "none";
      }
    }
    restoreExternalGridSelection(pdbId);
    syncDockingModeUI();
    renderDockingConfigSummary();

    await refreshReceptorSummary();
    await refreshViewer();
  } catch (e) {
    console.error(e);
  }
}

// =====================================================
// Queue & Run Functions
// =====================================================

function normalizeQueueBatchId(batchId) {
  if (batchId === null || batchId === undefined || batchId === "") return null;
  const num = Number(batchId);
  if (Number.isFinite(num)) return String(Math.trunc(num));
  const text = String(batchId || "").trim();
  return text || null;
}

function getQueueItemsForBatch(batchId, queue = appState.queueData || []) {
  const normalizedBatchId = normalizeQueueBatchId(batchId);
  if (!normalizedBatchId) return [];
  return (Array.isArray(queue) ? queue : []).filter(
    (row) => normalizeQueueBatchId(row?.batch_id) === normalizedBatchId
  );
}

function updateQueueEditorUI() {
  const batchId = normalizeQueueBatchId(appState.selectedQueueBatchId);
  if (els.buildQueue) {
    els.buildQueue.textContent = batchId ? "Update Queue" : "Build queue";
  }
  if (els.clearQueueSelection) {
    els.clearQueueSelection.style.display = batchId ? "" : "none";
  }
  if (els.queueEditorStatus) {
    els.queueEditorStatus.textContent = batchId
      ? `Editing batch #${batchId}`
      : "New queue build will append as a separate batch.";
  }
}

function clearQueueBatchSelection({ keepForm = true } = {}) {
  appState.selectedQueueBatchId = null;
  appState.queueEditorReceptorIds = [];
  updateQueueEditorUI();
  if (!keepForm) {
    if (els.outRootName) els.outRootName.value = "";
    if (els.outRootPath && !els.outRootPath.value) {
      els.outRootPath.value = "data/dock";
    }
  }
}

async function setAppModeQuietly(mode) {
  const nextMode = mode === "Redocking" ? "Redocking" : "Docking";
  if (appState.mode !== nextMode) {
    await fetchJSON("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: nextMode }),
    });
    appState.mode = nextMode;
    updateModeUI();
  }
}

async function loadQueueBatchIntoEditor(batchId) {
  const items = getQueueItemsForBatch(batchId);
  if (!items.length) return;
  const normalizedBatchId = normalizeQueueBatchId(batchId);
  const first = items[0] || {};
  const receptorIds = [...new Set(items.map((row) => String(row?.pdb_id || "").trim().toUpperCase()).filter(Boolean))];
  appState.selectedQueueBatchId = normalizedBatchId;
  appState.queueEditorReceptorIds = receptorIds;

  if (document.getElementById("runCount")) {
    document.getElementById("runCount").value = String(first.run_count || 1);
  }
  if (document.getElementById("gridPadding")) {
    document.getElementById("gridPadding").value = String(first.padding ?? first.grid_pad ?? 0);
  }
  if (els.outRootPath) {
    els.outRootPath.value = first.out_root_path || "data/dock";
  }
  if (els.outRootName) {
    els.outRootName.value = first.out_root_name || "";
  }

  appState.dockingConfig = normalizeDockingConfig(first.docking_config || appState.dockingConfig || DEFAULT_DOCKING_CONFIG);
  applyAdvancedDockingConfigToModal(appState.dockingConfig);
  syncDockingModeUI();
  renderDockingConfigSummary();

  items.forEach((row) => {
    const pdbId = String(row?.pdb_id || "").trim().toUpperCase();
    if (!pdbId) return;
    if (!appState.selectionMap) appState.selectionMap = {};
    appState.selectionMap[pdbId] = {
      chain: normalizeChainValue(row.chain || "all"),
      ligand_resname: String(row.ligand_resname || row.ligand_name || ""),
      flex_residues: normalizeFlexResidueList(row.flex_residues || row.flex_residue_spec || []),
    };
    if (row.grid_params && typeof row.grid_params === "object") {
      gridDataPerReceptor[pdbId] = { ...row.grid_params };
    }
  });

  await setAppModeQuietly(first.job_type === "Redocking" ? "Redocking" : "Docking");
  await refreshReceptorSummary();
  if (receptorIds.length) {
    await selectReceptor(receptorIds[0]);
  }
  updateQueueEditorUI();
  renderQueueTable(appState.queueData || []);
  scheduleUIStateSave();
}

function renderRecentDockings(rows) {
  if (!els.recentDockingsTable) return;
  if (!Array.isArray(rows) || rows.length === 0) {
    els.recentDockingsTable.innerHTML = '<div class="helper">No incomplete dock roots found.</div>';
    return;
  }

  const runActive = isRunActiveStatus(appState.runStatus);
  const activeRootKey = normalizePathForCompare(appState.activeRunOutRoot);
  const body = rows.map((row) => {
    const expected = Number(row.expected_runs_total || 0);
    const completed = Number(row.completed_runs_total || 0);
    const progress = expected > 0 ? `${completed} / ${expected} runs` : `${completed} / - runs`;
    const progressPct = expected > 0 ? Math.max(0, Math.min(100, (completed / expected) * 100)) : 0;
    const resumable = !!row.resumable;
    const rootPath = String(row.out_root || row.resume_out_root || row.dock_root || "").trim();
    const rowRootKey = normalizePathForCompare(rootPath);
    const rowRunning = Boolean(row.is_running) || (runActive && !!rowRootKey && rowRootKey === activeRootKey);
    const liveElapsed = Number(appState.runElapsedSeconds || 0);
    const displayElapsedSeconds = rowRunning && !!rowRootKey && rowRootKey === activeRootKey
      ? liveElapsed
      : Number(row.elapsed_seconds || 0);
    const elapsedLabel = formatElapsedSince(displayElapsedSeconds);
    const statusLabel = rowRunning ? "Running" : (resumable ? "Ready" : "Needs Attention");
    const statusClass = rowRunning ? "running" : (resumable ? "" : "not-ready");
    const canContinue = resumable && !runActive;
    const canDelete = !rowRunning;
    const continueBtn = canContinue
      ? `<button class="recent-resume-btn" data-id="${escapeHtml(row.id || "")}" data-out-root="${escapeHtml(rootPath)}">Continue Queue</button>`
      : `<button class="recent-resume-btn" disabled title="${escapeHtml(runActive ? "Another queue is currently running." : (row.resume_reason || "Missing metadata"))}">Continue Queue</button>`;
    const stopBtn = rowRunning
      ? `<button class="secondary danger-soft recent-stop-btn" type="button">Stop Queue</button>`
      : "";
    const actionBtn = `${continueBtn}${stopBtn}`;
    const deleteBtn = `<button class="recent-delete-btn" data-id="${escapeHtml(row.id || "")}" data-out-root="${escapeHtml(rootPath)}" ${canDelete ? "" : "disabled"} title="${canDelete ? "Remove from recent list" : "Cannot delete while running"}">&times;</button>`;

    return `
      <article class="recent-docking-item">
        <div class="recent-docking-top">
          <div class="recent-docking-root" title="${escapeHtml(row.dock_root || "-")}">${escapeHtml(row.dock_root || "-")}</div>
          <div class="recent-docking-top-right">
            <span class="recent-docking-status ${statusClass}">${escapeHtml(statusLabel)}</span>
            ${deleteBtn}
          </div>
        </div>
        <div class="recent-docking-path" title="${escapeHtml(rootPath || "-")}">${escapeHtml(rootPath || "-")}</div>
        <div class="recent-docking-meta">
          <span class="recent-meta-chip">Progress ${escapeHtml(progress)}</span>
          <span class="recent-meta-chip">Elapsed ${escapeHtml(elapsedLabel)}</span>
          <span class="recent-meta-chip">Updated ${escapeHtml(row.last_update || "-")}</span>
        </div>
        <div class="recent-docking-progress" aria-hidden="true">
          <div class="recent-docking-progress-bar" style="width:${progressPct.toFixed(1)}%"></div>
        </div>
        <div class="recent-docking-actions">${actionBtn}</div>
      </article>
    `;
  }).join("");

  els.recentDockingsTable.innerHTML = `<div class="recent-docking-list">${body}</div>`;

  Array.from(els.recentDockingsTable.querySelectorAll(".recent-resume-btn")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const itemId = btn.dataset.id || "";
      const outRootHint = btn.dataset.outRoot || "";
      if (!itemId) return;
      try {
        await continueRecentQueue(itemId, outRootHint);
      } catch (err) {
        alert(err.message || "Failed to continue queue.");
      }
    });
  });

  Array.from(els.recentDockingsTable.querySelectorAll(".recent-delete-btn")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const itemId = btn.dataset.id || "";
      const outRoot = btn.dataset.outRoot || "";
      if (!itemId || btn.disabled) return;
      if (!confirm("Remove this recent docking entry from the list?")) return;
      try {
        await fetchJSON("/api/run/recent/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item_id: itemId, out_root: outRoot }),
        });
        await refreshRecentDockings();
      } catch (err) {
        alert(err.message || "Failed to delete recent docking entry.");
      }
    });
  });

  Array.from(els.recentDockingsTable.querySelectorAll(".recent-stop-btn")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await stopRunQueue();
      } catch (err) {
        alert(err.message || "Failed to stop queue.");
      }
    });
  });
}

async function refreshRecentDockings() {
  if (els.recentDockingsMeta) {
    els.recentDockingsMeta.textContent = "Scanning incomplete dockings...";
  }
  const data = await fetchJSON("/api/run/recent?limit=3");
  const rows = Array.isArray(data.rows) ? data.rows : [];
  renderRecentDockings(rows);
  if (els.recentDockingsMeta) {
    els.recentDockingsMeta.textContent = `${rows.length} incomplete dock root(s)`;
  }
}

async function continueRecentQueue(itemId, outRootHint = "") {
  if (isRunActiveStatus(appState.runStatus)) {
    const shouldStop = confirm("A queue is currently running. Do you want to stop it first?");
    if (!shouldStop) return;
    await stopRunQueue({ silent: true });
    if (isRunActiveStatus(appState.runStatus)) {
      throw new Error("Queue is still running. Please wait until it stops.");
    }
  }
  if (els.recentDockingsMeta) {
    els.recentDockingsMeta.textContent = "Continuing queue from saved progress...";
  }
  const data = await fetchJSON("/api/run/recent/continue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, replace_queue: true }),
  });

  appState.runStatus = data.status || "running";
  const cmdOutRoot = extractOutRootFromCommand(data.command || "");
  appState.activeRunOutRoot = String(data.out_root || cmdOutRoot || outRootHint || appState.activeRunOutRoot || "").trim();
  appState.runElapsedSeconds = 0;
  setRunStatus(appState.runStatus);
  appState.queueCount = data.queue_count || 0;
  updateQueueCount();
  if (data.queue) {
    appState.queueData = Array.isArray(data.queue) ? data.queue : [];
    renderQueueTable(appState.queueData);
  }

  if (els.recentDockingsMeta) {
    els.recentDockingsMeta.textContent = data.message || "Continue queue started.";
  }
  updateRunMetrics({
    status: appState.runStatus,
    totalRuns: data.prepared_count || 0,
    completedRuns: 0,
    elapsedSeconds: 0,
    command: data.command || "",
  });
  pollRunStatus();
  await refreshRecentDockings();
}

async function buildQueue() {
  const runCount = document.getElementById("runCount")?.value || 10;
  const padding = document.getElementById("gridPadding")?.value || 0;
  const activeSet = new Set(activeLigands || []);
  const normalizedSelectionMap = normalizeSelectionMapState(JSON.parse(JSON.stringify(appState.selectionMap || {})));
  const selectedBatchId = normalizeQueueBatchId(appState.selectedQueueBatchId);
  const queueEditorReceptorIds = selectedBatchId && (appState.queueEditorReceptorIds || []).length === 0
    ? getQueueItemsForBatch(selectedBatchId, appState.queueData)
        .map((row) => String(row?.pdb_id || "").trim().toUpperCase())
        .filter(Boolean)
    : (appState.queueEditorReceptorIds || []);
  const receptorFilter = selectedBatchId
    ? new Set(queueEditorReceptorIds.map((pdbId) => String(pdbId || "").trim().toUpperCase()).filter(Boolean))
    : null;
  const selectionMap = {};
  Object.entries(normalizedSelectionMap).forEach(([pdbId, row]) => {
    const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
    if (!normalizedPdbId) return;
    if (receptorFilter && !receptorFilter.has(normalizedPdbId)) return;
    selectionMap[normalizedPdbId] = { ...row };
  });
  Object.keys(selectionMap).forEach((pdbId) => {
    const row = selectionMap[pdbId] || {};
    const lig = String(row.ligand_resname || row.ligand || "").trim();
    if (!lig || lig === "all_set") return;
    if (!activeSet.has(lig)) {
      row.ligand_resname = "";
      row.ligand = "";
      selectionMap[pdbId] = row;
    }
    row.flex_residues = normalizeFlexResidueList(row.flex_residues || row.flex_residue_spec || []);
  });
  const gridData = {};
  Object.entries(gridDataPerReceptor || {}).forEach(([pdbId, grid]) => {
    const normalizedPdbId = String(pdbId || "").trim().toUpperCase();
    if (!normalizedPdbId || !grid || typeof grid !== "object") return;
    if (receptorFilter && !receptorFilter.has(normalizedPdbId)) return;
    gridData[normalizedPdbId] = { ...grid };
  });

  const payload = {
    run_count: parseInt(runCount),
    padding: parseFloat(padding),
    docking_config: normalizeDockingConfig(appState.dockingConfig || DEFAULT_DOCKING_CONFIG),
    selection_map: selectionMap,
    grid_data: gridData,
    mode: appState.mode || "Docking",
    out_root_path: document.getElementById("outRootPath")?.value || "data/dock",
    out_root_name: document.getElementById("outRootName")?.value || "",
    replace_queue: false,
  };
  if (selectedBatchId) {
    payload.update_batch_id = parseInt(selectedBatchId, 10);
  }

  const data = await fetchJSON("/api/queue/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  appState.queueData = Array.isArray(data.queue) ? data.queue : [];
  appState.queueCount = data.queue_count || 0;
  if (selectedBatchId) {
    appState.selectedQueueBatchId = selectedBatchId;
  } else {
    appState.selectedQueueBatchId = null;
    appState.queueEditorReceptorIds = [];
  }
  updateQueueCount();
  updateQueueEditorUI();
  renderQueueTable(appState.queueData);
}

function renderQueueTable(queue) {
  if (!els.queueTable) return;
  appState.queueData = Array.isArray(queue) ? queue.map((row) => ({ ...row })) : [];
  const queueRows = appState.queueData;
  els.queueTable.innerHTML = "";
  // We change display to block because we will have multiple tables/headers
  els.queueTable.style.display = queueRows.length > 0 ? "block" : "none";

  if (queueRows.length === 0) {
    appState.selectedQueueBatchId = null;
    appState.queueEditorReceptorIds = [];
    updateQueueEditorUI();
    return;
  }
  const knownBatchIds = new Set(queueRows.map((row) => normalizeQueueBatchId(row?.batch_id)).filter(Boolean));
  if (appState.selectedQueueBatchId && !knownBatchIds.has(normalizeQueueBatchId(appState.selectedQueueBatchId))) {
    appState.selectedQueueBatchId = null;
    appState.queueEditorReceptorIds = [];
  }

  // Group by batch_id
  const batches = {};
  queueRows.forEach(item => {
    const bid = item.batch_id || "default";
    if (!batches[bid]) batches[bid] = [];
    batches[bid].push(item);
  });

  // Render each batch
  // Sort batches by ID (timestamp) descending? Or ascending?
  // Let's do descending to show newest first
  const batchIds = Object.keys(batches).sort().reverse();

  batchIds.forEach(bid => {
    const items = batches[bid];
    const normalizedBatchId = normalizeQueueBatchId(bid);
    const isSelectedBatch = normalizedBatchId && normalizedBatchId === normalizeQueueBatchId(appState.selectedQueueBatchId);
    const firstItem = items[0] || {};

    // Batch Container
    const batchContainer = document.createElement("div");
    batchContainer.style.marginBottom = "16px";
    batchContainer.style.border = "1px solid var(--border)";
    batchContainer.style.borderRadius = "6px";
    batchContainer.style.overflow = "hidden";
    batchContainer.style.cursor = "pointer";
    if (isSelectedBatch) {
      batchContainer.classList.add("queue-batch-selected");
    }

    // Batch Header
    const batchHeader = document.createElement("div");
    batchHeader.style.background = "var(--surface-2)";
    batchHeader.style.padding = "8px 12px";
    batchHeader.style.display = "flex";
    batchHeader.style.justifyContent = "space-between";
    batchHeader.style.alignItems = "center";
    batchHeader.style.borderBottom = "1px solid var(--border)";

    // Calculate total runs
    const totalRuns = items.reduce((sum, item) => sum + (item.run_count || 1), 0);

    // Determine batch type (assume all items in batch have same type)
    const batchType = items[0]?.job_type || "Docking";

    const title = document.createElement("span");
    title.style.fontWeight = "600";
    title.style.fontSize = "13px";
    title.innerHTML = `Batch #${bid} <span style="font-weight:normal; color:var(--muted); margin-left:8px;">[${batchType}]</span> (${items.length} jobs, ${totalRuns} total runs)`;

    const titleWrap = document.createElement("div");
    const meta = document.createElement("div");
    meta.className = "queue-batch-meta";
    meta.innerHTML = `
      <span>Path: <code>${escapeHtml(String(firstItem.out_root_path || "data/dock"))}</code></span>
      <span>Folder: <code>${escapeHtml(String(firstItem.out_root_name || ""))}</code></span>
      <span>Runs: ${escapeHtml(String(firstItem.run_count || 1))}</span>
      <span>Padding: ${escapeHtml(String(firstItem.padding ?? firstItem.grid_pad ?? 0))}</span>
    `;
    titleWrap.appendChild(title);
    titleWrap.appendChild(meta);

    const headerActions = document.createElement("div");
    headerActions.style.display = "flex";
    headerActions.style.gap = "8px";
    headerActions.style.alignItems = "center";

    const editBtn = document.createElement("button");
    editBtn.textContent = "Edit Queue";
    editBtn.className = "secondary";
    editBtn.style.padding = "2px 8px";
    editBtn.style.fontSize = "11px";
    editBtn.style.height = "auto";
    editBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      await loadQueueBatchIntoEditor(bid);
    });

    const delBtn = document.createElement("button");
    delBtn.textContent = "Delete Batch";
    delBtn.className = "secondary";
    delBtn.style.padding = "2px 8px";
    delBtn.style.fontSize = "11px";
    delBtn.style.height = "auto";
    delBtn.style.color = "var(--danger)";
    delBtn.onclick = async (event) => {
      event.stopPropagation();
      if (confirm("Delete this batch?")) {
        const res = await fetchJSON("/api/queue/remove_batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ batch_id: parseInt(bid) })
        });
        appState.queueData = Array.isArray(res.queue) ? res.queue : [];
        if (normalizeQueueBatchId(appState.selectedQueueBatchId) === normalizedBatchId) {
          clearQueueBatchSelection();
        }
        appState.queueCount = res.queue_count || 0;
        updateQueueCount();
        renderQueueTable(appState.queueData);
      }
    };

    batchHeader.addEventListener("click", async () => {
      await loadQueueBatchIntoEditor(bid);
    });
    headerActions.appendChild(editBtn);
    headerActions.appendChild(delBtn);
    batchHeader.appendChild(titleWrap);
    batchHeader.appendChild(headerActions);
    batchContainer.appendChild(batchHeader);

    // Table Header
    const tableHeader = document.createElement("div");
    tableHeader.className = "table-row header";
    tableHeader.style.gridTemplateColumns = "0.6fr 0.5fr 0.4fr 1fr 1.2fr 0.4fr";
    tableHeader.style.borderBottom = "1px solid var(--border)";
    tableHeader.style.background = "var(--surface-1)";
    tableHeader.innerHTML = "<div>Mode</div><div>PDB</div><div>Chain</div><div>Ligand</div><div>Grid (Center / Size)</div><div>Runs</div>";
    batchContainer.appendChild(tableHeader);

    // Rows
    items.forEach(item => {
      const row = document.createElement("div");
      row.className = "table-row";
      row.style.gridTemplateColumns = "0.6fr 0.5fr 0.4fr 1fr 1.2fr 0.4fr";
      row.style.fontSize = "12px";

      // Ligand
      let ligDisplay = item.ligand_name || item.ligand_resname || "-";

      // Grid
      let gridDisplay = "-";
      if (item.grid_params) {
        const g = item.grid_params;
        const c = `${formatNumber(g.cx, 1)},${formatNumber(g.cy, 1)},${formatNumber(g.cz, 1)}`;
        const s = `${formatNumber(g.sx, 1)},${formatNumber(g.sy, 1)},${formatNumber(g.sz, 1)}`;
        gridDisplay = `<div class="queue-grid-lines"><div>C:[${c}]</div><div>S:[${s}]</div></div>`;
      }
      const dockingMode = normalizeDockingMode(item?.docking_config?.docking_mode || item?.docking_mode || "standard");
      const modeLabel = dockingMode === "flexible" ? "Flexible" : "Standard";

      row.innerHTML = `
            <div class="queue-mode-text">${modeLabel}</div>
            <div>${item.pdb_id}</div>
            <div>${item.chain}</div>
            <div title="${ligDisplay}" style="overflow:hidden;text-overflow:ellipsis;">${ligDisplay}</div>
            <div class="queue-grid-cell">${gridDisplay}</div>
            <div>${item.run_count || 1}</div>
        `;
      row.addEventListener("click", async () => {
        await loadQueueBatchIntoEditor(bid);
      });
      batchContainer.appendChild(row);
    });

    els.queueTable.appendChild(batchContainer);
  });
  updateQueueEditorUI();
}

async function startRun() {
  if (isRunActiveStatus(appState.runStatus)) {
    const shouldStop = confirm("A queue is currently running. Do you want to stop it first?");
    if (!shouldStop) return;
    await stopRunQueue({ silent: true });
    if (isRunActiveStatus(appState.runStatus)) {
      alert("Queue is still running/stopping. Please wait and try again.");
      return;
    }
  }

  const isTestMode = document.getElementById("testModeCheck")?.checked || false;
  const batchIds = [...new Set((appState.queueData || []).map((row) => normalizeQueueBatchId(row?.batch_id)).filter(Boolean))];
  let targetBatchId = normalizeQueueBatchId(appState.selectedQueueBatchId);
  if (!targetBatchId && batchIds.length === 1) {
    targetBatchId = batchIds[0];
  }
  if (!targetBatchId && batchIds.length > 1) {
    alert("Select the queue batch you want to run first.");
    return;
  }
  const data = await fetchJSON("/api/run/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      is_test_mode: isTestMode,
      batch_id: targetBatchId ? parseInt(targetBatchId, 10) : null,
    })
  });
  appState.runStatus = "running";
  appState.activeRunOutRoot = String(data.out_root || extractOutRootFromCommand(data.command || "") || "").trim();
  appState.runElapsedSeconds = 0;
  setRunStatus("running");
  updateRunMetrics({
    status: "running",
    totalRuns: 0,
    completedRuns: 0,
    elapsedSeconds: 0,
    command: data.command || "",
  });
  pollRunStatus();
  await refreshRecentDockings();
}

async function stopRunQueue({ silent = false } = {}) {
  if (!isRunActiveStatus(appState.runStatus) && appState.runStatus !== "stopped") {
    if (!silent) {
      alert("No running queue to stop.");
    }
    return;
  }
  const shouldStop = silent ? true : confirm("Stop the currently running queue?");
  if (!shouldStop) return;

  const data = await fetchJSON("/api/run/stop", { method: "POST" });
  appState.runStatus = data.status || "stopped";
  appState.runElapsedSeconds = 0;
  setRunStatus(appState.runStatus);
  if (appState.runStatus !== "running" && appState.runStatus !== "stopping") {
    if (runPoll) {
      clearInterval(runPoll);
      runPoll = null;
    }
  }
  if (!silent) {
    alert(data.message || "Queue stop request submitted.");
  }
  await refreshRecentDockings();
}

function pollRunStatus() {
  if (runPoll) clearInterval(runPoll);
  runPoll = setInterval(async () => {
    try {
      const data = await fetchJSON("/api/run/status");
      appState.runStatus = data.status || "idle";
      appState.activeRunOutRoot = String(data.out_root || extractOutRootFromCommand(data.command || "") || appState.activeRunOutRoot || "").trim();
      appState.runElapsedSeconds = Number(data.elapsed_seconds || 0);
      if (els.runLog) els.runLog.textContent = data.log || "";
      setRunStatus(data.status || "idle");
      updateRunMetrics({
        command: data.command || "",
        totalRuns: data.total_runs || 0,
        completedRuns: data.completed_runs || 0,
        elapsedSeconds: data.elapsed_seconds || 0,
        status: data.status || "idle",
      });
      if (isRunActiveStatus(data.status || "idle")) {
        const now = Date.now();
        if (now - recentAutoRefreshTs >= 5000) {
          recentAutoRefreshTs = now;
          refreshRecentDockings().catch((err) => console.error("Recent docking refresh failed:", err));
        }
      }
      if (!isRunActiveStatus(data.status || "idle")) {
        clearInterval(runPoll);
        runPoll = null;
        recentAutoRefreshTs = 0;
        refreshRecentDockings().catch((err) => console.error("Recent docking refresh failed:", err));
      }
    } catch (e) {
      clearInterval(runPoll);
      runPoll = null;
    }
  }, 1000);
}

// =====================================================
// Event Bindings
// =====================================================

function bindEvents() {
  // Mode toggle
  if (els.modeToggle) {
    els.modeToggle.addEventListener("click", async (event) => {
      const btn = event.target.closest("button[data-mode]");
      if (!btn) return;
      const mode = btn.dataset.mode;
      if (mode === appState.mode) return;

      const goingToResults = mode === "Results";
      const leavingResultsLikeMode = appState.mode === "Results" || appState.mode === "Report";
      if (!goingToResults && !leavingResultsLikeMode) {
        // Clear configuration but keep receptors when switching docking modes
        appState.selectionMap = {};
        gridDataPerReceptor = {};
      }

      await fetchJSON("/api/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: mode }),
      });
      appState.mode = mode;
      if (mode !== "Results" && mode !== "Report") {
        setCurrentResultFlexSelection(null);
      }
      updateModeUI();
      scheduleUIStateSave();

      if (goingToResults) {
        try {
          await refreshResultsDockFolders(els.resultsRootPath?.value || RESULTS_DOCK_ROOT);
          await scanResults();
        } catch (err) {
          console.error(err);
          renderResultsTable();
          renderResidueTable([]);
          renderResultDetail(null);
        }
        return;
      }

      if (mode === "Report") {
        fetchReports();
        return;
      }

      // Refresh UI to reflect cleared config
      await refreshReceptorSummary();
      await refreshViewer();
    });
  }

  if (els.openLigand3dPopup) {
    els.openLigand3dPopup.addEventListener("click", () => {
      openLigand3dModal();
    });
  }
  if (els.closeLigand3dPopup) {
    els.closeLigand3dPopup.addEventListener("click", () => {
      closeLigand3dModal();
    });
  }
  if (els.ligand3dModal) {
    els.ligand3dModal.addEventListener("click", (event) => {
      if (event.target === els.ligand3dModal) {
        closeLigand3dModal();
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els.ligand3dModal?.classList.contains("active")) {
      closeLigand3dModal();
    }
  });
  window.addEventListener("message", async (event) => {
    if (event.origin !== window.location.origin) return;
    const msg = event.data || {};
    if (msg.type !== "docking:ligands-updated") return;
    const copiedNames = Array.isArray(msg?.payload?.copied)
      ? msg.payload.copied.map((name) => String(name || "").trim()).filter(Boolean)
      : [];
    try {
      const beforeSet = new Set(uploadedLigands || []);
      await refreshLigands();
      const currentSet = new Set(uploadedLigands || []);
      let toActivate = Array.from(currentSet).filter((name) => !beforeSet.has(name));
      if (!toActivate.length) {
        toActivate = copiedNames.filter((name) => currentSet.has(name));
      }
      if (toActivate.length) {
        const res = await fetchJSON("/api/ligands/active/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ names: toActivate }),
        });
        activeLigands = Array.isArray(res.active_ligands) ? res.active_ligands : activeLigands;
        appState.activeLigands = [...activeLigands];
        renderActiveLigands();
      }
      await refreshReceptorSummary();
    } catch (err) {
      console.error("Failed to refresh ligands after popup update:", err);
    }
  });

  // Ligand upload
  if (els.ligandUpload) {
    els.ligandUpload.addEventListener("change", async (event) => {
      const files = event.target.files;
      setFileLabel(els.ligandUpload, els.ligandUploadName, { multiple: true });
      if (!files.length) return;
      const form = new FormData();
      Array.from(files).forEach((file) => form.append("files", file));
      await fetchJSON("/api/ligands/upload", { method: "POST", body: form });
      await refreshLigands();
      await refreshReceptorSummary(); // Auto-refresh receptor table to show new ligands
    });
  }

  // Receptor upload
  if (els.receptorUpload) {
    els.receptorUpload.addEventListener("change", async (event) => {
      const files = event.target.files;
      setFileLabel(els.receptorUpload, els.receptorUploadName, { multiple: true });
      if (!files.length) return;
      const form = new FormData();
      Array.from(files).forEach((file) => form.append("files", file));
      await fetchJSON("/api/receptors/upload", { method: "POST", body: form });
      await refreshReceptorFiles();
    });
  }

  // Load receptors
  if (els.loadReceptors) {
    els.loadReceptors.addEventListener("click", async () => {
      const normalizedIds = normalizeReceptorIds(els.pdbIds?.value || "");
      if (els.pdbIds) {
        els.pdbIds.value = normalizedIds.join("\n");
      }
      const result = await fetchJSON("/api/receptors/store", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdb_ids: normalizedIds.join("\n") }),
      });
      const ignored = Array.isArray(result?.ignored_ids) ? result.ignored_ids : [];
      if (ignored.length) {
        alert(`Ignored invalid/unavailable receptor IDs: ${ignored.join(", ")}`);
      }
      await refreshReceptorFiles();
    });
  }

  // Grid file upload
  if (els.gridUpload) {
    els.gridUpload.addEventListener("change", async (event) => {
      const file = event.target.files[0];
      setFileLabel(els.gridUpload, els.gridUploadName, { multiple: false });
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const data = await fetchJSON("/api/grid/upload", { method: "POST", body: form });
      appState.gridFilePath = data.grid_file || "";
      updateGridPath();
      await refreshViewer();
    });
  }

  // Build queue
  if (els.buildQueue) {
    els.buildQueue.addEventListener("click", async () => {
      try {
        await buildQueue();
      } catch (err) {
        alert(err.message || "Failed to build queue.");
      }
    });
  }
  if (els.clearQueueSelection) {
    els.clearQueueSelection.addEventListener("click", () => {
      clearQueueBatchSelection();
    });
  }

  // Run queue
  if (els.runQueue) {
    els.runQueue.addEventListener("click", async () => {
      try {
        await startRun();
      } catch (err) {
        alert(err.message || "Failed to start run.");
      }
    });
  }

  if (els.stopRunQueue) {
    els.stopRunQueue.addEventListener("click", async () => {
      try {
        await stopRunQueue();
      } catch (err) {
        alert(err.message || "Failed to stop queue.");
      }
    });
  }

  if (els.openDockingConfigModal) {
    els.openDockingConfigModal.addEventListener("click", () => {
      openDockingConfigModal();
    });
  }

  if (els.closeDockingConfigModal) {
    els.closeDockingConfigModal.addEventListener("click", () => {
      closeDockingConfigModal({ restore: true });
    });
  }

  if (els.cancelDockingConfigModal) {
    els.cancelDockingConfigModal.addEventListener("click", () => {
      closeDockingConfigModal({ restore: true });
    });
  }

  if (els.saveDockingConfigModal) {
    els.saveDockingConfigModal.addEventListener("click", () => {
      saveDockingConfigModal();
    });
  }

  if (els.dockCfgDockingMode) {
    els.dockCfgDockingMode.addEventListener("change", () => {
      syncDockingModeUI();
      renderDockingConfigSummary();
      dispatchFlexSelectionContext("mode-change");
    });
  }

  if (els.dockingConfigModal) {
    els.dockingConfigModal.addEventListener("click", (event) => {
      if (event.target === els.dockingConfigModal) {
        closeDockingConfigModal({ restore: true });
      }
    });
  }

  if (els.refreshRecentDockings) {
    els.refreshRecentDockings.addEventListener("click", async () => {
      try {
        await refreshRecentDockings();
      } catch (err) {
        if (els.recentDockingsMeta) {
          els.recentDockingsMeta.textContent = `Failed to load recent dockings: ${err.message || err}`;
        }
      }
    });
  }

  // Results scan
  if (els.scanResults) {
    els.scanResults.addEventListener("click", async () => {
      try {
        await scanResults();
      } catch (err) {
        alert(err.message || "Failed to load results.");
      }
    });
  }

  if (els.resultsDockFolderSelect) {
    els.resultsDockFolderSelect.addEventListener("change", async (event) => {
      const selected = String(event.target.value || RESULTS_DOCK_ROOT).trim() || RESULTS_DOCK_ROOT;
      if (els.resultsRootPath) {
        els.resultsRootPath.value = selected;
      }
      appState.resultsRootPath = selected;
      scheduleUIStateSave();
      try {
        await scanResults();
      } catch (err) {
        alert(err.message || "Failed to load results.");
      }
    });
  }

  if (els.btnRenderPreview) {
    els.btnRenderPreview.addEventListener("click", () => initiateRender(false));
  }

  if (els.deleteAllRenderImages) {
    els.deleteAllRenderImages.addEventListener("click", () => deleteAllReportImages("render"));
  }

  if (els.btnGenerateGraphs) {
    els.btnGenerateGraphs.addEventListener("click", () => initiateGraphs());
  }

  if (els.deleteAllPlotImages) {
    els.deleteAllPlotImages.addEventListener("click", () => deleteAllReportImages("plot"));
  }

  if (els.btnGenerateReport) {
    els.btnGenerateReport.addEventListener("click", () => generateReportDoc());
  }

  if (els.saveReportRootMeta) {
    els.saveReportRootMeta.addEventListener("click", () => saveReportRootMetadata());
  }

  if (els.resetReportRootMeta) {
    els.resetReportRootMeta.addEventListener("click", () => resetReportRootMetadata());
  }

  if (els.closeReportMetaModal) {
    els.closeReportMetaModal.addEventListener("click", () => closeReportMetaModal());
  }

  if (els.cancelReportRootMeta) {
    els.cancelReportRootMeta.addEventListener("click", () => closeReportMetaModal());
  }

  if (els.reportMetaModal) {
    els.reportMetaModal.addEventListener("click", (event) => {
      if (event.target === els.reportMetaModal) {
        closeReportMetaModal();
      }
    });
  }

  if (els.pickResultsRoot && els.resultsRootPicker) {
    els.pickResultsRoot.addEventListener("click", () => {
      els.resultsRootPicker.click();
    });
    els.resultsRootPicker.addEventListener("change", async (event) => {
      try {
        const path = await resolvePathFromPicker(event.target.files, "results", event.target);
        if (path && els.resultsRootPath) {
          els.resultsRootPath.value = path;
          appState.resultsRootPath = path;
          renderResultsDockFolderOptions(path);
          scheduleUIStateSave();
          await scanResults();
        }
      } catch (err) {
        alert(err.message || "Failed to resolve folder.");
      } finally {
        event.target.value = "";
      }
    });
  }

  if (els.pickReportRoot && els.reportRootPicker) {
    els.pickReportRoot.addEventListener("click", () => {
      els.reportRootPicker.click();
    });
    els.reportRootPicker.addEventListener("change", async (event) => {
      try {
        const path = await resolvePathFromPicker(event.target.files, "report", event.target);
        if (path && els.reportRootPath) {
          els.reportRootPath.value = path;
          if (els.reportOutputPath) {
            els.reportOutputPath.value = defaultReportOutputPath(path);
          }
          if (els.reportDocRootPath) {
            const docRoot = defaultReportOutputPath(path);
            els.reportDocRootPath.value = docRoot;
            reportDocRootPath = docRoot;
          }
          reportSelectedLinkedRoot = "";
          reportSelectedReceptors = new Set();
          reportSelectedRuns = new Map();
          reportSelectedDocImages = new Set();
          reportDocImageOrder = [];
          reportDocManualOrder = false;
          reportFigureCaptionText = new Map();
          reportFigureCaptionCustom = new Set();
          reportExtraSections = [];
          reportFigureStartNumber = 1;
          await fetchReports();
        } else {
          alert("Selected folder could not be resolved. Source reset to data/dock.");
          if (els.reportRootPath) els.reportRootPath.value = REPORT_DOCK_ROOT;
          if (els.reportOutputPath) els.reportOutputPath.value = defaultReportOutputPath(REPORT_DOCK_ROOT);
          if (els.reportDocRootPath) {
            const docRoot = defaultReportOutputPath(REPORT_DOCK_ROOT);
            els.reportDocRootPath.value = docRoot;
            reportDocRootPath = docRoot;
          }
          reportSelectedLinkedRoot = "";
          reportSelectedReceptors = new Set();
          reportSelectedRuns = new Map();
          reportSelectedDocImages = new Set();
          reportDocImageOrder = [];
          reportDocManualOrder = false;
          reportFigureCaptionText = new Map();
          reportFigureCaptionCustom = new Set();
          reportExtraSections = [];
          reportFigureStartNumber = 1;
          await fetchReports();
        }
      } catch (err) {
        alert(err.message || "Failed to resolve folder.");
      } finally {
        event.target.value = "";
      }
    });
  }

  if (els.pickReportOutput && els.reportOutputPicker) {
    els.pickReportOutput.addEventListener("click", () => {
      els.reportOutputPicker.click();
    });
    els.reportOutputPicker.addEventListener("change", async (event) => {
      try {
        const path = await resolvePathFromPicker(event.target.files, "report", event.target);
        if (path && els.reportOutputPath) {
          const prevOutput = (els.reportOutputPath.value || "").trim();
          els.reportOutputPath.value = path;
          if (els.reportDocRootPath) {
            const currentDocRoot = (els.reportDocRootPath.value || "").trim();
            if (!currentDocRoot || currentDocRoot === prevOutput) {
              els.reportDocRootPath.value = path;
              reportDocRootPath = path;
            }
          }
          reportSelectedDocImages = new Set();
          reportDocImageOrder = [];
          reportDocManualOrder = false;
          reportFigureCaptionText = new Map();
          reportFigureCaptionCustom = new Set();
          reportExtraSections = [];
          reportFigureStartNumber = 1;
          await fetchReports();
        }
      } catch (err) {
        alert(err.message || "Failed to resolve output folder.");
      } finally {
        event.target.value = "";
      }
    });
  }

  if (els.pickReportDocRoot && els.reportDocRootPicker) {
    els.pickReportDocRoot.addEventListener("click", () => {
      els.reportDocRootPicker.click();
    });
    els.reportDocRootPicker.addEventListener("change", async (event) => {
      try {
        const path = await resolvePathFromPicker(event.target.files, "report", event.target);
        if (path && els.reportDocRootPath) {
          els.reportDocRootPath.value = path;
          reportDocRootPath = path;
          await fetchReportDocImages({ resetSelection: true });
        }
      } catch (err) {
        alert(err.message || "Failed to resolve report image root.");
      } finally {
        event.target.value = "";
      }
    });
  }

  if (els.refreshReportDocImages) {
    els.refreshReportDocImages.addEventListener("click", async () => {
      try {
        await fetchReportDocImages();
      } catch (err) {
        alert("Failed to load report images: " + err.message);
      }
    });
  }

  if (els.reportFigureStartNumber) {
    els.reportFigureStartNumber.addEventListener("change", () => {
      parseFigureStartFromInput();
      renderReportDocImageGallery();
    });
  }

  if (els.addReportExtraSection) {
    els.addReportExtraSection.addEventListener("click", () => {
      reportExtraSections.push({ title: "", body: "" });
      renderReportExtraSectionsEditor();
    });
  }

  if (els.saveReportDocConfig) {
    els.saveReportDocConfig.addEventListener("click", async () => {
      try {
        await saveReportDocConfig({ silent: false, refresh: false });
      } catch (err) {
        alert("Failed to save report layout: " + err.message);
      }
    });
  }

  if (els.selectAllDocImages) {
    els.selectAllDocImages.addEventListener("change", (event) => {
      const checked = Boolean(event.target.checked);
      const paths = (reportDocImageOrder || []).map((path) => String(path || "").trim()).filter(Boolean);
      if (checked) {
        reportSelectedDocImages = new Set(paths);
      } else {
        reportSelectedDocImages = new Set();
      }
      Array.from(document.querySelectorAll(".report-doc-image-checkbox")).forEach((box) => {
        box.checked = checked;
      });
      updateReportDocSelectionSummary();
    });
  }

  if (els.reportTabToggle) {
    els.reportTabToggle.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-tab]");
      if (!btn) return;
      setReportTab(btn.dataset.tab || "images");
    });
  }

  if (els.refreshReportsBtn) {
    els.refreshReportsBtn.addEventListener("click", () => fetchReports());
  }

  if (els.pickOutRoot && els.outRootPicker) {
    els.pickOutRoot.addEventListener("click", () => {
      els.outRootPicker.click();
    });
    els.outRootPicker.addEventListener("change", async (event) => {
      try {
        const path = await resolvePathFromPicker(event.target.files, "generic", event.target);
        if (path && els.outRootPath) {
          els.outRootPath.value = path;
          renderDockingConfigSummary();
          scheduleUIStateSave();
        }
      } catch (err) {
        alert(err.message || "Failed to resolve folder.");
      } finally {
        event.target.value = "";
      }
    });
  }

  // Results view toggle
  if (els.resultsViewToggle) {
    els.resultsViewToggle.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-view]");
      if (!btn) return;
      const view = btn.dataset.view;
      appState.resultsView = view;
      Array.from(els.resultsViewToggle.querySelectorAll(".toggle")).forEach((el) => {
        el.classList.toggle("active", el.dataset.view === view);
      });
      renderResultsTable();
      renderResidueTable([]);
      renderResultDetail(null);
      interactionResiduesByType = {};
      interactionResidueInfo = {};
      renderInteractionLegend();
      updateRepresentations();
    });
  }
  // Viewer controls
  [els.colorScheme, els.viewerChain, els.showSurface, els.showNativeLigand, els.showDockedLigand, els.showInteractions, els.showSticks, els.showFlexResidues].forEach((el) => {
    if (el) {
      el.addEventListener("change", async () => {
        if (el === els.viewerChain && appState.selectedReceptor && appState.mode !== "Results" && appState.mode !== "Report") {
          const previousChain = getSelectedChainForReceptor(appState.selectedReceptor);
          const nextChain = setSelectedChainForReceptor(appState.selectedReceptor, els.viewerChain.value || "all");
          const currentLigand = String(appState.selectionMap?.[appState.selectedReceptor]?.ligand_resname || "");
          if (nextChain !== previousChain) {
            clearFlexResiduesForReceptor(appState.selectedReceptor);
          }
          clearResidueSearchSelection({ refreshUI: false, reason: "viewer-chain-change" });
          renderFlexResidueHighlight();
          syncDockingModeUI();
          renderDockingConfigSummary();
          dispatchFlexSelectionContext("viewer-chain-change");
          await fetchJSON("/api/ligands/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              pdb_id: appState.selectedReceptor,
              chain: nextChain,
              ligand: currentLigand,
            }),
          });
          await Promise.resolve(window.DockUPPocketFinder?.syncSelectedChain?.(appState.selectedReceptor, nextChain));
          await refreshReceptorSummary();
          await refreshViewer();
          scheduleUIStateSave();
          return;
        }
        updateRepresentations();
        scheduleUIStateSave();
      });
    }
  });

  // Show grid checkbox
  if (els.showGrid) {
    els.showGrid.addEventListener("change", () => {
      applyGridbox();
      scheduleUIStateSave();
    });
  }

  // Gridbox controls
  if (els.createGridLigand) {
    els.createGridLigand.addEventListener("click", () => createGridboxForSelection());
  }

  // Gridbox range sliders
  [els.gridCx, els.gridCy, els.gridCz, els.gridSx, els.gridSy, els.gridSz].forEach((el) => {
    if (el) {
      el.addEventListener("input", () => updateGridboxFromSliders());
    }
  });

  // Gridbox number inputs
  [els.gridCxVal, els.gridCyVal, els.gridCzVal, els.gridSxVal, els.gridSyVal, els.gridSzVal].forEach((el) => {
    if (el) {
      el.addEventListener("input", () => updateGridboxFromInputs());
    }
  });

  const persistInputs = [
    els.runCount,
    document.getElementById("gridPadding"),
    els.outRootPath,
    els.outRootName,
    els.resultsRootPath,
    els.reportRootPath,
    els.reportOutputPath,
    els.reportDocRootPath,
    els.reportDpi,
    els.fixedGridSize,
    document.getElementById("testModeCheck"),
  ];
  persistInputs.forEach((el) => {
    if (!el) return;
    el.addEventListener("change", () => {
      renderDockingConfigSummary();
      scheduleUIStateSave();
    });
    el.addEventListener("input", () => {
      renderDockingConfigSummary();
      scheduleUIStateSave();
    });
  });
  window.addEventListener("beforeunload", () => {
    saveUIState();
  });
  // Save Config
  // Save Config
  const saveBtn = document.getElementById("saveConfig");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      // Prepare payload with client-side state
      const runCountVal = document.getElementById("runCount")?.value || 10;
      const paddingVal = document.getElementById("gridPadding")?.value || 0;

      const payload = {
        selection_map: normalizeSelectionMapState(appState.selectionMap || {}),
        grid_data: gridDataPerReceptor || {},
        docking_config: normalizeDockingConfig(appState.dockingConfig || DEFAULT_DOCKING_CONFIG),
        run_count: parseInt(runCountVal),
        padding: parseFloat(paddingVal)
      };

      // Fetch blob from backend
      try {
        const response = await fetch("/api/config/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        if (!response.ok) throw new Error("Failed to save config");

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "docking_config.xlsx";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (e) {
        console.error(e);
        alert("Error saving configuration: " + e.message);
      }
    });
  }

  // Load Config
  const loadBtn = document.getElementById("loadConfigBtn");
  const loadInput = document.getElementById("loadConfigInput");
  if (loadBtn && loadInput) {
    loadBtn.addEventListener("click", () => loadInput.click());
    loadInput.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/api/config/load", {
          method: "POST",
          body: formData
        });
        const data = await res.json();

        if (data.error) throw new Error(data.error);

        // Restore client-side state
        if (data.selection_map) appState.selectionMap = normalizeSelectionMapState(data.selection_map);
        if (data.grid_data) gridDataPerReceptor = data.grid_data;
        if (data.queue) {
          appState.queueData = Array.isArray(data.queue) ? data.queue : [];
          appState.queueCount = appState.queueData.length;
          updateQueueCount();
          renderQueueTable(appState.queueData);
        }
        if (data.docking_config) {
          appState.dockingConfig = normalizeDockingConfig(data.docking_config);
          applyAdvancedDockingConfigToModal(appState.dockingConfig);
          renderDockingConfigSummary();
        }
        syncDockingModeUI();

        // Auto-switch mode if present in config (it's in the first row of General sheet usually, but backend might not return it explicitly in JSON response unless we add it)
        // Wait, the backend load_config updates STATE["mode"]. We should fetch the current mode from backend or trust the backend response if we update it.
        // Let's update the backend to return the mode in the response.
        // Assuming backend returns 'mode' in the response now (I'll update backend next).
        if (data.mode) {
          appState.mode = data.mode;
          updateModeUI();
        }

        // Refresh UI
        await refreshReceptorSummary();
        await refreshViewer();

        loadInput.value = "";
      } catch (err) {
        console.error(err);
        alert("Failed to load configuration: " + err.message);
      }
    });
  }
}


// =====================================================
// Initialization
// =====================================================

async function init() {
  initElements();
  updateSelectedAtomInfo();
  updateGridSelectionInfo();
  await loadState();
  await restoreUIState();
  bindEvents();
  await refreshLigands();
  await refreshReceptorSummary();
  await refreshViewer();
  await refreshResultsDockFolders(RESULTS_DOCK_ROOT);
  if (appState.mode === "Results") {
    try {
      await scanResults();
    } catch (e) {
      console.error(e);
    }
  }
  if (isRunActiveStatus(appState.runStatus)) {
    pollRunStatus();
  }
  if (appState.mode === "Report") {
    try {
      await fetchReports();
    } catch (e) {
      console.error(e);
    }
  }
  try {
    await refreshRecentDockings();
  } catch (e) {
    if (els.recentDockingsMeta) {
      els.recentDockingsMeta.textContent = `Failed to load recent dockings: ${e.message || e}`;
    }
  }
  scheduleUIStateSave();
}

// =====================================================
// Report Functions
// =====================================================
const REPORT_DOCK_ROOT = "data/dock";
const REPORT_PREDEFINED_PLOTS = [
  {
    id: "affinity_table_plus_boxplot",
    title: "Affinity Table + Boxplot",
    description: "Primary affinity figure (aligned with dimer report order).",
  },
  {
    id: "interaction_frequency_heatmap",
    title: "Interaction Frequency Heatmap",
    description: "Interacting residue frequency matrix across runs.",
  },
  {
    id: "common_residue_heatmap",
    title: "Common Residue Heatmap",
    description: "Common residue map by dominant interaction type.",
  },
  {
    id: "interaction_stacked_bar",
    title: "Interaction Stacked Bar",
    description: "Stacked interaction class counts by receptor-ligand.",
  },
];

let reportActiveTab = "images";
let reportRenderCatalog = [];
let reportPlotCatalog = [];
let reportSummary = null;
let reportCurrentRoot = REPORT_DOCK_ROOT;
let reportCurrentSource = REPORT_DOCK_ROOT;
let reportCurrentOutput = "";
let reportSourceFolders = [];
let reportReceptors = [];
let reportLinkedRoot = "";
let reportLinkedError = "";
let reportDockValidation = null;
let reportSourceMetadata = null;
let reportDocInfo = null;
let reportLastUpdated = null;
let renderTimer = null;
let plotTimer = null;
let reportSelectedReceptors = new Set();
let reportSelectedPlots = new Set(REPORT_PREDEFINED_PLOTS.map((item) => item.id));
let reportSelectedRuns = new Map();
let reportSelectedLinkedRoot = "";
let reportDocRootPath = "";
let reportDocImages = [];
let reportSelectedDocImages = new Set();
let reportDocImageOrder = [];
let reportDocDraggingCard = null;
let reportDocManualOrder = false;
let reportFigureCaptionText = new Map();
let reportFigureCaptionCustom = new Set();
let reportFigureStartNumber = 1;
let reportExtraSections = [];

function defaultReportOutputPath(sourcePath) {
  const safe = String(sourcePath || REPORT_DOCK_ROOT).replace(/[\\/]+$/, "");
  return `${safe}/report_outputs`;
}

function ensureReportSourceValue() {
  if (!els.reportRootPath) return REPORT_DOCK_ROOT;
  const value = (els.reportRootPath.value || "").trim();
  if (!value) {
    els.reportRootPath.value = REPORT_DOCK_ROOT;
    return REPORT_DOCK_ROOT;
  }
  return value;
}

function ensureReportOutputValue() {
  if (!els.reportOutputPath) return defaultReportOutputPath(ensureReportSourceValue());
  const value = (els.reportOutputPath.value || "").trim();
  if (!value) {
    const auto = defaultReportOutputPath(ensureReportSourceValue());
    els.reportOutputPath.value = auto;
    return auto;
  }
  return value;
}

function ensureReportDocRootValue() {
  const fallback = ensureReportOutputValue();
  if (!els.reportDocRootPath) return fallback;
  const value = (els.reportDocRootPath.value || "").trim();
  if (!value) {
    els.reportDocRootPath.value = fallback;
    return fallback;
  }
  return value;
}

function setReportTab(tabName = "images") {
  reportActiveTab = tabName;
  if (els.reportTabToggle) {
    Array.from(els.reportTabToggle.querySelectorAll(".toggle")).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === reportActiveTab);
    });
  }
  if (els.reportImagesTab) els.reportImagesTab.style.display = reportActiveTab === "images" ? "block" : "none";
  if (els.reportGraphsTab) els.reportGraphsTab.style.display = reportActiveTab === "graphs" ? "block" : "none";
  if (els.reportDocTab) els.reportDocTab.style.display = reportActiveTab === "report" ? "block" : "none";
}

function formatReportTime(epochSec) {
  if (!epochSec) return "-";
  const dt = new Date(epochSec * 1000);
  if (Number.isNaN(dt.getTime())) return "-";
  return dt.toLocaleString();
}

function formatBytes(size) {
  if (!size || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(value >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function setReportMetaText(text) {
  if (els.reportMetaText) {
    els.reportMetaText.textContent = text;
  }
}

function updateReportStats(summary) {
  const total = summary?.total ?? (reportRenderCatalog.length + reportPlotCatalog.length);
  const rendered = summary?.rendered ?? reportRenderCatalog.length;
  const graph = summary?.plots ?? reportPlotCatalog.length;
  const reportReady = summary?.report_ready ?? Boolean(reportDocInfo?.exists);
  if (els.reportStatTotal) els.reportStatTotal.textContent = String(total);
  if (els.reportStatRendered) els.reportStatRendered.textContent = String(rendered);
  if (els.reportStatGraphs) els.reportStatGraphs.textContent = String(graph);
  if (els.reportStatOther) els.reportStatOther.textContent = reportReady ? "Yes" : "No";
}

function getSelectedReceptors() {
  return Array.from(document.querySelectorAll(".report-receptor-checkbox:checked")).map((el) => el.value);
}

function getSelectedRunByReceptor() {
  const runMap = {};
  Array.from(document.querySelectorAll(".report-run-select")).forEach((el) => {
    const receptor = (el.dataset.receptor || "").trim();
    if (!receptor) return;
    const runVal = (el.value || "").trim();
    reportSelectedRuns.set(receptor, runVal);
    runMap[receptor] = runVal;
  });
  return runMap;
}

function getSelectedPlots() {
  return Array.from(document.querySelectorAll(".report-plot-checkbox:checked")).map((el) => el.value);
}

function openReportMetaModal() {
  if (!els.reportMetaModal) return;
  els.reportMetaModal.classList.add("active");
}

function closeReportMetaModal() {
  if (!els.reportMetaModal) return;
  els.reportMetaModal.classList.remove("active");
}

function enableDragReorder(tbody) {
  if (!tbody) return;
  let draggingRow = null;
  const rows = Array.from(tbody.querySelectorAll("tr[data-id]"));
  rows.forEach((row) => {
    row.draggable = true;
    row.classList.add("report-draggable-row");
    row.addEventListener("dragstart", (event) => {
      draggingRow = row;
      row.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", row.dataset.id || "");
      }
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      draggingRow = null;
    });
    row.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (!draggingRow || draggingRow === row) return;
      const rect = row.getBoundingClientRect();
      const insertAfter = event.clientY > rect.top + rect.height / 2;
      tbody.insertBefore(draggingRow, insertAfter ? row.nextSibling : row);
    });
    row.addEventListener("drop", (event) => {
      event.preventDefault();
    });
  });
}

function renderRootMetadataEditor() {
  if (!els.reportMainTypeInput || !els.reportReceptorAliasBody || !els.reportLigandAliasBody) return;
  const meta = reportSourceMetadata || {};
  const receptorRows = Array.isArray(meta.receptors) ? meta.receptors : [];
  const ligandRows = Array.isArray(meta.ligands) ? meta.ligands : [];

  els.reportMainTypeInput.value = String(meta.main_type || "").trim();
  if (els.reportRootMetaInfo) {
    const customized = meta.customized ? "custom" : "auto";
    const sourceLabel = ensureReportSourceValue();
    els.reportRootMetaInfo.textContent = `Source: ${sourceLabel} | labels: ${customized}`;
  }

  els.reportReceptorAliasBody.innerHTML = "";
  if (!receptorRows.length) {
    els.reportReceptorAliasBody.innerHTML = '<tr><td colspan="3" class="helper">No receptors detected for this source.</td></tr>';
  } else {
    receptorRows.forEach((row) => {
      const id = String(row.id || "").trim();
      const label = String(row.label || id).trim();
      const tr = document.createElement("tr");
      tr.dataset.id = id;
      tr.innerHTML = `
        <td style="text-align:center;"><span class="report-order-handle">::</span></td>
        <td style="font-family:'IBM Plex Mono', monospace; font-size:12px;">${id}</td>
        <td><input type="text" class="report-meta-receptor-input" data-id="${id}" value="${label}" style="width:100%;"></td>
      `;
      els.reportReceptorAliasBody.appendChild(tr);
    });
    enableDragReorder(els.reportReceptorAliasBody);
  }

  els.reportLigandAliasBody.innerHTML = "";
  if (!ligandRows.length) {
    els.reportLigandAliasBody.innerHTML = '<tr><td colspan="3" class="helper">No ligands detected for this source.</td></tr>';
  } else {
    ligandRows.forEach((row) => {
      const id = String(row.id || "").trim();
      const label = String(row.label || id).trim();
      const tr = document.createElement("tr");
      tr.dataset.id = id;
      tr.innerHTML = `
        <td style="text-align:center;"><span class="report-order-handle">::</span></td>
        <td style="font-family:'IBM Plex Mono', monospace; font-size:12px;">${id}</td>
        <td><input type="text" class="report-meta-ligand-input" data-id="${id}" value="${label}" style="width:100%;"></td>
      `;
      els.reportLigandAliasBody.appendChild(tr);
    });
    enableDragReorder(els.reportLigandAliasBody);
  }
}

function collectRootMetadataForm() {
  const mainType = (els.reportMainTypeInput?.value || "").trim();
  const receptorLabels = {};
  const ligandLabels = {};
  const receptorOrder = Array.from(els.reportReceptorAliasBody?.querySelectorAll("tr[data-id]") || [])
    .map((row) => String(row.dataset.id || "").trim())
    .filter(Boolean);
  const ligandOrder = Array.from(els.reportLigandAliasBody?.querySelectorAll("tr[data-id]") || [])
    .map((row) => String(row.dataset.id || "").trim())
    .filter(Boolean);

  Array.from(document.querySelectorAll(".report-meta-receptor-input")).forEach((input) => {
    const id = String(input.dataset.id || "").trim();
    if (!id) return;
    receptorLabels[id] = String(input.value || "").trim();
  });

  Array.from(document.querySelectorAll(".report-meta-ligand-input")).forEach((input) => {
    const id = String(input.dataset.id || "").trim();
    if (!id) return;
    ligandLabels[id] = String(input.value || "").trim();
  });

  return { mainType, receptorLabels, ligandLabels, receptorOrder, ligandOrder };
}

async function saveReportRootMetadata() {
  const sourcePath = ensureReportSourceValue();
  const { mainType, receptorLabels, ligandLabels, receptorOrder, ligandOrder } = collectRootMetadataForm();
  try {
    const res = await fetchJSON("/api/reports/root-metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_path: REPORT_DOCK_ROOT,
        source_path: sourcePath,
        main_type: mainType,
        receptor_labels: receptorLabels,
        ligand_labels: ligandLabels,
        receptor_order: receptorOrder,
        ligand_order: ligandOrder,
      }),
    });
    reportSourceMetadata = res.metadata || null;
    renderRootMetadataEditor();
    await fetchReports();
    closeReportMetaModal();
  } catch (err) {
    alert("Failed to save root metadata: " + err.message);
  }
}

async function resetReportRootMetadata() {
  const sourcePath = ensureReportSourceValue();
  if (!confirm("Reset all custom labels for this source?")) return;
  try {
    const res = await fetchJSON("/api/reports/root-metadata", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_path: REPORT_DOCK_ROOT,
        source_path: sourcePath,
        reset: true,
      }),
    });
    reportSourceMetadata = res.metadata || null;
    renderRootMetadataEditor();
    await fetchReports();
    closeReportMetaModal();
  } catch (err) {
    alert("Failed to reset root metadata: " + err.message);
  }
}

async function deleteReportSourceFolder(sourcePath) {
  const path = String(sourcePath || "").trim();
  if (!path) return;
  if (!confirm(`Delete this source folder?\n${path}\n\nThis will remove all contents under this folder.`)) return;
  try {
    const res = await fetchJSON("/api/reports/source/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_path: REPORT_DOCK_ROOT,
        source_path: path,
      }),
    });
    const nextSource = String(res.next_source_path || REPORT_DOCK_ROOT).trim() || REPORT_DOCK_ROOT;
    if (els.reportRootPath) {
      els.reportRootPath.value = nextSource;
    }
    if (els.reportOutputPath) {
      els.reportOutputPath.value = defaultReportOutputPath(nextSource);
    }
    if (els.reportDocRootPath) {
      const nextDocRoot = defaultReportOutputPath(nextSource);
      els.reportDocRootPath.value = nextDocRoot;
      reportDocRootPath = nextDocRoot;
    }
    reportSelectedReceptors = new Set();
    reportSelectedRuns = new Map();
    reportSelectedDocImages = new Set();
    reportDocImageOrder = [];
    reportDocManualOrder = false;
    reportFigureCaptionText = new Map();
    reportFigureCaptionCustom = new Set();
    reportExtraSections = [];
    reportFigureStartNumber = 1;
    closeReportMetaModal();
    await fetchReports();
  } catch (err) {
    alert("Failed to delete source folder: " + err.message);
  }
}

function renderSourceFoldersTable() {
  if (!els.reportSourceTableBody) return;
  els.reportSourceTableBody.innerHTML = "";
  if (!reportSourceFolders.length) {
    const helperText = reportLinkedError
      ? `No docking roots found. ${reportLinkedError}`
      : "No docking roots found under selected source.";
    els.reportSourceTableBody.innerHTML = `<tr><td colspan="7" class="helper">${helperText}</td></tr>`;
    return;
  }

  const folders = [...reportSourceFolders].sort((a, b) =>
    String(a.path || "").localeCompare(String(b.path || ""), undefined, { sensitivity: "base" })
  );

  folders.forEach((item) => {
    const tr = document.createElement("tr");
    const selected = item.path === reportCurrentSource || item.selected === true;
    const mainType = String(item.main_type || "").trim() || "-";
    tr.innerHTML = `
      <td style="font-weight:${selected ? 700 : 500}; color:${selected ? "var(--ink)" : "var(--muted)"};">${item.name}</td>
      <td>${mainType}</td>
      <td>${item.receptor_count ?? "-"}</td>
      <td>${item.ready_receptors ?? "-"}</td>
      <td>${item.docking_count ?? "-"}</td>
      <td style="font-family:'IBM Plex Mono', monospace; font-size:12px;">${item.path}</td>
      <td style="display:flex; gap:6px; flex-wrap:wrap;">
        <button class="secondary report-use-source" type="button" data-path="${item.path}" ${selected ? "disabled" : ""}>${selected ? "Selected" : "Use"}</button>
        <button class="secondary report-edit-source" type="button" data-path="${item.path}">Edit</button>
        <button class="secondary report-delete-source" type="button" data-path="${item.path}" style="color:#b91c1c; border-color:#fecaca;">Delete</button>
      </td>
    `;
    els.reportSourceTableBody.appendChild(tr);
  });

  Array.from(els.reportSourceTableBody.querySelectorAll(".report-use-source")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const selectedPath = btn.dataset.path || REPORT_DOCK_ROOT;
      if (els.reportRootPath) {
        els.reportRootPath.value = selectedPath;
      }
      if (els.reportOutputPath) {
        els.reportOutputPath.value = defaultReportOutputPath(selectedPath);
      }
      if (els.reportDocRootPath) {
        const docRoot = defaultReportOutputPath(selectedPath);
        els.reportDocRootPath.value = docRoot;
        reportDocRootPath = docRoot;
      }
      reportSelectedLinkedRoot = "";
      reportSelectedReceptors = new Set();
      reportSelectedRuns = new Map();
      reportSelectedDocImages = new Set();
      reportDocImageOrder = [];
      reportDocManualOrder = false;
      reportFigureCaptionText = new Map();
      reportFigureCaptionCustom = new Set();
      reportExtraSections = [];
      reportFigureStartNumber = 1;
      await fetchReports();
      setReportTab("images");
    });
  });

  Array.from(els.reportSourceTableBody.querySelectorAll(".report-edit-source")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const selectedPath = btn.dataset.path || REPORT_DOCK_ROOT;
      if (els.reportRootPath) {
        els.reportRootPath.value = selectedPath;
      }
      if (els.reportOutputPath) {
        els.reportOutputPath.value = defaultReportOutputPath(selectedPath);
      }
      if (els.reportDocRootPath) {
        const docRoot = defaultReportOutputPath(selectedPath);
        els.reportDocRootPath.value = docRoot;
        reportDocRootPath = docRoot;
      }
      reportSelectedReceptors = new Set();
      reportSelectedRuns = new Map();
      reportSelectedDocImages = new Set();
      reportDocImageOrder = [];
      reportDocManualOrder = false;
      reportFigureCaptionText = new Map();
      reportFigureCaptionCustom = new Set();
      reportExtraSections = [];
      reportFigureStartNumber = 1;
      await fetchReports();
      setReportTab("images");
      openReportMetaModal();
    });
  });

  Array.from(els.reportSourceTableBody.querySelectorAll(".report-delete-source")).forEach((btn) => {
    btn.addEventListener("click", async () => {
      await deleteReportSourceFolder(btn.dataset.path || "");
    });
  });
}

function renderReceptorTable() {
  if (!els.reportReceptorBody) return;
  els.reportReceptorBody.innerHTML = "";
  if (!reportReceptors.length) {
    els.reportReceptorBody.innerHTML =
      '<tr><td colspan="6" class="helper">No receptor rows found for selected docking root.</td></tr>';
    return;
  }

  const readyRows = reportReceptors.filter((row) => row.ready);
  if (reportSelectedReceptors.size === 0) {
    readyRows.forEach((row) => reportSelectedReceptors.add(row.id));
  }

  reportReceptors.forEach((row) => {
    const tr = document.createElement("tr");
    const checked = row.ready && reportSelectedReceptors.has(row.id);
    const ligandText = (row.valid_ligands_display || row.valid_ligands || []).join(", ") || "-";
    const receptorLabel = row.display_id || row.id;
    const runOptions = Array.isArray(row.run_options) ? row.run_options : [];
    const storedRun = reportSelectedRuns.get(row.id) || "";
    let selectedRun = runOptions.includes(storedRun) ? storedRun : (row.default_run || "");
    if (!selectedRun && runOptions.includes("run1")) {
      selectedRun = "run1";
    }
    if (!selectedRun && runOptions.length > 0) {
      selectedRun = runOptions[0];
    }
    const runSelectOptions = runOptions
      .map((runName) => `<option value="${runName}" ${selectedRun === runName ? "selected" : ""}>${runName}</option>`)
      .join("");
    const runCell = row.ready && runOptions.length > 0
      ? `<select class="report-run-select" data-receptor="${row.id}">${runSelectOptions}</select>`
      : `<span class="helper">-</span>`;

    tr.innerHTML = `
      <td style="text-align:center;">
        <input type="checkbox" class="report-receptor-checkbox" value="${row.id}" ${checked ? "checked" : ""} ${row.ready ? "" : "disabled"}>
      </td>
      <td style="font-weight:600;">${receptorLabel}</td>
      <td>${ligandText}</td>
      <td>${row.runs_per_ligand_min || 0}</td>
      <td>${runCell}</td>
      <td>${row.ready ? '<span class="status-chip">Ready</span>' : '<span class="status-chip" style="border-color:#fca5a5;color:#b91c1c;background:#fff1f2;">Missing Inputs</span>'}</td>
    `;
    els.reportReceptorBody.appendChild(tr);

    if (row.ready) {
      reportSelectedRuns.set(row.id, selectedRun || "");
    } else {
      reportSelectedRuns.delete(row.id);
    }
  });

  Array.from(document.querySelectorAll(".report-receptor-checkbox")).forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      if (event.target.checked) {
        reportSelectedReceptors.add(event.target.value);
      } else {
        reportSelectedReceptors.delete(event.target.value);
      }
    });
  });

  Array.from(document.querySelectorAll(".report-run-select")).forEach((selector) => {
    selector.addEventListener("change", (event) => {
      const receptorId = (event.target.dataset.receptor || "").trim();
      if (!receptorId) return;
      reportSelectedRuns.set(receptorId, (event.target.value || "").trim());
    });
  });

  if (els.selectAllReceptors) {
    const allReady = readyRows.length > 0 && readyRows.every((row) => reportSelectedReceptors.has(row.id));
    els.selectAllReceptors.checked = allReady;
    els.selectAllReceptors.onchange = (event) => {
      const checked = event.target.checked;
      Array.from(document.querySelectorAll(".report-receptor-checkbox")).forEach((checkbox) => {
        if (checkbox.disabled) return;
        checkbox.checked = checked;
        if (checked) {
          reportSelectedReceptors.add(checkbox.value);
        } else {
          reportSelectedReceptors.delete(checkbox.value);
        }
      });
    };
  }
}

function renderPlotTable() {
  if (!els.reportPlotBody) return;
  els.reportPlotBody.innerHTML = "";
  REPORT_PREDEFINED_PLOTS.forEach((plot) => {
    const checked = reportSelectedPlots.has(plot.id);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="text-align:center;">
        <input type="checkbox" class="report-plot-checkbox" value="${plot.id}" ${checked ? "checked" : ""}>
      </td>
      <td style="font-weight:600;">${plot.title}</td>
      <td>${plot.description}</td>
    `;
    els.reportPlotBody.appendChild(tr);
  });
  Array.from(document.querySelectorAll(".report-plot-checkbox")).forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      if (event.target.checked) {
        reportSelectedPlots.add(event.target.value);
      } else {
        reportSelectedPlots.delete(event.target.value);
      }
    });
  });
  if (els.selectAllPlots) {
    els.selectAllPlots.checked = REPORT_PREDEFINED_PLOTS.every((plot) => reportSelectedPlots.has(plot.id));
    els.selectAllPlots.onchange = (event) => {
      const checked = event.target.checked;
      Array.from(document.querySelectorAll(".report-plot-checkbox")).forEach((checkbox) => {
        checkbox.checked = checked;
      });
      reportSelectedPlots = checked
        ? new Set(REPORT_PREDEFINED_PLOTS.map((plot) => plot.id))
        : new Set();
    };
  }
}

function renderReportViews() {
  renderReports(reportRenderCatalog, els.reportImagesGallery, { emptyText: "No generated render images yet." });
  renderReports(reportPlotCatalog, els.reportGraphsGallery, { emptyText: "No generated predefined plots yet." });
  renderReportDocStatus(reportDocInfo);
  renderSourceFoldersTable();
  renderReceptorTable();
  renderPlotTable();

  const validation = reportDockValidation || {};
  const dockRootsFound = validation.linked_roots_found ?? reportSourceFolders.length;
  const selectedReady = validation.selected_ready_receptors ?? reportReceptors.filter((row) => row.ready).length;
  const selectedDockings = validation.selected_docking_count ?? 0;
  const validationLine = `Dock roots: ${dockRootsFound} | Ready receptors: ${selectedReady} | Docking count: ${selectedDockings}`;
  const mainType = String(reportSourceMetadata?.main_type || "").trim();
  if (els.reportValidationBox) {
    els.reportValidationBox.innerHTML = `
      <span class="status-chip">Inside dock: ${validation.inside_dock === false ? "No" : "Yes"}</span>
      <span class="status-chip">Detected roots: ${dockRootsFound}</span>
      <span class="status-chip">Ready receptors: ${selectedReady}</span>
      <span class="status-chip">Dockings: ${selectedDockings}</span>
    `;
  }
  const extraError = reportLinkedError ? ` | Warning: ${reportLinkedError}` : "";
  setReportMetaText(
    `Root: ${reportCurrentRoot} | Source: ${reportCurrentSource} | Type: ${mainType || "-"} | Linked: ${reportLinkedRoot || "-"} | ${validationLine} | Last update: ${reportLastUpdated || "-"}${extraError}`
  );
}

function renderReportDocStatus(docInfo) {
  if (!els.reportDocContent) return;
  if (!docInfo || !docInfo.exists) {
    els.reportDocContent.innerHTML = '<div class="helper">Build report to generate <code>docking_report_mvp.docx</code>.</div>';
    return;
  }
  const rootPath = encodeURIComponent(REPORT_DOCK_ROOT);
  const sourcePath = encodeURIComponent(ensureReportSourceValue());
  const outputPath = encodeURIComponent(ensureReportOutputValue());
  const downloadUrl = docInfo.download_url || `/api/reports/doc?root_path=${rootPath}&source_path=${sourcePath}&output_path=${outputPath}`;
  const docPath = docInfo.path || "report_outputs/docking_report_mvp.docx";
  els.reportDocContent.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
      <div>
        <div style="font-weight:600; color:var(--ink);">Report ready</div>
        <div class="helper">${docPath}</div>
      </div>
      <a class="primary" href="${downloadUrl}" style="text-decoration:none;" download>Download .docx</a>
    </div>
  `;
}

async function deleteReportImage(imagePath) {
  const pathText = String(imagePath || "").trim();
  if (!pathText) return;
  if (!confirm(`Delete image?\n${pathText}`)) return;
  try {
    await fetchJSON("/api/reports/image/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_path: REPORT_DOCK_ROOT,
        source_path: ensureReportSourceValue(),
        output_path: ensureReportOutputValue(),
        images_root_path: REPORT_DOCK_ROOT,
        path: pathText,
      }),
    });
    await fetchReports();
    await fetchReportDocImages();
  } catch (err) {
    alert("Failed to delete image: " + err.message);
  }
}

async function deleteAllReportImages(scope) {
  const scopeText = scope === "render" ? "render images" : "plot images";
  if (!confirm(`Delete all ${scopeText} in current output folder?`)) return;
  try {
    const res = await fetchJSON("/api/reports/images/delete-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root_path: REPORT_DOCK_ROOT,
        source_path: ensureReportSourceValue(),
        output_path: ensureReportOutputValue(),
        scope,
      }),
    });
    await fetchReports();
    await fetchReportDocImages({ resetSelection: true });
    if ((res.deleted_count || 0) > 0) {
      alert(`Deleted ${res.deleted_count} ${scopeText}.`);
    }
  } catch (err) {
    alert(`Failed to delete all ${scopeText}: ${err.message}`);
  }
}

function getOrderedReportDocImages() {
  const imageByPath = new Map();
  reportDocImages.forEach((item) => {
    const path = String(item.path || "").trim();
    if (path) {
      imageByPath.set(path, item);
    }
  });

  const ordered = [];
  reportDocImageOrder.forEach((path) => {
    const item = imageByPath.get(path);
    if (!item) return;
    ordered.push(item);
    imageByPath.delete(path);
  });
  Array.from(imageByPath.values()).forEach((item) => ordered.push(item));
  return ordered;
}

function reportDocImagePriority(item) {
  const category = String(item?.category || "").trim().toLowerCase();
  const raw = `${String(item?.name || "")} ${String(item?.path || "")} ${String(item?.root_relative_path || "")}`.toLowerCase();
  if (category === "render" || raw.includes("/render_images/") || raw.includes("\\render_images\\")) return 0;
  if (category === "plot" || raw.includes("/plots/") || raw.includes("\\plots\\")) return 1;
  return 2;
}

function extractReportReceptorId(textValue) {
  const text = String(textValue || "");
  const matched = /(?:^|[_/\\-])(D\d+)(?:[_/\\-]|$)/i.exec(text);
  return matched ? String(matched[1] || "").toUpperCase() : "";
}

function getReportPlotOrderIndex(item) {
  const text = `${String(item?.name || "")} ${String(item?.path || "")}`.toLowerCase();
  if (text.includes("affinity_boxplot")) return 0;
  if (text.includes("run_frequency_heatmap")) return 1;
  if (text.includes("common_residue_heatmap")) return 2;
  if (text.includes("interaction_stacked_bar")) return 3;
  return 99;
}

function sortReportDocImagesForDefault(items) {
  const receptorOrder = Array.isArray(reportSourceMetadata?.receptor_order) ? reportSourceMetadata.receptor_order : [];
  const receptorIndex = new Map(receptorOrder.map((id, idx) => [String(id || "").toUpperCase(), idx]));
  return [...items].sort((a, b) => {
    const priorityDiff = reportDocImagePriority(a) - reportDocImagePriority(b);
    if (priorityDiff !== 0) return priorityDiff;
    if (priorityDiff === 0 && reportDocImagePriority(a) === 0) {
      const recA = extractReportReceptorId(String(a?.name || ""));
      const recB = extractReportReceptorId(String(b?.name || ""));
      const recIdxA = receptorIndex.has(recA) ? receptorIndex.get(recA) : 10 ** 6;
      const recIdxB = receptorIndex.has(recB) ? receptorIndex.get(recB) : 10 ** 6;
      if (recIdxA !== recIdxB) return recIdxA - recIdxB;
    }
    if (priorityDiff === 0 && reportDocImagePriority(a) === 1) {
      const plotIdxA = getReportPlotOrderIndex(a);
      const plotIdxB = getReportPlotOrderIndex(b);
      if (plotIdxA !== plotIdxB) return plotIdxA - plotIdxB;
    }
    const timeA = Number(a?.mtime || 0);
    const timeB = Number(b?.mtime || 0);
    if (timeA !== timeB) return timeB - timeA;
    const pathA = String(a?.root_relative_path || a?.path || a?.name || "");
    const pathB = String(b?.root_relative_path || b?.path || b?.name || "");
    return pathA.localeCompare(pathB, undefined, { sensitivity: "base" });
  });
}

function updateReportDocSelectionSummary() {
  if (els.reportDocSelectedCount) {
    els.reportDocSelectedCount.textContent = `${reportSelectedDocImages.size} selected`;
  }
  if (els.selectAllDocImages) {
    const paths = reportDocImageOrder.filter(Boolean);
    const allChecked = paths.length > 0 && paths.every((path) => reportSelectedDocImages.has(path));
    els.selectAllDocImages.checked = allChecked;
  }
}

function normalizeReportDocImageOrder({ resetOrder = false } = {}) {
  const validPathList = sortReportDocImagesForDefault(reportDocImages)
    .map((item) => String(item.path || "").trim())
    .filter(Boolean);
  if (resetOrder || !reportDocManualOrder || reportDocImageOrder.length === 0) {
    reportDocImageOrder = [...validPathList];
    if (resetOrder) {
      reportDocManualOrder = false;
    }
    const validSet = new Set(validPathList);
    reportFigureCaptionText = new Map(
      Array.from(reportFigureCaptionText.entries()).filter(([path]) => validSet.has(String(path || "").trim()))
    );
    reportFigureCaptionCustom = new Set(
      Array.from(reportFigureCaptionCustom).filter((path) => validSet.has(String(path || "").trim()))
    );
    return new Set(validPathList);
  }
  const validSet = new Set(validPathList);
  const nextOrder = reportDocImageOrder.filter((path) => validSet.has(path));
  const seen = new Set(nextOrder);
  validPathList.forEach((path) => {
    if (seen.has(path)) return;
    nextOrder.push(path);
    seen.add(path);
  });
  reportDocImageOrder = nextOrder;
  reportFigureCaptionText = new Map(
    Array.from(reportFigureCaptionText.entries()).filter(([path]) => validSet.has(String(path || "").trim()))
  );
  reportFigureCaptionCustom = new Set(
    Array.from(reportFigureCaptionCustom).filter((path) => validSet.has(String(path || "").trim()))
  );
  return validSet;
}

function normalizeReportExtraSections(rawSections) {
  if (!Array.isArray(rawSections)) return [];
  return rawSections
    .map((row) => ({
      title: String(row?.title || "").trim(),
      body: String(row?.body || "").trim(),
    }))
    .filter((row) => row.title || row.body);
}

function parseFigureStartFromInput() {
  const rawValue = Number(els.reportFigureStartNumber?.value || reportFigureStartNumber || 1);
  const next = Number.isFinite(rawValue) ? Math.max(1, Math.min(999, Math.round(rawValue))) : 1;
  reportFigureStartNumber = next;
  if (els.reportFigureStartNumber) {
    els.reportFigureStartNumber.value = String(next);
  }
  return next;
}

function renderReportExtraSectionsEditor() {
  if (!els.reportExtraSectionsEditor) return;
  els.reportExtraSectionsEditor.innerHTML = "";
  if (!Array.isArray(reportExtraSections) || reportExtraSections.length === 0) {
    const helper = document.createElement("div");
    helper.className = "helper";
    helper.textContent = 'No additional sections. Click "Add Heading/Text" to append custom blocks.';
    els.reportExtraSectionsEditor.appendChild(helper);
    return;
  }
  reportExtraSections.forEach((section, idx) => {
    const row = document.createElement("div");
    row.className = "report-extra-section-row";

    const actions = document.createElement("div");
    actions.className = "report-extra-section-actions";
    const removeBtn = document.createElement("button");
    removeBtn.className = "secondary report-remove-extra-section";
    removeBtn.type = "button";
    removeBtn.dataset.index = String(idx);
    removeBtn.style.color = "#b91c1c";
    removeBtn.style.borderColor = "#fecaca";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => {
      const removeIndex = Number(removeBtn.dataset.index || -1);
      if (!Number.isInteger(removeIndex) || removeIndex < 0 || removeIndex >= reportExtraSections.length) return;
      reportExtraSections.splice(removeIndex, 1);
      renderReportExtraSectionsEditor();
    });
    actions.appendChild(removeBtn);

    const titleInput = document.createElement("input");
    titleInput.type = "text";
    titleInput.className = "report-extra-section-title";
    titleInput.dataset.index = String(idx);
    titleInput.placeholder = "Heading";
    titleInput.value = String(section.title || "");
    titleInput.addEventListener("input", (event) => {
      const inputIndex = Number(event.target.dataset.index || -1);
      if (!Number.isInteger(inputIndex) || inputIndex < 0 || inputIndex >= reportExtraSections.length) return;
      reportExtraSections[inputIndex].title = String(event.target.value || "");
    });

    const bodyInput = document.createElement("textarea");
    bodyInput.className = "report-extra-section-body";
    bodyInput.dataset.index = String(idx);
    bodyInput.rows = 3;
    bodyInput.placeholder = "Section text";
    bodyInput.value = String(section.body || "");
    bodyInput.addEventListener("input", (event) => {
      const inputIndex = Number(event.target.dataset.index || -1);
      if (!Number.isInteger(inputIndex) || inputIndex < 0 || inputIndex >= reportExtraSections.length) return;
      reportExtraSections[inputIndex].body = String(event.target.value || "");
    });

    row.appendChild(actions);
    row.appendChild(titleInput);
    row.appendChild(bodyInput);
    els.reportExtraSectionsEditor.appendChild(row);
  });
}

function syncReportDocConfigFromMetadata() {
  const reportCfg = reportSourceMetadata?.report || {};
  const startVal = Number(reportCfg.figure_start_number ?? reportSourceMetadata?.figure_start_number ?? 1);
  reportFigureStartNumber = Number.isFinite(startVal) ? Math.max(1, Math.min(999, Math.round(startVal))) : 1;
  if (els.reportFigureStartNumber) {
    els.reportFigureStartNumber.value = String(reportFigureStartNumber);
  }

  reportExtraSections = normalizeReportExtraSections(reportCfg.extra_sections ?? reportSourceMetadata?.extra_sections ?? []);

  const captionMap = reportCfg.figure_caption_overrides ?? reportSourceMetadata?.figure_caption_overrides ?? {};
  const normalizedCaptionMap = new Map();
  Object.entries(captionMap || {}).forEach(([key, value]) => {
    const k = String(key || "").trim();
    const v = String(value || "").trim();
    if (!k || !v) return;
    normalizedCaptionMap.set(k, v);
  });
  reportFigureCaptionText = normalizedCaptionMap;
  reportFigureCaptionCustom = new Set(Array.from(normalizedCaptionMap.keys()));

  renderReportExtraSectionsEditor();
}

async function saveReportDocConfig({ silent = false, refresh = false } = {}) {
  parseFigureStartFromInput();
  reportExtraSections = normalizeReportExtraSections(reportExtraSections);
  const captionOverrides = {};
  Array.from(reportFigureCaptionCustom).forEach((path) => {
    const key = String(path || "").trim();
    const text = String(reportFigureCaptionText.get(path) || "").trim();
    if (!key || !text) return;
    captionOverrides[key] = text;
  });

  const payload = {
    root_path: REPORT_DOCK_ROOT,
    source_path: ensureReportSourceValue(),
    figure_start_number: reportFigureStartNumber,
    extra_sections: reportExtraSections.map((row) => ({ title: row.title, body: row.body })),
    figure_caption_overrides: captionOverrides,
  };
  const res = await fetchJSON("/api/reports/doc-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  reportFigureStartNumber = Number(res.figure_start_number || reportFigureStartNumber || 1);
  if (els.reportFigureStartNumber) {
    els.reportFigureStartNumber.value = String(reportFigureStartNumber);
  }
  reportExtraSections = normalizeReportExtraSections(res.extra_sections || reportExtraSections);
  const nextCaptionMap = new Map();
  Object.entries(res.figure_caption_overrides || {}).forEach(([key, value]) => {
    const k = String(key || "").trim();
    const v = String(value || "").trim();
    if (!k || !v) return;
    nextCaptionMap.set(k, v);
  });
  reportFigureCaptionText = nextCaptionMap;
  reportFigureCaptionCustom = new Set(Array.from(nextCaptionMap.keys()));
  if (reportSourceMetadata && typeof reportSourceMetadata === "object") {
    reportSourceMetadata.figure_start_number = reportFigureStartNumber;
    reportSourceMetadata.extra_sections = reportExtraSections;
    reportSourceMetadata.figure_caption_overrides = Object.fromEntries(nextCaptionMap.entries());
    reportSourceMetadata.report = {
      figure_start_number: reportFigureStartNumber,
      extra_sections: reportExtraSections,
      figure_caption_overrides: Object.fromEntries(nextCaptionMap.entries()),
    };
  }
  renderReportExtraSectionsEditor();
  if (refresh) {
    await fetchReports();
  } else {
    renderReportDocImageGallery();
  }
  if (!silent) {
    alert("Report layout saved.");
  }
}

function buildReportFigureCaption(img, figureIndex) {
  const figureNo = Math.max(1, Number(reportFigureStartNumber || 1) + Number(figureIndex || 1) - 1);
  const rawName = String(img?.name || "");
  const rawPath = String(img?.root_relative_path || img?.path || rawName);
  const lower = `${rawName} ${rawPath}`.toLowerCase();
  const mainType = String(reportSourceMetadata?.main_type || "").trim();
  const receptorRows = Array.isArray(reportSourceMetadata?.receptors) ? reportSourceMetadata.receptors : [];
  const ligandRows = Array.isArray(reportSourceMetadata?.ligands) ? reportSourceMetadata.ligands : [];
  const receptorOrder = Array.isArray(reportSourceMetadata?.receptor_order) ? reportSourceMetadata.receptor_order : [];

  const receptorLabelMap = new Map();
  receptorRows.forEach((row) => {
    const id = String(row?.id || "").trim().toUpperCase();
    const label = String(row?.label || row?.id || "").trim();
    if (id) receptorLabelMap.set(id, label || id);
  });

  const ligandLabels = ligandRows
    .map((row) => String(row?.label || row?.id || "").trim())
    .filter(Boolean)
    .slice(0, 4);
  const ligandClause = ligandLabels.map((name, idx) => `${String.fromCharCode(65 + idx)}) ${name}`).join(", ");

  const receptorMatch = /(?:^|[_/\\-])(D\d+)(?:[_/\\-]|$)/i.exec(rawName || rawPath);
  const receptorId = receptorMatch ? receptorMatch[1].toUpperCase() : "";
  let receptorLabel = receptorLabelMap.get(receptorId) || receptorId;
  if (!receptorLabel && receptorOrder.length) {
    const first = String(receptorOrder[0] || "").toUpperCase();
    receptorLabel = receptorLabelMap.get(first) || first;
  }
  const receptorListText = receptorOrder
    .map((id) => receptorLabelMap.get(String(id || "").toUpperCase()) || String(id || ""))
    .filter(Boolean)
    .join(", ");
  const typeText = mainType ? `${mainType} receptor subtypes` : (receptorListText || "selected receptor subtypes");

  if (String(img?.category || "").toLowerCase() === "render" || lower.includes("/render_images/") || lower.includes("\\render_images\\")) {
    const ligandText = ligandClause ? ` ${ligandClause}` : "";
    return `Figure ${figureNo}. Final docking and interaction visualization for ${receptorLabel || "selected receptor"}.${ligandText}`;
  }
  if (lower.includes("affinity_boxplot")) {
    const ligandText = ligandLabels.join(", ") || "selected ligands";
    return `Figure ${figureNo}. Box plots illustrating the distribution of docking scores for ${ligandText} against ${typeText}. (Top) Mean binding affinities (kcal/mol) and standard deviations are tabulated.`;
  }
  if (lower.includes("run_frequency_heatmap")) {
    const ligandText = ligandLabels.join(", ") || "selected ligands";
    return `Figure ${figureNo}. Interacting residues obtained via PLIP across ${typeText} for ${ligandText}. Cell values (0-5) indicate in how many runs each residue was detected as interacting.`;
  }
  if (lower.includes("common_residue_heatmap")) {
    const ligandText = ligandLabels.join(", ") || "selected ligands";
    return `Figure ${figureNo}. Common interacting residues (frequency = 5 for each run) by dominant interaction type across runs for ${ligandText} in ${typeText}.`;
  }
  if (lower.includes("interaction_stacked_bar")) {
    const ligandText = ligandLabels.join(", ") || "selected ligands";
    return `Figure ${figureNo}. Common stacked counts of PLIP interaction instances for ${ligandText} across ${typeText}, using the best-scoring run for each receptor-ligand pair.`;
  }
  return `Figure ${figureNo}. ${String(img?.name || "Generated figure")}`;
}

function enableReportDocCardDrag(container) {
  if (!container) return;
  const cards = Array.from(container.querySelectorAll(".report-doc-card[data-path]"));
  cards.forEach((card) => {
    const handle = card.querySelector(".report-doc-handle");
    const image = card.querySelector(".report-image");
    if (image) {
      image.draggable = false;
    }
    if (!handle) return;
    handle.draggable = true;
    handle.addEventListener("dragstart", (event) => {
      reportDocDraggingCard = card;
      card.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", card.dataset.path || "");
      }
    });
    handle.addEventListener("dragend", () => {
      cards.forEach((item) => {
        item.classList.remove("dragging", "drop-before", "drop-after");
      });
      reportDocDraggingCard = null;
      reportDocImageOrder = Array.from(container.querySelectorAll(".report-doc-card[data-path]"))
        .map((el) => String(el.dataset.path || "").trim())
        .filter(Boolean);
      reportDocManualOrder = true;
      renderReportDocImageGallery();
    });

    card.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (!reportDocDraggingCard || reportDocDraggingCard === card) return;
      const rect = card.getBoundingClientRect();
      const insertBefore = event.clientY <= rect.top + rect.height / 2;
      cards.forEach((item) => {
        if (item !== card) {
          item.classList.remove("drop-before", "drop-after");
        }
      });
      card.classList.toggle("drop-before", insertBefore);
      card.classList.toggle("drop-after", !insertBefore);
      container.insertBefore(reportDocDraggingCard, insertBefore ? card : card.nextSibling);
    });
    card.addEventListener("dragleave", () => {
      card.classList.remove("drop-before", "drop-after");
    });
    card.addEventListener("drop", (event) => {
      event.preventDefault();
      card.classList.remove("drop-before", "drop-after");
    });
  });

  if (!container.dataset.dragScrollBound) {
    container.dataset.dragScrollBound = "1";
    container.addEventListener("dragover", (event) => {
      if (!reportDocDraggingCard) return;
      const rect = container.getBoundingClientRect();
      const scrollZone = 64;
      if (event.clientY < rect.top + scrollZone) {
        container.scrollTop -= 18;
      } else if (event.clientY > rect.bottom - scrollZone) {
        container.scrollTop += 18;
      }
    });
  }
}

function renderReportDocImageGallery() {
  if (!els.reportDocImageGallery) return;
  els.reportDocImageGallery.innerHTML = "";

  const orderedImages = getOrderedReportDocImages();
  if (!Array.isArray(orderedImages) || orderedImages.length === 0) {
    els.reportDocImageGallery.innerHTML = '<div class="helper">No images found in selected report image root.</div>';
    if (els.reportDocSelectedCount) {
      els.reportDocSelectedCount.textContent = "0 selected";
    }
    if (els.selectAllDocImages) {
      els.selectAllDocImages.checked = false;
    }
    return;
  }

  orderedImages.forEach((img, idx) => {
    const imagePath = String(img.path || "").trim();
    if (!imagePath) return;
    const figureNo = Math.max(1, Number(reportFigureStartNumber || 1) + idx);
    const checked = reportSelectedDocImages.has(imagePath);
    const imageUrl = `/api/reports/image/${encodeURIComponent(imagePath)}`;
    const figureCaption = buildReportFigureCaption(img, idx + 1);

    const card = document.createElement("div");
    card.className = "report-card report-doc-card";
    card.dataset.path = imagePath;

    const imgContainer = document.createElement("div");
    imgContainer.className = "report-image-container";
    const imageEl = document.createElement("img");
    imageEl.className = "report-image";
    imageEl.src = imageUrl;
    imageEl.alt = img.name || "image";
    imageEl.loading = "lazy";
    imageEl.addEventListener("click", () => openReportOverlay(imageUrl, img.name || "image"));
    imgContainer.appendChild(imageEl);

    const meta = document.createElement("div");
    meta.className = "report-meta";

    const metaHeader = document.createElement("div");
    metaHeader.className = "report-doc-meta-header";

    const leftHeader = document.createElement("div");
    leftHeader.style.display = "inline-flex";
    leftHeader.style.alignItems = "center";
    leftHeader.style.gap = "8px";

    const rank = document.createElement("span");
    rank.className = "report-doc-rank";
    rank.textContent = `F${figureNo}`;

    const selectLabel = document.createElement("label");
    selectLabel.style.display = "inline-flex";
    selectLabel.style.alignItems = "center";
    selectLabel.style.gap = "6px";
    selectLabel.style.fontSize = "12px";
    selectLabel.style.fontWeight = "600";
    selectLabel.style.color = "var(--ink)";

    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "report-doc-image-checkbox";
    box.dataset.path = imagePath;
    box.checked = checked;
    const boxLabel = document.createElement("span");
    boxLabel.textContent = "Use";
    selectLabel.appendChild(box);
    selectLabel.appendChild(boxLabel);

    leftHeader.appendChild(rank);
    leftHeader.appendChild(selectLabel);

    const handle = document.createElement("span");
    handle.className = "report-order-handle report-doc-handle";
    handle.title = "Drag to reorder";
    handle.textContent = "::";

    metaHeader.appendChild(leftHeader);
    metaHeader.appendChild(handle);

    const title = document.createElement("div");
    title.className = "report-title";
    const titleText = String(img.root_relative_path || img.name || imagePath);
    title.title = titleText;
    title.textContent = titleText;

    const category = document.createElement("div");
    category.className = "report-category";
    category.textContent = `${formatBytes(img.size_bytes || 0)} | ${formatReportTime(img.mtime || 0)}`;

    const actions = document.createElement("div");
    actions.className = "report-actions";

    const openBtn = document.createElement("button");
    openBtn.className = "secondary report-doc-open";
    openBtn.type = "button";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", () => {
      openReportOverlay(imageUrl, img.name || "image");
    });

    const dlLink = document.createElement("a");
    dlLink.className = "secondary";
    dlLink.href = imageUrl;
    dlLink.download = img.name || "image.png";
    dlLink.textContent = "Download";
    dlLink.style.textDecoration = "none";
    dlLink.style.display = "inline-flex";
    dlLink.style.alignItems = "center";

    const delBtn = document.createElement("button");
    delBtn.className = "secondary report-doc-delete";
    delBtn.type = "button";
    delBtn.dataset.path = imagePath;
    delBtn.textContent = "Delete";
    delBtn.style.color = "#b91c1c";
    delBtn.style.borderColor = "#fecaca";
    delBtn.addEventListener("click", async () => {
      await deleteReportImage(imagePath);
    });

    actions.appendChild(openBtn);
    actions.appendChild(dlLink);
    actions.appendChild(delBtn);

    const captionWrap = document.createElement("div");
    captionWrap.className = "report-caption-editor";
    const captionHeader = document.createElement("div");
    captionHeader.className = "report-caption-header";
    const captionLabel = document.createElement("span");
    captionLabel.className = "helper";
    captionLabel.textContent = `Figure ${figureNo} caption`;
    const autoBtn = document.createElement("button");
    autoBtn.className = "secondary report-caption-auto-btn";
    autoBtn.type = "button";
    autoBtn.textContent = "Auto";
    captionHeader.appendChild(captionLabel);
    captionHeader.appendChild(autoBtn);

    const captionInput = document.createElement("textarea");
    captionInput.className = "report-caption-input";
    captionInput.rows = 3;
    captionInput.dataset.path = imagePath;
    captionInput.dataset.defaultCaption = figureCaption;
    const savedCaption = String(reportFigureCaptionText.get(imagePath) || "").trim();
    const captionText = reportFigureCaptionCustom.has(imagePath) && savedCaption ? savedCaption : figureCaption;
    captionInput.value = captionText;
    if (!reportFigureCaptionCustom.has(imagePath)) {
      reportFigureCaptionText.set(imagePath, figureCaption);
    }

    captionInput.addEventListener("input", () => {
      const nextText = String(captionInput.value || "").trim();
      const defaultCaption = String(captionInput.dataset.defaultCaption || "").trim();
      if (!nextText || nextText === defaultCaption) {
        reportFigureCaptionCustom.delete(imagePath);
        reportFigureCaptionText.set(imagePath, defaultCaption);
        return;
      }
      reportFigureCaptionCustom.add(imagePath);
      reportFigureCaptionText.set(imagePath, nextText);
    });

    autoBtn.addEventListener("click", () => {
      reportFigureCaptionCustom.delete(imagePath);
      reportFigureCaptionText.set(imagePath, figureCaption);
      captionInput.value = figureCaption;
    });

    captionWrap.appendChild(captionHeader);
    captionWrap.appendChild(captionInput);

    box.addEventListener("change", (event) => {
      if (event.target.checked) {
        reportSelectedDocImages.add(imagePath);
      } else {
        reportSelectedDocImages.delete(imagePath);
      }
      updateReportDocSelectionSummary();
    });

    meta.appendChild(metaHeader);
    meta.appendChild(title);
    meta.appendChild(category);
    meta.appendChild(actions);
    meta.appendChild(captionWrap);

    card.appendChild(imgContainer);
    card.appendChild(meta);
    els.reportDocImageGallery.appendChild(card);
  });

  enableReportDocCardDrag(els.reportDocImageGallery);
  updateReportDocSelectionSummary();
}

async function fetchReportDocImages({ resetSelection = false } = {}) {
  const rootPath = REPORT_DOCK_ROOT;
  const sourcePath = ensureReportSourceValue();
  const outputPath = ensureReportOutputValue();
  const imagesRoot = ensureReportDocRootValue();
  const query = new URLSearchParams({
    root_path: rootPath,
    source_path: sourcePath,
    output_path: outputPath,
    images_root_path: imagesRoot,
  });
  const data = await fetchJSON(`/api/reports/images?${query.toString()}`);
  reportDocRootPath = data.images_root_path || imagesRoot;
  if (els.reportDocRootPath && reportDocRootPath) {
    els.reportDocRootPath.value = reportDocRootPath;
  }
  reportDocImages = Array.isArray(data.images) ? data.images : [];

  const validPaths = normalizeReportDocImageOrder({ resetOrder: resetSelection });
  if (resetSelection || reportSelectedDocImages.size === 0) {
    reportSelectedDocImages = new Set(validPaths);
  } else {
    reportSelectedDocImages = new Set(Array.from(reportSelectedDocImages).filter((path) => validPaths.has(path)));
    if (reportSelectedDocImages.size === 0) {
      reportSelectedDocImages = new Set(validPaths);
    }
  }
  renderReportDocImageGallery();
}

async function fetchReports() {
  if (els.reportImagesGallery) {
    els.reportImagesGallery.innerHTML = '<div class="helper">Loading generated render images...</div>';
  }
  if (els.reportGraphsGallery) {
    els.reportGraphsGallery.innerHTML = '<div class="helper">Loading generated plot images...</div>';
  }

  const rootPath = REPORT_DOCK_ROOT;
  const sourcePath = ensureReportSourceValue();
  const outputPath = ensureReportOutputValue();
  const query = new URLSearchParams({
    root_path: rootPath,
    source_path: sourcePath,
    output_path: outputPath,
  });
  const url = `/api/reports/list?${query.toString()}`;

  try {
    const prevDocRoot = reportDocRootPath || (els.reportDocRootPath ? String(els.reportDocRootPath.value || "").trim() : "");
    const data = await fetchJSON(url);
    reportRenderCatalog = data.render_images || [];
    reportPlotCatalog = data.plot_images || [];
    reportSummary = data.summary || null;
    reportCurrentRoot = data.root_path || rootPath;
    reportCurrentSource = data.source_path || sourcePath;
    reportCurrentOutput = data.output_path || outputPath;
    reportSourceFolders = data.source_folders || [];
    reportReceptors = data.receptors || [];
    reportLinkedRoot = data.linked_root_path || "";
    reportLinkedError = data.linked_error || "";
    reportDockValidation = data.dock_validation || null;
    reportSourceMetadata = data.source_metadata || null;
    reportDocInfo = data.report_doc || null;
    reportLastUpdated = new Date().toLocaleTimeString();
    reportSelectedLinkedRoot = "";
    if (els.reportRootPath && reportCurrentSource) els.reportRootPath.value = reportCurrentSource;
    if (els.reportOutputPath && reportCurrentOutput) els.reportOutputPath.value = reportCurrentOutput;
    const defaultRows = Array.isArray(data.default_receptors) ? data.default_receptors : [];
    if (reportSelectedReceptors.size === 0 && defaultRows.length > 0) {
      reportSelectedReceptors = new Set(defaultRows);
    } else {
      const valid = new Set((reportReceptors || []).filter((row) => row.ready).map((row) => row.id));
      reportSelectedReceptors = new Set(Array.from(reportSelectedReceptors).filter((id) => valid.has(id)));
      if (reportSelectedReceptors.size === 0) {
        defaultRows.forEach((id) => reportSelectedReceptors.add(id));
      }
    }

    const nextRunSelection = new Map();
    reportReceptors.forEach((row) => {
      if (!row.ready) return;
      const options = Array.isArray(row.run_options) ? row.run_options : [];
      const prev = (reportSelectedRuns.get(row.id) || "").trim();
      let selectedRun = prev;
      if (selectedRun && options.length && !options.includes(selectedRun)) {
        selectedRun = "";
      }
      if (!selectedRun) {
        selectedRun = (row.default_run || "").trim();
      }
      nextRunSelection.set(row.id, selectedRun);
    });
    reportSelectedRuns = nextRunSelection;

    if (!prevDocRoot) {
      reportDocRootPath = reportCurrentOutput || outputPath;
      if (els.reportDocRootPath) {
        els.reportDocRootPath.value = reportDocRootPath;
      }
    } else {
      reportDocRootPath = prevDocRoot;
      if (els.reportDocRootPath) {
        els.reportDocRootPath.value = prevDocRoot;
      }
    }

    updateReportStats(reportSummary);
    renderReportViews();
    renderRootMetadataEditor();
    syncReportDocConfigFromMetadata();
    try {
      await fetchReportDocImages();
    } catch (docErr) {
      reportDocImages = [];
      reportSelectedDocImages = new Set();
      reportDocImageOrder = [];
      reportDocManualOrder = false;
      reportFigureCaptionText = new Map();
      reportFigureCaptionCustom = new Set();
      reportExtraSections = [];
      reportFigureStartNumber = 1;
      renderReportExtraSectionsEditor();
      renderReportDocImageGallery();
      if (els.reportDocContent) {
        els.reportDocContent.innerHTML = `<div class="helper" style="color:var(--danger)">Failed to load report images: ${docErr.message}</div>`;
      }
    }
  } catch (err) {
    reportRenderCatalog = [];
    reportPlotCatalog = [];
    reportSummary = { total: 0, rendered: 0, plots: 0, report_ready: false };
    reportSourceFolders = [];
    reportReceptors = [];
    reportLinkedRoot = "";
    reportSelectedLinkedRoot = "";
    reportSelectedRuns = new Map();
    reportSourceMetadata = null;
    reportDocInfo = null;
    reportDocImages = [];
    reportSelectedDocImages = new Set();
    reportDocImageOrder = [];
    reportDocManualOrder = false;
    reportFigureCaptionText = new Map();
    reportFigureCaptionCustom = new Set();
    reportExtraSections = [];
    reportFigureStartNumber = 1;
    reportLinkedError = String(err.message || "");
    reportDockValidation = null;
    updateReportStats(reportSummary);
    const msg = `<div class="helper" style="color:var(--danger)">Failed to load: ${err.message}</div>`;
    if (els.reportImagesGallery) els.reportImagesGallery.innerHTML = msg;
    if (els.reportGraphsGallery) els.reportGraphsGallery.innerHTML = msg;
    renderReportDocStatus(null);
    renderSourceFoldersTable();
    renderReceptorTable();
    renderPlotTable();
    renderRootMetadataEditor();
    renderReportExtraSectionsEditor();
    if (els.reportValidationBox) els.reportValidationBox.innerHTML = "";
    setReportMetaText("Failed to load generated report outputs.");
    renderReportDocImageGallery();
  }
}

function renderReports(images, container, options = {}) {
  if (!container) return;
  const emptyText = options.emptyText || "No images found.";
  container.innerHTML = "";
  if (images.length === 0) {
    container.innerHTML = `<div class="helper">${emptyText}</div>`;
    return;
  }

  images.forEach((img) => {
    const card = document.createElement("div");
    card.className = "report-card";

    const imgContainer = document.createElement("div");
    imgContainer.className = "report-image-container";

    const imageUrl = `/api/reports/image/${encodeURIComponent(img.path)}`;
    const elImg = document.createElement("img");
    elImg.className = "report-image";
    elImg.src = imageUrl;
    elImg.alt = img.name;
    elImg.loading = "lazy";
    elImg.addEventListener("click", () => openReportOverlay(imageUrl, img.name));
    imgContainer.appendChild(elImg);

    const meta = document.createElement("div");
    meta.className = "report-meta";

    const title = document.createElement("div");
    title.className = "report-title";
    title.title = img.name;
    title.textContent = img.name;

    const category = document.createElement("div");
    category.className = "report-category";
    category.textContent = `${img.category || "other"} | ${formatBytes(img.size_bytes || 0)} | ${formatReportTime(img.mtime || 0)}`;

    const actions = document.createElement("div");
    actions.className = "report-actions";

    const openBtn = document.createElement("button");
    openBtn.className = "secondary";
    openBtn.type = "button";
    openBtn.textContent = "Open";
    openBtn.addEventListener("click", () => openReportOverlay(imageUrl, img.name));

    const dlLink = document.createElement("a");
    dlLink.className = "secondary";
    dlLink.href = imageUrl;
    dlLink.textContent = "Download";
    dlLink.download = img.name || "report.png";
    dlLink.style.textDecoration = "none";
    dlLink.style.display = "inline-flex";
    dlLink.style.alignItems = "center";

    const delBtn = document.createElement("button");
    delBtn.className = "secondary";
    delBtn.type = "button";
    delBtn.textContent = "Delete";
    delBtn.style.color = "#b91c1c";
    delBtn.style.borderColor = "#fecaca";
    delBtn.addEventListener("click", async () => {
      await deleteReportImage(img.path);
    });

    actions.appendChild(openBtn);
    actions.appendChild(dlLink);
    actions.appendChild(delBtn);
    meta.appendChild(title);
    meta.appendChild(category);
    meta.appendChild(actions);

    card.appendChild(imgContainer);
    card.appendChild(meta);
    container.appendChild(card);
  });
}

function openReportOverlay(src, alt) {
  let overlay = document.getElementById("reportGlobalOverlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "reportGlobalOverlay";
    overlay.className = "report-overlay";
    const img = document.createElement("img");
    img.className = "report-overlay-img";
    overlay.appendChild(img);
    overlay.addEventListener("click", () => {
      overlay.classList.remove("active");
    });
    document.body.appendChild(overlay);
  }
  const overlayImg = overlay.querySelector("img");
  overlayImg.src = src;
  overlayImg.alt = alt;
  overlay.classList.add("active");
}

async function initiateRender(isPreview) {
  const rootPath = REPORT_DOCK_ROOT;
  const sourcePath = ensureReportSourceValue();
  const outputPath = ensureReportOutputValue();
  const receptors = getSelectedReceptors();
  const runByReceptor = getSelectedRunByReceptor();
  if (!receptors.length) {
    alert("Select at least one render-ready receptor.");
    return;
  }
  const dpiVal = Number(els.reportDpi?.value || 120);
  const dpi = Number.isFinite(dpiVal) ? Math.max(30, Math.min(600, Math.round(dpiVal))) : 120;
  if (els.reportDpi) els.reportDpi.value = String(dpi);
  try {
    const payload = {
      root_path: rootPath,
      source_path: sourcePath,
      output_path: outputPath,
      receptors,
      run_by_receptor: runByReceptor,
      dpi,
      is_preview: isPreview,
    };
    const res = await fetchJSON("/api/reports/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.status === "started" && els.renderProgressPanel) {
      els.renderProgressPanel.style.display = "flex";
      if (els.renderProgressBar) els.renderProgressBar.style.width = "0%";
      if (els.renderElapsed) {
        els.renderElapsed.textContent = `Expected: ~${Math.round(res.expected_time || 0)}s`;
      }
      pollRenderStatus();
    }
  } catch (err) {
    alert("Render failed to start: " + err.message);
  }
}

async function pollRenderStatus() {
  if (renderTimer) clearTimeout(renderTimer);
  try {
    const state = await fetchJSON("/api/reports/status");
    if (els.renderProgressText) {
      els.renderProgressText.textContent = state.message || "Rendering...";
    }
    if (state.total > 0 && els.renderProgressBar) {
      const pct = Math.min(100, Math.round((state.progress / state.total) * 100));
      els.renderProgressBar.style.width = `${pct}%`;
    }

    if (state.status === "running" && (state.task || "") === "render") {
      renderTimer = setTimeout(pollRenderStatus, 1000);
    } else {
      if (els.renderProgressPanel) els.renderProgressPanel.style.display = "none";
      if (Array.isArray(state.errors) && state.errors.length) {
        alert("Render completed with errors: " + state.errors.join(" | "));
      }
      await fetchReports();
    }
  } catch (err) {
    console.error("Error polling render status:", err);
    if (els.renderProgressText) els.renderProgressText.textContent = "Error polling status.";
    renderTimer = setTimeout(pollRenderStatus, 2000);
  }
}

async function initiateGraphs() {
  const selectedPlots = getSelectedPlots();
  if (!selectedPlots.length) {
    alert("Select at least one plot.");
    return;
  }
  try {
    const payload = {
      root_path: REPORT_DOCK_ROOT,
      source_path: ensureReportSourceValue(),
      output_path: ensureReportOutputValue(),
      scripts: selectedPlots,
    };
    const res = await fetchJSON("/api/reports/graphs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === "started" && els.graphProgressPanel) {
      els.graphProgressPanel.style.display = "flex";
      if (els.graphProgressBar) els.graphProgressBar.style.width = "0%";
      if (els.graphElapsed) els.graphElapsed.textContent = `0 / ${selectedPlots.length}`;
      pollGraphStatus();
    }
  } catch (err) {
    alert("Predefined plot generation failed to start: " + err.message);
  }
}

async function pollGraphStatus() {
  if (plotTimer) clearTimeout(plotTimer);
  try {
    const state = await fetchJSON("/api/reports/status");
    if (els.graphProgressText) els.graphProgressText.textContent = state.message || "Generating plots...";

    if (state.total > 0 && els.graphProgressBar) {
      const pct = Math.min(100, Math.round((state.progress / state.total) * 100));
      els.graphProgressBar.style.width = `${pct}%`;
    }
    if (els.graphElapsed && state.total > 0) {
      els.graphElapsed.textContent = `${state.progress || 0} / ${state.total}`;
    }

    if (state.status === "running" && ((state.task || "") === "plots" || (state.task || "") === "graphs")) {
      plotTimer = setTimeout(pollGraphStatus, 1000);
      return;
    }

    if (els.graphProgressPanel) els.graphProgressPanel.style.display = "none";
    if (Array.isArray(state.errors) && state.errors.length) {
      alert("Plot generation completed with errors: " + state.errors.join(" | "));
    }
    await fetchReports();
  } catch (err) {
    console.error("Error polling plot status:", err);
    if (els.graphProgressText) els.graphProgressText.textContent = "Error polling status.";
    plotTimer = setTimeout(pollGraphStatus, 2000);
  }
}

async function generateReportDoc() {
  if (!els.reportDocContent) return;
  const rootPath = REPORT_DOCK_ROOT;
  const sourcePath = ensureReportSourceValue();
  const outputPath = ensureReportOutputValue();
  const imagesRootPath = ensureReportDocRootValue();
  const selectedImages = reportDocImageOrder.filter((path) => reportSelectedDocImages.has(path));
  if (!Array.isArray(reportDocImages) || reportDocImages.length === 0) {
    alert("No images found in selected report image root.");
    return;
  }
  if (!selectedImages.length) {
    alert("Select at least one image for report compilation.");
    return;
  }
  try {
    parseFigureStartFromInput();
    const imageByPath = new Map();
    (reportDocImages || []).forEach((item) => {
      const path = String(item.path || "").trim();
      if (path) imageByPath.set(path, item);
    });
    const figureCaptions = {};
    selectedImages.forEach((path, idx) => {
      const img = imageByPath.get(path) || { path, name: path };
      const defaultCaption = buildReportFigureCaption(img, idx + 1);
      const savedText = String(reportFigureCaptionText.get(path) || "").trim();
      const finalCaption = reportFigureCaptionCustom.has(path) && savedText ? savedText : defaultCaption;
      figureCaptions[path] = finalCaption;
    });

    Object.entries(figureCaptions).forEach(([path, captionText]) => {
      const key = String(path || "").trim();
      if (!key) return;
      reportFigureCaptionText.set(key, String(captionText || ""));
    });
    await saveReportDocConfig({ silent: true, refresh: false });

    const payload = {
      root_path: rootPath,
      source_path: sourcePath,
      output_path: outputPath,
      images_root_path: imagesRootPath,
      selected_images: selectedImages,
      figure_captions: figureCaptions,
      figure_start_number: reportFigureStartNumber,
      extra_sections: reportExtraSections.map((row) => ({ title: row.title, body: row.body })),
    };
    const res = await fetchJSON("/api/reports/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    reportDocInfo = {
      exists: true,
      path: res.doc_path || "report_outputs/docking_report_mvp.docx",
      download_url:
        res.download_url ||
        `/api/reports/doc?root_path=${encodeURIComponent(rootPath)}&source_path=${encodeURIComponent(sourcePath)}&output_path=${encodeURIComponent(outputPath)}`,
    };
    renderReportDocStatus(reportDocInfo);
    await fetchReports();
  } catch (err) {
    alert("Report compilation failed: " + err.message);
  }
}

init();
