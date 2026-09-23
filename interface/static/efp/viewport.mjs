/** Zoom and pan the SVG viewBox while keeping the legend and values stationary. */
export function createViewport(svg, viewport, onChange) {
  const original = svg.getAttribute('viewBox');
  const [x, y, width, height] = original.split(/[\s,]+/).map(Number);
  svg.dataset.originalViewBox = original;
  svg.style.height = '100%';
  const min = 0.5, max = 4;
  let zoom = 1, cx = x + width / 2, cy = y + height / 2;
  let enabled = false, drag = null;

  function update() {
    if (zoom <= 1) { cx = x + width / 2; cy = y + height / 2; }
    else {
      cx = Math.max(x, Math.min(x + width, cx));
      cy = Math.max(y, Math.min(y + height, cy));
    }
    svg.setAttribute('viewBox', `${cx - width / zoom / 2} ${cy - height / zoom / 2} ${width / zoom} ${height / zoom}`);
    svg.dataset.zoom = String(zoom);
    viewport.dataset.zoomed = String(enabled && zoom > 1);
    onChange({zoom, canZoomIn: enabled && zoom < max, canZoomOut: enabled && zoom > min, enabled});
  }

  function zoomTo(next, anchor) {
    if (!enabled) return;
    next = Math.max(min, Math.min(max, next));
    if (anchor) {
      cx = anchor.x + (cx - anchor.x) * zoom / next;
      cy = anchor.y + (cy - anchor.y) * zoom / next;
    }
    zoom = next;
    update();
  }

  const point = (event, inverse = svg.getScreenCTM().inverse()) =>
    new DOMPoint(event.clientX, event.clientY).matrixTransform(inverse);

  viewport.addEventListener('wheel', event => {
    if (!enabled) return;
    event.preventDefault();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1);
    zoomTo(zoom * Math.exp(-Math.max(-500, Math.min(500, delta)) * .002), point(event));
  }, {passive: false});

  viewport.addEventListener('pointerdown', event => {
    if (!enabled || zoom <= 1 || event.button !== 0 || drag) return;
    const inverse = svg.getScreenCTM().inverse();
    drag = {id: event.pointerId, start: point(event, inverse), inverse, cx, cy};
    viewport.setPointerCapture(event.pointerId);
    viewport.dataset.dragging = 'true';
    event.preventDefault();
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const current = point(event, drag.inverse);
    cx = drag.cx - (current.x - drag.start.x);
    cy = drag.cy - (current.y - drag.start.y);
    update();
  });
  function endDrag() {
    if (!drag) return;
    const id = drag.id;
    drag = null;
    viewport.dataset.dragging = 'false';
    if (viewport.hasPointerCapture(id)) viewport.releasePointerCapture(id);
  }
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) viewport.addEventListener(name, endDrag);

  function reset() {
    endDrag();
    zoom = 1;
    update();
  }

  viewport.addEventListener('keydown', event => {
    if (!enabled) return;
    if (['+', '=', '-', '0'].includes(event.key)) {
      event.preventDefault();
      if (event.key === '0') reset();
      else zoomTo(zoom * (event.key === '-' ? 1 / 1.25 : 1.25));
    } else if (zoom > 1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'ArrowLeft') cx -= width / zoom / 10;
      if (event.key === 'ArrowRight') cx += width / zoom / 10;
      if (event.key === 'ArrowUp') cy -= height / zoom / 10;
      if (event.key === 'ArrowDown') cy += height / zoom / 10;
      update();
    }
  });
  update();
  return {
    zoomIn: () => zoomTo(zoom * 1.25),
    zoomOut: () => zoomTo(zoom / 1.25),
    reset,
    setEnabled(value) { enabled = value; if (!value) reset(); else update(); },
    isDragging: () => drag !== null,
  };
}
