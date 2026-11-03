/* Framework-free view; callers supply a transport, never a BTT dependency. */
(function (root) {
  "use strict";
  const Model = root.LightControlState;
  const Kind = Model.Kind;
  const rounded = value => Math.round(value);
  const bounded = (value, low, high) => Math.max(low, Math.min(high, value));

  function kelvinTint(kelvin) {
    const t = bounded(kelvin, 2000, 7000) / 100;
    const red = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
    const green = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 :
      288.1221695283 * Math.pow(t - 60, -0.0755148492);
    const blue = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    return `rgb(${[red, green, blue].map(channel => rounded(bounded(channel, 0, 255))).join(", ")})`;
  }
  function swatch(light) {
    if (light.color_mode === "color_temp" && Number.isFinite(light.color_temp_kelvin)) {
      return kelvinTint(light.color_temp_kelvin);
    }
    if (Number.isFinite(light.hue)) {
      const saturation = Number.isFinite(light.saturation) ? bounded(light.saturation, 0, 100) : 100;
      // Dashboard HS uses HSV saturation; at full value, less saturation means more HSL lightness.
      return `hsl(${bounded(light.hue, 0, 360)} 100% ${100 - saturation / 2}%)`;
    }
    return "var(--muted)";
  }
  function element(document, tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(document, label, handler, className = "") {
    const node = element(document, "button", className, label);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  }
  function makeChips(document, parent, lights, model) {
    const all = button(document, "All", () => model.select(null), "light-chip all-chip");
    parent.append(all);
    const groups = new Map();
    const chips = new Map();
    lights.forEach(light => {
      const label = light.group_label || "Lights";
      if (!groups.has(label)) {
        const group = element(document, "div", "light-group");
        group.append(element(document, "span", "group-label", label));
        groups.set(label, group);
        parent.append(group);
      }
      const chip = button(document, "", () => model.select(light.entity_id), "light-chip");
      const dot = element(document, "span", "swatch");
      dot.setAttribute("aria-hidden", "true");
      chip.append(dot, element(document, "span", "", light.label || light.entity_id));
      groups.get(label).append(chip);
      chips.set(light.entity_id, {chip, dot});
    });
    return {all, chips};
  }
  function picker(document, parent, kind, model) {
    const host = element(document, "div", "picker-input");
    host.tabIndex = 0;
    host.setAttribute("role", "slider");
    host.setAttribute("aria-label", kind === Kind.colour ? "Colour: left/right hue, up/down saturation" : "White temperature");
    host.setAttribute("aria-valuemin", kind === Kind.colour ? "0" : "2000");
    host.setAttribute("aria-valuemax", kind === Kind.colour ? "360" : "7000");
    parent.append(host);
    const options = kind === Kind.colour ? {component: root.iro.ui.Wheel} :
      {component: root.iro.ui.Slider, options: {sliderType: "kelvin"}};
    const instance = new root.iro.ColorPicker(host, {width: 190, color: "#ffffff", layout: [options],
      layoutDirection: "vertical", minTemperature: 2000, maxTemperature: 7000,
      borderWidth: 1, borderColor: "#ffffff33", handleRadius: 7});
    const fields = () => kind === Kind.colour ?
      {hue: rounded(instance.color.hsv.h), saturation: rounded(instance.color.hsv.s)} :
      {kelvin: bounded(rounded(instance.color.kelvin), model.snapshot().capabilities.range.min, model.snapshot().capabilities.range.max)};
    instance.on("input:start", () => model.beginDrag(kind));
    instance.on("input:change", () => model.preview(kind, fields()));
    instance.on("input:end", () => model.release(kind, fields()));
    host.addEventListener("keydown", event => {
      pickerKey(event, kind, model);
      const value = model.snapshot().values;
      if (kind === Kind.colour) instance.color.hsv = {h: value.hue, s: value.saturation, v: 100};
      else instance.color.kelvin = value.kelvin;
    });
    host.addEventListener("keyup", event => {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        const value = model.snapshot().values;
        model.release(kind, kind === Kind.colour ? {hue: value.hue, saturation: value.saturation} : {kelvin: value.kelvin});
      }
    });
    host.addEventListener("blur", () => model.cancelDrag(kind));
    return {host, instance};
  }
  function pickerKey(event, kind, model) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const state = model.snapshot();
    if (!state.capabilities[kind]) return;
    model.beginDrag(kind);
    const value = state.values;
    const direction = ["ArrowLeft", "ArrowDown"].includes(event.key) ? -1 : 1;
    if (kind === Kind.white) {
      const range = state.capabilities.range;
      const kelvin = event.key === "Home" ? range.min : event.key === "End" ? range.max : value.kelvin + direction * 100;
      model.preview(kind, {kelvin: bounded(kelvin, range.min, range.max)});
    } else {
      const saturation = ["ArrowUp", "ArrowDown"].includes(event.key) ? bounded(value.saturation + direction, 0, 100) : value.saturation;
      const hue = event.key === "Home" ? 0 : event.key === "End" ? 360 :
        ["ArrowLeft", "ArrowRight"].includes(event.key) ? bounded(value.hue + direction, 0, 360) : value.hue;
      model.preview(kind, {hue, saturation});
    }
  }
  function updateChips(view, state) {
    const available = state.lights.filter(Model.available);
    view.all.disabled = !available.length;
    view.all.setAttribute("aria-pressed", String(available.length > 0 && available.every(light => state.selected.includes(light.entity_id))));
    state.lights.forEach(light => {
      const {chip, dot} = view.chips.get(light.entity_id);
      chip.disabled = !Model.available(light);
      chip.setAttribute("aria-pressed", String(state.selected.includes(light.entity_id)));
      chip.setAttribute("aria-label", `${light.label || light.entity_id}: ${light.state || "unavailable"}`);
      dot.style.background = swatch(light);
      dot.classList.toggle("off", light.state !== "on");
    });
  }
  function updatePicker(view, state, kind) {
    const colour = kind === Kind.colour;
    const value = state.values;
    view.host.setAttribute("aria-disabled", String(!state.capabilities[kind]));
    view.host.tabIndex = state.capabilities[kind] ? 0 : -1;
    view.host.classList.toggle("disabled", !state.capabilities[kind]);
    view.host.setAttribute("aria-valuenow", String(rounded(colour ? value.hue : value.kelvin)));
    view.host.setAttribute("aria-valuetext", colour ? `${rounded(value.hue)} degrees, ${rounded(value.saturation)}% saturation` : `${rounded(value.kelvin)} kelvin`);
    if (state.dragging.includes(Kind.colour)) return;
    if (!colour) {
      const {min, max} = state.capabilities.range;
      view.host.setAttribute("aria-valuemin", String(min));
      view.host.setAttribute("aria-valuemax", String(max));
      // iro divides by the range; a single shared temperature still needs a nonzero visual span.
      view.instance.setOptions({minTemperature: min, maxTemperature: Math.max(min + 1, max)});
    }
    if (colour) view.instance.color.hsv = {h: value.hue, s: value.saturation, v: 100};
    else view.instance.color.kelvin = value.kelvin;
  }
  function buildView(document, container, lights, model) {
    const groups = element(document, "div", "light-groups");
    groups.setAttribute("aria-label", "Select lights");
    const chips = makeChips(document, groups, lights, model);
    const toolbar = element(document, "div", "light-toolbar");
    const colour = button(document, "Colour", () => model.setMode(Kind.colour));
    const white = button(document, "White", () => model.setMode(Kind.white));
    const power = button(document, "On", () => model.release(Kind.power, {on: !model.snapshot().values.on}), "power-button");
    toolbar.append(colour, white, power);
    const pickerArea = element(document, "div", "picker-area");
    const colourPanel = element(document, "div", "picker-panel");
    const whitePanel = element(document, "div", "picker-panel white-panel");
    const wheel = picker(document, colourPanel, Kind.colour, model);
    const temperature = picker(document, whitePanel, Kind.white, model);
    const kelvin = element(document, "output", "kelvin-value");
    whitePanel.append(kelvin);
    pickerArea.append(colourPanel, whitePanel);
    const brightnessRow = element(document, "label", "brightness-row");
    brightnessRow.append(element(document, "span", "", "Brightness"));
    const brightness = element(document, "input");
    brightness.type = "range";
    brightness.min = "1"; brightness.max = "100"; brightness.step = "1";
    brightness.setAttribute("aria-label", "Brightness");
    const percent = element(document, "output");
    brightnessRow.append(percent, brightness);
    brightness.addEventListener("input", () => {
      model.beginDrag(Kind.brightness);
      model.preview(Kind.brightness, {brightness: Number(brightness.value)});
    });
    brightness.addEventListener("change", () => model.release(Kind.brightness, {brightness: Number(brightness.value)}));
    brightness.addEventListener("pointercancel", () => model.cancelDrag(Kind.brightness));
    const status = element(document, "div", "light-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    container.append(groups, toolbar, pickerArea, brightnessRow, status);
    return {chips, colour, white, power, wheel, temperature, colourPanel, whitePanel, kelvin, brightness, percent, status};
  }
  function render(view, state) {
    updateChips(view.chips, state);
    for (const kind of [Kind.colour, Kind.white]) {
      view[kind].disabled = !state.capabilities[kind];
      view[kind].setAttribute("aria-pressed", String(state.mode === kind));
    }
    view.power.disabled = !state.selected.length;
    view.power.textContent = state.values.on ? "Turn off" : "Turn on";
    view.power.setAttribute("aria-label", `${state.values.on ? "Turn off" : "Turn on"} selected lights`);
    view.power.setAttribute("aria-pressed", String(state.values.on));
    view.colourPanel.hidden = state.mode !== Kind.colour;
    view.whitePanel.hidden = state.mode !== Kind.white;
    updatePicker(view.wheel, state, Kind.colour);
    updatePicker(view.temperature, state, Kind.white);
    view.kelvin.textContent = `${rounded(state.values.kelvin)} K`;
    view.brightness.disabled = !state.selected.length;
    if (!state.dragging.includes(Kind.brightness)) view.brightness.value = String(rounded(state.values.brightness));
    view.percent.textContent = `${rounded(state.values.brightness)}%`;
    view.status.textContent = state.status;
    view.status.classList.toggle("error", Boolean(state.error) && !state.busy);
  }
  function mount(rootElement, {lights, transport}) {
    let view;
    let destroyed = false;
    const model = Model.create({lights, transport, onChange: state => {
      if (view && !destroyed) render(view, state);
    }});
    const container = element(rootElement.ownerDocument, "section", "light-control");
    view = buildView(rootElement.ownerDocument, container, lights, model);
    rootElement.replaceChildren(container);
    render(view, model.snapshot());
    return {refresh: options => model.refresh(options),
      open: () => model.refresh({resetSelection: true}),
      destroy: () => { destroyed = true; rootElement.replaceChildren(); }};
  }
  root.LightControl = Object.freeze({mount, swatch});
})(globalThis);
