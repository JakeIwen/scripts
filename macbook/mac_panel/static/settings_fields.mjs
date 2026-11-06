// How a settings declaration names its fields; shared by the Settings form and the search index.
export const settingLabel = setting => setting.label || setting.name.replace(/_/g, ' ');

export const settingAnchor = (tool, name) => `${tool}-${name}`;
