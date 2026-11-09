// Caption files contain prose, including in a standalone HTML presentation.
export function renderCaption(container, cue) {
  if (!cue) {
    container.replaceChildren();
    return;
  }
  if (typeof cue.text !== "string") throw new TypeError("Caption text must be a string");
  const span = container.ownerDocument.createElement("span");
  span.textContent = cue.text;
  container.replaceChildren(span);
}
