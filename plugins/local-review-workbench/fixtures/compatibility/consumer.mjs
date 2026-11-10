export function widgetNames(response) {
  return response.items.map((item) => item.name);
}
