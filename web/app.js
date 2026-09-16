const API_BASE = window.location.origin + "/api";

let currentRoute = "#/";
let activeProjectDetailId = null;

let overviewMap = null;
let detailMap = null;
let newProjectMap = null;

const _mapLayers = { protected: [], nofly: [], depots: [] };
let _overviewProjects = [];
let _overviewMapContext = null;
let _allProjectsCache = {};
let _allResources = [];

function toggleMapLayer(layerName, visible) {
  if (!overviewMap) return;
  (_mapLayers[layerName] || []).forEach(layer => {
    if (visible) overviewMap.addLayer(layer);
    else overviewMap.removeLayer(layer);
  });
}

function countUp(elementId, target, duration = 800) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const start = 0;
  const startTime = performance.now();
  function step(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    el.textContent = Math.round(start + (target - start) * eased);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function getOperationDomain(p) {
  const t = (p.title || '').toUpperCase();
  if (t.includes('HERITAGE') || t.includes('FORT') || t.includes('GPR') || t.includes('EXCAVAT') || t.includes('ASI') || (p.required_equipment || []).includes('RADAR')) {
    return 'HERITAGE';
  }
  if (t.includes('LIDAR') || t.includes('TOPO') || t.includes('MAPPING')) return 'SURVEY';
  return 'CONSERVATION';
}

function getDomainGlyph(domain) {
  switch (domain) {
    case 'HERITAGE': return '🏛';
    case 'SURVEY': return '📡';
    default: return '🌿';
  }
}

function getDomainColor(domain) {
  switch (domain) {
    case 'HERITAGE': return '#C97B4A';
    case 'SURVEY': return '#3FB6C9';
    default: return '#3FA66E';
  }
}

function computeRiskScore(p) {
  let score = 0;
  const riskFlags = p.risk_flags || [];
  score += riskFlags.length * 25;
  if ((p.days_waiting || 0) > 7) score += 20;
  if ((p.days_waiting || 0) > 14) score += 15;
  if (p.status === 'COMPENSATING') score += 30;
  if ((p.budget_remaining || p.budget_requested || 0) < 50000) score += 15;
  return Math.min(score, 100);
}

function getRiskColor(score) {
  if (score >= 75) return '#C24F4F';
  if (score >= 50) return '#E8833A';
  if (score >= 25) return '#C97B4A';
  return '#3FA66E';
}

function getRiskLabel(score) {
  if (score >= 75) return 'CRITICAL';
  if (score >= 50) return 'HIGH';
  if (score >= 25) return 'MEDIUM';
  return 'LOW';
}

function getSagaStepperHTML(project) {
  // 5 steps: FUNDS, RESOURCES, TEAM, PERMIT, ACTIVE
  const steps = ['FUNDS', 'RESOURCES', 'TEAM', 'PERMIT', 'ACTIVE'];
  const statusToStep = {
    'DRAFT': 0,
    'PENDING_FUNDS': 0,
    'PENDING_RESOURCES': 1,
    'PENDING_TEAM': 2,
    'PENDING_PERMIT': 3,
    'ACTIVE': 5,
    'COMPENSATING': 3,
    'CANCELLED': 5,
  };
  
  const isCompensating = project.status === 'COMPENSATING' || project.status === 'CANCELLED';
  let currentStep = statusToStep[project.status] ?? 0;
  if (project.status === 'ACTIVE') currentStep = 5;
  
  let html = '<div class="saga-stepper">';
  steps.forEach((step, idx) => {
    let dotCls = 'pending';
    let barCls = '';
    if (isCompensating) {
      dotCls = idx < currentStep ? 'failed' : 'pending';
    } else if (idx < currentStep) {
      dotCls = 'done';
      barCls = 'done';
    } else if (idx === currentStep) {
      dotCls = 'current';
    }
    
    if (idx > 0) {
      const prevDone = isCompensating ? false : idx <= currentStep;
      html += `<div class="saga-step ${prevDone ? 'done' : (isCompensating && idx < currentStep ? 'failed' : '')}"></div>`;
    }
    html += `<div class="saga-step-dot ${dotCls}" title="${step}"></div>`;
  });
  html += '</div>';
  return html;
}

function getPriorityBadgeHTML(priority) {
  const colors = {
    'CRITICAL': '#C24F4F',
    'HIGH': '#E8833A',
    'MEDIUM': '#C97B4A',
    'LOW': '#3FA66E',
  };
  const color = colors[(priority || 'MEDIUM').toUpperCase()] || '#C1B8AA';
  return `<span class="priority-badge" style="color: ${color}; border-color: ${color};">${priority || 'MEDIUM'}</span>`;
}

// Decorative pixel-mosaic block used in every page-hero. Renders once per
// .pixel-mosaic container found in the DOM — each view keeps its own copy
// since views are hidden/shown, not re-created, so this only needs to run once.
function renderPixelMosaics() {
  const shades = ['#1c2b18', '#22351c', '#2c4a24', '#357b4d', '#499c63', '#7ea83e', '#c7d79a'];
  const cols = 14, rows = 5;
  document.querySelectorAll('.pixel-mosaic').forEach(el => {
    let html = '';
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const bias = (r / (rows - 1)) * 0.55 + (c / (cols - 1)) * 0.45;
        const show = Math.random() < (0.3 + bias * 0.55);
        if (!show) { html += '<span class="px"></span>'; continue; }
        const weighted = Math.min(shades.length - 1, Math.floor(Math.random() * shades.length * (0.4 + bias * 0.9)));
        html += `<span class="px" style="background:${shades[weighted]}"></span>`;
      }
    }
    el.innerHTML = html;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("hashchange", handleRouting);
  handleRouting();
  renderPixelMosaics();
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
      "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      {
        attribution: "&copy; OpenStreetMap contributors",
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

// ── OVERVIEW AND ATLAS ───────────────────────────────────────────────────

async function loadOverview() {
  try {
    const [analyticsRes, projectsRes, mapCtxRes] = await Promise.all([
      fetch(`${API_BASE}/analytics`),
      fetch(`${API_BASE}/projects`),
      fetch(`${API_BASE}/map_context`).catch(() => null),
    ]);

    const data = await analyticsRes.json();
    const m = data.metrics || {};

    _overviewProjects = await projectsRes.json();
    if (mapCtxRes && mapCtxRes.ok) {
      _overviewMapContext = await mapCtxRes.json();
    }

    // Compute metrics
    const activeCount = _overviewProjects.filter(p => p.status === 'ACTIVE').length;
    const pendingCount = _overviewProjects.filter(p => p.status === 'DRAFT').length;
    const compCount = _overviewProjects.filter(p => p.status === 'COMPENSATING').length;
    const atRisk = _overviewProjects.filter(p => (p.risk_flags || []).length > 0 || p.status === 'COMPENSATING').length;
    const totalNonCancelled = _overviewProjects.filter(p => p.status !== 'CANCELLED').length;
    const healthyCount = _overviewProjects.filter(p => p.status === 'ACTIVE' || p.status === 'DRAFT').length;
    const healthPct = totalNonCancelled > 0 ? Math.round((healthyCount / totalNonCancelled) * 100) : 100;

    // Animate counters
    countUp('hero-active', activeCount);
    countUp('hero-pending', pendingCount);
    countUp('hero-comp', compCount);
    countUp('hero-risk', atRisk);

    // Saga health bar
    setTimeout(() => {
      const fill = document.getElementById('saga-health-fill');
      const pctEl = document.getElementById('saga-health-pct');
      if (fill) fill.style.width = `${healthPct}%`;
      if (pctEl) pctEl.textContent = `${healthPct}% healthy`;
    }, 100);

    // Legacy summary
    const opSummary = document.getElementById('overview-op-summary');
    if (opSummary) {
      opSummary.textContent = `${String(activeCount).padStart(2,'0')} ACTIVE · ${String(pendingCount).padStart(2,'0')} CLEARANCE · ${String(compCount).padStart(2,'0')} COMPENSATING`;
    }

    renderOverviewOpList(_overviewProjects);
    initOverviewMap(_overviewProjects);
  } catch (e) {
    console.error('Error loading operations atlas:', e);
  }
}

// Placeholder field-note flavor text, keyed by domain, shown on field-log
// cards until a real `field_note` field exists on the project record.
// Picked deterministically per project so it doesn't change on re-render.
const FIELD_NOTES = {
  CONSERVATION: [
    'Bloom density higher than baseline near the western ridge — flagged for follow-up transect.',
    'Camera trap yield up 12% since last rotation.',
    'Water table readings stable across all sample points.',
  ],
  HERITAGE: [
    'Sub-surface anomaly detected at grid section C4 — pending GPR confirmation.',
    'Masonry erosion consistent with monsoon exposure, noted for restoration brief.',
    'Site access road cleared for equipment transport.',
  ],
  SURVEY: [
    'Canopy cover denser than survey plan assumed — flight altitude adjusted +15m.',
    'Point cloud density exceeds spec in the northern quadrant.',
    'Ridge-line scan complete; valley pass scheduled next.',
  ],
};

function getFieldNote(p, domain) {
  if (p.field_note) return p.field_note;
  const notes = FIELD_NOTES[domain] || FIELD_NOTES.CONSERVATION;
  let hash = 0;
  const idStr = String(p.project_id || p.title || '');
  for (let i = 0; i < idStr.length; i++) hash = (hash * 31 + idStr.charCodeAt(i)) >>> 0;
  return notes[hash % notes.length];
}

function getStampClass(status) {
  if (status === 'COMPENSATING' || status === 'CANCELLED') return 'compensating';
  if (status === 'ACTIVE') return '';
  return 'pending';
}

// Builds one field-log index card. opts.navigate=true routes to the full
// project detail page (used on Expeditions); default opens the quick
// saga drawer (used on Operations Atlas).
function buildFieldCardHTML(p, opts = {}) {
  const domain = getOperationDomain(p);
  const domainCls = domain === 'HERITAGE' ? 'domain-heritage' : domain === 'SURVEY' ? 'domain-survey' : '';
  const riskScore = computeRiskScore(p);
  const riskColor = getRiskColor(riskScore);
  const daysWaiting = p.days_waiting != null ? p.days_waiting : 0;
  const budget = p.budget_remaining != null
    ? Math.round(p.budget_remaining).toLocaleString()
    : (p.budget_requested || 0).toLocaleString();
  const note = getFieldNote(p, domain);
  const clickAction = opts.navigate
    ? `navigateTo('#/projects/${p.project_id}')`
    : `openSagaDrawer('${p.project_id}')`;

  return `
    <div class="field-card ${domainCls}" onclick="${clickAction}" data-id="${p.project_id}"
         data-status="${p.status}" data-priority="${p.priority || 'MEDIUM'}" data-domain="${domain}">
      <div class="field-card-top">
        <div>
          <div class="field-card-title">${p.title}</div>
          <div class="field-card-meta">${p.location_name} · ${domain}</div>
        </div>
        <span class="field-card-stamp ${getStampClass(p.status)}">${p.status}</span>
      </div>
      ${getSagaStepperHTML(p)}
      <div class="field-card-note">&ldquo;${note}&rdquo;</div>
      <div class="field-card-foot">
        <span>Day ${daysWaiting} in phase · ₹${budget}</span>
        <span class="risk-chip" style="color:${riskColor};">Risk ${riskScore}</span>
      </div>
    </div>
  `;
}


function buildFlatRowHTML(p, opts = {}) {
    const domain = getOperationDomain(p);
    const clickAction = opts.navigate
      ? `navigateTo('#/projects/${p.project_id}')`
      : `openSagaDrawer('${p.project_id}')`;

    const priorityStr = (p.priority || 'MEDIUM').toUpperCase();
    const daysWaiting = p.days_waiting != null ? p.days_waiting : 0;
    const nearestKm = p.nearest_asset_km != null ? `${p.nearest_asset_km} km` : null;
    const budget = p.budget_requested != null
      ? `₹${Math.round(p.budget_requested).toLocaleString('en-IN')}`
      : null;
    const hasRisk = (p.risk_flags || []).length > 0;

    // Status colours
    const isFailed = p.status === 'CANCELLED' || p.status === 'COMPENSATING';
    const isActive = p.status === 'ACTIVE';
    const statusColor = isFailed ? '#d46565' : (isActive ? '#71b071' : '#c49a6c');

    // Location token — truncate if long
    const loc = (p.location_name || '').toUpperCase();

    // Bottom metrics row
    const metrics = [];
    metrics.push(`${daysWaiting}d`);
    if (nearestKm) metrics.push(`ASSET ${nearestKm}`);
    if (budget) metrics.push(budget);
    if (hasRisk) metrics.push(`<span class="frow-risk">RISK</span>`);

    return `
      <div class="flat-op-row" onclick="${clickAction}">
        <div class="frow-top">
          <span class="frow-title">${p.title}</span>
          <span class="frow-status" style="color:${statusColor}"><span class="frow-dot" style="background:${statusColor}"></span>${p.status}</span>
        </div>
        <div class="frow-meta">
          <span class="frow-priority prio-${priorityStr.toLowerCase()}">${priorityStr}</span>
          <span class="frow-loc">${loc}</span>
          <span class="frow-domain">${domain}</span>
        </div>
        <div class="frow-foot">${metrics.join('<span class="frow-sep">·</span>')}</div>
      </div>
    `;
}

function renderOverviewOpList(projects) {
  const opList = document.getElementById('overview-op-list');
  if (!opList) return;

  if (!projects || projects.length === 0) {
    opList.innerHTML = `
      <div style="padding: 2.5rem 1rem; text-align: center;">
        <div style="font-family: 'Garet', sans-serif; font-size: 1rem; color: var(--parchment); margin-bottom: 0.75rem; letter-spacing: 0.1em;">NO FIELD OPERATIONS</div>
        <div style="font-size: 0.8rem; color: var(--stone); line-height: 1.7; margin-bottom: 1.25rem;">No active or planned operations in this region.<br>Ecotone orchestrates fund allocation, asset dispatch,<br>team assignment, and regulatory clearance as<br>a single transactional workflow.</div>
        <a href="#/projects/new" class="btn primary-btn" style="font-size: 0.75rem; padding: 0.5rem 1rem;">PLAN FIRST OPERATION</a>
      </div>`;
    return;
  }

  opList.innerHTML = projects.map(p => buildFlatRowHTML(p)).join('');
}

function applyOverviewFilters() {
  const statusFilter = document.getElementById('filter-status')?.value || '';
  const priorityFilter = document.getElementById('filter-priority')?.value || '';
  const typeFilter = document.getElementById('filter-type')?.value || '';
  const domainFilter = document.getElementById('filter-domain')?.value || '';

  let filtered = _overviewProjects;
  if (statusFilter) filtered = filtered.filter(p => p.status === statusFilter);
  if (priorityFilter) filtered = filtered.filter(p => (p.priority || 'MEDIUM') === priorityFilter);
  if (typeFilter) filtered = filtered.filter(p => getOperationDomain(p) === typeFilter);
  if (domainFilter) filtered = filtered.filter(p => getOperationDomain(p) === domainFilter);

  renderOverviewOpList(filtered);
  initOverviewMap(filtered);
}

async function initOverviewMap(projects) {
  if (overviewMap) {
    overviewMap.remove();
    overviewMap = null;
  }
  _mapLayers.protected = []; _mapLayers.nofly = []; _mapLayers.depots = [];
  // Wider view to cover all Western Ghats operations
  overviewMap = createMap("overview-map", 18.55, 73.60, 10);

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
        );
        rect.bindTooltip(`${z.name}<br><span style="font-size:0.7rem;color:#aaa;">${z.type.replace(/_/g, " ")}</span>`, {
          className: "constraint-tooltip",
          direction: "top",
        });
        
        if (z.type === "NO_FLY_ZONE") {
            _mapLayers.nofly.push(rect);
        } else {
            _mapLayers.protected.push(rect);
        }
        rect.addTo(overviewMap);
      }
    });

    // Plot depot markers
    const depots = _overviewMapContext.depots || [];
    depots.forEach(d => {
      const depotIcon = L.divIcon({
        className: "atlas-marker depot",
        html: "■",
        iconSize: [18, 18],
      });
      const marker = L.marker([d.lat, d.lon], { icon: depotIcon, title: d.name });
      marker.bindTooltip(`${d.name}<br><span style="font-size:0.7rem;color:#aaa;">${d.resource_count} assets: ${(d.types || []).join(", ")}</span>`, {
        className: "constraint-tooltip",
        direction: "top",
      });
      _mapLayers.depots.push(marker);
      marker.addTo(overviewMap);
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
        html: "●",
        iconSize: [22, 22],
      });
      const marker = L.marker([p.site_lat, p.site_lon], {
        icon,
        title: p.title,
      }).addTo(overviewMap);
      marker.bindTooltip(`<b>${p.title}</b><br><span style="font-size:0.7rem;">${p.status} · ${p.priority || "MEDIUM"}</span>`, {
        className: "constraint-tooltip",
        direction: "top",
      });
      marker.on("click", () => navigateTo(`#/projects/${p.project_id}`));
    }
  });
}

