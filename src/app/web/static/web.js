(() => {
  // Hamburger Menu Toggle
  function toggleMenu(event) {
    event.stopPropagation();
    const toggle = event.currentTarget;
    const menu = document.getElementById("menu-dropdown");
    const isOpen = menu.classList.contains("show");

    if (isOpen) {
      menu.classList.remove("show");
      toggle.classList.remove("active");
    } else {
      menu.classList.add("show");
      toggle.classList.add("active");
    }
  }

  // Close menu when clicking outside
  document.addEventListener("click", function(event) {
    const menuContainer = document.querySelector(".menu-container");
    const menu = document.getElementById("menu-dropdown");
    const toggle = document.getElementById("menu-toggle");

    if (menu && toggle && !menuContainer.contains(event.target)) {
      menu.classList.remove("show");
      toggle.classList.remove("active");
    }
  });

  function getSearchState() {
    const form = document.getElementById("search-form");
    const params = new URLSearchParams();
    if (!form) {
      return params;
    }
    new FormData(form).forEach((value, key) => {
      const text = String(value).trim();
      if (text) {
        params.set(key, text);
      }
    });
    return params;
  }

  async function refreshAlbumSidebar() {
    const sidebar = document.getElementById("album-sidebar");
    if (!sidebar) {
      return;
    }
    const sidebarUrl = sidebar.dataset.sidebarUrl;
    if (!sidebarUrl) {
      return;
    }

    const url = new URL(sidebarUrl, window.location.origin);
    const params = getSearchState();
    params.forEach((value, key) => {
      url.searchParams.set(key, value);
    });

    const response = await fetch(url.toString(), {
      headers: {
        "HX-Request": "true",
      },
    });
    if (!response.ok) {
      return;
    }
    sidebar.innerHTML = await response.text();
    initAlbumDragDrop(document);
  }

  async function addPhotoToAlbum(albumId, photoToken) {
    const body = new URLSearchParams();
    body.set("photo_token", photoToken);

    const response = await fetch(`/albums/${albumId}/add-photo`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "HX-Request": "true",
      },
      body: body.toString(),
    });

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      window.alert(errorPayload.error || "Foto konnte nicht zum Album hinzugefuegt werden.");
      return false;
    }

    await refreshAlbumSidebar();
    return true;
  }

  async function removePhotoFromAlbum(photoToken, albumId, buttonElement) {
    const body = new URLSearchParams();
    body.set("photo_token", photoToken);

    try {
      const response = await fetch(`/albums/${albumId}/remove-photo`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "HX-Request": "true",
        },
        body: body.toString(),
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        window.alert(errorPayload.error || "Foto konnte nicht aus Album entfernt werden.");
        return;
      }

      // Entferne die Fotokarte aus dem DOM mit Animation
      const card = buttonElement.closest(".photo-card");
      if (card) {
        card.style.opacity = "0.5";
        card.style.pointerEvents = "none";
        setTimeout(() => {
          card.remove();
          // Aktualisiere das Album-Sidebar mit der neuen Anzahl
          refreshAlbumSidebar();
        }, 300);
      }
    } catch (error) {
      window.alert(`Fehler: ${error}`);
    }
  }

  function initAlbumDragDrop(root = document) {
    root.querySelectorAll("[data-photo-token]").forEach((card) => {
      if (card.dataset.dragReady === "1") {
        return;
      }
      card.dataset.dragReady = "1";
      card.addEventListener("dragstart", (event) => {
        const token = card.dataset.photoToken;
        if (!token || !event.dataTransfer) {
          return;
        }
        event.dataTransfer.setData("text/plain", token);
        event.dataTransfer.effectAllowed = "copy";
        card.classList.add("is-dragging");
      });
      card.addEventListener("dragend", () => {
        card.classList.remove("is-dragging");
      });
    });

    root.querySelectorAll("[data-album-drop]").forEach((albumBox) => {
      if (albumBox.dataset.dropReady === "1") {
        return;
      }
      albumBox.dataset.dropReady = "1";

      albumBox.addEventListener("dragover", (event) => {
        event.preventDefault();
        albumBox.classList.add("is-drop-target");
      });

      albumBox.addEventListener("dragleave", () => {
        albumBox.classList.remove("is-drop-target");
      });

      albumBox.addEventListener("drop", async (event) => {
        event.preventDefault();
        albumBox.classList.remove("is-drop-target");
        const albumId = albumBox.dataset.albumId;
        const photoToken = event.dataTransfer ? event.dataTransfer.getData("text/plain") : "";
        if (!albumId || !photoToken) {
          return;
        }

        const ok = await addPhotoToAlbum(albumId, photoToken);
        if (ok) {
          albumBox.classList.add("is-drop-success");
          window.setTimeout(() => albumBox.classList.remove("is-drop-success"), 1200);
        }
      });
    });
  }



  function renameAlbumPrompt(buttonElement) {
    const albumId = buttonElement.getAttribute("data-album-id");
    const currentName = buttonElement.getAttribute("data-album-name");
    const newName = window.prompt("Neuer Albumname:", currentName);
    if (newName !== null && newName.trim()) {
      renameAlbum(albumId, newName);
    }
  }

  async function renameAlbum(albumId, newName) {
    const body = new URLSearchParams();
    body.set("name", newName);

    try {
      const response = await fetch(`/albums/${albumId}/rename`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "HX-Request": "true",
        },
        body: body.toString(),
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        window.alert(errorPayload.error || "Album konnte nicht umbenannt werden.");
        return;
      }

      window.alert("Album erfolgreich umbenannt.");
      await refreshAlbumSidebar();
    } catch (error) {
      window.alert(`Fehler: ${error}`);
    }
  }

  function deleteAlbumPrompt(buttonElement) {
    const albumId = buttonElement.getAttribute("data-album-id");
    const albumName = buttonElement.getAttribute("data-album-name");
    if (
      window.confirm(
        `Wirklich Album "${albumName}" löschen? Die Fotos bleiben im Index erhalten.`
      )
    ) {
      deleteAlbum(albumId);
    }
  }

  async function deleteAlbum(albumId) {
    try {
      const response = await fetch(`/albums/${albumId}`, {
        method: "DELETE",
        headers: {
          "HX-Request": "true",
        },
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        window.alert(errorPayload.error || "Album konnte nicht gelöscht werden.");
        return;
      }

      window.alert("Album gelöscht.");
      await refreshAlbumSidebar();
    } catch (error) {
      window.alert(`Fehler: ${error}`);
    }
  }

  function filterByPerson() {
    const select = document.getElementById("person-filter-select");
    const personName = select.value;
    const queryInput = document.querySelector("input[name='q']");

    if (!queryInput) return;

    let query = queryInput.value;

    // Entferne alten person: Filter - handle names with spaces using quoted pattern
    query = query.replace(/person:"[^"]*"|person:\S+/g, "").trim();

    // Füge neuen person: Filter hinzu wenn gewählt
    if (personName) {
      // Wenn der Name Leerzeichen enthält, in Anführungszeichen setzen
      const personPattern = personName.includes(" ") ? `person:"${personName}"` : `person:${personName}`;
      query = query ? `${query} ${personPattern}` : personPattern;
    }

    queryInput.value = query;
    document.getElementById("search-form").requestSubmit();
  }

  function updateDateFilter() {
    const monthSelect = document.getElementById("month-filter-select");
    const yearInput = document.getElementById("year-filter-input");
    const queryInput = document.querySelector("input[name='q']");

    if (!queryInput) return;

    let query = queryInput.value;

    // Entferne alte month: und year: Filter
    query = query.replace(/month:\d+|year:\d+/g, "").trim();

    // Füge neue Filter hinzu wenn gesetzt
    const month = monthSelect.value;
    const year = yearInput.value;

    if (month) {
      query = query ? `${query} month:${month}` : `month:${month}`;
    }

    if (year) {
      query = query ? `${query} year:${year}` : `year:${year}`;
    }

    queryInput.value = query;
    document.getElementById("search-form").requestSubmit();
  }

  function getTimelapseElements() {
    const panel = document.querySelector("[data-timelapse-panel='1']");
    if (!panel) {
      return null;
    }
    return {
      panel,
      albumId: panel.dataset.albumId,
      personInput: document.getElementById("timelapse-person-input"),
      fpsInput: document.getElementById("timelapse-fps-input"),
      holdInput: document.getElementById("timelapse-hold-input"),
      morphInput: document.getElementById("timelapse-morph-input"),
      sizeInput: document.getElementById("timelapse-size-input"),
      startBtn: document.getElementById("timelapse-start-btn"),
      statusBox: document.getElementById("timelapse-status"),
      downloadLink: document.getElementById("timelapse-download-link"),
    };
  }

  function setTimelapseStatus(text, isError = false) {
    const ui = getTimelapseElements();
    if (!ui || !ui.statusBox) {
      return;
    }
    ui.statusBox.textContent = text;
    ui.statusBox.style.color = isError ? "#ff7a7a" : "";
  }

  async function pollTimelapseStatus(statusUrl) {
    const maxRounds = 180; // bis ca. 6 Minuten bei 2s Polling
    for (let i = 0; i < maxRounds; i++) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));

      const response = await fetch(statusUrl);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || "Status konnte nicht geladen werden.");
      }

      const step = Number(payload.step || 0);
      const total = Number(payload.total || 0);
      const progress = total > 0 ? ` [${step}/${total}]` : "";
      const message = String(payload.message || "").trim();
      setTimelapseStatus((message || "Generierung laeuft...") + progress);

      if (payload.status === "done") {
        const ui = getTimelapseElements();
        if (ui && ui.downloadLink && payload.download_url) {
          ui.downloadLink.href = String(payload.download_url);
          ui.downloadLink.style.display = "inline-block";
        }
        setTimelapseStatus("Fertig. Film steht zum Download bereit.");
        return;
      }

      if (payload.status === "error") {
        throw new Error(payload.message || "Timelapse-Erstellung fehlgeschlagen.");
      }
    }

    throw new Error("Timeout: Timelapse dauert laenger als erwartet.");
  }

  async function startAlbumTimelapse() {
    const ui = getTimelapseElements();
    if (!ui || !ui.albumId) {
      return;
    }

    const person = ui.personInput ? ui.personInput.value.trim() : "";
    if (!person) {
      setTimelapseStatus("Bitte eine Person eingeben.", true);
      return;
    }

    const payload = {
      person,
      fps: Number(ui.fpsInput ? ui.fpsInput.value : 24) || 24,
      hold: Number(ui.holdInput ? ui.holdInput.value : 24) || 24,
      morph: Number(ui.morphInput ? ui.morphInput.value : 48) || 48,
      size: Number(ui.sizeInput ? ui.sizeInput.value : 512) || 512,
    };

    if (ui.startBtn) {
      ui.startBtn.disabled = true;
      ui.startBtn.textContent = "Erstelle...";
    }
    if (ui.downloadLink) {
      ui.downloadLink.style.display = "none";
      ui.downloadLink.removeAttribute("href");
    }
    setTimelapseStatus("Starte Timelapse-Generierung...");

    try {
      const response = await fetch(`/api/albums/${ui.albumId}/timelapse`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "HX-Request": "true",
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(data.error || "Timelapse konnte nicht gestartet werden.");
      }

      if (data.download_url && ui.downloadLink) {
        ui.downloadLink.href = String(data.download_url);
        ui.downloadLink.style.display = "inline-block";
        setTimelapseStatus("Video bereits vorhanden. Direkt herunterladen.");
        return;
      }

      if (!data.status_url) {
        throw new Error("Status-URL fehlt in der Serverantwort.");
      }

      await pollTimelapseStatus(String(data.status_url));
    } catch (error) {
      setTimelapseStatus(`Fehler: ${error}`, true);
    } finally {
      if (ui.startBtn) {
        ui.startBtn.disabled = false;
        ui.startBtn.textContent = "Film erstellen";
      }
    }
  }

  // Export functions globally for inline onclick handlers
  window.renameAlbumPrompt = renameAlbumPrompt;
  window.deleteAlbumPrompt = deleteAlbumPrompt;
  window.removePhotoFromAlbum = removePhotoFromAlbum;
  window.filterByPerson = filterByPerson;
  window.updateDateFilter = updateDateFilter;
  window.startAlbumTimelapse = startAlbumTimelapse;

  // Photo Modal functions
  async function openPhotoModal(photoToken) {
    const modal = document.getElementById("photo-modal");
    const photoImg = document.getElementById("modal-photo");
    const detailsDiv = document.getElementById("modal-details");

    if (!modal || !photoImg || !detailsDiv) return;

    // Set image URL
    photoImg.src = `/photo/${photoToken}`;

    // Load details
    detailsDiv.innerHTML = '<div class="details-loading">Lade Details...</div>';

    try {
      const response = await fetch(`/api/photo-details/${photoToken}`);
      if (!response.ok) {
        detailsDiv.innerHTML = '<div class="details-loading">Fehler beim Laden der Details.</div>';
        return;
      }

      const data = await response.json();
      detailsDiv.innerHTML = buildDetailsHTML(data);
    } catch (error) {
      console.error("Error loading photo details:", error);
      detailsDiv.innerHTML = '<div class="details-loading">Fehler beim Laden der Details.</div>';
    }

    modal.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  function closePhotoModal(event) {
    // Wenn event vorhanden ist und nicht auf modal selbst geklickt wurde, return
    if (event && event.target.id !== "photo-modal") {
      return;
    }

    const modal = document.getElementById("photo-modal");
    if (modal) {
      modal.classList.remove("active");
      document.body.style.overflow = "";
    }
  }

  function buildDetailsHTML(data) {
    const sections = [];

    // File Info Section
    if (data.file_info) {
      sections.push(buildSection("Dateiinformationen", [
        { label: "Pfad", value: data.file_info.path, code: true },
        { label: "Größe", value: formatFileSize(data.file_info.size_bytes) },
        { label: "Geändert", value: new Date(data.file_info.modified_ts * 1000).toLocaleString('de-DE') },
      ]));
    }

    // Image Info Section
    if (data.image_info) {
      sections.push(buildSection("Bildinformationen", [
        { label: "Auflösung", value: data.image_info.width && data.image_info.height ? `${data.image_info.width} x ${data.image_info.height}` : "-" },
        { label: "Format", value: data.image_info.format || "-" },
      ]));
    }

    // Labels Section
    if (data.labels && data.labels.length > 0) {
      sections.push(`
        <div class="detail-section">
          <div class="detail-section-title">Labels</div>
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            ${data.labels.map(label => `<span style="background: #1f2c3d; padding: 3px 8px; border-radius: 4px; font-size: 11px; color: #9ec3ff;">${escapeHtml(label)}</span>`).join('')}
          </div>
        </div>
      `);
    }

    // EXIF Data Section
    if (data.exif && Object.keys(data.exif).length > 0) {
      const exifRows = [];

      // Camera Info
      if (data.exif.camera_make || data.exif.camera_model) {
        exifRows.push({ label: "Kamera", value: [data.exif.camera_make, data.exif.camera_model].filter(Boolean).join(" ") });
      }
      if (data.exif.lens) {
        exifRows.push({ label: "Objektiv", value: data.exif.lens });
      }

      // Photo Settings
      if (data.exif.focal_length) {
        exifRows.push({ label: "Brennweite", value: data.exif.focal_length });
      }
      if (data.exif.f_number) {
        exifRows.push({ label: "Blende", value: data.exif.f_number });
      }
      if (data.exif.exposure_time) {
        exifRows.push({ label: "Belichtungszeit", value: data.exif.exposure_time });
      }
      if (data.exif.iso) {
        exifRows.push({ label: "ISO", value: data.exif.iso.toString() });
      }

      // Date/Time
      if (data.exif.datetime) {
        exifRows.push({ label: "Aufnahmedatum", value: new Date(data.exif.datetime * 1000).toLocaleString('de-DE') });
      }

      // Location
      if (data.exif.latitude && data.exif.longitude) {
        exifRows.push({
          label: "Koordinaten",
          value: `${data.exif.latitude.toFixed(6)}, ${data.exif.longitude.toFixed(6)}`
        });
      }

      if (exifRows.length > 0) {
        sections.push(buildSection("EXIF-Daten", exifRows));
      }
    }

    return sections.join('');
  }

  function buildSection(title, rows) {
    const rowsHTML = rows.map(row => `
      <div class="detail-row">
        <div class="detail-label">${escapeHtml(row.label)}</div>
        <div class="detail-value${row.code ? ' code' : ''}">${escapeHtml(row.value)}</div>
      </div>
    `).join('');

    return `
      <div class="detail-section">
        <div class="detail-section-title">${escapeHtml(title)}</div>
        ${rowsHTML}
      </div>
    `;
  }

  function formatFileSize(bytes) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  window.openPhotoModal = openPhotoModal;
  window.closePhotoModal = closePhotoModal;
  window.toggleMenu = toggleMenu;

  document.addEventListener("DOMContentLoaded", () => {
    initAlbumDragDrop(document);
  });
  document.body.addEventListener("htmx:afterSwap", () => {
    initAlbumDragDrop(document);
  });
})();
