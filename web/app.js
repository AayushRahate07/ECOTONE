const API_BASE = window.location.origin + "/api";

let currentRoute = "#/";
let activeProjectDetailId = null;

let overviewMap = null;
let detailMap = null;
let newProjectMap = null;

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("hashchange", handleRouting);
  handleRouting();
});

function handleRouting() {
  const hash = window.location.hash || "#/";
  currentRoute = hash;

  document
    .querySelectorAll(".view")
    .forEach((el) => el.classList.add("hidden"));
  document
    .querySelectorAll(".nav-item")
    .forEach((el) => el.classList.remove("active"));

  if (hash.startsWith("#/projects/new")) {
    showView("view-new-project", "nav-projects");
    initNewProject();
  } else if (hash.startsWith("#/projects/")) {
    const pid = hash.replace("#/projects/", "");
    activeProjectDetailId = pid;
    showView("view-project-detail", "nav-projects");
    loadProjectDetail(pid);
  } else if (hash.startsWith("#/projects")) {
    showView("view-projects", "nav-projects");
    loadProjectsList();
  } else if (hash.startsWith("#/resources")) {
    showView("view-resources", "nav-resources");
    loadResourcesData();
  } else if (hash.startsWith("#/system")) {
    showView("view-system", "nav-system");
    loadSystemHealth();
  } else {
    showView("view-overview", "nav-overview");
    loadOverview();
  }
}

function showView(viewId, navId) {
  const view = document.getElementById(viewId);
  if (view) view.classList.remove("hidden");
  const nav = document.getElementById(navId);
  if (nav) nav.classList.add("active");
}

function navigateTo(hash) {
  window.location.hash = hash;
}

async function deleteProject(id) {
  if (!confirm("Are you sure you want to permanently delete this expedition and release all allocated resources?")) {
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/projects/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (data.status === "SUCCESS") {
      // Reload active view or navigate to expeditions list
      if (window.location.hash.startsWith("#/projects/")) {
        navigateTo("#/projects");
      } else {
        loadProjectsList();
      }
    } else {
      alert("Error: " + (data.error || "Failed to delete expedition"));
    }
  } catch (e) {
    console.error("Network error deleting project:", e);
    alert("Network error occurred while deleting the expedition.");
  }
}

// ── CARTOGRAPHIC MAP HELPER ──────────────────────────────────────────────

function createMap(containerId, lat, lon, zoom = 9) {
  const map = L.map(containerId).setView([lat, lon], zoom);

  // Add default filtered class for TERRAIN
  map.getContainer().classList.add("map-filtered");

  const baseLayers = {
    "SATELLITE": L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        attribution: "Esri World Imagery",
        maxZoom: 18,
      },
    ),
    "TERRAIN": L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
      {
        attribution: "CARTO Voyager",
        maxZoom: 19,
      },
    ),
    "TOPOGRAPHIC": L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
      {
        attribution: "Esri World Topo Map",
        maxZoom: 19,
      },
    ),
  };

  // Default layer: TERRAIN
  baseLayers["TERRAIN"].addTo(map);

  // Custom Split Sleek Theme Switcher on Top Right
  const CustomThemeSwitcher = L.Control.extend({
    options: { position: "topright" },
    onAdd: function (m) {
      const container = L.DomUtil.create("div", "custom-theme-switcher");
      L.DomEvent.disableClickPropagation(container);

      const options = [
        { name: "SATELLITE", label: "Satellite" },
        { name: "TERRAIN", label: "Terrain" },
        { name: "TOPOGRAPHIC", label: "Topo Map" }
      ];

      options.forEach(opt => {
        const btn = L.DomUtil.create("button", "theme-btn", container);
        btn.innerText = opt.label.toUpperCase();

        if (m.hasLayer(baseLayers[opt.name])) {
          btn.classList.add("active");
        }

        btn.onclick = () => {
          Object.keys(baseLayers).forEach(k => {
            if (m.hasLayer(baseLayers[k])) m.removeLayer(baseLayers[k]);
          });
          baseLayers[opt.name].addTo(m);
          container.querySelectorAll(".theme-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          m.fire("baselayerchange", { name: opt.name, layer: baseLayers[opt.name] });
        };
      });

      return container;
    }
  });

  new CustomThemeSwitcher().addTo(map);

  map.on("baselayerchange", (e) => {
    if (e.name === "SATELLITE") {
      map.getContainer().classList.remove("map-filtered");
      map.getContainer().classList.add("map-unfiltered");
    } else {
      map.getContainer().classList.remove("map-unfiltered");
      map.getContainer().classList.add("map-filtered");
    }
  });

  return map;
}

