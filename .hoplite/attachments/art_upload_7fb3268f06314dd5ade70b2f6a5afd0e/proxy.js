const http = require("http");
const { Readable } = require("stream");

const UPSTREAM = "https://gateway.finest.so/gateway/openai/v1";
const PORT = 4444;
const STRIP = ["presence_penalty", "frequency_penalty", "top_p", "top_k", "temperature"];
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "*",
};
const DROP_REQ = ["host", "content-length", "connection", "accept-encoding"];
const DROP_RES = ["content-encoding", "content-length", "transfer-encoding", "connection"];

http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") { res.writeHead(204, CORS); return res.end(); }
  try {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    let body = Buffer.concat(chunks);

    if (body.length && (req.headers["content-type"] || "").includes("json")) {
      try {
        const json = JSON.parse(body.toString("utf8"));
        STRIP.forEach((k) => delete json[k]);
        body = Buffer.from(JSON.stringify(json));
      } catch (_) {}
    }

    const headers = {};
    for (const [k, v] of Object.entries(req.headers)) if (!DROP_REQ.includes(k)) headers[k] = v;

    const upstream = await fetch(UPSTREAM + req.url.replace(/^\/v1/, ""), {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
    });

    const out = { ...CORS };
    upstream.headers.forEach((v, k) => { if (!DROP_RES.includes(k)) out[k] = v; });
    res.writeHead(upstream.status, out);
    if (upstream.body) Readable.fromWeb(upstream.body).pipe(res);
    else res.end();
  } catch (e) {
    res.writeHead(502, { ...CORS, "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: { message: "Proxy error: " + e.message } }));
  }
}).listen(PORT, () => console.log(`Proxy running: http://0.0.0.0:${PORT}/v1`));