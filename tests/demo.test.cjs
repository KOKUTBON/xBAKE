const test = require("node:test");
const assert = require("node:assert/strict");
const {
  Simulation,
  DEFAULTS,
  classify,
  sanitizeSettings,
  csv,
} = require("../site/engine.js");

function advance(simulation, seconds) {
  for (let i = 0; i < seconds * 25; i++) simulation.tick(1 / 25);
}

test("seed history has exact counts, internally consistent metrics, and deterministic features", () => {
  const sim = new Simulation(DEFAULTS, 1000000);
  assert.equal(sim.total, 128);
  assert.deepEqual(sim.counts, {
    good: 112,
    burnt: 7,
    undercooked: 5,
    deformed: 4,
  });
  assert.equal(sim.yield, 87.5);
  assert.equal(sim.rejected, 16);
  assert.deepEqual(sim.records, new Simulation(DEFAULTS, 1000000).records);
  assert.equal(new Set(sim.records.map((row) => row.id)).size, 128);
});

test("pause freezes products and counters; resume counts every product exactly once", () => {
  const sim = new Simulation();
  sim.running = false;
  const before = JSON.stringify(sim);
  advance(sim, 10);
  assert.equal(JSON.stringify(sim), before);
  sim.running = true;
  advance(sim, 30);
  assert.ok(sim.total > 128);
  assert.equal(
    sim.total,
    Object.values(sim.counts).reduce((sum, n) => sum + n, 0),
  );
  assert.equal(
    new Set(sim.records.map((row) => row.id)).size,
    sim.records.length,
  );
  assert.equal(sim.rejected, sim.records.filter((row) => row.rejected).length);
});

test("classification follows geometry then burnt then pale rules; calibration affects new records only", () => {
  const features = { color: 150, circularity: 0.9, aspectRatio: 0.9 };
  assert.equal(classify(features, DEFAULTS).result, "good");
  assert.equal(classify({ ...features, color: 65 }, DEFAULTS).result, "burnt");
  assert.equal(
    classify({ ...features, color: 220 }, DEFAULTS).result,
    "undercooked",
  );
  assert.equal(
    classify({ ...features, color: 65, circularity: 0.4 }, DEFAULTS).result,
    "deformed",
  );
  const sim = new Simulation();
  const historical = JSON.stringify(sim.records);
  sim.settings.burntMax = 180;
  assert.equal(JSON.stringify(sim.records), historical);
  assert.equal(
    sim.record({ id: 999, type: "good", features }, Date.now()).result,
    "burnt",
  );
});

test("scenario changes alter the new product mix and speed changes throughput", () => {
  const sim = new Simulation();
  sim.scenario = "burnt";
  const burnt = Array.from({ length: 100 }, () => sim.nextType());
  assert.ok(burnt.filter((type) => type === "burnt").length > 55);
  assert.ok(burnt.every((type) => type === "burnt" || type === "good"));
  sim.settings.speed = 200;
  assert.equal(sim.throughput, 60);
  sim.settings.speed = 40;
  assert.equal(sim.throughput, 12);
});

test("reset clears history and pulses while preserving rules, scenario, and pause state", () => {
  const sim = new Simulation({ ...DEFAULTS, speed: 150 });
  sim.scenario = "deformed";
  sim.running = false;
  sim.pulseRemaining = 0.4;
  sim.reset(1000);
  assert.equal(sim.total, 0);
  assert.equal(sim.yield, 0);
  assert.equal(sim.throughput, 0);
  assert.equal(sim.records.length, 0);
  assert.equal(sim.items.length, 0);
  assert.equal(sim.pulseRemaining, 0);
  assert.equal(sim.elapsed, 0);
  assert.equal(sim.settings.speed, 150);
  assert.equal(sim.scenario, "deformed");
  assert.equal(sim.running, false);
  sim.running = true;
  advance(sim, 10);
  assert.ok(sim.total > 0);
  assert.ok(sim.records.every((row) => row.timestamp >= 1000));
});

test("saved settings are clamped and invalid values cannot corrupt the simulation", () => {
  const sanitized = sanitizeSettings({
    speed: Infinity,
    pulse: -5,
    circularity: "bad",
    burntMax: 190,
    rawMin: 100,
    unrelated: "ignored",
  });
  assert.equal(sanitized.speed, DEFAULTS.speed);
  assert.equal(sanitized.pulse, 0.1);
  assert.equal(sanitized.circularity, DEFAULTS.circularity);
  assert.ok(sanitized.burntMax < sanitized.rawMin);
  assert.equal(sanitized.unrelated, undefined);
  assert.deepEqual(sanitizeSettings(null), DEFAULTS);
});

test("history is bounded; exported data matches recorded features and includes UTC timestamps", () => {
  const sim = new Simulation(DEFAULTS, 1000000);
  const row = sim.records[0];
  const output = csv([row]);
  assert.equal(output.split("\r\n").length, 3);
  assert.ok(output.includes(new Date(row.timestamp).toISOString()));
  assert.ok(output.includes(`${row.result},${row.score},${row.color}`));
  assert.equal(csv([]).trim().split("\n").length, 1);
  for (let i = 0; i < 2100; i++)
    sim.record(
      {
        id: 1000 + i,
        type: "good",
        features: { color: 150, circularity: 0.9, aspectRatio: 0.9 },
      },
      1000000 + i,
    );
  assert.equal(sim.records.length, 2000);
  assert.equal(sim.total, 2228);
});
