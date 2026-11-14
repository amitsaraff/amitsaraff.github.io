// Data is loaded by steps.js before this deferred script.
const GOAL = 10000,
  fmt = (n) => n.toLocaleString("en-US"),
  $ = (id) => document.getElementById(id);
const data = new Map(
  CSV.trim()
    .split("\n")
    .slice(1)
    .map((row) => {
      const [d, n] = row.split(",");
      return [d, Number(n)];
    }),
);
const dates = Array.from(
    { length: 365 },
    (_, i) => new Date(Date.UTC(2026, 0, i + 1)),
  ),
  key = (d) => d.toISOString().slice(0, 10);
const values = dates.map((d) => data.get(key(d))),
  recorded = values.filter((n) => n !== undefined),
  last = values.findLastIndex((n) => n !== undefined);
const monthNames = Array.from({ length: 12 }, (_, i) =>
  new Date(Date.UTC(2026, i, 1)).toLocaleDateString("en-US", {
    month: "long",
    timeZone: "UTC",
  }),
);
let month = dates[Math.max(0, last)].getUTCMonth(),
  mobile = matchMedia("(max-width:600px)"),
  max = Math.max(20000, ...recorded) * 1.08;
const sum = recorded.reduce((a, b) => a + b, 0),
  hits = recorded.filter((n) => n >= GOAL).length;
let longest = 0,
  run = 0;
values.forEach((n) => {
  run = n >= GOAL ? run + 1 : 0;
  longest = Math.max(longest, run);
});
const annualGoal = dates.length * GOAL,
  annualPercent = (sum / annualGoal) * 100;
$("year-percent").textContent = annualPercent.toFixed(1) + "%";
$("as-of").textContent =
  last >= 0
    ? "As of " +
      dates[last].toLocaleDateString("en-GB", {
        day: "numeric",
        month: "long",
        timeZone: "UTC",
      })
    : "";
$("year-target").textContent = fmt(sum) + " of " + fmt(annualGoal) + " steps";
$("average").textContent = recorded.length
  ? fmt(Math.round(sum / recorded.length))
  : "—";
$("hits").textContent = fmt(hits);
$("streak").textContent = longest + " days";
$("hit-note").textContent = `of ${recorded.length} recorded days`;
$("updated").textContent =
  last >= 0 ? "LATEST ENTRY / " + key(dates[last]) : "NO ENTRIES YET";
