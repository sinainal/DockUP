/* Native DockUP workbench. No computation or energy interpretation in browser. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const escape = text => String(text ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let models = [], jobs = [], poseSets = [], capabilities = {}, activeTab = "build", openedSet = null, openedJob = null;
  const selected = new Set();
  let stage, renderRevision = 0, refreshPromise = null, noticeTimer, jobRevision = 0, pollTimer;
  let capabilityRefreshRequested = false, currentView = null, saveTimer, restoring = true;
  const UI_KEY = "dockup.quantum.workspace.v2";
  function readUI() {
    try { return JSON.parse(sessionStorage.getItem(UI_KEY) || "null"); } catch (_) { return null; }
  }
  const savedUI = readUI();
  function saveUI() {
    if (restoring) return;
    const forms = {};
    for (const id of ["qmBuild","qmPoses","qmCalculate","qmExperiment"]) {
      forms[id] = [...$(id).querySelectorAll("[name],select[id]")].map(e=>({key:e.name || e.id,value:e.type==="checkbox"?e.checked:e.value,choice:e.type==="checkbox"?e.value:null}));
    }
    const controls = {};
    for (const id of ["qmModelSearch","qmJobFilter","qmResultSearch","qmResultEngine","qmRepresentation","qmOverlay","qmExperimentStart","qmPoseSets"]) controls[id] = $(id).type==="checkbox"?$(id).checked:$(id).value;
    try { sessionStorage.setItem(UI_KEY,JSON.stringify({forms,controls,activeTab,selected:[...selected],poseSetId:openedSet?.id,jobId:openedJob?.id,view:currentView,scrollTop:document.querySelector(".qm-panel-scroll").scrollTop,pageScroll:window.scrollY})); } catch (_) {}
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer=setTimeout(saveUI,120); }
  function restoreFields() {
    if (!savedUI) return;
    for (const [id,rows] of Object.entries(savedUI.forms || {})) {
      if (!$(id)) continue;
      for (const row of rows) {
        const input=[...$(id).querySelectorAll("[name],select[id]")].find(e=>(e.name || e.id)===row.key && (e.type!=="checkbox" || e.value===row.choice));
        if (input) input.type==="checkbox"?input.checked=Boolean(row.value):input.value=row.value;
      }
    }
    for (const [id,value] of Object.entries(savedUI.controls || {})) if ($(id)) $(id).type==="checkbox"?$(id).checked=Boolean(value):$(id).value=value;
    for (const id of savedUI.selected || []) selected.add(id);
    const polymer=$("qmBuild").elements.family.value==="polymer";
    document.querySelector(".qm-polymer-fields").hidden=!polymer;
    document.querySelector(".qm-molecule-fields").hidden=polymer;
    tab(["build","poses","calculate","experiments"].includes(savedUI.activeTab)?savedUI.activeTab:"build");
  }
  function stableHTML(id,html) {
    const node=$(id);
    if (node._renderedHTML===html) return;
    if (node.contains(document.activeElement)) return; // Do not replace a focused row/dropdown during polling.
    const scroll=node.closest(".qm-table-wrap") || node;
    const top=scroll.scrollTop,left=scroll.scrollLeft;
    node.innerHTML=html; node._renderedHTML=html;
    scroll.scrollTop=top; scroll.scrollLeft=left;
  }
  async function api(path, body) {
    const response = await fetch("/api/modeling/" + path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail || result));
    return result;
  }
  function notice(message, error = false) {
    $("qmNotice").textContent = message; $("qmNotice").classList.toggle("error",error); $("qmNotice").hidden = false;
    clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $("qmNotice").hidden = true; },error ? 9000 : 5000);
  }
  function formData(form) {
    const data = {};
    for (const input of form.querySelectorAll("[name]")) {
      if (input.name === "polymers") continue;
      if (input.type === "checkbox") data[input.name] = input.checked;
      else if (input.type === "number") data[input.name] = input.value === "" ? null : Number(input.value);
      else data[input.name] = input.value;
    }
    return data;
  }
  function payload() {
    if (activeTab === "build") {
      const data = formData($("qmBuild"));
      for (const key of ["smiles","chembl_id","name"]) if (!data[key]) data[key] = null;
      if (data.family === "small_molecule") data.model_kind = "molecule";
      else { data.smiles = null; data.chembl_id = null; data.name = null; }
      return data;
    }
    if (activeTab === "poses") return formData($("qmPoses"));
    if (activeTab === "calculate") return {...formData($("qmCalculate")),model_ids:[...selected]};
    const data = formData($("qmExperiment"));
    data.repeats = data.repeats.split(/[,\s]+/).filter(Boolean).map(Number);
    data.polymers = [...$("qmExperiment").querySelectorAll('[name="polymers"]:checked')].map(x=>x.value);
    data.surface_model_ids = $("qmExperimentStart").value === "surfaces" ? [...selected].filter(id=>models.find(m=>m.id===id)?.source?.family==="polymer") : [];
    data.pose_set_ids = $("qmExperimentStart").value === "poses" && openedSet ? [openedSet.id] : [];
    return data;
  }
  function updateCLI() {
    const names = {build:"build",poses:"poses",calculate:"quantum",experiments:"experiment"};
    const json = JSON.stringify(payload()).replace(/'/g,"'\\''");
    $("qmCLI").textContent = `python -m docking_app.cli modeling ${names[activeTab]} --payload '${json}'`;
  }
  function tab(name) {
    activeTab = name;
    document.querySelectorAll("[data-tab]").forEach(e=>{e.classList.toggle("active",e.dataset.tab===name);e.setAttribute("aria-selected",String(e.dataset.tab===name));});
    document.querySelectorAll("[data-panel]").forEach(e=>e.hidden=e.dataset.panel!==name);
    updateCLI();
    scheduleSave();
  }
  function selectOptions(id, entries) {
    const current = $(id).value;
    stableHTML(id,entries.length ? entries.map(m=>`<option value="${escape(m.id)}">${escape(m.title || m.id)}</option>`).join("") : '<option value="">No matching saved structures</option>');
    if (entries.some(m=>m.id===current)) $(id).value = current;
  }
  function visibleModels() {
    const query = $("qmModelSearch").value.toLowerCase();
    return models.filter(m=>(!openedSet || openedSet.poses.some(p=>p.model_id===m.id)) && JSON.stringify([m.title,m.id,m.source?.polymer,m.source?.repeats]).toLowerCase().includes(query));
  }
  function renderModels() {
    stableHTML("qmModels",visibleModels().map(m=>`<tr class="${selected.has(m.id)?"selected":""}"><td><input type="checkbox" data-select="${escape(m.id)}" ${selected.has(m.id)?"checked":""} aria-label="Select ${escape(m.title)}"></td><td><button class="qm-button" data-view-model="${escape(m.id)}">${escape(m.title)}</button><small>${escape(m.id)}</small></td><td>${escape(m.formula || "—")}</td><td>${escape(m.atom_count)}</td><td>${escape(m.formal_charge)}</td><td>${escape(m.engine)}<small>${escape(m.model_kind)}</small></td><td>${["sdf","pdb","xyz"].map(ext=>`<a href="/api/modeling/models/${encodeURIComponent(m.id)}/structure?format=${ext}" download>${ext.toUpperCase()}</a>`).join("")}</td></tr>`).join("") || '<tr><td colspan="7">No matching structures. Build a model or import an existing Studio structure.</td></tr>');
    $("qmSelectedCount").textContent = `${selected.size} structure${selected.size===1?"":"s"} selected · saved library geometries`;
    updateCLI();
  }
  function renderJobs() {
    const filter = $("qmJobFilter").value;
    const list = jobs.filter(j=>filter==="all" || (filter==="running"?["queued","running"].includes(j.state):filter==="failed"?["failed","partially_succeeded","interrupted","timed_out"].includes(j.state):j.state===filter));
    stableHTML("qmJobs",list.map(j=>`<tr><td><button class="qm-button" data-job="${escape(j.id)}">${escape(j.payload.name || j.payload.title || j.id.slice(-8))}</button><small>${escape(j.id)}</small></td><td>${escape(j.kind)}${j.payload.engine?` · ${escape(j.payload.engine)}`:""}</td><td><span class="qm-state ${escape(j.state)}">${escape(j.state.replaceAll("_"," "))}</span></td><td>${escape(new Date(j.created*1000).toLocaleString())}</td><td>${j.result.successes??"—"} completed / ${j.result.failures??"—"} failed</td><td>${["queued","running"].includes(j.state)?`<button class="qm-button" data-cancel="${escape(j.id)}">Cancel</button>`:`<button class="qm-button" data-retry="${escape(j.id)}">Retry as new</button>`}</td></tr>`).join("") || '<tr><td colspan="6">No calculations yet.</td></tr>');
  }
  function renderResults() {
    const query=$("qmResultSearch").value.toLowerCase(), engine=$("qmResultEngine").value;
    const rows=[];
    for (const job of jobs) for (const record of job.result.records||[]) {
      if (!["xtb","pyscf","orca","crest"].includes(record.stage) || (engine!=="all" && engine!==record.stage)) continue;
      const value=record.result||{}, protocol=value.protocol?.method||value.method||value.cycle?.[0]?.protocol?.method||record.stage;
      const model=models.find(m=>m.id===record.model_id), name=model?.title||record.model_id||job.id;
      if (!`${name} ${protocol} ${job.id}`.toLowerCase().includes(query)) continue;
      rows.push(`<tr><td><button class="qm-button" data-result-job="${escape(job.id)}">${escape(name)}</button><small>${escape(job.id.slice(-8))}</small></td><td>${escape(protocol)}<small>${escape(value.protocol?.solvent_model||value.solvent||job.payload.solvent||"")}</small></td><td><span class="qm-state ${escape(record.status)}">${escape(record.status)}</span><small>${escape(record.reason||value.reason||"")}</small></td><td>${Number.isFinite(value.energy_hartree)?value.energy_hartree.toFixed(8):"—"}</td><td>${Number.isFinite(value.delta_E_int_kcal_mol)?value.delta_E_int_kcal_mol.toFixed(3):"—"}</td><td>${Number.isFinite(value.wall_time_seconds)?value.wall_time_seconds.toFixed(1)+" s":"—"}</td></tr>`);
    }
    stableHTML("qmResults",rows.join("")||'<tr><td colspan="6">No matching calculations.</td></tr>');
  }
  function engineSettings() {
    const engine = $("qmEngine").value;
    const allowed = {xtb:["optimization","singlepoint","interaction"],orca:["optimization","singlepoint","interaction","frequency"],pyscf:["singlepoint","interaction"],crest:["optimization"]}[engine];
    for (const option of $("qmCalculation").options) option.disabled = !allowed.includes(option.value);
    if (!allowed.includes($("qmCalculation").value)) $("qmCalculation").value = allowed[0];
    $("qmOptMode").disabled = engine!=="xtb";
    if (engine!=="xtb") $("qmOptMode").value="full";
    const notes = {xtb:"GFN2-xTB / ALPB water or gas phase. Explicit optimizer and SCC convergence checks.",orca:"r2SCAN-3c / CPCM water or gas phase. D4 and gCP are part of this method. ORCA requires a separately configured scientific executable.",pyscf:"PBE0-D3(BJ)/def2-TZVP / PCM water or gas phase. SP and frozen-fragment cycles only; separate from the ORCA protocol.",crest:"Constrained GFN-FF refinement of saved dopamine–surface pose members; not whole-surface global sampling."};
    $("qmProtocol").textContent = notes[engine] + (capabilities[engine]?.reason && capabilities[engine]?.status!=="ready"?" "+capabilities[engine].reason:"");
    $("qmRun").disabled = capabilities[engine]?.status!=="ready";
    updateCLI();
  }
  async function renderStructures(entries, title) {
    if (!stage) return false;
    const revision = ++renderRevision;
    stage.removeAllComponents();
    $("qmViewerEmpty").hidden = true;
    $("qmViewerTitle").textContent = title;
    const limit = $("qmOverlay").checked ? 16 : 1;
    for (const [i,entry] of entries.slice(0,limit).entries()) {
      const component = await stage.loadFile(entry.url,{ext:entry.ext || "pdb"});
      if (revision!==renderRevision) {stage.removeComponent(component); return false;}
      const params = i ? {colorScheme:"uniform",color:["#5275a1","#aa8063","#65968f","#9277a2"][i%4]} : {colorScheme:"element"};
      component.addRepresentation($("qmRepresentation").value,params);
      if (entries.length>1) component.addRepresentation("licorice",{sele:"POL",opacity:.18});
    }
    if (revision!==renderRevision) return false;
    stage.autoView();
    return true;
  }
  async function viewModel(id) {
    const model = models.find(m=>m.id===id);
    if (!model) return;
    currentView={kind:"model",id};
    $("qmOutputStrip").replaceChildren(); $("qmOutputStrip")._renderedHTML=null;
    const ids = $("qmOverlay").checked && selected.size ? [...selected] : [id];
    if (!await renderStructures(ids.map(mid=>({url:`/api/modeling/models/${encodeURIComponent(mid)}/structure?format=pdb`})),ids.length>1?`${Math.min(ids.length,16)} structures · overlay`:model.title)) return;
    $("qmViewerMeta").textContent = `${model.atom_count} atoms · charge ${model.formal_charge} · ${model.formula} · ${model.geometry_origin ? model.geometry_origin.geometry+" (derived)" : "original geometry"}`;
    $("qmDetails").textContent = JSON.stringify({source:model.source,geometry_origin:model.geometry_origin,validation:model.validation,conformers:model.conformer_results,warnings:model.warnings},null,2);
    saveUI();
  }
  async function openJob(id, autoView = true) {
    const revision = renderRevision;
    const requestRevision = ++jobRevision;
    const job = await api("jobs/"+id);
    if (requestRevision!==jobRevision) return;
    openedJob = job;
    $("qmJobLog").textContent = JSON.stringify({id:openedJob.id,state:openedJob.state,request:openedJob.payload,events:openedJob.events,result:openedJob.result},null,2)+"\n\nWORKER LOG\n"+(openedJob.log||"");
    const outputs = openedJob.result.outputs || [];
    stableHTML("qmArtifactDownloads",outputs.map((o,i)=>`<a class="qm-button" href="/api/modeling/jobs/${id}/artifacts/${i}" download title="${escape(o.label)}">${escape(o.geometry || o.label)} · ${escape(o.format)}</a>`).join(""));
    if (autoView || currentView?.kind==="artifact") stableHTML("qmOutputStrip",outputs.map((o,i)=>o.format==="pdb"?`<button class="qm-button" data-artifact="${i}" title="${escape(o.label)}">${escape(o.geometry || o.label)}</button>`:"").join(""));
    const chemical = outputs.findIndex(o=>o.format==="pdb");
    if (autoView && revision===renderRevision && chemical>=0) await viewArtifact(chemical);
    else if (autoView && revision===renderRevision) $("qmDetails").textContent = JSON.stringify(openedJob.result,null,2);
    saveUI();
  }
  async function viewArtifact(index) {
    const job = openedJob;
    const output = job.result.outputs[index];
    if (!output) throw new Error("This geometry is no longer available. Refresh the job.");
    currentView={kind:"artifact",jobId:job.id,index};
    if (!await renderStructures([{url:`/api/modeling/jobs/${job.id}/artifacts/${index}`}],output.label)) return;
    $("qmViewerMeta").textContent = `${output.geometry || "saved"} · checksum ${output.sha256.slice(0,12)}`;
    const sdfIndex = job.result.outputs.findIndex(o=>o.format==="sdf" && o.model_id===output.model_id && o.geometry===output.geometry);
    $("qmOutputStrip").querySelectorAll("[data-artifact]").forEach(e=>e.classList.toggle("active",Number(e.dataset.artifact)===index));
    const download = document.createElement("a"); download.className="qm-button"; download.textContent="Download PDB"; download.href=`/api/modeling/jobs/${job.id}/artifacts/${index}`; download.download="";
    const publishIndex = output.kind==="receptor"?index:sdfIndex;
    $("qmDetails").replaceChildren(download);
    if (publishIndex>=0) {
      const button = document.createElement("button"); button.className="qm-button"; button.textContent="Send this geometry to Docking"; button.style.marginLeft="8px";
      button.onclick=()=>api(`jobs/${job.id}/publish`,{artifact_index:publishIndex}).then(r=>notice("Added to DockUP: "+r.filename)).catch(e=>notice(e.message,true));
      $("qmDetails").append(button);
    }
    if (sdfIndex>=0) {
      const button=document.createElement("button");button.className="qm-button";button.textContent="Use for next calculation";button.style.marginLeft="8px";
      button.onclick=async()=>{try{const model=await api(`jobs/${job.id}/derive`,{artifact_index:sdfIndex});models=await api("models");selected.clear();selected.add(model.id);openedSet=null;renderModels();tab("calculate");await viewModel(model.id);notice("Selected geometry saved as a new model; original unchanged.");}catch(e){notice(e.message,true);}};
      $("qmDetails").append(button);
    }
    const info=document.createElement("pre"); info.textContent=JSON.stringify({artifact:output,calculations:job.result.records?.filter(r=>!output.model_id || r.model_id===output.model_id)},null,2); $("qmDetails").append(info);
    saveUI();
  }
  function refresh(withCapabilities = false) {
    capabilityRefreshRequested ||= withCapabilities;
    if (refreshPromise) return refreshPromise;
    refreshPromise=(async()=>{
      try {
        do {
          const probe=capabilityRefreshRequested;
          capabilityRefreshRequested=false;
          [models,jobs,poseSets]=await Promise.all([api("models"),api("jobs"),api("pose-sets")]);
          const available=new Set(models.map(m=>m.id));
          for (const id of selected) if (!available.has(id)) selected.delete(id);
          if (openedSet) openedSet=poseSets.find(p=>p.id===openedSet.id) || null;
          selectOptions("qmSurface",models.filter(m=>m.source?.family==="polymer"));
          selectOptions("qmAdsorbate",models.filter(m=>m.source?.compound==="dopamine"));
          selectOptions("qmPoseSets",poseSets.map(p=>({...p,title:`${p.id.slice(-10)} · ${p.poses.length} poses`})));
          renderModels(); renderJobs(); renderResults();
          // Poll the currently opened job without switching its geometry or camera.
          if (openedJob && ["running","queued"].includes(openedJob.state)) await openJob(openedJob.id,false);
          if (probe) {
            capabilities=await api("capabilities");
            stableHTML("qmCapabilities",["xtb","orca","pyscf","crest"].map(k=>`<span class="qm-engine ${capabilities[k]?.status==="ready"?"ready":""}" title="${escape(capabilities[k]?.reason||capabilities[k]?.version_banner||capabilities[k]?.version||"")}">${escape(k==="xtb"?"xTB":k.toUpperCase())} · ${capabilities[k]?.status==="ready"?"ready":"unavailable"}</span>`).join(""));
            for (const option of $("qmBuild").elements.engine.options) option.disabled=capabilities.builders?.[option.value]?.status!=="ready";
            engineSettings();
          }
        } while (capabilityRefreshRequested);
      } catch(error) { notice(error.message,true); }
      finally { refreshPromise=null; }
    })();
    return refreshPromise;
  }
  function startPolling() {
    clearInterval(pollTimer);
    pollTimer=setInterval(()=>{if(!document.hidden)refresh();},2500);
  }
  async function restoreWorkspace() {
    await refresh(true);
    if (savedUI) {
      openedSet=poseSets.find(p=>p.id===savedUI.poseSetId) || null;
      for (const [id,rows] of Object.entries(savedUI.forms || {})) {
        for (const row of rows) {
          // Surface/adsorbate selectors are populated only after the first API response.
          if (["surface_model_id","adsorbate_model_id"].includes(row.key)) {
            const field=$(id).elements[row.key];
            if ([...field.options].some(o=>o.value===row.value)) field.value=row.value;
          }
        }
      }
      if (savedUI.controls?.qmPoseSets && poseSets.some(p=>p.id===savedUI.controls.qmPoseSets)) $("qmPoseSets").value=savedUI.controls.qmPoseSets;
      renderModels();
      if (!renderRevision) {
        try {
          if (savedUI.jobId) await openJob(savedUI.jobId,false);
          if (savedUI.view?.kind==="artifact") {
            if (openedJob?.id!==savedUI.view.jobId) await openJob(savedUI.view.jobId,false);
            if (openedJob.result.outputs?.[savedUI.view.index]) await viewArtifact(savedUI.view.index);
          } else if (savedUI.view?.kind==="model") await viewModel(savedUI.view.id);
        } catch(error) {notice("Saved selection could not be restored: "+error.message,true);}
      }
      requestAnimationFrame(()=>{
        document.querySelector(".qm-panel-scroll").scrollTop=savedUI.scrollTop || 0;
        if (savedUI.pageScroll) window.scrollTo(0,savedUI.pageScroll);
      });
    }
    restoring=false;
    saveUI();
  }
  document.querySelectorAll("[data-tab]").forEach(button=>button.onclick=()=>tab(button.dataset.tab));
  document.querySelectorAll("[data-tab]").forEach(button=>button.setAttribute("role","tab"));
  $("qmExperiment").querySelector('.qm-note').insertAdjacentHTML("beforebegin",'<label class="qm-check" style="margin-top:10px"><input name="generate_poses" type="checkbox" checked>Generate surface poses (off = model matrix only)</label>');
  $("qmExperiment").elements.generate_poses.onchange=()=>{const form=$("qmExperiment");if(!form.elements.generate_poses.checked)form.elements.run_xtb.checked=false;form.elements.run_xtb.disabled=!form.elements.generate_poses.checked;updateCLI();};
  $("qmJobDetail").insertAdjacentHTML("beforeend",'<div id="qmArtifactDownloads" class="qm-tools" style="flex-wrap:wrap;margin-top:12px"></div>');
  for (const [id,path] of [["qmBuild","models"],["qmPoses","pose-sets"],["qmCalculate","quantum/jobs"],["qmExperiment","experiments"]]) {
    $(id).addEventListener("input",updateCLI);
    $(id).onsubmit=async event=>{
      event.preventDefault();
      const button=event.submitter || event.target.querySelector('button[type="submit"]'); button.disabled=true;
      try {
        if (id==="qmCalculate" && !selected.size) throw new Error("Select at least one structure from the library.");
        if (id==="qmExperiment" && $("qmExperimentStart").value==="poses" && !openedSet) throw new Error("Open a saved pose set first.");
        if (id==="qmExperiment" && $("qmExperimentStart").value==="surfaces" && !payload().surface_model_ids.length) throw new Error("Select saved polymer structures first.");
        const job=await api(path,payload()); notice("Queued "+job.kind+" · "+job.id.slice(-8)); await openJob(job.id,false); await refresh();
      } catch(error) {notice(error.message,true);} finally {button.disabled=false; if (id==="qmCalculate") engineSettings();}
    };
  }
  $("qmBuild").elements.family.onchange=()=>{const polymer=$("qmBuild").elements.family.value==="polymer";document.querySelector(".qm-polymer-fields").hidden=!polymer;document.querySelector(".qm-molecule-fields").hidden=polymer;updateCLI();};
  $("qmEngine").onchange=engineSettings;
  $("qmRefresh").onclick=async()=>{
    const button=$("qmRefresh");button.disabled=true;button.setAttribute("aria-busy","true");button.textContent="Refreshing…";
    try {await refresh(true);saveUI();} finally {button.disabled=false;button.removeAttribute("aria-busy");button.textContent="Refresh";}
  };
  $("qmModelSearch").oninput=renderModels;
  $("qmJobFilter").onchange=renderJobs;
  $("qmResultSearch").oninput=renderResults;
  $("qmResultEngine").onchange=renderResults;
  $("qmResults").onclick=event=>{const id=event.target.closest("[data-result-job]")?.dataset.resultJob;if(id)openJob(id).catch(e=>notice(e.message,true));};
  $("qmClearSelection").onclick=()=>{selected.clear();openedSet=null;renderModels();};
  $("qmSelectAll").onclick=()=>{visibleModels().forEach(m=>selected.add(m.id));renderModels();};
  $("qmOpenPoseSet").onclick=()=>{openedSet=poseSets.find(p=>p.id===$("qmPoseSets").value);selected.clear();openedSet?.poses.forEach(p=>selected.add(p.model_id));renderModels();if (openedSet?.poses.length) viewModel(openedSet.poses[0].model_id).catch(e=>notice(e.message,true));tab("calculate");};
  $("qmModels").onchange=event=>{const id=event.target.dataset.select;if(!id)return;event.target.checked?selected.add(id):selected.delete(id);renderModels();viewModel(id).catch(e=>notice(e.message,true));};
  $("qmModels").onclick=event=>{const id=event.target.closest("[data-view-model]")?.dataset.viewModel;if(id)viewModel(id).catch(e=>notice(e.message,true));};
  $("qmJobs").onclick=async event=>{
    const button=event.target.closest("button"); if(!button)return;
    try {
      if(button.dataset.job) await openJob(button.dataset.job);
      if(button.dataset.cancel){await api(`jobs/${button.dataset.cancel}/cancel`,{});await refresh();}
      if(button.dataset.retry){const row=await api(`jobs/${button.dataset.retry}/retry`,{});await openJob(row.id,false);await refresh();}
    } catch(error){notice(error.message,true);}
  };
  $("qmOutputStrip").onclick=event=>{const index=event.target.closest("[data-artifact]")?.dataset.artifact;if(index!==undefined)viewArtifact(Number(index)).catch(e=>notice(e.message,true));};
  $("qmCopyCLI").onclick=()=>navigator.clipboard.writeText($("qmCLI").textContent).then(()=>notice("CLI command copied")).catch(e=>notice(e.message,true));
  $("qmResetView").onclick=()=>stage?.autoView();
  $("qmSpin").onclick=()=>{const next=$("qmSpin").getAttribute("aria-pressed")!=="true";$("qmSpin").setAttribute("aria-pressed",String(next));stage?.setSpin(next);};
  $("qmRepresentation").onchange=()=>{stage?.eachComponent(c=>{c.removeAllRepresentations();c.addRepresentation($("qmRepresentation").value);});};
  $("qmOverlay").onchange=()=>{if(selected.size)viewModel([...selected][0]).catch(e=>notice(e.message,true));};
  $("qmBrowseArchive").onclick=async()=>{try{const rows=await api("archive?source="+$("qmArchiveSource").value);$("qmArchiveList").innerHTML=rows.map(m=>`<label class="qm-check"><input type="checkbox" value="${escape(m.id)}">${escape(m.title)} · ${escape(m.atom_count)} atoms</label>`).join("")||"No archive structures found.";}catch(e){notice(e.message,true);}};
  $("qmImportArchive").onclick=async()=>{try{const ids=[...$("qmArchiveList").querySelectorAll("input:checked")].map(x=>x.value);if(!ids.length)throw new Error("Check archive structures to import.");const result=await api("archive/import",{source:$("qmArchiveSource").value,model_ids:ids});notice("Imported "+result.imported.length+" original structures; source unchanged.");await refresh();}catch(e){notice(e.message,true);}};
  try { if(window.NGL){stage=new NGL.Stage("qmViewport",{backgroundColor:"#f8fafc",quality:"medium"});new ResizeObserver(()=>stage.handleResize()).observe($("qmViewport"));}else notice("NGL could not load; downloads and calculations remain available.",true); }catch(error){notice("3D viewer: "+error.message,true);}
  restoreFields();
  $("qmExperiment").elements.run_xtb.disabled=!$("qmExperiment").elements.generate_poses.checked;
  document.addEventListener("input",scheduleSave);
  document.addEventListener("change",scheduleSave);
  document.querySelector(".qm-panel-scroll").addEventListener("scroll",scheduleSave,{passive:true});
  window.addEventListener("scroll",scheduleSave,{passive:true});
  window.addEventListener("pagehide",()=>{saveUI();clearInterval(pollTimer);clearTimeout(saveTimer);});
  window.addEventListener("pageshow",event=>{startPolling();stage?.handleResize();if(event.persisted)refresh(true);});
  document.addEventListener("visibilitychange",()=>{if(!document.hidden){stage?.handleResize();refresh();}});
  restoreWorkspace().catch(error=>{restoring=false;notice(error.message,true);});
  startPolling();
})();
