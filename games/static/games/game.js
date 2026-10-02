(() => {
  "use strict";

  const app = document.getElementById("game-app");
  const dataElement = document.getElementById("game-data");
  if (!app || !dataElement) {
    return;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const payload = JSON.parse(dataElement.textContent);
  const kind = app.dataset.gameKind;
  const militaryCostIconUrls = new Map([
    [1, app.dataset.militaryCostOneIcon],
    [2, app.dataset.militaryCostTwoIcon],
    [3, app.dataset.militaryCostThreeIcon],
  ]);
  const topology = payload.topology || {};
  const rules = payload.rules || {};
  const presentation = payload.presentation || {};
  const selection = new Set();
  const items = new Map();
  const graphElements = new Map();

  let playVersion = Number.parseInt(app.dataset.playVersion, 10) || 0;
  let playStatus = app.dataset.playStatus || "active";
  let solutionLoaded = false;
  let pendingAttemptId = null;
  let isBusy = false;

  const edgesLayer = document.getElementById("graph-edges");
  const nodesLayer = document.getElementById("graph-nodes");
  const selectionList = document.getElementById("selection-list");
  const emptySelection = document.getElementById("empty-selection");
  const clearButton = document.getElementById("clear-selection");
  const submitButton = document.getElementById("submit-attempt");
  const revealButton = document.getElementById("reveal-solution");
  const budgetMeter = document.getElementById("budget-meter");
  const meterTrack = document.getElementById("meter-track");
  const meterFill = document.getElementById("meter-fill");
  const meterLabel = document.getElementById("meter-label");
  const budgetUsed = document.getElementById("budget-used");
  const budgetLimit = document.getElementById("budget-limit");
  const feedback = document.getElementById("feedback");
  const feedbackTitle = document.getElementById("feedback-title");
  const feedbackMessage = document.getElementById("feedback-message");
  const feedbackMetrics = document.getElementById("feedback-metrics");
  const playStatusLabel = document.getElementById("play-status-label");
  const csrfToken = document.querySelector(".csrf-source [name=csrfmiddlewaretoken]").value;

  function normalizeRecords(value, recordType) {
    if (Array.isArray(value)) {
      return value.map((record, index) => {
        if (record && typeof record === "object" && !Array.isArray(record)) {
          return { ...record, id: String(record.id ?? index) };
        }
        return { id: String(record ?? index) };
      });
    }

    if (!value || typeof value !== "object") {
      return [];
    }

    return Object.entries(value).map(([id, record]) => {
      if (Array.isArray(record) && recordType === "edge") {
        return { id: String(id), source: record[0], target: record[1] };
      }
      return {
        ...(record && typeof record === "object" ? record : {}),
        id: String(id),
      };
    });
  }

  const nodes = normalizeRecords(topology.nodes, "node");
  const edges = normalizeRecords(topology.edges, "edge");

  function nodeRole(node) {
    const role = node.role ?? node.node_prop ?? "unit";
    if (role === "dest") {
      return "destination";
    }
    return String(role);
  }

  function edgeEndpoints(edge) {
    return {
      source: String(edge.source ?? edge.u ?? edge.from ?? ""),
      target: String(edge.target ?? edge.v ?? edge.to ?? ""),
    };
  }

  function numericCost(record) {
    const value = Number(record.removal_cost ?? record.cost ?? record.endurance ?? 1);
    return Number.isFinite(value) && value >= 0 ? value : 1;
  }

  function rawPosition(node, index) {
    const configured = presentation.positions?.[String(node.id)] ?? node.position;
    if (Array.isArray(configured) && configured.length >= 2) {
      return { x: Number(configured[0]), y: Number(configured[1]) };
    }
    if (configured && typeof configured === "object") {
      return { x: Number(configured.x), y: Number(configured.y) };
    }
    if (Number.isFinite(Number(node.x)) && Number.isFinite(Number(node.y))) {
      return { x: Number(node.x), y: Number(node.y) };
    }

    const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length * 1.5)));
    return { x: index % columns, y: Math.floor(index / columns) };
  }

  function buildPositions() {
    const raw = nodes.map((node, index) => rawPosition(node, index));
    const validX = raw.map((position) => position.x).filter(Number.isFinite);
    const validY = raw.map((position) => position.y).filter(Number.isFinite);
    const minX = validX.length ? Math.min(...validX) : 0;
    const maxX = validX.length ? Math.max(...validX) : 1;
    const minY = validY.length ? Math.min(...validY) : 0;
    const maxY = validY.length ? Math.max(...validY) : 1;
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    const positions = new Map();

    nodes.forEach((node, index) => {
      const point = raw[index];
      const safeX = Number.isFinite(point.x) ? point.x : minX;
      const safeY = Number.isFinite(point.y) ? point.y : minY;
      positions.set(String(node.id), {
        x: 65 + ((safeX - minX) / spanX) * 770,
        y: 60 + ((safeY - minY) / spanY) * 480,
      });
    });
    return positions;
  }

  const positions = buildPositions();

  function svgElement(tagName, attributes = {}) {
    const element = document.createElementNS(SVG_NS, tagName);
    Object.entries(attributes).forEach(([name, value]) => {
      element.setAttribute(name, String(value));
    });
    return element;
  }

  function appendTitle(element, text) {
    const title = svgElement("title");
    title.textContent = text;
    element.appendChild(title);
  }

  function bindInteractive(element, id, label) {
    element.classList.add("graph-interactive");
    element.setAttribute("role", "button");
    element.setAttribute("tabindex", "0");
    element.setAttribute("aria-label", label);
    element.setAttribute("aria-pressed", "false");
    appendTitle(element, label);
    element.addEventListener("click", () => toggleSelection(id));
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleSelection(id);
      }
    });
  }

  function appendMilitaryCostIcon(group, cost, radius) {
    const iconUrl = militaryCostIconUrls.get(cost);
    if (!iconUrl) {
      const fallback = svgElement("text", {
        y: "0.32em",
        "aria-hidden": "true",
      });
      fallback.classList.add("graph-node__cost-fallback");
      fallback.textContent = String(cost);
      group.appendChild(fallback);
      return;
    }

    const size = Math.max(21, radius * 2.25);
    const icon = svgElement("image", {
      href: iconUrl,
      x: -size / 2,
      y: -size / 2,
      width: size,
      height: size,
      preserveAspectRatio: "xMidYMid meet",
      "aria-hidden": "true",
    });
    icon.classList.add("graph-node__cost-icon", `graph-node__cost-icon--${cost}`);
    group.appendChild(icon);
  }

  function renderEdges() {
    edges.forEach((edge) => {
      const id = String(edge.id);
      const { source, target } = edgeEndpoints(edge);
      const sourcePosition = positions.get(source);
      const targetPosition = positions.get(target);
      if (!sourcePosition || !targetPosition) {
        return;
      }

      const removable = edge.removable !== false;
      const group = svgElement("g", { "data-item-id": id });
      group.classList.add("graph-edge");
      group.classList.add(removable ? "graph-edge--selectable" : "graph-edge--protected");

      const visibleLine = svgElement("line", {
        x1: sourcePosition.x,
        y1: sourcePosition.y,
        x2: targetPosition.x,
        y2: targetPosition.y,
      });
      visibleLine.classList.add("graph-edge__line");
      group.appendChild(visibleLine);

      if (kind === "water" && removable) {
        const hitLine = svgElement("line", {
          x1: sourcePosition.x,
          y1: sourcePosition.y,
          x2: targetPosition.x,
          y2: targetPosition.y,
        });
        hitLine.classList.add("graph-edge__hit");
        group.appendChild(hitLine);
        const item = {
          id,
          label: edge.label || `Tubulação ${source} – ${target}`,
          cost: numericCost(edge),
        };
        items.set(id, item);
        graphElements.set(id, group);
        bindInteractive(group, id, `${item.label}. Custo ${item.cost}.`);
      } else {
        appendTitle(
          group,
          removable ? `Ligação ${source} – ${target}` : `Ligação protegida ${source} – ${target}`,
        );
      }
      edgesLayer.appendChild(group);
    });
  }

  function renderNodes() {
    const showAllLabels = nodes.length <= 36;
    nodes.forEach((node) => {
      const id = String(node.id);
      const position = positions.get(id);
      if (!position) {
        return;
      }

      const role = nodeRole(node);
      const protectedRole = ["origin", "destination", "headquarters", "secure", "protected"].includes(role);
      const removable = node.removable !== false && !protectedRole;
      const radius = role === "headquarters" ? 19 : nodes.length > 70 ? 10 : 14;
      const group = svgElement("g", {
        transform: `translate(${position.x} ${position.y})`,
        "data-item-id": id,
      });
      group.classList.add("graph-node", `graph-node--${role}`);

      const circle = svgElement("circle", { r: radius });
      circle.classList.add("graph-node__circle");
      group.appendChild(circle);

      const displayLabel = String(node.label ?? id);
      if (showAllLabels || protectedRole) {
        const label = svgElement("text", { y: radius + 16 });
        label.classList.add("graph-node__label");
        label.textContent = displayLabel;
        group.appendChild(label);
      }

      if (kind === "military" && removable) {
        group.classList.add("graph-node--selectable");
        const item = {
          id,
          label: node.label || `Unidade ${id}`,
          cost: numericCost(node),
        };
        group.dataset.removalCost = String(item.cost);
        if (militaryCostIconUrls.has(item.cost)) {
          group.classList.add(`graph-node--cost-${item.cost}`);
        }
        appendMilitaryCostIcon(group, item.cost, radius);
        items.set(id, item);
        graphElements.set(id, group);
        bindInteractive(group, id, `${item.label}. Custo de remoção ${item.cost}.`);
      } else {
        const roleLabel = {
          origin: "Origem",
          destination: "Destino",
          headquarters: "Quartel-general",
          secure: "Unidade protegida",
          protected: "Unidade protegida",
        }[role] || `Nó ${displayLabel}`;
        appendTitle(group, roleLabel);
      }
      nodesLayer.appendChild(group);
    });
  }

  function selectionCost() {
    return [...selection].reduce((total, id) => total + (items.get(id)?.cost || 0), 0);
  }

  function configuredBudget() {
    const budget = Number(rules.budget);
    return Number.isFinite(budget) && budget >= 0 ? budget : null;
  }

  function renderSelection() {
    selectionList.replaceChildren();
    const sortedIds = [...selection].sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));

    sortedIds.forEach((id) => {
      const item = items.get(id);
      const listItem = document.createElement("li");
      listItem.className = "selection-chip";

      const label = document.createElement("span");
      label.textContent = `${item.label} (${item.cost})`;
      listItem.appendChild(label);

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.setAttribute("aria-label", `Remover ${item.label} da seleção`);
      removeButton.textContent = "×";
      removeButton.addEventListener("click", () => toggleSelection(id));
      listItem.appendChild(removeButton);
      selectionList.appendChild(listItem);
    });

    graphElements.forEach((element, id) => {
      const selected = selection.has(id);
      element.classList.toggle(kind === "water" ? "graph-edge--selected" : "graph-node--selected", selected);
      element.setAttribute("aria-pressed", String(selected));
    });

    const cost = selectionCost();
    const budget = configuredBudget();
    emptySelection.hidden = selection.size > 0;
    const canAttempt = playStatus === "active";
    clearButton.disabled = selection.size === 0 || isBusy || !canAttempt;
    submitButton.disabled = selection.size === 0 || isBusy || !canAttempt;
    budgetUsed.textContent = String(cost);

    if (budget === null) {
      meterLabel.textContent = "Itens selecionados";
      budgetLimit.textContent = "";
      meterTrack.removeAttribute("aria-valuemax");
      meterTrack.setAttribute("aria-valuenow", String(selection.size));
      meterFill.style.width = selection.size ? `${Math.min(100, selection.size * 12)}%` : "0%";
      budgetMeter.classList.remove("meter--over");
    } else {
      const percentage = budget === 0 ? (cost ? 100 : 0) : Math.min(100, (cost / budget) * 100);
      meterLabel.textContent = "Orçamento usado";
      budgetLimit.textContent = ` / ${budget}`;
      meterTrack.setAttribute("aria-valuemax", String(budget));
      meterTrack.setAttribute("aria-valuenow", String(cost));
      meterFill.style.width = `${percentage}%`;
      budgetMeter.classList.toggle("meter--over", cost > budget);
    }
  }

  function toggleSelection(id) {
    if (isBusy || playStatus !== "active" || !items.has(id)) {
      return;
    }
    if (selection.has(id)) {
      selection.delete(id);
    } else {
      selection.add(id);
    }
    renderSelection();
  }

  function clearSelection() {
    if (isBusy) {
      return;
    }
    selection.clear();
    renderSelection();
  }

  function addMetric(label, value) {
    if (value === null || value === undefined || value === "") {
      return;
    }
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = String(value);
    wrapper.append(term, detail);
    feedbackMetrics.appendChild(wrapper);
  }

  function showFeedback(type, title, message, metrics = []) {
    feedback.hidden = false;
    feedback.className = `feedback${type === "success" ? "" : ` feedback--${type}`}`;
    feedbackTitle.textContent = title;
    feedbackMessage.textContent = message;
    feedbackMetrics.replaceChildren();
    metrics.forEach(([label, value]) => addMetric(label, value));
  }

  function friendlyReason(reason) {
    const reasons = {
      budget_exceeded: "O custo da seleção ultrapassa o orçamento disponível.",
      destination_still_connected: "Ainda existe um caminho entre a origem e o destino.",
      no_units_disconnected: "A seleção ainda não interrompe o abastecimento de nenhuma unidade.",
    };
    return reasons[reason] || "A estratégia ainda não atende ao objetivo deste cenário.";
  }

  function renderAttemptResult(result) {
    const metrics = [
      ["Pontuação", result.score],
      ["Objetivo", result.objective_value],
      ["Orçamento usado", result.budget_used],
      ["Diferença para o ótimo", result.gap],
    ];

    if (!result.valid) {
      showFeedback("warning", "Jogada fora das regras", friendlyReason(result.reason), metrics);
      return;
    }
    if (result.success && result.is_optimal) {
      showFeedback("success", "Solução ótima!", "Sua estratégia atingiu o melhor resultado conhecido.", metrics);
      return;
    }
    if (result.success) {
      const message = kind === "water"
        ? "Você interrompeu o fluxo. Tente reduzir o número de tubulações removidas."
        : "Você interrompeu parte do abastecimento. Ainda pode haver uma estratégia melhor.";
      showFeedback("success", "Objetivo alcançado", message, metrics);
      return;
    }
    showFeedback("warning", "Continue tentando", friendlyReason(result.reason), metrics);
  }

  function makeUuid() {
    if (globalThis.crypto?.randomUUID) {
      return globalThis.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
      const random = Math.floor(Math.random() * 16);
      const value = character === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  async function postJson(url, body) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(body),
      });
    } catch (error) {
      error.responseReceived = false;
      throw error;
    }

    let responsePayload = {};
    try {
      responsePayload = await response.json();
    } catch (_error) {
      responsePayload = {};
    }
    if (!response.ok) {
      const error = new Error(responsePayload.error?.message || "Não foi possível concluir a requisição.");
      error.responseReceived = true;
      error.payload = responsePayload;
      error.status = response.status;
      throw error;
    }
    return responsePayload;
  }

  function setBusy(value) {
    isBusy = value;
    app.setAttribute("aria-busy", String(value));
    revealButton.disabled = value || solutionLoaded;
    renderSelection();
  }

  function canonicalSelection() {
    const ids = [...selection];
    return kind === "water" ? { edge_ids: ids } : { node_ids: ids };
  }

  async function submitAttempt() {
    if (isBusy || selection.size === 0) {
      return;
    }

    pendingAttemptId ||= makeUuid();
    setBusy(true);
    try {
      const response = await postJson(app.dataset.attemptUrl, {
        request_id: pendingAttemptId,
        expected_version: playVersion,
        selection: canonicalSelection(),
      });
      pendingAttemptId = null;
      playVersion = Math.max(playVersion, response.play.version);
      playStatus = response.play.status;
      renderAttemptResult(response.attempt.result);
      if (response.play.status === "completed") {
        playStatusLabel.textContent = "Objetivo alcançado";
      }
      renderSelection();
    } catch (error) {
      if (error.responseReceived) {
        pendingAttemptId = null;
      }
      if (error.payload?.error?.code === "version_conflict") {
        playVersion = Math.max(playVersion, error.payload.error.current_version);
        playStatus = error.payload.error.current_status || playStatus;
        if (playStatus === "completed") {
          playStatusLabel.textContent = "Objetivo alcançado";
        } else if (playStatus === "revealed") {
          playStatusLabel.textContent = "Solução revelada";
        }
        renderSelection();
      }
      showFeedback(
        error.status === 409 ? "warning" : "error",
        error.status === 409 ? "Partida atualizada" : "Não foi possível avaliar",
        error.message || "Verifique sua conexão e tente novamente.",
      );
    } finally {
      setBusy(false);
    }
  }

  function solutionIds(solution) {
    const raw = solution.selection || {};
    const ids = kind === "water"
      ? raw.removed_edge_ids ?? raw.edge_ids ?? raw.selected_edge_ids ?? []
      : raw.removed_node_ids ?? raw.node_ids ?? raw.selected_node_ids ?? [];
    return Array.isArray(ids) ? ids.map(String) : [];
  }

  function highlightSolution(solution) {
    graphElements.forEach((element) => {
      element.classList.remove("graph-edge--solution", "graph-node--solution");
    });
    solutionIds(solution).forEach((id) => {
      const element = graphElements.get(id);
      if (element) {
        element.classList.add(kind === "water" ? "graph-edge--solution" : "graph-node--solution");
      }
    });
  }

  async function revealSolution() {
    if (isBusy) {
      return;
    }
    setBusy(true);
    try {
      const response = await postJson(app.dataset.revealUrl, {
        expected_version: playVersion,
      });
      playVersion = Math.max(playVersion, response.play.version);
      playStatus = response.play.status;
      solutionLoaded = true;
      highlightSolution(response.solution);
      playStatusLabel.textContent = "Solução revelada";
      revealButton.textContent = "Solução exibida";
      renderSelection();
      showFeedback(
        "warning",
        "Solução verificada",
        "Os itens em roxo formam a solução calculada pelo solver.",
        [["Objetivo ótimo", response.solution.objective_value]],
      );
    } catch (error) {
      if (error.payload?.error?.code === "version_conflict") {
        playVersion = Math.max(playVersion, error.payload.error.current_version);
        playStatus = error.payload.error.current_status || playStatus;
        if (playStatus === "revealed") {
          playStatusLabel.textContent = "Solução revelada";
        }
        renderSelection();
      }
      showFeedback(
        error.status === 409 ? "warning" : "error",
        "Solução indisponível",
        error.message || "Não foi possível carregar a solução.",
      );
    } finally {
      setBusy(false);
    }
  }

  renderEdges    ();
  renderNodes    ();
  renderSelection();

  clearButton .addEventListener("click", clearSelection);
  submitButton.addEventListener("click", submitAttempt );
  revealButton.addEventListener("click", revealSolution);
})();