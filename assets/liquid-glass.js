/*!
 * Liquid Glass — vanilla JS adaptation of stormaref/LiquidGlassSkill (MIT).
 * The refraction field is a port of liquid-glass-js
 * (https://github.com/dashersw/liquid-glass-js), Copyright (c) 2025 Armagan
 * Amcalar, MIT. Permission is hereby granted, free of charge, to any person
 * obtaining a copy of that software to deal in it without restriction,
 * provided this notice travels with it; it is provided "as is", without
 * warranty of any kind. Keep this header on any copy or port.
 * Full notices: liquid-glass.LICENSE.txt (including the LiquidGlassSkill port).
 *
 * Only navigation and the selection bar receive maps. Baking yields between
 * short slices, never runs on scroll, and releases replaced/hidden filters.
 */
const ns = 'http://www.w3.org/2000/svg';
const filters = new Map();
const controllers = [];
const transparency = matchMedia('(prefers-reduced-transparency: reduce)');
const contrast = matchMedia('(forced-colors: active)');
const brands = navigator.userAgentData?.brands;
const supported = brands ? brands.some(b => /Chromium|Google Chrome|Microsoft Edge/i.test(b.brand)) : /Chrome\//.test(navigator.userAgent);
let defs;
let nextId = 0;

function svg(tag, attributes = {}) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function ensureDefs() {
  if (!defs?.isConnected) {
    const root = svg('svg', {width: 0, height: 0, 'aria-hidden': 'true'});
    root.style.position = 'fixed';
    root.style.pointerEvents = 'none';
    defs = svg('defs');
    root.append(defs);
    document.body.append(root);
  }
  return defs;
}

const yieldFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
const clampByte = value => Math.max(0, Math.min(255, Math.round(value)));

async function buildMap(w, h, radius, pageW, pageH, cancelled) {
  const ss = Math.max(.25, Math.min(2, 1400 / Math.max(w, h)));
  const bw = Math.max(1, Math.round(w * ss));
  const bh = Math.max(1, Math.round(h * ss));
  const r = radius * ss;
  const minDim = Math.min(w, h);
  const dx = new Float32Array(bw * bh);
  const dy = new Float32Array(bw * bh);
  let maxAbs = 0;
  let slice = performance.now();
  for (let py = 0; py < bh; py++) {
    for (let px = 0; px < bw; px++) {
      const cx = (px + .5) / bw;
      const cy = (py + .5) / bh;
      const tx = Math.abs(px + .5 - bw / 2) - (bw / 2 - r);
      const ty = Math.abs(py + .5 - bh / 2) - (bh / 2 - r);
      const dist = Math.max(-(Math.hypot(Math.max(tx, 0), Math.max(ty, 0)) + Math.min(Math.max(tx, ty), 0) - r), 0) / ss;
      const rim = Math.exp(-dist * 1.7);
      const total = Math.exp(-dist * .5) * .015 + rim * .028;
      let nx = cx - .5, ny = cy - .5;
      const length = Math.hypot(nx, ny);
      if (length > 0) { nx /= length; ny /= length; }
      const corner = Math.exp(-Math.max(Math.min(cx, 1 - cx), Math.min(cy, 1 - cy)) * minDim * .3) * .06;
      const ripple = Math.sin(dist / minDim * 25) * .26 * rim;
      const i = py * bw + px;
      dx[i] = (nx * (total + corner) - ny * ripple) * pageW;
      dy[i] = (ny * (total + corner) + nx * ripple) * pageH;
      maxAbs = Math.max(maxAbs, Math.abs(dx[i]), Math.abs(dy[i]));
    }
    if (performance.now() - slice > 4) {
      await yieldFrame();
      if (cancelled()) return null;
      slice = performance.now();
    }
  }
  const scale = Math.max(maxAbs * 2, .0001);
  const bias = scale * (128 / 255 - .5);
  const canvas = document.createElement('canvas');
  canvas.width = bw; canvas.height = bh;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const image = ctx.createImageData(bw, bh);
  for (let row = 0; row < bh; row++) {
    for (let x = 0; x < bw; x++) {
      const i = row * bw + x;
      image.data[i * 4] = clampByte(255 * (.5 + (dx[i] - bias) / scale));
      image.data[i * 4 + 1] = clampByte(255 * (.5 + (dy[i] - bias) / scale));
      image.data[i * 4 + 2] = 128;
      image.data[i * 4 + 3] = 255;
    }
    if (performance.now() - slice > 4) {
      await yieldFrame();
      if (cancelled()) return null;
      slice = performance.now();
    }
  }
  ctx.putImageData(image, 0, 0);
  return {url: canvas.toDataURL('image/png'), scale};
}