async function openSagaDrawer(projectId) {
  const overlay = document.getElementById('saga-drawer-overlay');
  if (!overlay) { navigateTo(`#/projects/${projectId}`); return; }
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';

  // Pan map to selected project
  const proj = _overviewProjects.find(x => x.project_id === projectId);
  if (proj && overviewMap && proj.site_lat && proj.site_lon) {
    overviewMap.flyTo([proj.site_lat, proj.site_lon], 11, { duration: 0.8 });
  }

  // Show loading state
  document.getElementById('drawer-title').textContent = 'LOADING...';
  document.getElementById('drawer-body').innerHTML = '<div style="padding: 2rem; color: var(--stone); font-family: \'IBM Plex Mono\', monospace; font-size: 0.8rem;">Fetching saga timeline...</div>';

  try {
    let p = _allProjectsCache[projectId];
    if (!p) {
      const res = await fetch(`${API_BASE}/projects/${projectId}`);
      p = await res.json();
      _allProjectsCache[projectId] = p;
    }

    const domain = getOperationDomain(p);
    const riskScore = computeRiskScore(p);
    const riskColor = getRiskColor(riskScore);

    document.getElementById('drawer-title').textContent = p.title.toUpperCase();
    document.getElementById('drawer-subtitle').textContent = `${domain} · SAGA ORCHESTRATION TIMELINE`;

    // Saga steps
    const sagaSteps = [
      { name: 'PROJECT CREATED', status: 'done', detail: `Saga initiated · Budget ₹${(p.budget_requested||0).toLocaleString()}`, time: p.created_at },
      { name: 'FUNDS RESERVED', status: (p.funding_status === 'RELEASED' || p.status === 'COMPENSATING') ? 'failed' : 'done', detail: `Grant allocation confirmed`, time: p.created_at },
      { name: 'RESOURCES ALLOCATED', status: (p.resource_status === 'RELEASED' || p.status === 'COMPENSATING') ? 'failed' : (p.status === 'DRAFT' ? 'pending' : 'done'), detail: `Equipment dispatched from depot`, time: p.created_at },
      { name: 'TEAM ASSIGNED', status: (p.team_status === 'UNASSIGNED') ? 'failed' : (p.status === 'DRAFT' ? 'pending' : 'done'), detail: `Field team allocated`, time: p.created_at },
      { name: 'PERMIT REQUESTED', status: (p.permit_status === 'REJECTED') ? 'failed' : (p.status === 'ACTIVE' ? 'done' : 'current'), detail: p.permit_status === 'REJECTED' ? 'Karnataka Forest Department — Rejected' : 'Ministry of Environment', time: p.created_at },
      { name: p.status === 'ACTIVE' ? 'OPERATION ACTIVE' : (p.status === 'COMPENSATING' ? 'COMPENSATING' : p.status), status: p.status === 'ACTIVE' ? 'done' : (p.status === 'COMPENSATING' || p.status === 'CANCELLED' ? 'failed' : 'pending'), detail: p.status === 'ACTIVE' ? 'Field deployment confirmed' : (p.status === 'COMPENSATING' ? 'Compensation in progress — releasing all resources' : 'Not yet reached'), time: p.created_at },
    ];

    const sagaTimelineHTML = sagaSteps.map(step => `
      <div class="saga-timeline-node">
        <div class="saga-timeline-icon ${step.status}">${step.status === 'done' ? '✓' : step.status === 'failed' ? '✕' : step.status === 'current' ? '◉' : '○'}</div>
        <div class="saga-timeline-content">
          <div class="saga-timeline-step">${step.name}</div>
          <div class="saga-timeline-detail">${step.detail}</div>
          ${step.time ? `<div class="saga-timeline-time">${step.time.substring(0,19).replace('T',' ')}</div>` : ''}
        </div>
      </div>
    `).join('');

    const salogHtml = (p.saga_log || []).slice(0, 8).map(l =>
      `<div style="font-family: 'IBM Plex Mono', monospace; font-size: 0.65rem; color: var(--stone); margin-bottom: 0.2rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">[${(l.created_at||'').substring(11,19)}] ${l.step} — ${l.status} — ${l.details || ''}</div>`
    ).join('');

    document.getElementById('drawer-body').innerHTML = `
      <div class="drawer-meta-row">
        <div class="drawer-meta-cell">
          <div class="drawer-meta-label">Status</div>
          <div class="drawer-meta-value status-tag ${(p.status||'').toLowerCase()}">● ${p.status}</div>
        </div>
        <div class="drawer-meta-cell">
          <div class="drawer-meta-label">Priority</div>
          <div class="drawer-meta-value" style="color:${getRiskColor(riskScore)}">${p.priority}</div>
        </div>
        <div class="drawer-meta-cell">
          <div class="drawer-meta-label">Risk Score</div>
          <div class="drawer-meta-value" style="color:${riskColor}">${riskScore}/100</div>
        </div>
      </div>
      <div style="margin-bottom: 1.25rem;">
        <div style="font-family: 'IBM Plex Mono', monospace; font-size: 0.62rem; color: var(--stone); text-transform: uppercase; margin-bottom: 0.75rem;">Orchestration Timeline</div>
        ${sagaTimelineHTML}
      </div>
      ${salogHtml ? `<div style="margin-top: 1rem; border-top: 1px solid var(--border-subtle); padding-top: 0.75rem;"><div style="font-family: 'IBM Plex Mono', monospace; font-size: 0.62rem; color: var(--stone); margin-bottom: 0.5rem;">RAW SAGA LOG</div>${salogHtml}</div>` : ''}
      <div style="margin-top: 1.25rem; display: flex; gap: 0.75rem;">
        <a href="#/projects/${p.project_id}" class="btn primary-btn" style="font-size: 0.75rem; padding: 0.45rem 0.9rem;">Full Detail →</a>
        <button class="btn" onclick="closeSagaDrawer(null)" style="border: 1px solid var(--border-medium); font-size: 0.75rem; padding: 0.45rem 0.9rem;">Close</button>
      </div>
    `;
  } catch(e) {
    document.getElementById('drawer-body').innerHTML = '<div style="padding: 1rem; color: var(--rust);">Error loading project detail.</div>';
  }
}

