
(() => {
  "use strict";

  const page = document.querySelector(".gallery-page");
  if (!page) return;

  // Gallery persistence will be connected when the server-side gallery store is added.
  // Keeping this file page-scoped avoids running gallery code on other views.
})();
