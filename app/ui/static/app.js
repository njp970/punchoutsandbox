/* The only JavaScript on this site.
 *
 * It exists as a file rather than as inline `onclick` attributes for one
 * reason: the Content-Security-Policy sets `script-src 'self'`, and an inline
 * handler is inline script — CSP blocks it exactly as it blocks a <script>
 * block. The Copy buttons were written as onclick handlers and stopped working
 * the moment the CSP shipped, silently, because a blocked handler logs to the
 * console and nothing else.
 *
 * Delegated from the document so that markup rendered later needs no wiring.
 */
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;

  var value = button.getAttribute("data-copy");
  var restore = button.textContent;

  function done(message) {
    button.textContent = message;
    setTimeout(function () { button.textContent = restore; }, 1500);
  }

  /* navigator.clipboard is unavailable on insecure origins and in some
   * embedded browsers. Telling the user beats appearing to work. */
  if (!navigator.clipboard) {
    done("Select and copy");
    return;
  }
  navigator.clipboard.writeText(value).then(
    function () { done("Copied"); },
    function () { done("Press Ctrl+C"); }
  );
});
