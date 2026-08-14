/**
 * PunchOut Sandbox — edge proxy.
 *
 * WHY THIS EXISTS
 * ---------------
 * A Lambda Function URL VALIDATES the Host header: it only accepts requests
 * addressed to its own `<id>.lambda-url.<region>.on.aws` hostname. Cloudflare,
 * proxying a CNAME, forwards the visitor's Host (`punchoutsandbox.com`), and
 * Lambda answers `403 AccessDeniedException` with an empty JSON body.
 *
 * That failure is nastier to diagnose than it sounds: the origin works
 * perfectly when called directly, Cloudflare's config is entirely correct, and
 * the 403 carries `server: cloudflare` so it reads as a Cloudflare block. Only
 * `x-amzn-errortype: AccessDeniedException` in the response headers gives it
 * away.
 *
 * Cloudflare's own fix for this is the Origin Rules "Host Header Override",
 * which is a PAID feature — the API answers "not entitled to use the
 * HostHeader override" on the free plan. A Worker does the same job for free,
 * and we are already deploying Workers in the neighbouring Xenia repo.
 *
 * Rebuilding the request against a URL on the ORIGIN hostname is what sets
 * Host correctly; there is no need to set the header by hand, and doing so
 * would be ignored anyway since Host is a forbidden header in the Fetch API.
 *
 * The Worker also injects the edge secret, replacing the transform rule that
 * did it before. One less moving part, and it keeps the secret and the Host
 * rewrite in the same place — they are two halves of the same "this request
 * came through Cloudflare" claim.
 */
export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const origin = new URL(env.ORIGIN_URL);

    // Preserve path and query; move the request onto the origin's hostname.
    incoming.protocol = "https:";
    incoming.hostname = origin.hostname;
    incoming.port = "";

    const proxied = new Request(incoming.toString(), request);

    // Prove to app/http.py's require_edge() that this came via Cloudflare.
    // Absent secret means the Lambda leaves enforcement off, so this is safe
    // to deploy before the secret is set.
    if (env.EDGE_SHARED_SECRET) {
      proxied.headers.set("X-Edge-Secret", env.EDGE_SHARED_SECRET);
    }

    // Tell the app what the user actually asked for, since the URL it now
    // sees carries the origin's hostname rather than the real one.
    proxied.headers.set("X-Forwarded-Host", incoming.hostname);

    return fetch(proxied);
  },
};
