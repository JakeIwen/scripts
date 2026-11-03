/* Selection and serialized intent queue, independent of the DOM and BTT. */
(function (root) {
  "use strict";
  const Kind = Object.freeze({colour: "colour", white: "white", brightness: "brightness", power: "power"});
  const slot = kind => kind === Kind.white ? Kind.colour : kind;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const number = (value, fallback) => Number.isFinite(value) ? value : fallback;
  const available = light => light.available === true && light.state !== "unavailable";

  function whiteRange(lights) {
    const min = Math.max(2000, ...lights.map(light => number(light.min_color_temp_kelvin, 2000)));
    const max = Math.min(7000, ...lights.map(light => number(light.max_color_temp_kelvin, 7000)));
    return {min: Math.ceil(min), max: Math.floor(max)};
  }

  class ControlState {
    constructor({lights, transport, onChange = () => {}}) {
      this.records = lights.map(light => ({...light}));
      this.transport = transport;
      this.onChange = onChange;
      this.selected = new Set();
      this.dragging = new Set();
      this.pending = new Map();
      this.readWaiters = [];
      this.resetOnRead = false;
      this.current = null;
      this.busy = false;
      this.error = "";
      this.mode = Kind.colour;
      this.values = {hue: 0, saturation: 100, kelvin: 4000, brightness: 100, on: false};
    }
    chosen() { return this.records.filter(light => this.selected.has(light.entity_id) && available(light)); }
    locked(kind) {
      return this.dragging.has(slot(kind)) || this.pending.has(slot(kind)) ||
        (this.current && slot(this.current.change.kind) === slot(kind));
    }
    capabilities() {
      const targets = this.chosen();
      const range = whiteRange(targets);
      return {range, colour: targets.length > 0 && targets.every(light => light.supports_hue === true),
        white: targets.length > 0 && targets.every(light => light.supports_color_temperature === true) && range.min <= range.max};
    }
    syncControls(pickMode = false) {
      const targets = this.chosen();
      const first = targets[0];
      if (!first) return;
      const caps = this.capabilities();
      if (pickMode && !this.dragging.has(Kind.colour)) {
        this.mode = first.color_mode === "color_temp" ? Kind.white : Kind.colour;
        if (!caps[this.mode] && caps[Kind.colour]) this.mode = Kind.colour;
        else if (!caps[this.mode] && caps[Kind.white]) this.mode = Kind.white;
      }
      if (!this.locked(Kind.colour)) {
        this.values.hue = Math.round(clamp(number(first.hue, 0), 0, 360));
        this.values.saturation = Math.round(clamp(number(first.saturation, 100), 0, 100));
        this.values.kelvin = Math.round(clamp(number(first.color_temp_kelvin, 4000), caps.range.min, Math.max(caps.range.min, caps.range.max)));
      }
      if (!this.locked(Kind.brightness)) this.values.brightness = Math.round(clamp(number(first.brightness, 100), 1, 100));
      if (!this.locked(Kind.power)) this.values.on = targets.every(light => light.state === "on");
    }
    snapshot() {
      const targets = this.chosen();
      const summary = targets.length === 1 ? targets[0].label : `${targets.length} lights selected`;
      return {lights: this.records.map(light => ({...light})), selected: [...this.selected], mode: this.mode,
        values: {...this.values}, capabilities: this.capabilities(), dragging: [...this.dragging], busy: this.busy, error: this.error,
        status: this.busy ? "Sending…" : this.error || (targets.length ? summary : "Select an available light")};
    }
    emit() { this.onChange(this.snapshot()); }
    applyReply(reply, reset) {
      if (!reply || reply.ok !== true || !Array.isArray(reply.lights)) {
        this.error = reply && typeof reply.error === "string" ? reply.error : "Invalid lighting response";
        return;
      }
      this.error = "";
      const incoming = new Map(reply.lights.map(light => [light.entity_id, light]));
      this.records.forEach(light => {
        const update = incoming.get(light.entity_id);
        if (update) Object.assign(light, update);
        else Object.assign(light, {available: false, state: "unavailable"});
      });
      if (reset) this.selected.clear();
      this.records.forEach(light => {
        if (!available(light)) this.selected.delete(light.entity_id);
        else if (reset) this.selected.add(light.entity_id);
      });
      this.syncControls(reset);
    }
    async request(operation, reset = false) {
      try { this.applyReply(await operation(), reset); }
      catch (failure) { this.error = failure instanceof Error ? failure.message : String(failure); }
    }
    async drain() {
      if (this.busy) return;
      this.busy = true;
      this.emit();
      while (this.pending.size || this.readWaiters.length) {
        if (this.pending.size) {
          const [key, intent] = this.pending.entries().next().value;
          this.pending.delete(key);
          this.current = intent;
          await this.request(async () => {
            const reply = await this.transport.write(intent.entities, intent.change);
            this.current = null;
            return reply;
          });
          this.current = null;
        } else {
          const waiters = this.readWaiters;
          const reset = this.resetOnRead;
          this.readWaiters = [];
          this.resetOnRead = false;
          await this.request(() => this.transport.read(), reset);
          waiters.forEach(resolve => resolve());
        }
        this.emit();
      }
      this.busy = false;
      this.emit();
    }
    refresh({resetSelection = false} = {}) {
      this.resetOnRead = this.resetOnRead || resetSelection;
      const done = new Promise(resolve => this.readWaiters.push(resolve));
      void this.drain();
      return done;
    }
    select(entity) {
      if (entity === null) {
        this.selected.clear();
        this.records.filter(available).forEach(light => this.selected.add(light.entity_id));
      } else {
        const light = this.records.find(item => item.entity_id === entity);
        if (!light || !available(light)) return;
        if (this.selected.has(entity)) this.selected.delete(entity);
        else this.selected.add(entity);
      }
      this.error = "";
      this.syncControls(true);
      this.emit();
    }
    setMode(next) {
      if (![Kind.colour, Kind.white].includes(next) || !this.capabilities()[next]) return;
      this.mode = next;
      this.emit();
    }
    beginDrag(kind) { this.dragging.add(slot(kind)); }
    preview(kind, fields) {
      if (kind === Kind.colour) Object.assign(this.values, {hue: fields.hue, saturation: fields.saturation});
      else if (kind === Kind.white) this.values.kelvin = fields.kelvin;
      else if (kind === Kind.brightness) this.values.brightness = fields.brightness;
      else if (kind === Kind.power) this.values.on = fields.on;
      this.emit();
    }
    release(kind, fields) {
      this.dragging.delete(slot(kind));
      const entities = this.chosen().map(light => light.entity_id);
      const caps = this.capabilities();
      if (!entities.length || ([Kind.colour, Kind.white].includes(kind) && !caps[kind])) return;
      this.preview(kind, fields);
      // Capture targets at release: later chip changes must not retarget an accepted intent.
      this.pending.set(slot(kind), {entities, change: {kind, ...fields}});
      this.error = "";
      void this.drain();
    }
    cancelDrag(kind) { this.dragging.delete(slot(kind)); this.syncControls(); this.emit(); }
  }
  function create(options) { return new ControlState(options); }
  root.LightControlState = Object.freeze({Kind, create, whiteRange, available});
  if (typeof module !== "undefined") module.exports = root.LightControlState;
})(globalThis);
