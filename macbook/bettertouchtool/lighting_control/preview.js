/* Browser-only fixture adapter. It cannot access the shell or the dashboard. */
(function (root) {
  "use strict";
  const Kind = root.LightControlState.Kind;
  function create(initialLights) {
    const lights = initialLights.map(light => ({...light}));
    const delay = () => new Promise(resolve => setTimeout(resolve, 450));
    const reply = () => ({ok: true, lights: lights.map(light => ({...light}))});
    return {
      async read() { await delay(); return reply(); },
      async write(entities, change) {
        await delay();
        lights.filter(light => entities.includes(light.entity_id)).forEach(light => {
          if (change.kind === Kind.power) light.state = change.on ? "on" : "off";
          if (change.kind === Kind.brightness) light.brightness = change.brightness;
          if (change.kind === Kind.colour) Object.assign(light, {color_mode: "hs", hue: change.hue, saturation: change.saturation});
          if (change.kind === Kind.white) Object.assign(light, {color_mode: "color_temp", color_temp_kelvin: change.kelvin});
        });
        return reply();
      }
    };
  }
  root.LightingPreview = Object.freeze({create});
})(globalThis);