function release(controller) {
  const entry = filters.get(controller.key);
  if (entry && --entry.refs === 0) {
    entry.node.remove();
    filters.delete(controller.key);
  }
  controller.key = null;
  controller.element.style.removeProperty('backdrop-filter');
}

async function rebuild(controller) {
  const {element} = controller;
  const revision = ++controller.revision;
  if (!element.isConnected || !element.offsetWidth || !element.offsetHeight || transparency.matches || contrast.matches) {
    release(controller);
    return;
  }
  const w = Math.round(element.offsetWidth), h = Math.round(element.offsetHeight);
  const computed = getComputedStyle(element).borderTopLeftRadius;
  const radius = Math.min((parseFloat(computed) || 0) * (computed.endsWith('%') ? Math.min(w, h) / 100 : 1), Math.min(w, h) / 2);
  const pageW = innerWidth, pageH = innerHeight;
  const key = [w, h, radius, pageW, pageH].join('|');
  if (key === controller.key && filters.get(key)?.node.isConnected) return;
  release(controller);
  const cancelled = () => revision !== controller.revision || !element.isConnected || transparency.matches || contrast.matches;
  try {
    let entry = filters.get(key);
    if (!entry?.node.isConnected) {
      const map = await buildMap(w, h, radius, pageW, pageH, cancelled);
      if (!map || cancelled()) return;
      // A same-size surface may have completed while this bake yielded.
      entry = filters.get(key);
      if (!entry?.node.isConnected) {
        const id = `liquid-glass-${nextId++}`;
        const margin = map.scale / 2 + 3 * 2 * .35;
        const node = svg('filter', {id, x: `${-margin / w * 100}%`, y: `${-margin / h * 100}%`,
          width: `${100 + 2 * margin / w * 100}%`, height: `${100 + 2 * margin / h * 100}%`, 'color-interpolation-filters': 'sRGB'});
        node.append(svg('feImage', {href: map.url, x: 0, y: 0, width: w, height: h, preserveAspectRatio: 'none', result: 'map'}),
          svg('feDisplacementMap', {in: 'SourceGraphic', in2: 'map', scale: map.scale, xChannelSelector: 'R', yChannelSelector: 'G', result: 'displaced'}),
          svg('feGaussianBlur', {in: 'displaced', stdDeviation: .7}));
        ensureDefs().append(node);
        entry = {node, id, refs: 0};
        filters.set(key, entry);
      }
    }
    entry.refs++;
    controller.key = key;
    element.style.backdropFilter = `url(#${entry.id}) blur(12px) saturate(125%)`;
  } catch {
    // Canvas/SVG restrictions keep the CSS glass fallback; reading still works.
    release(controller);
  }
}

function schedule(controller) {
  ++controller.revision;
  clearTimeout(controller.timer);
  controller.timer = setTimeout(() => rebuild(controller), 180);
}

if (supported && typeof ResizeObserver === 'function') {
  document.querySelectorAll('[data-liquid-glass]').forEach(element => {
    const controller = {element, key: null, revision: 0, timer: null};
    controller.observer = new ResizeObserver(() => schedule(controller));
    controller.observer.observe(element);
    controllers.push(controller);
    schedule(controller);
  });
  const refresh = () => controllers.forEach(schedule);
  window.addEventListener('resize', refresh, {passive: true});
  transparency.addEventListener('change', refresh);
  contrast.addEventListener('change', refresh);
  // Release on page exit, and rebuild after a back/forward-cache restoration.
  window.addEventListener('pagehide', () => {
    controllers.forEach(controller => {
      ++controller.revision;
      clearTimeout(controller.timer);
      controller.observer.disconnect();
      release(controller);
    });
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) controllers.forEach(controller => {
      controller.observer.observe(controller.element);
      schedule(controller);
    });
  });
}