const KOYNA_ORGANIC_POLYGON = [
  [17.38, 73.68],
  [17.42, 73.65],
  [17.51, 73.66],
  [17.62, 73.71],
  [17.65, 73.8],
  [17.58, 73.84],
  [17.48, 73.82],
  [17.4, 73.75],
];

function addKoynaOverlay(map) {
  L.polygon(KOYNA_ORGANIC_POLYGON, {
    color: "#FF5722",
    weight: 1.8,
    dashArray: "5, 5",
    fillColor: "rgba(255, 152, 0, 0.25)",
    fillOpacity: 0.3,
  }).addTo(map);
}

// Global cache for overview data so filters can re-render without refetch
let _overviewProjects = [];
let _overviewMapContext = null;

async function loadOverview() {
  try {
    const [analyticsRes, projectsRes, mapCtxRes] = await Promise.all([
      fetch(`${API_BASE}/analytics`),
      fetch(`${API_BASE}/projects`),
      fetch(`${API_BASE}/map_context`).catch(() => null),
    ]);

    const data = await analyticsRes.json();
    const m = data.metrics || {};

    const opSummary = document.getElementById("overview-op-summary");
    if (opSummary) {
      const active = m.active_projects || 0;
      const pending = m.pending_projects || 0;
      const compensating = m.compensating_projects || 0;
      opSummary.innerText = `${String(active).padStart(2, "0")} ACTIVE \u00b7 ${String(pending).padStart(2, "0")} CLEARANCE \u00b7 ${String(compensating).padStart(2, "0")} COMPENSATING`;
    }

    _overviewProjects = await projectsRes.json();

    if (mapCtxRes && mapCtxRes.ok) {
      _overviewMapContext = await mapCtxRes.json();
    }

    renderOverviewOpList(_overviewProjects);
    initOverviewMap(_overviewProjects);
  } catch (e) {
    console.error("Error loading operations atlas:", e);
  }
}

function getOperationType(p) {
  const t = (p.title || "").toUpperCase();
  if (t.includes("HERITAGE") || t.includes("FORT") || t.includes("GPR") || t.includes("EXCAVAT") || t.includes("ASI")) return "HERITAGE";
  return "CONSERVATION";
}

function getPriorityColor(priority) {
  switch ((priority || "").toUpperCase()) {
    case "CRITICAL": return "#ff3366";
    case "HIGH": return "#ff9f1c";
    case "MEDIUM": return "var(--clay)";
    case "LOW": return "var(--stone)";
    default: return "var(--stone)";
  }
}

