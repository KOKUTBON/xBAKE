/* Standalone deterministic simulation. No production services or hardware. */
(function (root) {
  "use strict";
  const TYPES = ["good", "burnt", "undercooked", "deformed"];
  const LABELS = {
    good: "Good",
    burnt: "Burnt",
    undercooked: "Undercooked",
    deformed: "Deformed",
  };
  const DEFAULTS = Object.freeze({
    burntMax: 95,
    rawMin: 195,
    circularity: 0.65,
    aspectRatio: 0.68,
    speed: 100,
    pulse: 0.4,
  });
  const RANGES = {
    burntMax: [0, 190],
    rawMin: [100, 255],
    circularity: [0.1, 1],
    aspectRatio: [0.1, 1],
    speed: [40, 200],
    pulse: [0.1, 1],
  };
  function sanitizeSettings(value) {
    const settings = { ...DEFAULTS };
    if (!value || typeof value !== "object") return settings;
    Object.entries(RANGES).forEach(([key, [min, max]]) => {
      if (typeof value[key] === "number" && Number.isFinite(value[key]))
        settings[key] = Math.min(max, Math.max(min, value[key]));
    });
    // Keep color windows disjoint.
    settings.burntMax = Math.min(settings.burntMax, settings.rawMin - 1);
    return settings;
  }
  function classify(features, settings) {
    if (
      features.circularity < settings.circularity ||
      features.aspectRatio < settings.aspectRatio
    ) {
      return {
        result: "deformed",
        score: Math.round(Math.max(0.5, 1 - features.circularity) * 100),
      };
    }
    if (features.color <= settings.burntMax) {
      return {
        result: "burnt",
        score: Math.round(
          Math.min(
            0.99,
            Math.max(
              0.6,
              1 - (features.color / Math.max(1, settings.burntMax)) * 0.4,
            ),
          ) * 100,
        ),
      };
    }
    if (features.color >= settings.rawMin) {
      return {
        result: "undercooked",
        score: Math.round(
          Math.min(0.95, 0.65 + (features.color - settings.rawMin) / 60) * 100,
        ),
      };
    }
    return { result: "good", score: 95 };
  }
  class Simulation {
    constructor(settings = DEFAULTS, now = Date.now()) {
      this.settings = sanitizeSettings(settings);
      this.seed = 7;
      this.records = [];
      this.items = [];
      this.counts = Object.fromEntries(TYPES.map((type) => [type, 0]));
      this.total = 0;
      this.nextId = 1;
      this.elapsed = 0;
      this.distance = 0;
      this.spawnDistance = 0;
      this.scenario = "balanced";
      this.running = true;
      this.pulseRemaining = 0;
      this.startedAt = now;
      this.recentTimes = [];
      this.initialThroughput = 30;
      // Deliberately fixed fixture distribution: 112 good + 7 burnt + 5 pale + 4 shape.
      for (let i = 0; i < 128; i++) {
        const type =
          i < 120 && i % 18 === 0
            ? "burnt"
            : i < 120 && i % 24 === 5
              ? "undercooked"
              : i % 30 === 10
                ? "deformed"
                : "good";
        const features = this.features(type);
        // Seed history uses original defaults, independent of saved calibration.
        this.record(
          { id: this.nextId++, type, features },
          now - (128 - i) * 2000,
          DEFAULTS,
        );
      }
      this.seed = 17;
      // Start with a readable selection already moving through the scene.
      ["good", "burnt", "undercooked", "good", "deformed", "good"].forEach(
        (type, index) => {
          this.items.push(this.makeItem(type, 90 + index * 132, index % 3));
        },
      );
    }
    random() {
      this.seed = (Math.imul(1664525, this.seed) + 1013904223) >>> 0;
      return this.seed / 4294967296;
    }
    features(type) {
      const color = { good: 152, burnt: 65, undercooked: 222, deformed: 150 }[
        type
      ];
      return {
        color: Math.round(color + this.random() * 18 - 9),
        circularity:
          type === "deformed"
            ? 0.43
            : +(0.84 + this.random() * 0.12).toFixed(2),
        aspectRatio:
          type === "deformed" ? 0.56 : +(0.87 + this.random() * 0.1).toFixed(2),
      };
    }
    makeItem(type, x = -70, lane = this.nextId % 3) {
      return {
        id: this.nextId++,
        type,
        features: this.features(type),
        x,
        y: [140, 270, 400][lane],
        result: null,
        score: null,
        inspected: false,
        rejected: false,
        rotation: this.random() * 0.7 - 0.35,
      };
    }
    nextType() {
      const roll = this.random();
      if (this.scenario !== "balanced")
        return roll < 0.7 ? this.scenario : "good";
      return roll < 0.78
        ? "good"
        : roll < 0.87
          ? "burnt"
          : roll < 0.94
            ? "undercooked"
            : "deformed";
    }
    record(item, timestamp, settings = this.settings) {
      const verdict = classify(item.features, settings);
      const record = {
        id: item.id,
        type: item.type,
        ...item.features,
        ...verdict,
        rejected: verdict.result !== "good",
        timestamp,
      };
      this.total++;
      this.counts[record.result]++;
      this.records.unshift(record);
      if (this.records.length > 2000) this.records.pop();
      return record;
    }
    tick(seconds) {
      if (!this.running || seconds <= 0) return false;
      // Cap long gaps after tab suspension instead of fast-forwarding products.
      seconds = Math.min(seconds, 0.1);
      this.elapsed += seconds;
      const movement = this.settings.speed * seconds;
      this.distance += movement;
      this.spawnDistance += movement;
      this.pulseRemaining = Math.max(0, this.pulseRemaining - seconds);
      if (this.spawnDistance >= 200) {
        this.spawnDistance -= 200;
        this.items.push(this.makeItem(this.nextType()));
      }
      let changed = false;
      this.items.forEach((item) => {
        item.x += movement;
        if (!item.inspected && item.x >= 480) {
          item.inspected = true;
          const record = this.record(
            item,
            this.startedAt + this.elapsed * 1000,
          );
          item.result = record.result;
          item.score = record.score;
          this.recentTimes.push(this.elapsed);
          changed = true;
        }
        if (
          item.inspected &&
          item.result !== "good" &&
          item.x >= 800 &&
          !item.rejected
        ) {
          item.rejected = true;
          this.pulseRemaining = this.settings.pulse;
        }
      });
      this.items = this.items.filter(
        (item) => item.x < 1060 && !(item.rejected && item.x > 890),
      );
      this.recentTimes = this.recentTimes.filter(
        (time) => time >= this.elapsed - 60,
      );
      return changed;
    }
    get rejected() {
      return this.total - this.counts.good;
    }
    get yield() {
      return this.total ? (this.counts.good / this.total) * 100 : 0;
    }
    get throughput() {
      if (this.elapsed < 60 && this.initialThroughput)
        return Math.round((this.settings.speed / 200) * 60);
      return Math.round(
        (this.recentTimes.length / Math.max(1, Math.min(this.elapsed, 60))) *
          60,
      );
    }
    reset(now = Date.now()) {
      this.records = [];
      this.items = [];
      this.counts = Object.fromEntries(TYPES.map((type) => [type, 0]));
      this.total = 0;
      this.nextId = 1;
      this.elapsed = 0;
      this.distance = 0;
      this.spawnDistance = 0;
      this.pulseRemaining = 0;
      this.startedAt = now;
      this.recentTimes = [];
      this.initialThroughput = 0;
    }
  }
  function csv(records) {
    const columns = [
      "id",
      "timestamp",
      "result",
      "score",
      "color",
      "circularity",
      "aspectRatio",
      "rejected",
    ];
    return (
      [
        "item_id,timestamp,result,illustrative_rule_score_pct,mean_intensity,circularity,aspect_ratio,simulated_reject",
        ...records.map((record) =>
          columns
            .map((key) =>
              key === "timestamp"
                ? new Date(record[key]).toISOString()
                : record[key],
            )
            .join(","),
        ),
      ].join("\r\n") + "\r\n"
    );
  }
  const api = {
    TYPES,
    LABELS,
    DEFAULTS,
    sanitizeSettings,
    classify,
    Simulation,
    csv,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.XBakeDemo = api;
})(typeof window !== "undefined" ? window : globalThis);
