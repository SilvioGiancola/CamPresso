"use strict";

// Setup and navigation work without JavaScript. This only adds command copying.
if (navigator.clipboard && window.isSecureContext) {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    const source = document.getElementById(button.dataset.copy);
    if (!source) return;
    button.hidden = false;
    button.addEventListener("click", async () => {
      const status = document.getElementById("copy-status");
      try {
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = "Copied";
        status.textContent = "Installer command copied to clipboard.";
      } catch {
        button.textContent = "Select text";
        status.textContent = "Could not copy. Select the command and copy it manually.";
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(source);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      window.setTimeout(() => { button.textContent = "Copy"; }, 2500);
    });
  });
}