function renderOverviewOpList(projects) {
  const opList = document.getElementById("overview-op-list");
  if (!opList) return;

  if (!projects || projects.length === 0) {
    opList.innerHTML = `
      <div style="padding: 2.5rem 1rem; text-align: center;">
        <div style="font-family: 'Garet', sans-serif; font-size: 1rem; color: var(--parchment); margin-bottom: 0.75rem; letter-spacing: 0.1em;">NO FIELD OPERATIONS</div>
        <div style="font-size: 0.8rem; color: var(--stone); line-height: 1.6; margin-bottom: 1.25rem;">
          No active or planned operations in this region.<br>
          Ecotone orchestrates fund allocation, asset dispatch,<br>
          team assignment, and regulatory clearance as a single<br>
          transactional workflow.
        </div>
        <a href="#/projects/new" class="btn primary-btn" style="font-size: 0.75rem; padding: 0.5rem 1rem;">PLAN FIRST OPERATION</a>
      </div>`;
    return;
  }

  opList.innerHTML = projects.map(p => {
    const type = getOperationType(p);
    const statusCls = (p.status || "active").toLowerCase();
    const priorityColor = getPriorityColor(p.priority);
    const daysWaiting = p.days_waiting != null ? p.days_waiting : "—";
    const nearestAsset = p.nearest_asset_km != null ? `${p.nearest_asset_km} km` : "—";
    const budgetRemaining = p.budget_remaining != null ? `\u20b9${Math.round(p.budget_remaining).toLocaleString()}` : `\u20b9${(p.budget_requested || 0).toLocaleString()}`;
    const riskFlags = (p.risk_flags || []);
    const hasRisk = riskFlags.length > 0;

    return `
      <div class="op-card" style="border-bottom: 1px solid var(--border-subtle); padding: 0.9rem 0; cursor: pointer; transition: background 0.15s ease;" onmouseover="this.style.background='var(--surface-elevated)'" onmouseout="this.style.background='transparent'" onclick="navigateTo('#/projects/${p.project_id}')" data-status="${p.status}" data-priority="${p.priority || 'MEDIUM'}" data-type="${type}">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.3rem;">
          <div style="font-family: 'Garet', sans-serif; font-size: 0.9rem; color: var(--parchment); flex: 1; margin-right: 0.5rem;">${p.title}</div>
          <div class="status-tag ${statusCls}" style="font-size: 0.65rem; white-space: nowrap;">\u25cf ${p.status}</div>
        </div>
        <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem;">
          <span style="font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; color: ${priorityColor}; border: 1px solid ${priorityColor}; padding: 0.1rem 0.35rem;">${p.priority || "MEDIUM"}</span>
          <span style="font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; color: var(--stone);">${p.location_name.toUpperCase()}</span>
          <span style="font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; color: var(--parchment-muted);">${type}</span>
        </div>
        <div style="display: flex; gap: 1rem; font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; color: var(--stone);">
          <span>${daysWaiting}d</span>
          <span>ASSET ${nearestAsset}</span>
          <span>${budgetRemaining}</span>
          ${hasRisk ? `<span style="color: #ff3366;">RISK</span>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

function applyOverviewFilters() {
  const statusFilter = document.getElementById("filter-status").value;
  const priorityFilter = document.getElementById("filter-priority").value;
  const typeFilter = document.getElementById("filter-type").value;

  let filtered = _overviewProjects;

  if (statusFilter) {
    filtered = filtered.filter(p => p.status === statusFilter);
  }
  if (priorityFilter) {
    filtered = filtered.filter(p => (p.priority || "MEDIUM") === priorityFilter);
  }
  if (typeFilter) {
    filtered = filtered.filter(p => getOperationType(p) === typeFilter);
  }

  renderOverviewOpList(filtered);
  initOverviewMap(filtered);
}

async function initOverviewMap(projects) {
  if (overviewMap) {
    overviewMap.remove();
    overviewMap = null;
  }
  // Wider view to cover all Western Ghats operations
  overviewMap = createMap("overview-map", 18.20, 73.75, 9);

  // Draw constraint zones from map context
  if (_overviewMapContext) {
    const zones = _overviewMapContext.constraint_zones || [];
    zones.forEach(z => {
      const bounds = z.bounds;
      if (bounds && bounds.length === 2) {
        const rect = L.rectangle(
          [bounds[0], bounds[1]],
          {
            color: z.type === "NO_FLY_ZONE" ? "#ff3366" : "#ff9f1c",
            weight: 1.2,
            dashArray: "4, 4",
            fillColor: z.type === "NO_FLY_ZONE" ? "rgba(255, 51, 102, 0.12)" : "rgba(255, 159, 28, 0.10)",
            fillOpacity: 0.35,
          }
        ).addTo(overviewMap);
        rect.bindTooltip(`${z.name}<br><span style="font-size:0.7rem;color:#aaa;">${z.type.replace(/_/g, " ")}</span>`, {
          className: "constraint-tooltip",
          direction: "top",
        });
      }
    });

    // Plot depot markers
    const depots = _overviewMapContext.depots || [];
    depots.forEach(d => {
      const depotIcon = L.divIcon({
        className: "atlas-marker depot",
        html: "\u25a0",
        iconSize: [18, 18],
      });
      const marker = L.marker([d.lat, d.lon], { icon: depotIcon, title: d.name }).addTo(overviewMap);
      marker.bindTooltip(`${d.name}<br><span style="font-size:0.7rem;color:#aaa;">${d.resource_count} assets: ${(d.types || []).join(", ")}</span>`, {
        className: "constraint-tooltip",
        direction: "top",
      });
    });
  }

  // Plot all project locations
  projects.forEach((p) => {
    if (p.site_lat && p.site_lon) {
      let cls = "atlas-marker";
      if (p.status === "CANCELLED" || p.status === "COMPENSATING") {
        cls = "atlas-marker compensating";
      } else if (p.status === "DRAFT") {
        cls = "atlas-marker pending";
      }
      const icon = L.divIcon({
        className: cls,
        html: "\u25cf",
        iconSize: [22, 22],
      });
      const marker = L.marker([p.site_lat, p.site_lon], {
        icon,
        title: p.title,
      }).addTo(overviewMap);
      marker.bindTooltip(`<b>${p.title}</b><br><span style="font-size:0.7rem;">${p.status} \u00b7 ${p.priority || "MEDIUM"}</span>`, {
        className: "constraint-tooltip",
        direction: "top",
      });
      marker.on("click", () => navigateTo(`#/projects/${p.project_id}`));
    }
  });
}


// ── PLAN FIELD OPERATION (WIZARD) ─────────────────────────────────────

function initNewProject() {
  if (newProjectMap) {
    newProjectMap.remove();
    newProjectMap = null;
  }
  setTimeout(() => {
    newProjectMap = createMap("new-project-map", 17.45, 73.72, 9);

    document
      .getElementById("np-lat")
      .addEventListener("change", updateNewProjectMap);
    document
      .getElementById("np-lon")
      .addEventListener("change", updateNewProjectMap);

    // Click on map to select coordinates
    newProjectMap.on("click", (e) => {
      document.getElementById("np-lat").value = e.latlng.lat.toFixed(4);
      document.getElementById("np-lon").value = e.latlng.lng.toFixed(4);
      updateNewProjectMap();
    });

    updateNewProjectMap();
  }, 100);
}

function updateNewProjectMap() {
  const lat = parseFloat(document.getElementById("np-lat").value);
  const lon = parseFloat(document.getElementById("np-lon").value);
  if (!isNaN(lat) && !isNaN(lon) && newProjectMap) {
    newProjectMap.eachLayer((layer) => {
      if (layer instanceof L.Marker) newProjectMap.removeLayer(layer);
    });
    const icon = L.divIcon({
      className: "atlas-marker",
      html: "◉",
      iconSize: [20, 20],
    });
    const marker = L.marker([lat, lon], { icon, draggable: true }).addTo(
      newProjectMap,
    );

    // Drag end handler to update coordinates
    marker.on("dragend", (e) => {
      const pos = marker.getLatLng();
      document.getElementById("np-lat").value = pos.lat.toFixed(4);
      document.getElementById("np-lon").value = pos.lng.toFixed(4);
    });
  }
}

async function analyzeNewProject() {
  const lat = parseFloat(document.getElementById("np-lat").value);
  const lon = parseFloat(document.getElementById("np-lon").value);
  const checkboxes = document.querySelectorAll(
    "#np-equipment-list input:checked",
  );
  const reqEq = Array.from(checkboxes).map((c) => c.value);
  const budget = parseFloat(document.getElementById("np-budget").value);

  try {
    const res = await fetch(`${API_BASE}/projects/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_lat: lat,
        site_lon: lon,
        required_equipment: reqEq,
        budget_requested: budget,
      }),
    });
    const data = await res.json();

    const resEl = document.getElementById("analysis-result");
    resEl.classList.remove("hidden");

    const spatialNote = data.spatial_restrictions_clear
      ? '<div style="color: var(--moss-light); font-weight: 600;">✓ SPATIAL CLEARANCE: Site clear of restricted airspace</div>'
      : '<div style="color: var(--rust); font-weight: 600;">⚠️ RESTRICTED ZONE: Site intersects ' +
        data.spatial_violations.map((v) => v.name).join(", ") +
        "</div>";

    const recs = data.recommended_resources || [];
    const recsHtml = recs
      .map(
        (r) =>
          `<div style="font-size: 0.85rem; color: var(--parchment-muted); margin-bottom: 0.3rem;">• ${r.name} (${r.depot_name}) — ${r.dist_km} km away, battery ${r.battery_pct}%</div>`,
      )
      .join("");

    resEl.innerHTML = `
            ${spatialNote}
            <div style="margin: 0.75rem 0 0.5rem 0; font-family: monospace; font-size: 0.8rem; color: var(--stone);">RECOMMENDED FIELD ASSETS:</div>
            ${recsHtml}
            <button class="btn primary-btn" style="margin-top: 1rem;" onclick="startProject()">Execute Operation Plan →</button>
        `;
  } catch (e) {
    console.error("Error analyzing project:", e);
  }
}

async function startProject() {
  const title = document.getElementById("np-title").value;
  const location = document.getElementById("np-location").value;
  const lat = document.getElementById("np-lat").value;
  const lon = document.getElementById("np-lon").value;
  const budget = document.getElementById("np-budget").value;
  const simMode = document.getElementById("np-sim-mode").value;
  const checkboxes = document.querySelectorAll(
    "#np-equipment-list input:checked",
  );
  const reqEq = Array.from(checkboxes).map((c) => c.value);

  try {
    const res = await fetch(`${API_BASE}/projects/initiate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        location_name: location,
        site_lat: parseFloat(lat),
        site_lon: parseFloat(lon),
        budget_requested: parseFloat(budget),
        required_equipment: reqEq,
        sim_mode: simMode,
      }),
    });
    const data = await res.json();
    if (data.status === "SUCCESS") {
      navigateTo(`#/projects/${data.project.project_id}`);
    }
  } catch (e) {
    console.error("Error initiating operation:", e);
  }
}