function renderCumulative() {
  const width = Math.max(300, $("cumulative-chart").clientWidth),
    height = mobile.matches ? 285 : 340;
  const left = 42,
    right = 18,
    top = 65,
    bottom = 30,
    plotW = width - left - right,
    plotH = height - top - bottom;
  const ceiling = Math.max(annualGoal, sum) * 1.04,
    x = (day) => left + (day / 365) * plotW,
    y = (n) => height - bottom - (n / ceiling) * plotH,
    goalLabelY = y(annualGoal) - 12;
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-labelledby",
    "cumulative-svg-title cumulative-svg-desc",
  );
  function add(tag, attrs, text) {
    const el = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    if (text !== undefined) el.textContent = text;
    svg.append(el);
    return el;
  }
  add(
    "title",
    { id: "cumulative-svg-title" },
    "Recorded steps compared with the annual walking goal",
  );
  const gap = sum - (last + 1) * GOAL,
    asOf =
      last >= 0
        ? dates[last].toLocaleDateString("en-GB", {
            day: "numeric",
            month: "short",
            timeZone: "UTC",
          })
        : "—";
  add(
    "desc",
    { id: "cumulative-svg-desc" },
    `${fmt(sum)} recorded steps through ${asOf}. Annual target: ${fmt(annualGoal)}. Dashed line represents 10,000 steps per calendar day. Actual line ends at the latest entry.`,
  );
  const label = {
    fill: "var(--muted)",
    "font-family": "ui-monospace,monospace",
    "font-size": 12,
  };
  [0, 1000000, 2000000, 3000000].forEach((n) => {
    add("line", {
      x1: left,
      y1: y(n),
      x2: width - right,
      y2: y(n),
      stroke: "var(--line)",
      "stroke-width": 0.6,
    });
    add(
      "text",
      { ...label, x: left - 9, y: y(n) + 3, "text-anchor": "end" },
      n === 0 ? "0" : n / 1000000 + "m",
    );
  });
  [0, 3, 6, 9].forEach((m) => {
    const day = (Date.UTC(2026, m, 1) - Date.UTC(2026, 0, 1)) / 86400000;
    add(
      "text",
      {
        ...label,
        x: x(day),
        y: height - 8,
        "text-anchor": m === 0 ? "start" : "middle",
      },
      monthNames[m].slice(0, 3),
    );
  });
  add(
    "text",
    { ...label, x: x(365), y: height - 8, "text-anchor": "end" },
    "Dec 31",
  );
  add("line", {
    x1: x(0),
    y1: y(0),
    x2: x(365),
    y2: y(annualGoal),
    stroke: "var(--pace)",
    "stroke-width": 1.2,
    "stroke-dasharray": "5 6",
  });
  const goalLabel = add(
    "text",
    { ...label, x: x(365), y: goalLabelY, "text-anchor": "end" },
    "3.65m · goal",
  );
  add(
    "text",
    { ...label, x: x(205), y: y(205 * GOAL) + 20 },
    "10,000/day pace",
  );
  let total = 0,
    path = `M ${x(0)} ${y(0)}`,
    pen = true;
  for (let i = 0; i <= last; i++) {
    if (values[i] === undefined) {
      pen = false;
      continue;
    }
    total += values[i];
    path += ` ${pen ? "L" : "M"} ${x(i + 1)} ${y(total)}`;
    pen = true;
  }
  if (last >= 0) {
    add("path", {
      d: path,
      fill: "none",
      stroke: "var(--accent)",
      "stroke-width": 2.5,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    });
    const endX = x(last + 1),
      endY = y(sum);
    add("circle", {
      cx: endX,
      cy: endY,
      r: 4,
      fill: "var(--accent)",
      stroke: "var(--paper)",
      "stroke-width": 2,
    });
    const dateOffset = 14;
    const labelSpacing = 16;
    const labelY = endY - 42;
    if (goalLabelY > labelY - labelSpacing && goalLabelY < endY + labelSpacing) {
      goalLabel.setAttribute("y", labelY - labelSpacing);
    }
    const anchor = endX < 140 ? "start" : "end";
    add("line", {
      x1: endX,
      y1: labelY + dateOffset + 1,
      x2: endX,
      y2: endY - 8,
      stroke: "var(--accent)",
      "stroke-width": 0.7,
    });
    add(
      "text",
      {
        ...label,
        x: endX,
        y: labelY,
        "text-anchor": anchor,
        fill: "var(--accent)",
        "font-size": mobile.matches ? 13 : 15,
      },
      (sum / 1000000).toFixed(2) + "m steps",
    );
    add(
      "text",
      { ...label, x: endX, y: labelY + dateOffset, "text-anchor": anchor },
      asOf,
    );
  }
  $("cumulative-chart").replaceChildren(svg);
  $("pace-note").textContent =
    last < 0
      ? "Your year starts with the first recorded day."
      : fmt(Math.abs(gap)) +
        " steps " +
        (gap >= 0 ? "ahead of" : "behind") +
        " pace";
}
$("cumulative-chart")
  .closest("section")
  .nextElementSibling.addEventListener("toggle", () => {
    positionGoal();
  });
let chartIndices = [],
  activeChartDay = null;
