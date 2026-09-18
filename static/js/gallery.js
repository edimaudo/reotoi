(() => {
  "use strict";

  const page = document.querySelector(".gallery-page");
  if (!page) return;

  const storageKey = "reotoi-gallery";
  const galleryList = document.getElementById("gallery-list");
  const emptyState = document.getElementById("gallery-empty");

  if (!galleryList || !emptyState) return;

  function readGallery() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.error("Gallery read failed:", error);
      return [];
    }
  }

  function writeGallery(items) {
    localStorage.setItem(storageKey, JSON.stringify(items));
  }

  function formatDate(value) {
    if (!value) return "";

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";

    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  function render() {
    const items = readGallery();
    galleryList.innerHTML = "";
    emptyState.hidden = items.length > 0;

    if (!items.length) return;

    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "gallery-card";

      const frame = document.createElement("div");
      frame.className = "gallery-card__art";

      const image = document.createElement("img");
      image.src = item.artwork_url || "";
      image.alt = `Artwork in the ${item.theme || "surprise"} theme`;
      frame.appendChild(image);

      const details = document.createElement("div");
      details.className = "gallery-card__details";

      const theme = document.createElement("p");
      theme.className = "eyebrow";
      theme.textContent = String(item.theme || "surprise").replaceAll("-", " ");

      const date = document.createElement("p");
      date.className = "gallery-card__date";
      date.textContent = formatDate(item.saved_at);

      const actions = document.createElement("div");
      actions.className = "gallery-card__actions";

      const download = document.createElement("a");
      download.className = "button button--secondary";
      download.href = item.artwork_url || "";
      download.download = `${item.artwork_id || "reotoi-artwork"}.svg`;
      download.textContent = "Download";

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button button--secondary";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        const remaining = readGallery().filter(
          (entry) => entry?.artwork_id !== item.artwork_id
        );
        writeGallery(remaining);
        render();
      });

      actions.append(download, remove);
      details.append(theme, date, actions);
      card.append(frame, details);
      galleryList.appendChild(card);
    });
  }

  render();
})();