// ── PROJECT DETAIL (EDITORIAL & HORIZONTAL SAGA) ───────────────────────

async function loadProjectDetail(pid) {
  try {
    const res = await fetch(`${API_BASE}/projects/${pid}`);
    const p = await res.json();

    document.getElementById("pd-title").innerText = p.title;
    document.getElementById("pd-location-meta").innerText =
      `${p.location_name.toUpperCase()} · ${p.site_lat || 17.68}° N, ${p.site_lon || 74.01}° E`;

    const statusTag = document.getElementById("pd-status-tag");
    statusTag.innerText = `● ${p.status}`;
    statusTag.className = `status-tag ${(p.status || "active").toLowerCase()}`;

    const deleteBtn = document.getElementById("pd-delete-btn");
    if (deleteBtn) {
      deleteBtn.onclick = () => deleteProject(p.project_id);
    }

    // Render Horizontal Saga Workflow
    renderHorizontalSaga(p);

    // Render Field Allocation Matrix
    document.getElementById("pd-allocation-matrix").innerHTML = `
            <div class="matrix-cell">
                <label>FUNDS ALLOCATION</label>
                <div class="val">₹${p.budget_requested ? p.budget_requested.toLocaleString() : "350,000"}</div>
                <div class="sub">${p.funding_status || "RESERVED"}</div>
            </div>
            <div class="matrix-cell">
                <label>FIELD ASSET</label>
                <div class="val">${(p.required_equipment || ["DRONE"])[0]}</div>
                <div class="sub">${p.resource_status || "ALLOCATED"}</div>
            </div>
            <div class="matrix-cell">
                <label>FIELD TEAM</label>
                <div class="val">${(p.required_team || ["Ecology Lead"])[0]}</div>
                <div class="sub">${p.team_status || "ASSIGNED"}</div>
            </div>
            <div class="matrix-cell">
                <label>REGULATORY PERMIT</label>
                <div class="val">${p.permit_status || "APPROVED"}</div>
                <div class="sub">${p.status}</div>
            </div>
        `;

    // Render Event Stream Log
    document.getElementById("pd-event-log-container").innerHTML = (
      p.saga_log || []
    )
      .map(
        (l) =>
          `[${l.created_at.substring(11, 19)}] ${l.step.padEnd(16)} | Status: ${l.status.padEnd(10)} | ${l.details}`,
      )
      .join("\n");

    // Map
    if (detailMap) {
      detailMap.remove();
      detailMap = null;
    }
    setTimeout(() => {
      detailMap = createMap(
        "detail-map",
        p.site_lat || 17.5,
        p.site_lon || 73.9,
        10,
      );
      addKoynaOverlay(detailMap);
      const icon = L.divIcon({
        className: "atlas-marker",
        html: "◉",
        iconSize: [20, 20],
      });
      L.marker([p.site_lat, p.site_lon], { icon }).addTo(detailMap);
    }, 100);
  } catch (e) {
    console.error("Error loading project detail:", e);
  }
}

