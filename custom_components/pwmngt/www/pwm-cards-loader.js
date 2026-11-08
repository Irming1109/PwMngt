/**
 * PwMngt cards loader (issue #57).
 *
 * This small module is what PwMngt registers as the dashboard resource;
 * it loads the cards themselves (pwm-cards.js) and retries when that
 * fails. Seen on Claus's Android companion app on mobile data (via Nabu
 * Casa, 2026-11-08): the cards' file was never loaded in that app session
 * -- "Custom element doesn't exist: pwm-battery-card" -- while the same
 * dashboard worked at home on Wi-Fi and in Chrome on the same phone. A
 * module that failed to load is remembered as failed for the rest of the
 * page's life, so the app never asked for it again. Each retry here uses
 * a new address (a "retry" query parameter), alternating between this
 * loader's own folder (/local/pwmngt/) and /pwmngt_frontend/. Home
 * Assistant rebuilds a card as soon as its element gets defined, so the
 * cards appear by themselves once a retry succeeds.
 *
 * Every failed attempt is written to Home Assistant's system log
 * ("PwMngt cards: ..."), so the cause can be read there instead of
 * through the phone's remote debugging.
 */
const PWM_LOADER_DELAYS_S = [0, 2, 5, 10, 30, 60];
const PWM_LOADER_LOGGER = "frontend.pwmngt_cards";

const pwmLoaderSelf = new URL(import.meta.url);
const pwmLoaderVersion = pwmLoaderSelf.searchParams.get("v") || "0";
// Same folder first (the address Home Assistant already knows works),
// then the integration's own static path.
const pwmLoaderSources = [
  new URL("pwm-cards.js", pwmLoaderSelf),
  new URL("/pwmngt_frontend/pwm-cards.js", pwmLoaderSelf),
].filter((url, index, all) => all.findIndex((other) => other.pathname === url.pathname) === index);

const pwmLoaderSleep = (seconds) => new Promise((resolve) => setTimeout(resolve, seconds * 1000));

/** Home Assistant's frontend object, once it has a connection (or null). */
async function pwmLoaderHass() {
  for (let i = 0; i < 120; i++) {
    const root = document.querySelector("home-assistant");
    if (root && root.hass && root.hass.connection) return root.hass;
    await pwmLoaderSleep(1);
  }
  return null;
}

/** Write to Home Assistant's system log -- never throws. Warning or
 * above, since the system log drops lower levels by default. */
function pwmLoaderLog(level, message) {
  const text = `PwMngt cards: ${message} [${location.origin}, ${navigator.userAgent}]`;
  (level === "error" ? console.error : console.warn)(text);
  pwmLoaderHass()
    .then((hass) =>
      hass
        ? hass.callWS({ type: "system_log/write", logger: PWM_LOADER_LOGGER, level, message: text })
        : undefined
    )
    .catch(() => {});
}

async function pwmLoadCards() {
  const failures = [];
  for (let attempt = 0; attempt < PWM_LOADER_DELAYS_S.length; attempt++) {
    if (PWM_LOADER_DELAYS_S[attempt]) await pwmLoaderSleep(PWM_LOADER_DELAYS_S[attempt]);
    const url = new URL(pwmLoaderSources[attempt % pwmLoaderSources.length]);
    url.searchParams.set("v", pwmLoaderVersion);
    // The first attempt uses the plain address, the same one the
    // integration also adds as extra JS, so a copy that is already
    // loaded is simply reused.
    if (attempt > 0) url.searchParams.set("retry", String(attempt));
    try {
      await import(url.href);
      if (failures.length) {
        pwmLoaderLog(
          "warning",
          `loaded from ${url.pathname} on attempt ${attempt + 1} after: ${failures.join(" | ")}`
        );
      }
      return;
    } catch (error) {
      const reason = `${url.pathname}?${url.searchParams} -> ${(error && error.message) || error}`;
      failures.push(reason);
      pwmLoaderLog("warning", `attempt ${attempt + 1} failed: ${reason}`);
    }
  }
  pwmLoaderLog("error", `gave up after ${failures.length} attempts`);
}

pwmLoadCards();
