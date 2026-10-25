// Keep the compatibility behavior of the supplied proxy, without touching nested data.
const STRIP_PARAMETERS = Object.freeze([
  'presence_penalty', 'frequency_penalty', 'top_p', 'top_k', 'temperature',
]);

function prepareBody(payload, enabled = true) {
  const body = { ...payload };
  const removed = [];
  if (enabled) {
    for (const key of STRIP_PARAMETERS) {
      if (Object.hasOwn(body, key)) removed.push(key);
      delete body[key];
    }
  }
  return { body, removed };
}

module.exports = { STRIP_PARAMETERS, prepareBody };

if (require.main === module) {
  import('./server.mjs').then(({ start }) => start()).catch(() => {
    console.error('fine2api could not start. Check configuration and data directory permissions.');
    process.exitCode = 1;
  });
}