function renderHorizontalSaga(p) {
  const isCompensating =
    p.status === "COMPENSATING" ||
    p.status === "CANCELLED" ||
    p.permit_status === "REJECTED";
  const viz = document.getElementById("pd-saga-viz");

  const steps = [
    { name: "FUNDS", status: p.funding_status || "RESERVED" },
    { name: "RESOURCES", status: p.resource_status || "ALLOCATED" },
    { name: "TEAM", status: p.team_status || "ASSIGNED" },
    { name: "PERMIT", status: p.permit_status || "APPROVED" },
    { name: "OPERATION", status: p.status },
  ];

  let html = "";
  steps.forEach((s, idx) => {
    let symbol = "●";
    let nodeCls = "completed";
    if (
      s.status === "REJECTED" ||
      s.status === "CANCELLED" ||
      s.status === "RELEASED" ||
      s.status === "UNASSIGNED"
    ) {
      symbol = "✕";
      nodeCls = "failed";
    } else if (s.status === "PENDING" || s.status === "IN_PROGRESS") {
      symbol = "◉";
      nodeCls = "running";
    }

    html += `
            <div class="saga-node ${nodeCls}">
                <div class="saga-node-symbol">${symbol}</div>
                <div class="saga-node-name">${s.name}</div>
                <div class="saga-node-status">${s.status}</div>
            </div>
        `;

    if (idx < steps.length - 1) {
      const connectorCls = isCompensating
        ? "saga-connector rollback"
        : "saga-connector active";
      html += `<div class="${connectorCls}"></div>`;
    }
  });

  viz.innerHTML = html;
}

