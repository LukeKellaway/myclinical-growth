// HTTP Basic Auth gate for the UK National Data Observatory.
//
// While the observatory is in private testing this edge function sits in
// front of every file under /data/observatory/ and demands a username +
// password before anything (HTML, JSON, GeoJSON) is served. Because it runs
// at the edge, the public never even receives the files — this is real
// server-side protection, not a client-side screen.
//
// Credentials come from Netlify environment variables so nothing secret is
// committed to this PUBLIC repo:
//   OBSERVATORY_USER      (optional, defaults to "preview")
//   OBSERVATORY_PASSWORD  (required — if unset, the gate stays fully locked)
// Set them in Netlify: Site settings -> Environment variables.
//
// TO GO PUBLIC: delete this file (and its netlify/edge-functions folder if
// empty). With the function gone, Netlify serves /data/observatory/* as
// ordinary static files. No other change needed.

export default async (request, context) => {
  const USER = Netlify.env.get("OBSERVATORY_USER") || "preview";
  const PASS = Netlify.env.get("OBSERVATORY_PASSWORD");

  const unauthorized = (msg) =>
    new Response(msg || "Authentication required.", {
      status: 401,
      headers: {
        "WWW-Authenticate":
          'Basic realm="MyClinical Observatory (private preview)", charset="UTF-8"',
        "Cache-Control": "no-store",
      },
    });

  // Fail closed: no password configured means nobody gets in.
  if (!PASS) {
    return unauthorized(
      "Observatory preview is locked: set OBSERVATORY_PASSWORD in Netlify."
    );
  }

  const header = request.headers.get("authorization") || "";
  const [scheme, encoded] = header.split(" ");
  if (scheme !== "Basic" || !encoded) return unauthorized();

  let decoded;
  try {
    decoded = atob(encoded);
  } catch {
    return unauthorized();
  }

  const sep = decoded.indexOf(":");
  const user = sep === -1 ? decoded : decoded.slice(0, sep);
  const pass = sep === -1 ? "" : decoded.slice(sep + 1);

  if (user === USER && pass === PASS) {
    return context.next(); // credentials good — serve the requested file
  }
  return unauthorized();
};

// Gate the SPA itself and every asset it fetches (dashboard_data.json + the
// two GeoJSON boundary files all live under this path).
export const config = {
  path: ["/data/observatory", "/data/observatory/*"],
};