function closeSagaDrawer(event) {
  if (event && event.target !== document.getElementById('saga-drawer-overlay')) return;
  const overlay = document.getElementById('saga-drawer-overlay');
  if (overlay) overlay.classList.remove('open');
  document.body.style.overflow = '';
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
            <div style="margin: 0.75rem 0 0.5rem 0; font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; color: var(--stone);">RECOMMENDED FIELD ASSETS:</div>
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
  const volunteersVal = document.getElementById("np-volunteers")?.value;
  const volunteers = volunteersVal ? Math.max(0, parseInt(volunteersVal, 10) || 0) : 0;
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
        volunteers: volunteers,
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
                <label>VOLUNTEERS</label>
                <div class="val">${p.volunteers != null ? p.volunteers : 0}</div>
                <div class="sub">FIELD PERSONNEL</div>
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
    renderExpeditionBoard(projects);
  } catch (e) {
    console.error("Error loading expeditions list:", e);
  }
}

function buildExpeditionCardHTML(p) {
  const domain = getOperationDomain(p);
  const riskScore = computeRiskScore(p);
  const riskColor = getRiskColor(riskScore);
  const daysWaiting = p.days_waiting != null ? p.days_waiting : 0;
  const budget = p.budget_remaining != null
    ? Math.round(p.budget_remaining).toLocaleString('en-IN')
    : (p.budget_requested || 0).toLocaleString('en-IN');
  const note = getFieldNote(p, domain);

  // Status styling
  const isFailed = p.status === 'CANCELLED' || p.status === 'COMPENSATING';
  const isActive = p.status === 'ACTIVE';
  const statusColor = isFailed ? '#d46565' : (isActive ? '#71b071' : '#c49a6c');

  // Domain accent colour for left border
  const domainColor = domain === 'HERITAGE' ? 'var(--clay)' : domain === 'SURVEY' ? '#3FB6C9' : 'var(--moss-light)';

  // Compact saga progress bar — 4 segments (funds/resources/team/permit)
  const sagaMap = { 'DRAFT': 1, 'ACTIVE': 4, 'COMPENSATING': 2, 'CANCELLED': 0 };
  const sagaStep = sagaMap[p.status] ?? 1;
  const segments = ['FUNDS', 'ASSETS', 'TEAM', 'PERMIT'];
  const segHTML = segments.map((label, i) => {
    const done = i < sagaStep;
    const current = i === sagaStep - 1 && !isActive;
    const failed = isFailed && i < 2;
    const color = failed ? '#d46565' : (done ? '#71b071' : 'rgba(255,255,255,0.08)');
    return `<div class="exp-seg-wrap">
      <div class="exp-seg" style="background:${color};"></div>
      <div class="exp-seg-label">${label}</div>
    </div>`;
  }).join('');

  // Priority colour
  const priorityColors = { CRITICAL: '#d46565', HIGH: '#c49a6c', MEDIUM: '#a8a097', LOW: '#71b071' };
  const priorityStr = (p.priority || 'MEDIUM').toUpperCase();
  const priorityColor = priorityColors[priorityStr] || '#a8a097';

  return `
    <div class="exp-card"
         onclick="navigateTo('#/projects/${p.project_id}')"
         data-id="${p.project_id}" data-status="${p.status}">

      <div class="exp-card-top">
        <div class="exp-card-title">${p.title}</div>
        <span class="exp-card-status" style="color:${statusColor};">
          <span class="exp-card-dot" style="background:${statusColor};"></span>${p.status}
        </span>
      </div>

      <div class="exp-card-meta">
        <span class="exp-priority" style="color:${priorityColor}; border-color:${priorityColor}40;">${priorityStr}</span>
        <span class="exp-loc">${(p.location_name || '').toUpperCase()}</span>
        <span class="exp-domain">${domain}</span>
      </div>

      <div class="exp-saga">
        ${segHTML}
      </div>

      <div class="exp-note">${note}</div>

      <div class="exp-foot">
        <span>DAY ${daysWaiting} IN PHASE &nbsp;·&nbsp; ₹${budget}</span>
        <span style="color:${riskColor};">RISK ${riskScore}</span>
      </div>
    </div>
  `;
}