// ── EXPEDITIONS LIST (ALL PROJECTS) ────────────────────────────────────

async function loadProjectsList() {
  try {
    const res = await fetch(`${API_BASE}/projects`);
    const projects = await res.json();

    const tbody = document.getElementById("all-projects-body");
    tbody.innerHTML = projects
      .map(
        (p) => `
            <tr class="ledger-row" onclick="navigateTo('#/projects/${p.project_id}')">
                <td>${p.title}</td>
                <td style="color: var(--stone);">${p.location_name}</td>
                <td><span class="status-tag ${(p.status || "active").toLowerCase()}">● ${p.status}</span></td>
                <td style="font-family: monospace;">₹${p.budget_requested ? p.budget_requested.toLocaleString() : "350,000"}</td>
                <td style="display: flex; gap: 1rem; align-items: center;">
                    <a href="#/projects/${p.project_id}" class="btn secondary-btn" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;">View Expedition →</a>
                    <button class="btn" onclick="event.stopPropagation(); deleteProject('${p.project_id}')" style="color: var(--rust); background: transparent; border: none; font-size: 1.1rem; padding: 0.25rem; cursor: pointer;" title="Delete Expedition">🗑️</button>
                </td>
            </tr>
        `,
      )
      .join("");
  } catch (e) {
    console.error("Error loading expeditions list:", e);
  }
}

