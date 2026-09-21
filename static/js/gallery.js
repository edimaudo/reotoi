(() => {
  "use strict";

  const page = document.querySelector(".gallery-page");
  if (!page) return;

  const storageKey = "reotoi-gallery";

  const galleryList =
    document.getElementById("gallery-list");

  const emptyState =
    document.getElementById("gallery-empty");

  if (!galleryList || !emptyState) return;

  function readGallery() {
    try {
      const parsed = JSON.parse(
        localStorage.getItem(storageKey) || "[]"
      );

      return Array.isArray(parsed)
        ? parsed
        : [];
    } catch (error) {
      console.error(
        "Gallery read failed:",
        error
      );

      return [];
    }
  }

  function writeGallery(items) {
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify(items)
      );

      return true;
    } catch (error) {
      console.error(
        "Gallery write failed:",
        error
      );

      return false;
    }
  }

  function formatDate(value) {
    if (!value) return "";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return "";
    }

    return date.toLocaleDateString(
      undefined,
      {
        year: "numeric",
        month: "short",
        day: "numeric",
      }
    );
  }

  function createActionLink(
    text,
    href,
    downloadName
  ) {
    const link = document.createElement("a");

    link.className =
      "button button--secondary";

    link.href = href;

    if (downloadName) {
      link.download = downloadName;
    }

    link.textContent = text;

    return link;
  }

  function render() {
    const items = readGallery();

    galleryList.replaceChildren();

    emptyState.hidden =
      items.length > 0;

    items.forEach((item) => {
      if (!item?.artwork_url) return;

      const card =
        document.createElement("article");

      card.className =
        "gallery-card";

      const art =
        document.createElement("div");

      art.className =
        "gallery-card__art";

      const image =
        document.createElement("img");

      image.src =
        item.artwork_url;

      image.alt =
        `Artwork generated with the ${
          item.theme || "surprise"
        } theme`;

      image.loading = "lazy";

      art.appendChild(image);

      const details =
        document.createElement("div");

      details.className =
        "gallery-card__details";

      const theme =
        document.createElement("p");

      theme.className = "eyebrow";

      theme.textContent =
        String(
          item.theme || "surprise"
        ).replaceAll("-", " ");

      const date =
        document.createElement("p");

      date.className =
        "gallery-card__date";

      date.textContent =
        formatDate(item.saved_at);

      const actions =
        document.createElement("div");

      actions.className =
        "gallery-card__actions";

      const download =
        createActionLink(
          "Download",
          item.artwork_url,
          `${
            item.artwork_id ||
            "reotoi-artwork"
          }.svg`
        );

      const remove =
        document.createElement("button");

      remove.type = "button";
      remove.className =
        "button button--secondary";
      remove.textContent = "Remove";

      remove.addEventListener(
        "click",
        () => {
          const remaining =
            readGallery().filter(
              (entry) =>
                entry?.artwork_id !==
                item.artwork_id
            );

          if (writeGallery(remaining)) {
            render();
          }
        }
      );

      actions.append(
        download,
        remove
      );

      details.append(
        theme,
        date,
        actions
      );

      card.append(
        art,
        details
      );

      galleryList.appendChild(card);
    });
  }

  render();

  window.addEventListener(
    "storage",
    (event) => {
      if (
        event.key === storageKey
      ) {
        render();
      }
    }
  );
})();