function showChartDay(i) {
  if (i === activeChartDay) return;
  activeChartDay = i;
  const n = values[i];
  $("chart-readout").textContent =
    dates[i].toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    }) +
    " · " +
    (n === undefined ? "No entry" : fmt(n) + " steps");
  $("chart")
    .querySelectorAll(".bar")
    .forEach((b) => {
      const active = Number(b.dataset.day) === i;
      b.classList.toggle("active", active);
      b.tabIndex = active ? 0 : -1;
    });
}
$("chart").onpointermove = (e) => {
  const r = $("chart").getBoundingClientRect(),
    slot = Math.max(
      0,
      Math.min(
        chartIndices.length - 1,
        Math.floor(((e.clientX - r.left) / r.width) * chartIndices.length),
      ),
    );
  showChartDay(chartIndices[slot]);
};
function renderChart() {
  activeChartDay = null;
  const indices = dates
    .map((_, i) => i)
    .filter((i) => !mobile.matches || dates[i].getUTCMonth() === month);
  chartIndices = indices;
  $("chart").replaceChildren();
  indices.forEach((i) => {
    const b = document.createElement("button"),
      n = values[i];
    b.className = "bar" + (n === undefined ? " empty" : n < GOAL ? " low" : "");
    b.style.height =
      (n === undefined ? 0 : Math.max(0.8, (n / max) * 100)) + "%";
    b.type = "button";
    b.setAttribute(
      "aria-label",
      key(dates[i]) + ": " + (n === undefined ? "no entry" : fmt(n) + " steps"),
    );
    b.dataset.day = i;
    b.tabIndex = -1;
    b.onfocus = () => showChartDay(i);
    b.onclick = () => showChartDay(i);
    b.onkeydown = (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const at = chartIndices.indexOf(i),
          next = Math.max(
            0,
            Math.min(
              chartIndices.length - 1,
              at + (e.key === "ArrowRight" ? 1 : -1),
            ),
          );
        $("chart").children[next].focus();
      }
    };
    $("chart").append(b);
  });
  $("axis").innerHTML = mobile.matches
    ? "<span>01</span><span>15</span><span>" + indices.length + "</span>"
    : monthNames
        .map((n) => "<span>" + n.slice(0, 3).toUpperCase() + "</span>")
        .join("");
  $("goal").style.bottom = (230 * GOAL) / max + "px";
  $("month-label").textContent = monthNames[month] + " 2026";
  $("prev").disabled = month === 0;
  $("next").disabled = month === 11;
  showChartDay(indices.includes(last) ? last : indices[0]);
  positionGoal();
}
function positionGoal() {
  $("goal").style.bottom = ($("chart").clientHeight * GOAL) / max + "px";
}
function select(i) {
  $("calendar-detail").textContent =
    dates[i].toLocaleDateString("en-GB", {
      day: "numeric",
      month: "long",
      timeZone: "UTC",
    }) +
    " · " +
    (values[i] === undefined ? "No entry" : fmt(values[i]) + " steps");
  document.querySelectorAll(".day").forEach((el) => {
    const active = Number(el.dataset.index) === i;
    el.classList.toggle("active", active);
    el.setAttribute("aria-pressed", String(active));
  });
}
monthNames.forEach((name, m) => {
  const section = document.createElement("div");
  section.innerHTML =
    '<div class="month-title">' +
    name +
    "<span>" +
    values.filter((n, i) => dates[i].getUTCMonth() === m && n >= GOAL).length +
    ' GOAL DAYS</span></div><div class="grid"></div>';
  const grid = section.querySelector(".grid");
  ["M", "T", "W", "T", "F", "S", "S"].forEach((s) => {
    const e = document.createElement("span");
    e.className = "weekday";
    e.textContent = s;
    grid.append(e);
  });
  const first = dates.findIndex((d) => d.getUTCMonth() === m);
  for (let j = 0; j < (dates[first].getUTCDay() + 6) % 7; j++)
    grid.append(document.createElement("span"));
  dates.forEach((d, i) => {
    if (d.getUTCMonth() !== m) return;
    const n = values[i],
      b = document.createElement("button");
    b.className =
      "day " + (n === undefined ? "missing" : n >= GOAL ? "hit" : "");
    b.textContent = d.getUTCDate();
    b.dataset.index = i;
    b.setAttribute(
      "aria-label",
      key(d) + ": " + (n === undefined ? "no entry" : fmt(n) + " steps"),
    );
    b.title = b.getAttribute("aria-label");
    b.onclick = () => select(i);
    grid.append(b);
  });
  $("months").append(section);
});
$("prev").onclick = () => {
  if (month > 0) {
    month--;
    renderChart();
  }
};
$("next").onclick = () => {
  if (month < 11) {
    month++;
    renderChart();
  }
};
const forest = {
  light: [
    "#F5F3EC",
    "#292E2B",
    "#666C65",
    "#DDDFD5",
    "#356B50",
    "#E5E8DC",
    "#AD623F",
    "#858B84",
  ],
  dark: [
    "#19251F",
    "#EAEDE7",
    "#ADB8AE",
    "#36483D",
    "#9AC5A5",
    "#2B3D31",
    "#E5AA81",
    "#8D9F91",
  ],
};
let dark = false;
try {
  dark = localStorage.getItem("steps-theme") === "dark";
} catch {}
function applyAppearance() {
  document.body.classList.toggle("dark", dark);
  document.body.style.colorScheme = dark ? "dark" : "light";
  [
    "paper",
    "ink",
    "muted",
    "line",
    "accent",
    "soft",
    "selected",
    "pace",
  ].forEach((key, i) =>
    document.body.style.setProperty(
      "--" + key,
      forest[dark ? "dark" : "light"][i],
    ),
  );
  $("theme").textContent = dark ? "LIGHT ◑" : "DARK ◐";
  $("theme").setAttribute(
    "aria-label",
    dark ? "Switch to light mode" : "Switch to dark mode",
  );
  try {
    localStorage.setItem("steps-theme", dark ? "dark" : "light");
  } catch {}
}
$("theme").onclick = () => {
  dark = !dark;
  applyAppearance();
};
applyAppearance();
mobile.addEventListener("change", () => {
  renderChart();
  renderCumulative();
});
window.addEventListener("resize", () => {
  positionGoal();
  renderCumulative();
});
renderChart();
renderCumulative();