function renderExpeditionBoard(projects) {
  const board = document.getElementById("expedition-board");
  if (!board) return;

  if (!projects || projects.length === 0) {
    board.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 3rem 1rem; text-align: center;">
        <div style="font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; color: var(--stone); letter-spacing: 0.1em; margin-bottom: 1rem;">NO EXPEDITIONS LOGGED</div>
        <a href="#/projects/new" class="btn primary-btn" style="font-size: 0.75rem; padding: 0.5rem 1.25rem;">PLAN FIRST OPERATION</a>
      </div>`;
    return;
  }

  board.innerHTML = projects.map(p => `
    <div style="position: relative;">
      ${buildExpeditionCardHTML(p)}
      <button onclick="event.stopPropagation(); deleteProject('${p.project_id}')"
        style="position: absolute; top: 0.75rem; right: 0.75rem; background: transparent; border: none; color: rgba(194,79,79,0.5); font-size: 0.8rem; cursor: pointer; padding: 0; line-height: 1; transition: color 0.15s ease;"
        onmouseover="this.style.color='#c24f4f'" onmouseout="this.style.color='rgba(194,79,79,0.5)'"
        title="Delete expedition">✕</button>
    </div>
  `).join('');
}

// ── FIELD ASSETS (RESOURCES) ───────────────────────────────────────────

async function loadResourcesData() {
  try {
    const res = await fetch(`${API_BASE}/resources`);
    const data = await res.json();
    _allResources = data.resources || [];

    renderAssetGrid(_allResources);

    // Teams table
    document.getElementById('res-teams-body').innerHTML = (data.team_members || []).map(t => `
      <tr>
        <td style="font-weight: 600;">${t.name}</td>
        <td style="color: var(--stone);">${t.role}</td>
        <td style="color: var(--stone);">${t.specialty}</td>
        <td>${t.location_base}</td>
        <td><span class="status-tag ${t.availability_status === 'AVAILABLE' ? 'active' : 'pending'}" style="font-size:0.7rem;">● ${t.availability_status}</span></td>
      </tr>
    `).join('');
  } catch(e) {
    console.error('Error loading field assets:', e);
  }
}

function renderAssetGrid(resources) {
  const grid = document.getElementById('asset-grid');
  if (!grid) return;

  if (!resources || resources.length === 0) {
    grid.innerHTML = '<div style="color:var(--stone); font-size:0.8rem; padding:1.5rem 0; grid-column:1/-1;">No assets match the current filters.</div>';
    return;
  }

  grid.innerHTML = resources.map(r => {
    const isAvailable = r.status === 'AVAILABLE';
    const cardStatusCls = isAvailable ? 'status-available' : 'status-reserved';
    const assetId = r.resource_id || r.asset_id || '—';
    const rate = r.hourly_cost != null ? `₹${r.hourly_cost}/hr` : '—';
    return `
      <div class="asset-card">
        <div class="asset-card-body">
          <div class="asset-card-header">
            <div class="asset-name">${r.name}</div>
          </div>
          <div><span class="asset-type-badge">${r.resource_type}</span></div>
          <div class="asset-card-meta-grid">
            <span class="asset-meta-label">ID</span>
            <span class="asset-meta-value">${assetId}</span>
            <span class="asset-meta-label">BASE</span>
            <span class="asset-meta-value">${r.depot_name || '—'}</span>
            <span class="asset-meta-label">RATE</span>
            <span class="asset-meta-value">${rate}</span>
          </div>
        </div>
        <div class="asset-bottom-status ${cardStatusCls}">${r.status}</div>
      </div>
    `;
  }).join('');
}

function applyAssetFilters() {
  const typeFilter = document.getElementById('asset-filter-type')?.value || '';
  const statusFilter = document.getElementById('asset-filter-status')?.value || '';
  let filtered = _allResources;
  if (typeFilter) filtered = filtered.filter(r => r.resource_type === typeFilter);
  if (statusFilter) filtered = filtered.filter(r => r.status === statusFilter);
  renderAssetGrid(filtered);
}

// ── SYSTEM CONSOLE ─────────────────────────────────────────────────────

async function loadSystemHealth() {
  try {
    const [sysRes, projectsRes] = await Promise.all([
      fetch(`${API_BASE}/system/events`),
      fetch(`${API_BASE}/projects`),
    ]);
    const data = await sysRes.json();
    const projects = await projectsRes.json();
    const m = data.metrics || {};

    // Topic health tiles
    const topics = [
      { name: 'project.created', count: m.total_projects || 0 },
      { name: 'funds.reserved', count: m.active_projects || 0 },
      { name: 'resources.reserved', count: m.active_projects || 0 },
      { name: 'team.assigned', count: m.active_projects || 0 },
      { name: 'permit.approved', count: m.active_projects || 0 },
      { name: 'permit.rejected', count: m.compensating_projects || 0 },
      { name: 'project.activated', count: m.active_projects || 0 },
      { name: 'project.cancelled', count: m.cancelled_projects || 0 },
      { name: 'resources.released', count: m.compensating_projects || 0 },
      { name: 'funds.released', count: m.compensating_projects || 0 },
    ];

    const topicGrid = document.getElementById('topic-grid');
    if (topicGrid) {
      topicGrid.innerHTML = topics.map(t => {
        const isHealthy = t.count >= 0;
        const dotCls = t.count > 0 ? 'healthy' : 'warn';
        return `
          <div class="topic-tile">
            <div class="topic-name">aegis.event.${t.name}</div>
            <div class="topic-status">
              <div class="topic-dot ${dotCls}"></div>
              <span class="topic-status-text">${t.count > 0 ? t.count + ' events' : 'Idle'}</span>
            </div>
          </div>
        `;
      }).join('');
    }

    // Populate saga flow select
    const sagaSelect = document.getElementById('saga-flow-select');
    if (sagaSelect && projects.length) {
      const existingOptions = Array.from(sagaSelect.options).map(o => o.value);
      projects.forEach(p => {
        if (!existingOptions.includes(p.project_id)) {
          const opt = document.createElement('option');
          opt.value = p.project_id;
          opt.textContent = `${p.title} — ${p.status}`;
          sagaSelect.appendChild(opt);
        }
      });
      // Store projects for flow rendering
      window._consoleProjects = projects;
    }

    // Event stream
    const events = data.recent_events || [];
    const stream = document.getElementById('sys-stream');
    if (stream) {
      if (events.length) {
        stream.innerHTML = events.map(e => {
          const ts = (e.timestamp || '').substring(11, 19);
          const type = (e.event_type || '').padEnd(40);
          return `<div style="margin-bottom: 0.2rem; font-size: 0.75rem;"><span style="color: var(--moss-light);">[${ts}]</span> <span style="color: var(--parchment);">${e.event_type}</span> <span style="color: var(--stone);">saga:${e.saga_id || 'N/A'}</span></div>`;
        }).join('');
      } else {
        stream.innerHTML = '<div style="color: var(--stone); font-size: 0.8rem; padding: 1rem;">No events in stream. Run seed_operations.py and restart server to populate.</div>';
      }
    }
  } catch (e) {
    console.error('System console error:', e);
  }
}

function loadSagaFlow() {
  const select = document.getElementById('saga-flow-select');
  const pid = select?.value;
  const projects = window._consoleProjects || [];
  const p = projects.find(x => x.project_id === pid);
  if (!p) return;

  const isCompensating = p.status === 'COMPENSATING' || p.status === 'CANCELLED';
  const nodes = [
    { name: 'PROJECT\nCREATED', status: 'done' },
    { name: 'FUNDS\nRESERVED', status: 'done' },
    { name: 'RESOURCES\nRESERVED', status: p.status === 'DRAFT' ? 'current' : 'done' },
    { name: 'TEAM\nASSIGNED', status: p.status === 'DRAFT' ? 'pending' : 'done' },
    { name: 'PERMIT\nREQUESTED', status: p.permit_status === 'REJECTED' ? 'failed' : (p.status === 'ACTIVE' ? 'done' : 'current') },
    { name: p.status === 'ACTIVE' ? 'ACTIVATED' : (isCompensating ? 'CANCELLED' : 'PENDING'), status: p.status === 'ACTIVE' ? 'done' : (isCompensating ? 'failed' : 'pending') },
  ];

  const nodesEl = document.getElementById('saga-flow-nodes');
  if (!nodesEl) return;

  nodesEl.innerHTML = nodes.map((node, idx) => {
    const arrowCls = isCompensating ? 'failed' : (node.status === 'done' ? 'active' : '');
    return `
      <div class="saga-flow-node">
        <div class="saga-flow-node-box ${node.status}">
          <div class="saga-flow-node-name">${node.name.replace('\n', '<br>')}</div>
        </div>
      </div>
      ${idx < nodes.length - 1 ? `<div class="saga-flow-arrow ${arrowCls}">${isCompensating ? '←' : '→'}</div>` : ''}
    `;
  }).join('');
}


// ── FIELD ASSETS: Add Asset Modal Functions ───────────────────────────────────

function openAddAssetModal() {
  document.getElementById('add-asset-modal').style.display = 'flex';
}

function closeAddAssetModal() {
  document.getElementById('add-asset-modal').style.display = 'none';
  document.getElementById('add-asset-form').reset();
  document.getElementById('add-asset-error').textContent = '';
}

function submitAddAsset() {
  const name = document.getElementById('na-name').value.trim();
  const type = document.getElementById('na-type').value.trim();
  const assetId = document.getElementById('na-id').value.trim();
  const base = document.getElementById('na-base').value.trim();
  const status = document.getElementById('na-status').value.trim();
  const rate = document.getElementById('na-rate').value.trim();
  const notes = document.getElementById('na-notes').value.trim();
  const errEl = document.getElementById('add-asset-error');

  if (!name || !type || !assetId || !base || !status) {
    errEl.textContent = 'Name, Type, Asset ID, Base, and Status are required.';
    return;
  }
  const rateNum = rate ? parseFloat(rate) : null;
  if (rate && isNaN(rateNum)) {
    errEl.textContent = 'Rate must be a number.';
    return;
  }

  // Check for duplicate Asset ID
  const duplicate = _allResources.find(r => (r.resource_id || r.asset_id) === assetId);
  if (duplicate) {
    errEl.textContent = `Asset ID "${assetId}" already exists.`;
    return;
  }

  const newAsset = {
    resource_id: assetId,
    asset_id: assetId,
    name: name,
    resource_type: type.toUpperCase(),
    status: status.toUpperCase(),
    depot_name: base,
    hourly_cost: rateNum,
    notes: notes,
  };

  _allResources.push(newAsset);
  applyAssetFilters();
  closeAddAssetModal();
}

// ── FIELD TEAMS: Add Member Modal Functions ───────────────────────────────────

window._localTeamMembers = window._localTeamMembers || [];

function openAddMemberModal() {
  document.getElementById('add-member-modal').style.display = 'flex';
}

function closeAddMemberModal() {
  document.getElementById('add-member-modal').style.display = 'none';
  document.getElementById('add-member-form').reset();
  document.getElementById('add-member-error').textContent = '';
}

function submitAddMember() {
  const name = document.getElementById('nm-name').value.trim();
  const role = document.getElementById('nm-role').value.trim();
  const specialty = document.getElementById('nm-specialty').value.trim();
  const base = document.getElementById('nm-base').value.trim();
  const status = document.getElementById('nm-status').value.trim();
  const errEl = document.getElementById('add-member-error');

  if (!name || !role || !base || !status) {
    errEl.textContent = 'Name, Role, Base, and Status are required.';
    return;
  }

  const member = {
    name, role, specialty, location_base: base,
    availability_status: status.toUpperCase(),
  };
  window._localTeamMembers.push(member);
  appendTeamMemberRow(member);
  closeAddMemberModal();
}

function appendTeamMemberRow(t) {
  const tbody = document.getElementById('res-teams-body');
  if (!tbody) return;
  const statusCls = t.availability_status === 'AVAILABLE' ? 'active' : 'pending';
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td style="font-weight:600;">${t.name}</td>
    <td style="color:var(--stone);">${t.role}</td>
    <td style="color:var(--stone);">${t.specialty || '—'}</td>
    <td>${t.location_base}</td>
    <td><span class="status-tag ${statusCls}" style="font-size:0.7rem;">● ${t.availability_status}</span></td>
  `;
  tbody.appendChild(tr);
}
