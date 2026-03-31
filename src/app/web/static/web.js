(() => {
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

  // Export functions globally for inline onclick handlers
  window.renameAlbumPrompt = renameAlbumPrompt;
  window.deleteAlbumPrompt = deleteAlbumPrompt;
  window.removePhotoFromAlbum = removePhotoFromAlbum;
  window.filterByPerson = filterByPerson;

  document.addEventListener("DOMContentLoaded", () => initAlbumDragDrop(document));
  document.body.addEventListener("htmx:afterSwap", () => initAlbumDragDrop(document));
})();
