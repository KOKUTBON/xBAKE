(function () {
  "use strict";
  const {
    TYPES,
    LABELS,
    DEFAULTS,
    sanitizeSettings,
    classify,
    Simulation,
    csv,
  } = window.XBakeDemo;
  const $ = (id) => document.getElementById(id);
  const storage = {
    get(key) {
      try {
        return localStorage.getItem("xbake-demo-" + key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem("xbake-demo-" + key, value);
        return true;
      } catch {
        return false;
      }
    },
  };
  let settings;
  try {
    settings = sanitizeSettings(JSON.parse(storage.get("settings")));
  } catch {
    settings = { ...DEFAULTS };
  }
  const simulation = new Simulation(settings);
  const canvas = $("conveyor");
  const ctx = canvas.getContext("2d");
  const images = {};
  const colors = {
    good: "#70b68b",
    burnt: "#e68c76",
    undercooked: "#e1be65",
    deformed: "#b4a0d5",
  };
  let view = "annotated",
    filter = "all",
    tab = "color",
    toastTimer,
    last = 0,
    lastRender = 0;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion) simulation.running = false;
  TYPES.forEach((type) => {
    const img = new Image();
    img.src = "assets/" + type + ".png";
    images[type] = img;
  });
  const theme = storage.get("theme") === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  function updateThemeLabel() {
    $("themeButton").setAttribute(
      "aria-label",
      `Switch to ${document.documentElement.dataset.theme === "dark" ? "light" : "dark"} theme`,
    );
  }
  updateThemeLabel();
  $("themeButton").onclick = () => {
    const next =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    storage.set("theme", next);
    updateThemeLabel();
  };
  function toast(message) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").classList.add("visible");
    toastTimer = setTimeout(() => $("toast").classList.remove("visible"), 3500);
  }
  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function formatId(id) {
    return "XB-" + String(id).padStart(5, "0");
  }
  function formatTime(timestamp) {
    return new Date(timestamp).toLocaleTimeString([], { hour12: false });
  }
  function formatDuration(seconds) {
    return [
      Math.floor(seconds / 3600),
      Math.floor(seconds / 60) % 60,
      Math.floor(seconds) % 60,
    ]
      .map((value) => String(value).padStart(2, "0"))
      .join(":");
  }
  function tag(type) {
    const span = document.createElement("span");
    span.className = "result-tag " + type;
    span.textContent = LABELS[type];
    return span;
  }
  function renderMetrics() {
    const yieldValue = simulation.yield.toFixed(1);
    $("total").textContent = simulation.total.toLocaleString();
    $("yield").textContent = yieldValue;
    $("rejected").textContent = simulation.rejected.toLocaleString();
    $("goodCount").textContent =
      simulation.counts.good.toLocaleString() + " passed";
    $("defectRate").textContent =
      (simulation.total ? 100 - simulation.yield : 0).toFixed(1) +
      "% defect rate";
    $("throughput").textContent = simulation.throughput;
    $("yieldBar").style.width = yieldValue + "%";
    $("donutYield").textContent = yieldValue + "%";
    let stop = 0;
    const segments = TYPES.map((type) => {
      const start = stop;
      stop += simulation.total
        ? (simulation.counts[type] / simulation.total) * 100
        : 0;
      return `var(--${{ good: "green", burnt: "red", undercooked: "yellow", deformed: "purple" }[type]}) ${start}% ${stop}%`;
    });
    $("donut").style.background = simulation.total
      ? `conic-gradient(${segments.join(",")})`
      : "var(--line)";
    $("donut").setAttribute(
      "aria-label",
      TYPES.map((type) => `${LABELS[type]}: ${simulation.counts[type]}`).join(
        ", ",
      ),
    );
    $("distribution").replaceChildren();
    TYPES.forEach((type) => {
      const row = document.createElement("div");
      row.className = "distribution-row";
      const dot = document.createElement("i");
      dot.className = {
        good: "good-bg",
        burnt: "burnt-bg",
        undercooked: "raw-bg",
        deformed: "shape-bg",
      }[type];
      const label = document.createElement("span");
      label.textContent = LABELS[type];
      const amount = document.createElement("span");
      amount.className = "amount";
      amount.textContent = simulation.counts[type];
      const percent = document.createElement("span");
      percent.className = "percent";
      percent.textContent =
        (simulation.total
          ? (simulation.counts[type] / simulation.total) * 100
          : 0
        ).toFixed(1) + "%";
      row.append(dot, label, amount, percent);
      $("distribution").append(row);
    });
    $("navCount").textContent = simulation.total;
    $("logCount").textContent = simulation.total;
  }
  function renderLog() {
    const search = $("search").value.trim().toLowerCase();
    const matches = simulation.records.filter(
      (row) =>
        (filter !== "rejected" || row.rejected) &&
        (!search ||
          formatId(row.id).toLowerCase().includes(search) ||
          row.result.includes(search)),
    );
    const records = matches.slice(0, 50);
    const body = $("logBody");
    body.replaceChildren();
    records.forEach((record) => {
      const row = document.createElement("tr");
      const id = row.insertCell();
      const button = document.createElement("button");
      button.className = "item-link";
      button.textContent = formatId(record.id);
      button.setAttribute(
        "aria-label",
        `Inspect ${formatId(record.id)}, ${LABELS[record.result]}`,
      );
      button.onclick = () => openItem(record);
      id.append(button);
      row.insertCell().textContent = formatTime(record.timestamp);
      row.insertCell().append(tag(record.result));
      row.insertCell().textContent = record.score + "%";
      const action = row.insertCell();
      action.textContent = record.rejected ? "↗ Reject queued" : "✓ Passed";
      action.className = record.rejected ? "action-reject" : "action-pass";
      row.insertCell().textContent = "→";
      row.onclick = (event) => {
        if (!event.target.closest("button")) openItem(record);
      };
      body.append(row);
    });
    if (!records.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 6;
      cell.className = "empty-row";
      cell.textContent = simulation.total
        ? "No inspections match your filters."
        : "A fresh batch. Start the line to inspect new products.";
    }
    $("shownCount").textContent =
      `Showing ${records.length} of ${matches.length} matching records${simulation.total > simulation.records.length ? " · recent 2,000 retained" : ""}`;
  }
  function openItem(record) {
    $("itemTitle").textContent = formatId(record.id);
    $("itemImage").src = images[record.type].src;
    $("itemImage").alt =
      `Synthetic ${LABELS[record.type].toLowerCase()} flatbread`;
    $("itemResult").replaceChildren(tag(record.result));
    $("itemFeatures").replaceChildren();
    [
      ["Recorded", formatTime(record.timestamp)],
      ["Mean intensity", record.color + " / 255"],
      ["Circularity", record.circularity.toFixed(2)],
      ["Aspect ratio", record.aspectRatio.toFixed(2)],
      ["Illustrative rule score", record.score + "%"],
      ["Simulated action", record.rejected ? "Reject queued" : "Passed"],
    ].forEach(([label, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      $("itemFeatures").append(dt, dd);
    });
    $("itemDialog").showModal();
  }
  $("search").oninput = renderLog;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.onclick = () => {
      filter = button.dataset.filter;
      selectButtons("[data-filter]", button);
      renderLog();
    };
  });
  function selectButtons(selector, selected) {
    document.querySelectorAll(selector).forEach((button) => {
      const active = button === selected;
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.onclick = () => {
      view = button.dataset.view;
      selectButtons("[data-view]", button);
      draw();
    };
  });
  function updateRunningState() {
    $("pauseButton").innerHTML = simulation.running
      ? '<span aria-hidden="true">Ⅱ</span> Pause line'
      : '<span aria-hidden="true">▷</span> Resume line';
    $("pauseOverlay").hidden = simulation.running;
    $("lineStatus").textContent = simulation.running
      ? "Line running"
      : "Line paused";
    $("liveLabel").lastChild.textContent = simulation.running
      ? " LIVE DEMO"
      : " PAUSED";
    $("feedStatus").textContent = simulation.running
      ? "Processing frames"
      : "Paused · sample data";
    $("runningDot").style.background = simulation.running
      ? "var(--green)"
      : "var(--yellow)";
  }
  $("pauseButton").onclick = () => {
    simulation.running = !simulation.running;
    updateRunningState();
  };
  $("scenario").onchange = () => {
    simulation.scenario = $("scenario").value;
    toast("Scenario changed. New products will use this batch mix.");
  };
  $("testRejector").onclick = () => {
    simulation.pulseRemaining = simulation.settings.pulse;
    draw();
    toast("Simulated rejector pulse — no hardware is connected.");
    setTimeout(() => {
      if (!simulation.running) {
        simulation.pulseRemaining = 0;
        draw();
      }
    }, simulation.settings.pulse * 1000);
  };
  $("saveFrame").onclick = () =>
    canvas.toBlob((blob) => {
      if (blob) {
        download(blob, "xbake-demo-frame.png");
        toast("Synthetic frame saved.");
      }
    }, "image/png");
  $("exportButton").onclick = () => {
    download(
      new Blob([csv(simulation.records)], { type: "text/csv;charset=utf-8" }),
      "xbake-demo-inspections.csv",
    );
    toast(
      `Exported ${simulation.records.length} simulated inspection records.`,
    );
  };
  $("expandButton").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $("streamWrap").requestFullscreen();
    } catch {
      toast("Fullscreen is unavailable in this browser.");
    }
  };
  const fields = {
    color: [
      [
        "burntMax",
        "Burnt: maximum intensity",
        0,
        190,
        1,
        "Darker products at or below this are burnt.",
      ],
      [
        "rawMin",
        "Undercooked: minimum intensity",
        100,
        255,
        1,
        "Paler products at or above this are undercooked.",
      ],
    ],
    geometry: [
      [
        "circularity",
        "Minimum circularity",
        0.1,
        1,
        0.01,
        "1.00 is a perfect circle. Lower values indicate deformation.",
      ],
      [
        "aspectRatio",
        "Minimum aspect ratio",
        0.1,
        1,
        0.01,
        "Ratio of the short side to the long side.",
      ],
    ],
    conveyor: [
      [
        "speed",
        "Belt speed",
        40,
        200,
        5,
        "Pixels per second. Faster belts increase simulated throughput.",
      ],
      [
        "pulse",
        "Rejector pulse duration",
        0.1,
        1,
        0.05,
        "Seconds the simulated rejector remains active.",
      ],
    ],
  };
  function renderFields() {
    $("calibrationFields").replaceChildren();
    fields[tab].forEach(([key, label, min, max, step, help]) => {
      const field = document.createElement("div");
      field.className = "field";
      const head = document.createElement("label");
      head.htmlFor = "setting-" + key;
      head.textContent = label;
      const output = document.createElement("output");
      output.htmlFor = head.htmlFor;
      const format = (value) =>
        key === "speed"
          ? value + " px/s"
          : key === "pulse"
            ? Number(value).toFixed(2) + " s"
            : step < 1
              ? Number(value).toFixed(2)
              : value;
      output.value = format(simulation.settings[key]);
      head.append(output);
      const hint = document.createElement("small");
      hint.textContent = help;
      const input = document.createElement("input");
      input.type = "range";
      input.id = head.htmlFor;
      input.min = min;
      input.max = max;
      input.step = step;
      input.value = simulation.settings[key];
      input.oninput = () => {
        let value = Number(input.value);
        if (key === "burntMax")
          value = Math.min(value, simulation.settings.rawMin - 1);
        if (key === "rawMin")
          value = Math.max(value, simulation.settings.burntMax + 1);
        input.value = value;
        simulation.settings[key] = value;
        output.value = format(value);
        $("savedState").textContent = "Unsaved";
        $("savedState").style.color = "var(--accent)";
        renderMetrics();
        draw();
      };
      field.append(head, hint, input);
      $("calibrationFields").append(field);
    });
  }
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.onclick = () => {
      tab = button.dataset.tab;
      selectButtons("[data-tab]", button);
      renderFields();
    };
  });
  $("saveCalibration").onclick = () => {
    const saved = storage.set("settings", JSON.stringify(simulation.settings));
    $("savedState").textContent = saved ? "Saved locally" : "Session only";
    $("savedState").style.color = "var(--green)";
    toast(
      saved
        ? "Calibration saved in this browser."
        : "Browser storage is unavailable. Settings apply to this session.",
    );
  };
  $("restoreCalibration").onclick = () => {
    simulation.settings = { ...DEFAULTS };
    $("savedState").textContent = "Unsaved";
    $("savedState").style.color = "var(--accent)";
    renderFields();
    renderMetrics();
    draw();
    toast("Default rules restored. Save to keep them.");
  };
  if (storage.get("settings")) $("savedState").textContent = "Saved locally";
  $("aboutButton").onclick = $("noticeAbout").onclick = () =>
    $("aboutDialog").showModal();
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.onclick = () => button.closest("dialog").close();
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          dialog.close();
      }
    });
  });
  $("resetButton").onclick = () => $("resetDialog").showModal();
  $("confirmReset").onclick = () => {
    simulation.reset();
    $("resetDialog").close();
    $("search").value = "";
    renderMetrics();
    renderLog();
    draw();
    toast("Session reset. Your calibration settings are kept.");
  };
  document.querySelectorAll("a.nav-link").forEach((link) => {
    link.onclick = () => {
      document
        .querySelectorAll("a.nav-link")
        .forEach((other) => other.classList.toggle("active", other === link));
    };
  });
  function draw() {
    const mask = view === "mask";
    ctx.fillStyle = mask ? "#080b0a" : "#1e2622";
    ctx.fillRect(0, 0, 960, 540);
    if (!mask) {
      // Belt weave moves with the simulation; it is generated, not camera footage.
      for (let y = 0; y < 540; y += 5) {
        ctx.strokeStyle = y % 10 ? "#25302a" : "#18201c";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(960, y);
        ctx.stroke();
      }
      const offset = simulation.distance % 12;
      for (let x = offset; x < 960; x += 12) {
        ctx.fillStyle = "#8b9a8b07";
        ctx.fillRect(x, 0, 1, 540);
      }
      ctx.strokeStyle = "#bacaba14";
      ctx.setLineDash([7, 10]);
      [205, 335].forEach((y) => {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(960, y);
        ctx.stroke();
      });
    }
    ctx.setLineDash([8, 7]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#91b3985e";
    ctx.strokeRect(35, 65, 700, 410);
    ctx.setLineDash([]);
    ctx.font = "11px monospace";
    ctx.fillStyle = "#9fbaa7";
    ctx.fillText("INSPECTION ZONE", 45, 57);
    ctx.strokeStyle = "#91b39850";
    ctx.setLineDash([5, 8]);
    ctx.beginPath();
    ctx.moveTo(480, 65);
    ctx.lineTo(480, 475);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#9db0a4";
    ctx.font = "9px monospace";
    ctx.fillText("COUNT LINE", 451, 491);
    simulation.items.forEach((item) => {
      const preview = classify(item.features, simulation.settings);
      const result = item.result || preview.result;
      const color = colors[result];
      let y = item.y;
      if (item.rejected) y += Math.max(0, item.x - 800) * 2.8;
      ctx.save();
      ctx.translate(item.x, y);
      ctx.rotate(item.rotation);
      if (mask) {
        ctx.fillStyle = "#eef7f0";
        ctx.beginPath();
        ctx.ellipse(
          0,
          0,
          54,
          item.type === "deformed" ? 32 : 54,
          0,
          0,
          Math.PI * 2,
        );
        ctx.fill();
      } else if (images[item.type].complete && images[item.type].naturalWidth) {
        ctx.shadowColor = "#0008";
        ctx.shadowBlur = 12;
        ctx.shadowOffsetY = 4;
        ctx.drawImage(images[item.type], -68, -68, 136, 136);
      }
      ctx.restore();
      if (!mask) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.strokeRect(item.x - 64, y - 64, 128, 128);
        const label = `${formatId(item.id)}  ${LABELS[result].toUpperCase()}`;
        ctx.font = "10px monospace";
        const width = ctx.measureText(label).width + 12;
        ctx.fillStyle = color;
        ctx.fillRect(item.x - 64, y - 82, width, 18);
        ctx.fillStyle = "#17251f";
        ctx.fillText(label, item.x - 58, y - 69);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(item.x, y, 2, 0, Math.PI * 2);
        ctx.fill();
      }
    });
    ctx.fillStyle = simulation.pulseRemaining > 0 ? "#dc947631" : "#d69b7012";
    ctx.fillRect(790, 66, 20, 410);
    ctx.strokeStyle = simulation.pulseRemaining > 0 ? "#ffa774" : "#b88459";
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(800, 66);
    ctx.lineTo(800, 476);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "10px monospace";
    ctx.fillStyle = "#d4a47f";
    ctx.fillText(
      simulation.pulseRemaining > 0 ? "PULSE ACTIVE" : "REJECT LINE",
      766,
      491,
    );
    ctx.font = "10px monospace";
    ctx.fillStyle = "#738c7d";
    ctx.fillText("BELT DIRECTION  →", 42, 511);
    ctx.fillText(`${simulation.settings.speed} px/s`, 648, 511);
  }
  function frame(timestamp) {
    const seconds = last ? (timestamp - last) / 1000 : 0;
    last = timestamp;
    const changed = document.hidden ? false : simulation.tick(seconds);
    if (changed) {
      renderMetrics();
      renderLog();
    }
    if (timestamp - lastRender >= 40) {
      draw();
      $("sessionTime").textContent =
        "Demo session · " + formatDuration(simulation.elapsed).slice(3);
      $("streamClock").textContent = formatDuration(simulation.elapsed);
      lastRender = timestamp;
    }
    requestAnimationFrame(frame);
  }
  // Hidden tabs must not accrue time or generate unseen inspections.
  document.addEventListener("visibilitychange", () => {
    last = 0;
  });
  renderMetrics();
  renderLog();
  renderFields();
  updateRunningState();
  draw();
  requestAnimationFrame(frame);
})();