// ── FIELD ASSETS (RESOURCES) ───────────────────────────────────────────

async function loadResourcesData() {
  try {
    const res = await fetch(`${API_BASE}/resources`);
    const data = await res.json();

    document.getElementById("res-equipment-body").innerHTML = (
      data.resources || []
    )
      .map(
        (r) => `
            <tr>
                <td style="font-weight: 600;">${r.name}</td>
                <td style="color: var(--stone);">${r.resource_type}</td>
                <td>${r.depot_name}</td>
                <td><span class="status-tag active">${r.status} (${r.battery_pct}% bat)</span></td>
            </tr>
        `,
      )
      .join("");

    document.getElementById("res-teams-body").innerHTML = (
      data.team_members || []
    )
      .map(
        (t) => `
            <tr>
                <td style="font-weight: 600;">${t.name}</td>
                <td style="color: var(--stone);">${t.role}</td>
                <td>${t.location_base}</td>
                <td><span class="status-tag active">${t.availability_status}</span></td>
            </tr>
        `,
      )
      .join("");
  } catch (e) {
    console.error("Error loading field assets:", e);
  }
}

// ── SYSTEM CONSOLE ─────────────────────────────────────────────────────

async function loadSystemHealth() {
  try {
    const res = await fetch(`${API_BASE}/system/health`);
    const data = await res.json();
    const h = data.health || {};
    const m = data.metrics || {};

    document.getElementById("sys-health-grid").innerHTML = `
            <div class="matrix-cell">
                <label>POSTGRESQL WAL ENGINE</label>
                <div class="val">${h.postgresql || "Healthy"}</div>
            </div>
            <div class="matrix-cell">
                <label>KAFKA EVENT BUS</label>
                <div class="val">${h.kafka || "Healthy"}</div>
            </div>
            <div class="matrix-cell">
                <label>REDIS CACHE MODEL</label>
                <div class="val">${h.redis || "Healthy"}</div>
            </div>
            <div class="matrix-cell">
                <label>POSTGIS SPATIAL ENGINE</label>
                <div class="val">${h.postgis || "Healthy"}</div>
            </div>
        `;

    const sRes = await fetch(`${API_BASE}/analytics`);
    const aData = await sRes.json();
    const events = aData.recent_events || [];

    document.getElementById("sys-stream").innerText = events
      .map(
        (e) =>
          `[${e.timestamp ? e.timestamp.substring(11, 19) : "00:00:00"}] EVENT_ID: ${e.event_id || "N/A"} | TYPE: ${(e.event_type || "").padEnd(30)} | SAGA: ${e.saga_id || "N/A"}`,
      )
      .join("\n");
  } catch (e) {
    console.error("Error loading system console:", e);
  }
}
